"""
extensions/step_critic.py
=========================
Extension 1: Step-level reward critic.
Stated paper limitation (Section D - Limitations):
  "The Puppeteer's optimization currently depends on coarse-grained
   rewards based only on final outputs and token usage, lacking
   informative intermediate feedback."

We address this limitation by providing step-level dense feedback (s_t).
We instantiate a lightweight, deterministic LLM critic to evaluate each
intermediate agent output s_t ∈ [0, 1] relative to the overall task.
"""

import os
import re
import asyncio
import logging
import dataclasses
import types
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

# ── sys.path guard for direct execution ──────────────────────────────────────
import sys as _sys
import os as _os
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

from core.agent import (
    Agent,
    AgentFactory,
    AgentOutput,
    ModelTier,
    ReasoningPattern,
    GroqClient,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class StepCriticConfig:
    """
    Configuration for the step-level judge LLM.
    """
    model_tier:  ModelTier = ModelTier.FAST   # small model, cheap
    temperature: float     = 0.1             # very deterministic scoring
    max_tokens:  int       = 64              # score + one sentence rationale
    enabled:     bool      = True


@dataclasses.dataclass
class StepCriticScore:
    """
    Result of a step evaluation by the StepCritic.
    """
    step:        int
    agent_name:  str
    score:       float      # s_t ∈ [0.0, 1.0]
    rationale:   str        # explanation of the score
    tokens_used: int
    latency_ms:  float

    def to_dict(self) -> dict:
        """Serialize StepCriticScore to dictionary."""
        return dataclasses.asdict(self)


class StepCritic:
    """
    Evaluates intermediate agent outputs step-by-step using a fast LLM.
    """

    SYSTEM_PROMPT = (
        "You are a step-level quality evaluator for a multi-agent\n"
        "reasoning system.\n\n"
        "Your job: given a task and one agent's output, score how much\n"
        "this output advances progress toward solving the task.\n\n"
        "Scoring rubric:\n"
        "  0.0 — completely irrelevant or wrong\n"
        "  0.3 — some relevant content but mostly off-track\n"
        "  0.5 — partially useful, advances solution somewhat\n"
        "  0.7 — good contribution, clearly moves toward solution\n"
        "  1.0 — excellent, directly and correctly addresses the task\n\n"
        "Respond in this exact format and nothing else:\n"
        "SCORE: 0.7\n"
        "RATIONALE: One sentence explaining the score."
    )

    def __init__(
        self,
        config: Optional[StepCriticConfig] = None,
        groq_client: Optional[GroqClient] = None,
    ) -> None:
        """
        Initialise StepCritic with configuration and shared client.
        """
        self.config = config or StepCriticConfig()

        # Build ReasoningPattern.CRITIC agent using factory
        self._critic_agent = AgentFactory.create(
            pattern=ReasoningPattern.CRITIC,
            tier=self.config.model_tier,
            groq_client=groq_client,
        )

        # Override configurations to guarantee the scoring persona
        self._critic_agent.config.role_prompt = self.SYSTEM_PROMPT
        self._critic_agent.config.temperature = self.config.temperature
        self._critic_agent.config.max_tokens = self.config.max_tokens

        # Override _build_action_prompt to return evaluation prompt directly
        self._critic_agent._build_action_prompt = types.MethodType(
            lambda self_agent, task, context: task,
            self._critic_agent
        )

        # Override _parse_output to bypass reasoning parsing
        self._critic_agent._parse_output = lambda raw_text: (raw_text.strip(), raw_text.strip())

        logger.info(
            "StepCritic initialized with model=%s, temperature=%.2f",
            self.config.model_tier.value,
            self.config.temperature,
        )

    async def score(
        self,
        task: str,
        agent_name: str,
        agent_output: str,
        step: int,
        context: str = "",
    ) -> StepCriticScore:
        """
        Score a single agent step output asynchronously.
        """
        if not self.config.enabled:
            return StepCriticScore(
                step=step,
                agent_name=agent_name,
                score=0.0,
                rationale="critic disabled",
                tokens_used=0,
                latency_ms=0.0,
            )

        # Build evaluation prompt
        eval_prompt = (
            f"TASK: {task}\n"
            f"AGENT: {agent_name}\n"
            f"AGENT OUTPUT: {agent_output[:500]}\n"
            f"PRIOR CONTEXT: {context[:300] if context else 'None'}\n\n"
            f"Score this output on how much it advances the task solution."
        )

        # Execute agent invocation in a thread since BaseAgent.execute is synchronous
        output = await asyncio.to_thread(self._critic_agent.execute, eval_prompt)
        raw_response = output.raw_response.strip()

        # Parse score & rationale
        score_val = 0.5
        rationale_val = "parse error"

        score_match = re.search(r"SCORE:\s*([0-9.]+)", raw_response, re.IGNORECASE)
        rationale_match = re.search(r"RATIONALE:\s*(.+)", raw_response, re.IGNORECASE | re.DOTALL)

        if score_match and rationale_match:
            try:
                score_val = float(score_match.group(1))
                rationale_val = rationale_match.group(1).strip()
                if "\n" in rationale_val:
                    rationale_val = rationale_val.split("\n")[0].strip()
            except ValueError:
                score_val = 0.5
                rationale_val = "parse error"

        # Clamp score to [0.0, 1.0]
        score_val = max(0.0, min(1.0, score_val))

        return StepCriticScore(
            step=step,
            agent_name=agent_name,
            score=score_val,
            rationale=rationale_val,
            tokens_used=output.tokens_used,
            latency_ms=output.latency_ms,
        )

    def score_sync(
        self,
        task: str,
        agent_name: str,
        agent_output: str,
        step: int,
        context: str = "",
    ) -> StepCriticScore:
        """
        Synchronous score wrapper.
        """
        return asyncio.run(self.score(task, agent_name, agent_output, step, context))

    async def score_trajectory(
        self,
        task: str,
        trajectory: list[dict],
    ) -> list[StepCriticScore]:
        """
        Score each step of a completed episode trajectory sequentially.
        """
        scores = []
        context_parts = []

        for item in trajectory:
            step = item["step"]
            name = item["agent_name"]
            output_dict = item.get("agent_output", {})
            content = ""
            if isinstance(output_dict, dict):
                content = output_dict.get("content", "")
            elif hasattr(output_dict, "content"):
                content = getattr(output_dict, "content", "")

            # Prior context is everything accumulated up to this step
            prior_context = "\n".join(context_parts)

            # Score this step
            step_score = await self.score(
                task=task,
                agent_name=name,
                agent_output=content,
                step=step,
                context=prior_context,
            )
            scores.append(step_score)

            # Append this step's output to context parts for subsequent steps
            context_parts.append(f"[{name}] {content}")

        return scores

    async def close(self) -> None:
        """
        Clean up resources.
        """
        if hasattr(self._critic_agent, "close"):
            self._critic_agent.close()


if __name__ == "__main__":
    import sys
    # Force UTF-8 on Windows so box-drawing / tick chars print safely.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("  Puppeteer++ -- extensions/step_critic.py smoke test")
    print("=" * 60 + "\n")

    groq_key_exists = bool(os.environ.get("GROQ_API_KEY"))

    if not groq_key_exists:
        # ── STRUCTURAL MOCK TEST (Offline) ────────────────────────────────────
        print("─── Running Offline Structural Tests ─────────────────────────")
        cfg = StepCriticConfig(
            model_tier=ModelTier.FAST,
            temperature=0.1,
            max_tokens=64,
            enabled=True
        )
        assert cfg.enabled is True
        score_obj = StepCriticScore(
            step=0,
            agent_name="PlannerAgent",
            score=0.7,
            rationale="Decent decomposition of tasks.",
            tokens_used=120,
            latency_ms=350.0
        )
        assert score_obj.to_dict()["score"] == 0.7
        print("[OK] Config and Score dataclasses created and serialized correctly.")
        print("\n[PARTIAL] No API key — structural tests passed")
        sys.exit(0)

    # ── LIVE CRITIC TEST (Requires Groq API Key) ──────────────────────────────
    print("─── Running Live Critic Tests ────────────────────────────────")
    try:
        critic = StepCritic()

        # Test 1: Single step scoring
        task = "What is 25% of 840?"
        agent_output = "To find 25% of 840, I multiply 840 × 0.25 = 210."
        print(f"Task: {task}")
        print(f"Agent Output: {agent_output}")
        print("Scoring high-quality output...")

        score = critic.score_sync(
            task=task,
            agent_name="ReasoningAgent",
            agent_output=agent_output,
            step=1,
        )

        print("-" * 40)
        print(f"Agent:     {score.agent_name}")
        print(f"Score:     {score.score:.2f}")
        print(f"Rationale: {score.rationale}")
        print(f"Tokens:    {score.tokens_used}")
        print("-" * 40)

        assert score.score >= 0.5, f"Expected score >= 0.5, got {score.score}"
        print("[OK] Test 1: High-quality output scored successfully.")

        # Test 2: Poor output scoring
        poor_output = "I am not sure how to approach this problem."
        print(f"\nPoor Agent Output: {poor_output}")
        print("Scoring poor-quality output...")

        poor_score = critic.score_sync(
            task=task,
            agent_name="ReasoningAgent",
            agent_output=poor_output,
            step=0,
        )

        print("-" * 40)
        print(f"Agent:     {poor_score.agent_name}")
        print(f"Score:     {poor_score.score:.2f}")
        print(f"Rationale: {poor_score.rationale}")
        print("-" * 40)

        assert poor_score.score < score.score, f"Expected poor score < good score ({poor_score.score} vs {score.score})"
        print("[OK] Test 2: Poor-quality output scored appropriately lower.")

        # Test 3: Full trajectory scoring
        print("\nScoring full mock trajectory...")
        trajectory = [
            {
                "step": 0,
                "agent_name": "PlannerAgent",
                "agent_output": {"content": "I will calculate 25% of 840 step by step."}
            },
            {
                "step": 1,
                "agent_name": "ReasoningAgent",
                "agent_output": {"content": "25% = 0.25, so 840 × 0.25 = 210."}
            },
            {
                "step": 2,
                "agent_name": "ConcluderAgent",
                "agent_output": {"content": "The answer is 210."}
            },
        ]

        scores = asyncio.run(critic.score_trajectory(task, trajectory))

        print("\nTrajectory Score Table:")
        print(f"{'Step':4s} | {'Agent':15s} | {'Score':5s} | {'Rationale'}")
        print("-" * 65)
        for s in scores:
            print(f"{s.step:<4d} | {s.agent_name:15s} | {s.score:<5.1f} | {s.rationale}")
        print("-" * 65)

        assert len(scores) == 3, f"Expected 3 scores, got {len(scores)}"
        assert all(0.0 <= s.score <= 1.0 for s in scores), "Scores must be clamped between 0.0 and 1.0"
        print("[OK] Test 3: Full trajectory scored successfully.")

        # Cleanup
        asyncio.run(critic.close())
        print("\n[PASS] extensions/step_critic.py working")

    except Exception as e:
        print(f"\n✗ Live critic tests failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
