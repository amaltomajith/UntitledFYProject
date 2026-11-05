"""
The four methods. All use one model and the same per-call output cap, so the only difference is orchestration.

  direct     one call: answer only
  cot        one call: think step by step
  fixed      Planner -> Reasoning -> Critic -> Concluder, the core agents in a fixed order
  puppeteer  core.Orchestrator: the LLM policy picks each agent (untrained; the core's prompt-space
             REINFORCE is not run here)

Each returns a MethodResult. `reply` is what the checker scores: the whole reply for one-call methods, and for
the multi-agent methods the raw reply of the step that Environment.get_final_answer() would pick. Scoring the raw
reply (not only the text after the agent's FINAL ANSWER tag) applies the same lenient parser to every method.
"""

from __future__ import annotations

import dataclasses

from core.agent import (
    ConcluderAgent, CriticAgent, PlannerAgent, ReasoningAgent, TerminatorAgent,
)
from core.environment import EnvironmentConfig
from core.orchestrator import Orchestrator, OrchestratorConfig

from benchmarks.reasoning.datasets import FORMAT

METHODS = ("direct", "cot", "fixed", "puppeteer")

# Engine settings, recorded in every result file.
MAX_STEPS = 4             # EnvironmentConfig default (the paper's episode length)
MAX_TOKENS = 4096         # per call, for every method. The core's own caps (128-1024) are too small for a model
                          # that spends output tokens on hidden reasoning; the same cap for all keeps it fair.
POLICY_MAX_TOKENS = 1024  # the policy's cap (core default 128), raised for the same reason
BASELINE_TEMPERATURE = 0.7   # ReasoningAgent's temperature; core agents keep their own (0.2 to 0.7)

FIXED_ORDER = ("PlannerAgent", "ReasoningAgent", "CriticAgent", "ConcluderAgent")


@dataclasses.dataclass
class MethodResult:
    reply: str
    engine_answer: str = ""      # what the engine itself returned (multi-agent methods)
    status: str = "completed"
    steps: int = 1
    agents: list = dataclasses.field(default_factory=list)
    policy_raw: list = dataclasses.field(default_factory=list)
    policy_fallbacks: int = 0


def task_text(task: dict) -> str:
    return f"{task['question']}\n\n{FORMAT[task['kind']]}"


DIRECT_SYSTEM = ("Answer the question. Do not show your working. "
                 "Reply with one line: FINAL ANSWER: <answer>")
COT_SYSTEM = ("Answer the question. Think step by step and show your reasoning, "
              "then finish with one line: FINAL ANSWER: <answer>")


def _one_call(client, model: str, system: str, task: dict) -> MethodResult:
    # The instruction is repeated at the end of the user turn, as the core agents put their action prompt there:
    # Nemotron-3.5-Lightning with thinking off follows the user turn's format hint over a system-only instruction
    # (a CoT prompt in the system turn alone gave 3-token replies in the smoke check).
    reply = client.chat(model=model, messages=[{"role": "system", "content": system},
                                               {"role": "user", "content": f"{task_text(task)}\n\n{system}"}],
                        temperature=BASELINE_TEMPERATURE, max_tokens=MAX_TOKENS)
    return MethodResult(reply=reply["content"])


def run_direct(client, model, task):
    return _one_call(client, model, DIRECT_SYSTEM, task)


def run_cot(client, model, task):
    return _one_call(client, model, COT_SYSTEM, task)


def _pool(client, model: str) -> list:
    pool = [cls(groq_client=client, model_name=model)
            for cls in (PlannerAgent, ReasoningAgent, CriticAgent, ConcluderAgent, TerminatorAgent)]
    for agent in pool:
        agent.config.max_tokens = MAX_TOKENS
    return pool


class RecordingOrchestrator(Orchestrator):
    """core.Orchestrator, unchanged, except that it records what the policy said."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy_raw: list[str] = []
        self.fallbacks = 0
        original = self._policy_agent._parse_output

        def parse(raw_text):
            self.policy_raw.append(raw_text.strip()[:80])
            return original(raw_text)
        self._policy_agent._parse_output = parse

    async def _select_agent(self, context: str) -> str:
        name = await super()._select_agent(context)
        if name not in self._pool:
            self.fallbacks += 1
        return name


class FixedOrchestrator(Orchestrator):
    """The same loop with a fixed agent order and no policy calls."""

    async def _select_agent(self, context: str) -> str:
        return FIXED_ORDER[min(self._env.current_step, len(FIXED_ORDER) - 1)]


def _scored_reply(env) -> str:
    """Raw reply of the step Environment.get_final_answer() picks (same priority order)."""
    steps = env.step_history
    for name in ("ConcluderAgent", "TerminatorAgent"):
        for record in reversed(steps):
            if record.agent_name == name:
                return record.agent_output.raw_response
    for record in reversed(steps):
        if record.agent_output.success:
            return record.agent_output.raw_response
    return ""


def _episode(orch: Orchestrator, task: dict) -> MethodResult:
    result = orch.run_episode_sync(task_text(task))
    return MethodResult(
        reply=_scored_reply(orch._env), engine_answer=result.final_answer, status=result.status,
        steps=result.total_steps, agents=[s["agent_name"] for s in result.trajectory],
        policy_raw=list(getattr(orch, "policy_raw", [])), policy_fallbacks=getattr(orch, "fallbacks", 0),
    )


def _make(cls, client, model: str, task: dict) -> Orchestrator:
    orch = cls(agent_pool=_pool(client, model),
               env_config=EnvironmentConfig(task=task_text(task), task_id=task["id"], max_steps=MAX_STEPS),
               orch_config=OrchestratorConfig(max_tokens=POLICY_MAX_TOKENS))
    orch._policy_agent.config.model_name = model      # AgentFactory only knows the gpt-oss ids
    orch._policy_agent.config.max_tokens = POLICY_MAX_TOKENS
    return orch


def run_fixed(client, model, task):
    return _episode(_make(FixedOrchestrator, client, model, task), task)


def run_puppeteer(client, model, task):
    return _episode(_make(RecordingOrchestrator, client, model, task), task)


RUNNERS = {"direct": run_direct, "cot": run_cot, "fixed": run_fixed, "puppeteer": run_puppeteer}

__all__ = ["METHODS", "RUNNERS", "MethodResult", "task_text"]
