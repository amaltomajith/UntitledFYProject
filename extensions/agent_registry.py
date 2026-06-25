"""
extensions/agent_registry.py
============================
Extension 2: Dynamic Agent Injection.
The second original research contribution.

Paper limitation (Section D - Limitations):
  "The framework assumes a fixed set of agents and tools,
   limiting adaptability and responsiveness to task variations.
   Enabling dynamic agent or tool modification during inference
   would improve flexibility and robustness."

This file addresses that limitation by implementing a dynamic agent registry.
It classifies the task requirements at runtime and injects specialized agents
(e.g., PythonAgent, WebSearchAgent) into the active pool mid-episode when needed.
It also serves as the integration bridge for Roshan's NeuralForge sector router,
exposing set_agent_pool() to inject domain-specific agents before an episode begins.

Research Claim:
  Dynamic agent injection enables task completion on tool-required scenarios
  where a fixed pool would fail, improving the overall task success rate.
"""

import os
import json
import time
import asyncio
import logging
import dataclasses
import types
import ssl
from typing import Optional, Any, Union

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

# ── sys.path guard for direct execution ──────────────────────────────────────
import sys as _sys
import os as _os
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

# Import base agent structures
from core.agent import (
    Agent,
    AgentFactory,
    AgentOutput,
    AgentConfig,
    ModelTier,
    ReasoningPattern,
    GroqClient,
    BaseAgent,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  DOMAIN-SPECIFIC SPECIALIZED AGENTS
# ═════════════════════════════════════════════════════════════════════════════

class PythonAgent(BaseAgent):
    """
    Agent specialized in code generation and execution.
    It receives the task and executes programmatic solutions.
    """
    def __init__(self, groq_client: GroqClient, model_name: str = ModelTier.FAST.value):
        config = AgentConfig(
            model_name=model_name,
            role_prompt=(
                "You are the PythonAgent.\n"
                "Your objective is to generate clean, correct, and executable Python code "
                "to address programmatic tasks or data analysis requirements.\n"
                "Write clear code with comments.\n"
                "End your response with:\n"
                "REASONING RESULT: [your step-by-step logic]\n"
                "FINAL ANSWER: [the complete code block or output]"
            ),
            tools=["python_interpreter"],
            name="PythonAgent",
            description="Generates and executes Python code to solve programmatic queries.",
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        return (
            f"Generate python code to solve this task.\n"
            f"Task: {task}\n\n"
            f"Current Episode Context:\n{context}\n"
        )


class WebSearchAgent(BaseAgent):
    """
    Agent specialized in web search and information retrieval.
    """
    def __init__(self, groq_client: GroqClient, model_name: str = ModelTier.FAST.value):
        config = AgentConfig(
            model_name=model_name,
            role_prompt=(
                "You are the WebSearchAgent.\n"
                "Your objective is to search external sources to retrieve precise, "
                "up-to-date information for queries.\n"
                "End your response with:\n"
                "REASONING RESULT: [your step-by-step search strategy and findings]\n"
                "FINAL ANSWER: [the synthesized answer from web sources]"
            ),
            tools=["web_search"],
            name="WebSearchAgent",
            description="Queries the web to gather factual real-time information.",
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        return (
            f"Query the web to answer this task.\n"
            f"Task: {task}\n\n"
            f"Current Episode Context:\n{context}\n"
        )


class ModifierAgent(BaseAgent):
    """
    Agent specialized in creative writing, editing, and content modification.
    """
    def __init__(self, groq_client: GroqClient, model_name: str = ModelTier.FAST.value):
        config = AgentConfig(
            model_name=model_name,
            role_prompt=(
                "You are the ModifierAgent.\n"
                "Your objective is to refine, modify, edit, or write creative and descriptive content.\n"
                "End your response with:\n"
                "REASONING RESULT: [details of your edits / structure]\n"
                "FINAL ANSWER: [the refined text or creative composition]"
            ),
            tools=[],
            name="ModifierAgent",
            description="Performs creative adjustments, text editing, and writing tasks.",
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        return (
            f"Refine and modify content to solve this task.\n"
            f"Task: {task}\n\n"
            f"Current Episode Context:\n{context}\n"
        )


class SummarizerAgent(BaseAgent):
    """
    Agent specialized in data analysis, synthesis, and summarization.
    """
    def __init__(self, groq_client: GroqClient, model_name: str = ModelTier.FAST.value):
        config = AgentConfig(
            model_name=model_name,
            role_prompt=(
                "You are the SummarizerAgent.\n"
                "Your objective is to analyze dense context or data, extract key insights, "
                "and synthesize them into structured summaries.\n"
                "End your response with:\n"
                "REASONING RESULT: [analysis of key data points and summaries]\n"
                "FINAL ANSWER: [the final concise summary / synthesis]"
            ),
            tools=[],
            name="SummarizerAgent",
            description="Summarizes complex documents and extracts core analytical insights.",
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        return (
            f"Summarize and analyze this task.\n"
            f"Task: {task}\n\n"
            f"Current Episode Context:\n{context}\n"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 2.  TASK CLASSIFIER
# ═════════════════════════════════════════════════════════════════════════════

class TaskClassifier:
    """
    Lightweight LLM-based classifier that detects what capabilities a task requires.
    """

    SYSTEM_PROMPT = (
        "You are a task capability classifier.\n"
        "Given a task, identify which agent capabilities it requires.\n\n"
        "Output a JSON object with these boolean fields:\n"
        "{\n"
        '  "needs_code_execution": bool,\n'
        '  "needs_web_search": bool,\n'
        '  "needs_mathematical_reasoning": bool,\n'
        '  "needs_creative_writing": bool,\n'
        '  "needs_data_analysis": bool,\n'
        '  "domain": "engineering" | "hr" | "finance" | "general"\n'
        "}\n\n"
        "Output ONLY valid JSON. No explanation."
    )

    def __init__(self, groq_client: Optional[GroqClient] = None) -> None:
        """
        Build a small Agent (FAST tier, PLANNER pattern) as our classifier.
        """
        try:
            if groq_client is None:
                groq_client = GroqClient()
        except EnvironmentError:
            # Allows offline construction for structural tests
            groq_client = None

        if groq_client is not None:
            self._classifier_agent = AgentFactory.create(
                pattern=ReasoningPattern.PLANNER,
                tier=ModelTier.FAST,
                groq_client=groq_client
            )
            # Override configurations to ensure classifier behavior
            self._classifier_agent.config.role_prompt = self.SYSTEM_PROMPT
            self._classifier_agent.config.temperature = 0.1
            self._classifier_agent.config.max_tokens = 256

            # Override _build_action_prompt to instruct the LLM to classify the task
            self._classifier_agent._build_action_prompt = types.MethodType(
                lambda self_agent, task, context: (
                    f"Please classify the following task:\n"
                    f"Task: \"{task}\"\n\n"
                    f"Provide only the JSON object as specified."
                ),
                self._classifier_agent
            )

            # Override _parse_output to bypass reasoning parser
            self._classifier_agent._parse_output = lambda raw_text: (raw_text.strip(), raw_text.strip())
        else:
            self._classifier_agent = None
            logger.warning("[TaskClassifier] GroqClient not loaded. classify() will return fallback General domain.")

    async def classify(self, task: str) -> dict:
        """
        Call the classifier agent asynchronously with the task and parse the JSON.
        """
        if self._classifier_agent is None:
            return {
                "needs_code_execution": False,
                "needs_web_search": False,
                "needs_mathematical_reasoning": False,
                "needs_creative_writing": False,
                "needs_data_analysis": False,
                "domain": "general"
            }

        try:
            output = await asyncio.to_thread(
                self._classifier_agent.execute, task, ""
            )
            raw_text = output.content.strip()

            # Clean markdown code blocks if any
            json_str = raw_text
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)
        except Exception as e:
            raw_val = output.raw_response if 'output' in locals() and hasattr(output, 'raw_response') else None
            content_val = output.content if 'output' in locals() and hasattr(output, 'content') else None
            logger.warning("[TaskClassifier] Failed to parse classification JSON: %s. Raw response: %r, Content: %r", e, raw_val, content_val)
            data = {}

        # Ensure schema structure and fallback values
        result = {
            "needs_code_execution": bool(data.get("needs_code_execution", False)),
            "needs_web_search": bool(data.get("needs_web_search", False)),
            "needs_mathematical_reasoning": bool(data.get("needs_mathematical_reasoning", False)),
            "needs_creative_writing": bool(data.get("needs_creative_writing", False)),
            "needs_data_analysis": bool(data.get("needs_data_analysis", False)),
            "domain": str(data.get("domain", "general")),
        }

        if result["domain"] not in ("engineering", "hr", "finance", "general"):
            result["domain"] = "general"

        return result

    def classify_sync(self, task: str) -> dict:
        """
        Synchronous wrapper around classification.
        """
        return asyncio.run(self.classify(task))


# ═════════════════════════════════════════════════════════════════════════════
# 3.  REGISTRY CONFIGURATION
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class AgentRegistryConfig:
    """
    Configuration hyperparameters for the dynamic agent registry.
    """
    check_every_n_steps:  int  = 2     # re-classify every N steps
    max_pool_size:        int  = 9     # never exceed this many agents
    min_pool_size:        int  = 3     # always keep at least this many
    enable_pruning:       bool = True  # remove inactive agents


# ═════════════════════════════════════════════════════════════════════════════
# 4.  AGENT REGISTRY MAIN CLASS
# ═════════════════════════════════════════════════════════════════════════════

class AgentRegistry:
    """
    Manages the active pool of agents dynamically, injecting sector-specific modules
    or task-specific utility agents at runtime. Also handles pruning inactive agents.
    """

    def __init__(
        self,
        base_pool: list[Agent],
        config: Optional[AgentRegistryConfig] = None,
    ) -> None:
        """
        Store base_pool and setup active pool tracking structures.
        """
        self.config = config or AgentRegistryConfig()
        
        # Store base_pool as dict {name: agent}
        self.base_pool = {agent.name: agent for agent in base_pool}
        
        # Active pool starts as a shallow copy of base_pool
        self._active_pool = dict(self.base_pool)
        
        # Tracks manual or internal activation counts
        self._activation_counts: dict[str, int] = {}
        
        # Records events: [{event, agent_name, step, reason, timestamp}]
        self._injection_log: list[dict] = []
        
        # Current step tracker internally
        self._current_step: int = 0

        # Build TaskClassifier sharing the client of an existing agent if possible
        shared_client = None
        for agent in base_pool:
            if hasattr(agent, "groq_client"):
                shared_client = agent.groq_client
                break
        self.classifier = TaskClassifier(groq_client=shared_client)

        logger.info(
            "[AgentRegistry] Initialized. Base pool size: %d, Min capacity: %d, Max capacity: %d",
            len(self.base_pool),
            self.config.min_pool_size,
            self.config.max_pool_size,
        )

    @property
    def active_agents(self) -> list[Agent]:
        """Current active pool of Agent objects as a list."""
        return list(self._active_pool.values())

    @property
    def active_names(self) -> list[str]:
        """Sorted list of names of active agents."""
        return sorted(list(self._active_pool.keys()))

    @property
    def injection_count(self) -> int:
        """Total injection events performed."""
        return sum(1 for log in self._injection_log if log["event"] == "inject")

    def _create_agent_by_name(self, agent_name: str) -> Agent:
        """
        Construct specialized agents on demand with shared groq client.
        """
        # Find shared client
        client = None
        for a in self.base_pool.values():
            if hasattr(a, "groq_client"):
                client = a.groq_client
                break
        if client is None:
            for a in self._active_pool.values():
                if hasattr(a, "groq_client"):
                    client = a.groq_client
                    break
        if client is None:
            client = GroqClient()

        if agent_name == "ReasoningAgent":
            return AgentFactory.create(ReasoningPattern.REASONING, groq_client=client)
            # Or use standard library imports for custom ones
        elif agent_name == "PythonAgent":
            return PythonAgent(groq_client=client)
        elif agent_name == "WebSearchAgent":
            return WebSearchAgent(groq_client=client)
        elif agent_name == "ModifierAgent":
            return ModifierAgent(groq_client=client)
        elif agent_name == "SummarizerAgent":
            return SummarizerAgent(groq_client=client)
        else:
            raise ValueError(f"Unknown agent type to inject: {agent_name}")

    async def update(
        self,
        task: str,
        step: int,
        memory,
    ) -> list[str]:
        """
        Called by orchestrator at each step of an episode.
        Decides whether to inject capabilities and prune inactive agents.
        """
        self._current_step = step

        # 1. Skip if not on our update cadence
        if step % self.config.check_every_n_steps != 0:
            return self.active_names

        # 2. Classify task capabilities using LLM TaskClassifier
        try:
            caps = await self.classifier.classify(task)
        except Exception as e:
            logger.warning("[AgentRegistry] Classification failed: %s. Skipping injection check.", e)
            caps = {}

        # 3. Check capabilities and inject missing agents if pool capacity allows
        capability_map = {
            "needs_code_execution": "PythonAgent",
            "needs_web_search": "WebSearchAgent",
            "needs_mathematical_reasoning": "ReasoningAgent",
            "needs_creative_writing": "ModifierAgent",
            "needs_data_analysis": "SummarizerAgent",
        }

        for cap_field, agent_name in capability_map.items():
            if caps.get(cap_field, False):
                if agent_name not in self._active_pool:
                    if len(self._active_pool) < self.config.max_pool_size:
                        try:
                            new_agent = self._create_agent_by_name(agent_name)
                            self.inject(new_agent)
                        except Exception as e:
                            logger.error("[AgentRegistry] Injection of %s failed: %s", agent_name, e)
                    else:
                        logger.warning(
                            "[AgentRegistry] Required agent %s cannot be injected: active pool size (%d) >= max (%d)",
                            agent_name,
                            len(self._active_pool),
                            self.config.max_pool_size
                        )

        # 4. Pruning logic
        if self.config.enable_pruning and step >= 4:
            # Gather activation counts from memory
            activation_counts = {}
            for name in list(self._active_pool.keys()):
                if hasattr(memory, "get_activation_count"):
                    activation_counts[name] = memory.get_activation_count(name)
                elif hasattr(memory, "_agent_activation_counts"):
                    activation_counts[name] = memory._agent_activation_counts.get(name, 0)
                else:
                    activation_counts[name] = 0

            # Pruning candidates are agents with 0 activations after step 4+
            # Safety checks: never prune below min_pool_size, never Concluder or Terminator
            prune_candidates = []
            for name in list(self._active_pool.keys()):
                if name in ("ConcluderAgent", "TerminatorAgent"):
                    continue
                if activation_counts.get(name, 0) == 0:
                    prune_candidates.append(name)

            for name in prune_candidates:
                if len(self._active_pool) <= self.config.min_pool_size:
                    break
                self.prune(name)

        return self.active_names

    def set_agent_pool(self, agents: list[Agent]) -> None:
        """
        Replace active pool entirely.
        This is the interface Roshan's NeuralForge SectorRouter calls before an episode starts.
        """
        self._active_pool = {agent.name: agent for agent in agents}
        
        timestamp = time.time()
        self._injection_log.append({
            "event": "set_pool",
            "agent_name": ", ".join(self.active_names),
            "step": self._current_step,
            "reason": "SectorRouter domain pool injection",
            "timestamp": timestamp,
        })
        logger.info("[AgentRegistry] Pool replaced via set_agent_pool. Active agents: %s", self.active_names)

    def inject(self, agent: Agent) -> bool:
        """
        Add one agent to active pool.
        Returns True if injected, False if pool is at max_pool_size or already present.
        """
        if agent.name in self._active_pool:
            return True

        if len(self._active_pool) >= self.config.max_pool_size:
            logger.warning("[AgentRegistry] Injection failed: Active pool is at max capacity.")
            return False

        self._active_pool[agent.name] = agent
        
        timestamp = time.time()
        self._injection_log.append({
            "event": "inject",
            "agent_name": agent.name,
            "step": self._current_step,
            "reason": "Task capability requirement match",
            "timestamp": timestamp,
        })
        logger.info("[AgentRegistry] Injected agent %s into active pool.", agent.name)
        return True

    def prune(self, agent_name: str) -> bool:
        """
        Remove agent from active pool.
        Returns False if agent not found, would violate min_pool_size, or is a protected agent.
        """
        if agent_name not in self._active_pool:
            return False

        if agent_name in ("ConcluderAgent", "TerminatorAgent"):
            return False

        if len(self._active_pool) <= self.config.min_pool_size:
            return False

        del self._active_pool[agent_name]

        timestamp = time.time()
        self._injection_log.append({
            "event": "prune",
            "agent_name": agent_name,
            "step": self._current_step,
            "reason": "Pruned inactive agent from active pool",
            "timestamp": timestamp,
        })
        logger.info("[AgentRegistry] Pruned agent %s from active pool.", agent_name)
        return True

    def get_injection_log(self) -> list[dict]:
        """Returns copy of the internal event logs."""
        return list(self._injection_log)

    def reset(self) -> None:
        """Reset to base pool configurations and clear logs."""
        self._active_pool = dict(self.base_pool)
        self._activation_counts.clear()
        self._injection_log.clear()
        self._current_step = 0
        logger.info("[AgentRegistry] Registry reset to base pool configuration.")


# ═════════════════════════════════════════════════════════════════════════════
# 5.  __main__ — SMOKE TEST SUITE
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    # Force UTF-8 on Windows so console prints safely
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("  Puppeteer++ -- extensions/agent_registry.py smoke test")
    print("=" * 60 + "\n")

    # Inject a mock GROQ_API_KEY if not already set to ensure tests run offline
    original_key_set = "GROQ_API_KEY" in os.environ
    if not original_key_set:
        os.environ["GROQ_API_KEY"] = "mock-key-for-structural-smoke-tests"
        print("[INFO] No GROQ_API_KEY found. Setting temporary mock key for offline tests.\n")

    # Test 1 — Registry construction
    print("Test 1 — Registry construction:")
    base_pool = AgentFactory.create_pool([
        ReasoningPattern.PLANNER,
        ReasoningPattern.REASONING,
        ReasoningPattern.CRITIC,
        ReasoningPattern.CONCLUDER,
        ReasoningPattern.TERMINATOR,
    ])
    registry = AgentRegistry(base_pool)
    print(f"  Active agent names: {registry.active_names}")
    print(f"  Pool size         : {len(registry.active_agents)}")
    assert len(registry.active_agents) == 5, "Pool size must be 5"
    print("[OK] Registry construction verified.\n")

    # Test 2 — Manual inject and prune
    print("Test 2 — Manual inject and prune:")
    # Instantiate the concrete ModifierAgent from this module
    modifier_agent = ModifierAgent(
        groq_client=registry.active_agents[0].groq_client,
        model_name=ModelTier.FAST.value
    )

    # Inject
    registry.inject(modifier_agent)
    print(f"  Names after injection: {registry.active_names}")
    assert "ModifierAgent" in registry.active_names, "ModifierAgent should be in active pool"
    
    # Prune
    registry.prune("ModifierAgent")
    print(f"  Names after pruning: {registry.active_names}")
    assert "ModifierAgent" not in registry.active_names, "ModifierAgent should be pruned"
    print("[OK] Manual inject and prune verified.\n")

    # Test 3 — Safety guards
    print("Test 3 — Safety guards:")
    # Attempt to prune Concluder
    res_concluder = registry.prune("ConcluderAgent")
    assert not res_concluder, "Should not be able to prune ConcluderAgent"
    # Attempt to prune Terminator
    res_terminator = registry.prune("TerminatorAgent")
    assert not res_terminator, "Should not be able to prune TerminatorAgent"
    print("  Concluder and Terminator safety guards block pruning.")
    print("[OK] Safety guards verified.\n")

    # Test 4 — set_agent_pool()
    print("Test 4 — set_agent_pool() router interface:")
    new_pool = AgentFactory.create_pool([
        ReasoningPattern.PLANNER,
        ReasoningPattern.REASONING,
        ReasoningPattern.CONCLUDER,
    ])
    registry.set_agent_pool(new_pool)
    print(f"  Active names after set_agent_pool(): {registry.active_names}")
    assert len(registry.active_agents) == 3, "Pool size should be 3"
    print("[OK] set_agent_pool working (NeuralForge SectorRouter integration interface verified).\n")

    # Test 5 — TaskClassifier (requires API key)
    print("Test 5 — TaskClassifier:")
    if original_key_set and not os.environ["GROQ_API_KEY"].startswith("mock"):
        try:
            classifier = TaskClassifier()
            print("  Invoking TaskClassifier synchronously on sample query...")
            result = classifier.classify_sync(
                "Write a Python function to sort a list of integers"
            )
            print(f"  Classification output: {json.dumps(result, indent=2)}")
            # Verify mathematical reasoning or code execution triggers
            assert result["needs_mathematical_reasoning"] or result["needs_code_execution"], \
                "Classifier should flag math reasoning or code execution for code task"
            assert result["domain"] in ("engineering", "general"), \
                "Classifier domain should be general or engineering"
            print("[OK] TaskClassifier working.\n")
        except Exception as e:
            print(f"[FAIL] TaskClassifier test failed: {e}\n")
            raise
    else:
        print("  [SKIP] Skipping TaskClassifier API test (requires valid GROQ_API_KEY).\n")

    # Test 6 — Injection log
    print("Test 6 — Injection log:")
    log = registry.get_injection_log()
    assert len(log) > 0, "Log should contain events from manual injections"
    print(f"  Log contains {len(log)} events.")
    print("  First 3 entries:")
    for idx, entry in enumerate(log[:3]):
        print(f"    Entry {idx + 1}: {entry}")
    print("[OK] Injection log verified.\n")

    # Test 7 — reset()
    print("Test 7 — reset():")
    registry.reset()
    print(f"  Pool size after reset: {len(registry.active_agents)}")
    assert len(registry.active_agents) == 5, "Registry should return to base size (5)"
    print("[OK] reset verified.\n")

    # Clean up mock key if we set it
    if not original_key_set:
        del os.environ["GROQ_API_KEY"]

    print("-" * 60)
    print("[PASS] extensions/agent_registry.py working correctly.")
    print("-" * 60 + "\n")
