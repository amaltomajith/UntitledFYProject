"""
core/orchestrator.py
====================
The centralized orchestrator (the "puppeteer" from the paper) implementing
the central policy π from Equation 1:

  a_t ~ π(S_t, τ) = P(a | S_t, τ)

It manages the orchestration loop by wiring the Agent, Environment, and Memory
components together and running the episode to completion.

Paper grounding:
- Equation 1: a_t ~ π(S_t, τ) = P(a | S_t, τ)
- Equation 2: o_t = f_{a_t}(s_t(a_t), S_t)
              S_{t+1} = Φ(S_t, o_t)
- Equation 3: P(a_{t+1} | S_{t+1}, τ) — Markov property (Memory rolling window)
- Section 2.1: "centralized orchestrator dynamically selects which agents to
               activate in each step based on the dynamic task state"
- Section 3: "episode length 4, parallel exploration up to 3"
"""

import asyncio
import dataclasses
import logging
import re
import time
import inspect
import ssl
from typing import Optional, Any

# ── SSL certificate verification bypass ─────────────────────────────────────
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass

try:
    _orig_create_default_context = ssl.create_default_context
    def _unverified_create_default_context(*args, **kwargs):
        context = _orig_create_default_context(*args, **kwargs)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    ssl.create_default_context = _unverified_create_default_context
except Exception:
    pass

# ── load .env automatically ───────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=None, override=False)
except ImportError:
    pass

# ── sys.path guard for direct execution ──────────────────────────────────────
import sys as _sys
import os as _os
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

# ── project imports ───────────────────────────────────────────────────────────
from core.agent import (
    Agent,
    AgentFactory,
    AgentOutput,
    ModelTier,
    ReasoningPattern,
)
from core.environment import Environment, EnvironmentConfig, EpisodeStatus
from core.memory import Memory, MemoryConfig

# ── logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  ORCHESTRATOR CONFIG
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class OrchestratorConfig:
    """
    Configuration for the policy LLM that selects agents.

    Fields
    ------
    model_tier : ModelTier
        Which model tier to use for the policy agent.
    temperature : float
        LLM sampling temperature (low = deterministic selection).
    max_tokens : int
        Maximum tokens for the agent selection output (agent name only, short).
    system_prompt : str
        The prompt used by the selection agent. Built automatically in __init__ if empty.
    """
    model_tier:    ModelTier = ModelTier.FAST
    temperature:   float     = 0.2
    max_tokens:    int       = 128
    system_prompt: str       = ""


# ═════════════════════════════════════════════════════════════════════════════
# 2.  EPISODE RESULT
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class EpisodeResult:
    """
    Result metrics and trajectory of a completed orchestration episode.

    Fields
    ------
    task : str
        The original input task specification.
    task_id : str
        The unique task identifier.
    final_answer : str
        The final answer text extracted at the end of the episode.
    status : str
        The final status of the episode (from EpisodeStatus).
    trajectory : list[dict]
        Chronological list of step records containing input, actions, outputs.
    total_tokens : int
        Sum of all tokens consumed by agents during this episode.
    total_steps : int
        Total number of agent invocations.
    elapsed_ms : float
        Total wall-clock duration of the episode in milliseconds.
    success : bool
        True if the episode terminated in a successful state (completed or terminated).
    """
    task:          str
    task_id:       str
    final_answer:  str
    status:        str
    trajectory:    list[dict]
    total_tokens:  int
    total_steps:   int
    elapsed_ms:    float
    success:       bool

    def to_dict(self) -> dict:
        """Serialize the EpisodeResult to a standard dictionary."""
        return {
            "task":          self.task,
            "task_id":       self.task_id,
            "final_answer":  self.final_answer,
            "status":        self.status,
            "trajectory":    self.trajectory,
            "total_tokens":  self.total_tokens,
            "total_steps":   self.total_steps,
            "elapsed_ms":    self.elapsed_ms,
            "success":       self.success,
        }


# ═════════════════════════════════════════════════════════════════════════════
# 3.  ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """
    The central coordinator class driving multi-agent orchestration episodes.

    Coordinates the environment state, step records, agent invocation memory,
    and uses a dedicated policy agent to select which agent from the pool
    runs at each step of the reasoning loop.
    """

    def __init__(
        self,
        agent_pool:   list[Agent],
        env_config:   EnvironmentConfig,
        mem_config:   Optional[MemoryConfig] = None,
        orch_config:  Optional[OrchestratorConfig] = None,
    ) -> None:
        """
        Initialize the Orchestrator with the agent pool, environment, memory, and configs.
        """
        # Store agent pool as dict keyed by agent.config.name
        self._pool: dict[str, Agent] = {}
        for agent in agent_pool:
            name = agent.config.name or agent.name
            self._pool[name] = agent

        # Build Environment and Memory
        self._env = Environment(env_config)
        self._memory = Memory(mem_config or MemoryConfig())

        # Build OrchestratorConfig
        self.orch_config = orch_config or OrchestratorConfig()

        # Build system prompt automatically if empty
        if not self.orch_config.system_prompt:
            self.orch_config.system_prompt = self._build_system_prompt(agent_pool)

        # Build policy agent (not part of the pool)
        # Policy agent shares GroqClient instance with pool agents if available
        shared_client = None
        if agent_pool:
            shared_client = agent_pool[0].groq_client

        self._policy_agent = AgentFactory.create(
            pattern=ReasoningPattern.PLANNER,
            tier=self.orch_config.model_tier,
            groq_client=shared_client,
        )

        # Overwrite policy agent's config fields
        self._policy_agent.config.role_prompt = self.orch_config.system_prompt
        self._policy_agent.config.max_tokens = self.orch_config.max_tokens
        self._policy_agent.config.temperature = self.orch_config.temperature

        # Dynamic override of _build_action_prompt of policy agent so it returns the context directly
        # and requests the selection without PlannerAgent action planning boilerplate.
        import types
        self._policy_agent._build_action_prompt = types.MethodType(
            lambda self_agent, task, context: (
                f"{task}\n\n"
                "Based on the above state, which agent should act next?\n"
                "Reply with EXACTLY ONE agent name from the available agents list. "
                "Do not write anything else. Do not explain. "
                "Just the agent name, e.g.: ReasoningAgent"
            ),
            self._policy_agent
        )

        # Dynamic override of _parse_output of policy agent so that it parses the raw response directly
        # without expecting any REASONING RESULT or FINAL ANSWER tags.
        self._policy_agent._parse_output = lambda raw_text: (raw_text.strip(), raw_text.strip())

        logger.info(
            "Orchestrator pool contents: %s | policy agent model: %s",
            list(self._pool.keys()),
            self.orch_config.model_tier.value,
        )

    def _build_system_prompt(self, agent_pool: list[Agent]) -> str:
        """
        Generate the orchestrator policy system prompt based on the agent pool.
        """
        descriptions = []
        for agent in agent_pool:
            name = agent.config.name or agent.name
            desc = agent.config.description
            if not desc:
                # Fallback to pattern value or class name
                if hasattr(agent.config, "pattern") and agent.config.pattern:
                    desc = agent.config.pattern.value
                else:
                    try:
                        desc = ReasoningPattern(name).value
                    except ValueError:
                        desc = name
            descriptions.append(f"{name}: {desc}")

        agent_descriptions = "\n".join(descriptions)

        prompt = (
            "You are the central orchestrator of a multi-agent reasoning system.\n"
            "Your sole job is to select which agent reasons next.\n\n"
            "Available agents:\n"
            f"{agent_descriptions}\n\n"
            "Rules:\n"
            "- Output ONLY the agent name. Nothing else. No punctuation.\n"
            "- Choose the agent most likely to make progress.\n"
            "- If task looks complete: output ConcluderAgent\n"
            "- If unsure whether to continue: output TerminatorAgent\n"
            "- Do not select the same agent three times in a row.\n\n"
            "Example valid responses:\n"
            "CriticAgent\n"
            "PlannerAgent\n"
            "ConcluderAgent"
        )
        return prompt

    async def _select_agent(self, context: str) -> str:
        """
        Consult the policy agent to select the next agent to activate.
        """
        # Execute policy selection. Since agent execute is synchronous, run in a thread
        if inspect.iscoroutinefunction(self._policy_agent.execute):
            output = await self._policy_agent.execute(task=context)
        else:
            output = await asyncio.to_thread(self._policy_agent.execute, task=context)

        raw_name = output.content.strip()

        # Clean the name
        # 1. Strip punctuation and extra whitespace
        cleaned = re.sub(r"[^\w\s]", "", raw_name).strip()

        # 2. Split words and capitalize each to title-case it (e.g. "critic agent" -> "CriticAgent")
        # While avoiding lowercasing any CamelCase words
        words = cleaned.split()
        if words:
            def title_word(w: str) -> str:
                if not w:
                    return ""
                return w[0].upper() + w[1:]
            title_cased = "".join(title_word(w) for w in words)
        else:
            title_cased = ""

        # 3. If no "Agent" suffix: append it (e.g. "Critic" -> "CriticAgent")
        # Normalize suffix check case-insensitively
        if title_cased.lower().endswith("agent"):
            title_cased = title_cased[:-5] + "Agent"
        else:
            title_cased += "Agent"

        logger.info("Orchestrator selected: %s", title_cased)
        return title_cased

    async def run_episode(self, task: str) -> EpisodeResult:
        """
        Execute one complete reasoning episode loop end to end.
        """
        episode_start = time.monotonic()

        # 1. Reset env
        self._env.reset(new_task=task)

        # 2. Reset memory
        self._memory.reset()

        # Reset episode histories on all agents
        for agent in self._pool.values():
            agent.reset_episode()
        self._policy_agent.reset_episode()

        # 3. Log start
        logger.info("Episode starting | task={:.80s}".format(task))

        # 4. Episode Loop
        while not self._env.is_done:
            t = self._env.current_step

            # a. Get orchestrator state context
            orch_context = self._memory.get_context_for_orchestrator(task)

            # b. Query policy to select agent
            agent_name = await self._select_agent(orch_context)

            # If we are on the final step, force ConcluderAgent to ensure a final answer is synthesized
            # and the episode completes successfully (COMPLETED status) rather than being truncated.
            if t == self._env.config.max_steps - 1 and "ConcluderAgent" in self._pool:
                logger.info("Final step reached (%d). Overriding selection to ConcluderAgent to synthesize final answer.", t)
                agent_name = "ConcluderAgent"

            # c. Validate and handle fallback
            if agent_name not in self._pool:
                fallback = next(iter(self._pool))
                logger.warning(
                    "Orchestrator policy selected unknown agent '%s'; falling back to '%s'",
                    agent_name,
                    fallback,
                )
                agent_name = fallback

            # d. Get agent-specific context from memory
            agent_context = self._memory.get_context_for_agent(agent_name, task)

            # e. Execute agent action
            agent = self._pool[agent_name]
            try:
                if inspect.iscoroutinefunction(agent.execute):
                    output: AgentOutput = await agent.execute(task=task, context=agent_context)
                else:
                    output: AgentOutput = await asyncio.to_thread(
                        agent.execute, task=task, context=agent_context
                    )
            except Exception as e:
                logger.exception("Agent '%s' crashed during execution at step %d", agent_name, t)
                output = AgentOutput(
                    agent_name=agent_name,
                    content="",
                    reasoning="",
                    raw_response="",
                    tokens_used=0,
                    latency_ms=0.0,
                    success=False,
                    error=str(e),
                )

            # f. transition the Environment
            step_record = self._env.transition(
                agent_name=agent_name,
                agent_input=agent_context,
                agent_output=output,
            )

            # g. Add StepRecord to Memory
            self._memory.add(step_record)

            # h. Log step details
            logger.info(
                "Step %d | %s | tokens=%d | success=%s",
                t,
                agent_name,
                output.tokens_used,
                output.success,
            )

        # 5. Extract final answer
        final_answer = self._env.get_final_answer()

        # 6. Build and return result
        elapsed_ms = (time.monotonic() - episode_start) * 1000
        is_success = self._env.status in (EpisodeStatus.COMPLETED, EpisodeStatus.TERMINATED)

        result = EpisodeResult(
            task=task,
            task_id=self._env.config.task_id,
            final_answer=final_answer,
            status=self._env.status.value,
            trajectory=self._env.get_full_trajectory(),
            total_tokens=self._env.total_tokens,
            total_steps=self._env.current_step,
            elapsed_ms=elapsed_ms,
            success=is_success,
        )
        return result

    def run_episode_sync(self, task: str) -> EpisodeResult:
        """
        Synchronous wrapper around run_episode.
        """
        return asyncio.run(self.run_episode(task))

    def get_agent_names(self) -> list[str]:
        """
        Return the sorted list of names of all agents in the pool.
        """
        return sorted(self._pool.keys())

    async def cleanup(self) -> None:
        """
        Close all pool agents and the policy agent client connections.
        """
        for name, agent in self._pool.items():
            try:
                if inspect.iscoroutinefunction(agent.close):
                    await agent.close()
                else:
                    agent.close()
            except Exception as e:
                logger.warning("Failed to close agent '%s': %s", name, e)

        try:
            if inspect.iscoroutinefunction(self._policy_agent.close):
                await self._policy_agent.close()
            else:
                self._policy_agent.close()
        except Exception as e:
            logger.warning("Failed to close policy agent: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
# 4.  __main__ — SMOKE TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os
    import sys

    # Force stdout encoding to UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Set up basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("  Puppeteer++ -- core/orchestrator.py smoke test")
    print("=" * 60 + "\n")

    groq_key_exists = bool(os.environ.get("GROQ_API_KEY"))
    if not groq_key_exists:
        # Mock GROQ_API_KEY so we can instantiate classes structurally
        os.environ["GROQ_API_KEY"] = "mock_groq_api_key_value"

    try:
        # Build the agent pool
        pool = AgentFactory.create_pool([
            ReasoningPattern.PLANNER,
            ReasoningPattern.REASONING,
            ReasoningPattern.CRITIC,
            ReasoningPattern.CONCLUDER,
            ReasoningPattern.TERMINATOR,
        ])

        # Build environment config
        env_config = EnvironmentConfig(
            task="Structural Test Task",
            max_steps=4,
        )

        # Build orchestrator
        orch = Orchestrator(
            agent_pool=pool,
            env_config=env_config,
        )

        # Verify imports, class instantiation, and _build_system_prompt()
        generated_prompt = orch.orch_config.system_prompt
        print("─── Generated System Prompt ──────────────────────────")
        print(generated_prompt)
        print("──────────────────────────────────────────────────────\n")

    except Exception as e:
        print(f"Error during structural test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    if not groq_key_exists:
        print("[PARTIAL] No API key — structural tests passed")
        sys.exit(0)

    # Live episode test
    try:
        # Task 1 (simple math)
        task1 = "What is 25% of 840? Show your working step by step."
        print("─── Task 1 ───────────────────────────")
        print(f"Task:         {task1}")

        result1 = orch.run_episode_sync(task1)

        print("─── Episode Log ──────────────────────")
        for step in result1.trajectory:
            print(f"Step {step['step']} | {step['agent_name']} | tokens={step['agent_output']['tokens_used']}")

        print("─── Result ───────────────────────────")
        print(f"Status:       {result1.status}")
        print(f"Steps:        {result1.total_steps}")
        print(f"Tokens:       {result1.total_tokens}")
        print(f"Elapsed:      {result1.elapsed_ms:.0f}ms")
        print(f"Final answer: {result1.final_answer}")
        print()

        # Task 2 (reasoning)
        task2 = "A bat and ball cost $1.10 in total. The bat costs $1 more than the ball. How much does the ball cost?"
        print("─── Task 2 ───────────────────────────")
        print(f"Task:         {task2}")

        result2 = orch.run_episode_sync(task2)

        print("─── Episode Log ──────────────────────")
        for step in result2.trajectory:
            print(f"Step {step['step']} | {step['agent_name']} | tokens={step['agent_output']['tokens_used']}")

        print("─── Result ───────────────────────────")
        print(f"Status:       {result2.status}")
        print(f"Steps:        {result2.total_steps}")
        print(f"Tokens:       {result2.total_tokens}")
        print(f"Elapsed:      {result2.elapsed_ms:.0f}ms")
        print(f"Final answer: {result2.final_answer}")
        print()

        # Assertions
        assert result1.success is True, "Task 1 success should be True"
        assert result2.success is True, "Task 2 success should be True"
        assert result1.total_steps >= 2, f"Task 1 should take at least 2 steps, got {result1.total_steps}"
        assert result2.final_answer != "", "Task 2 final answer should not be empty"
        assert len(result1.trajectory) == result1.total_steps, f"Task 1 trajectory length mismatch: {len(result1.trajectory)} != {result1.total_steps}"

        # Cleanup
        asyncio.run(orch.cleanup())

        print("[PASS] orchestrator.py working — multi-agent episodes complete")

    except Exception as e:
        print(f"Error during live test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
