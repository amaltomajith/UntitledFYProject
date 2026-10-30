"""
puppeteer_code/engine.py
========================
CodeOrchestrator: the Puppeteer++ loop specialised for coding.

    for t in 0..T:
        registry.update()            inject TestWriter / DocsSearch / Explorer on demand
        allowed = shield(S_t)         hard action mask
        a_t     = π(S_t, allowed)     heuristic | linear (REINFORCE) | llm | fixed
        o_t     = a_t.run(env)        agent sees only a bounded snapshot
        S_t+1   = Φ(S_t, o_t)         env.transition + checkpoint / rollback
    final  = env.finalize()           independent test run (+ hidden holdout tests)
    R      = episode_rewards()        test-based r, credited edits, normalised cost
    π.end_episode(R)                  policy update (training runs only)

The user's repository is never modified; the result carries the verified
diff, which `pcode fix --apply` applies only after confirmation.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Optional

from core.environment import EnvironmentConfig
from core.memory import Memory
from extensions.agent_registry import AgentRegistry, AgentRegistryConfig
from puppeteer_code.agents import BASE_POOL, make_agent, make_pool
from puppeteer_code.environment import CodeEnvironment
from puppeteer_code.policy import HeuristicPolicy, Policy
from puppeteer_code.reward import episode_rewards
from puppeteer_code.shield import allowed_actions

logger = logging.getLogger(__name__)

_UNKNOWN_API = re.compile(r"ImportError|ModuleNotFoundError|No module named|has no attribute|cannot import")


@dataclasses.dataclass
class RunConfig:
    max_steps: int = 12
    snapshot_chars: Optional[int] = None     # override every agent's context budget
    tdd: bool = False
    test_timeout: int = 120
    holdout_dir: Optional[str] = None
    use_registry: bool = True
    explore: bool = True
    budget_usd: Optional[float] = None
    budget_tokens: Optional[int] = None


class CodeTaskClassifier:
    """Zero-token capability classifier for AgentRegistry, driven by CodeState."""

    def __init__(self, env: CodeEnvironment, explore: bool = True):
        self.env = env
        self.explore = explore

    async def classify(self, task: str) -> dict:
        st = self.env.state
        msgs = " ".join(st.last_tests.cases[n].message + " " + st.last_tests.cases[n].text[-500:]
                        for n in st.last_tests.failing)
        return {
            "needs_new_tests": st.tdd or not st.baseline.failing,
            "needs_docs": bool(_UNKNOWN_API.search(msgs)),
            "needs_exploration": self.explore and (st.stall >= 2 or len(st.rollbacks) >= 2),
        }


CAPABILITY_MAP = {
    "needs_new_tests": "TestWriterAgent",
    "needs_docs": "DocsSearchAgent",
    "needs_exploration": "ExplorerAgent",
}


@dataclasses.dataclass
class RunResult:
    task: str
    repo: str
    status: str
    success: bool
    steps: list[dict]
    final: dict
    rewards: dict
    usage: dict
    policy: str
    wall_s: float
    injections: list[dict]
    config: dict

    @property
    def diff(self) -> str:
        return self.final.get("diff", "")

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1, default=str), encoding="utf-8")
        return path


class CodeOrchestrator:
    def __init__(self, session, policy: Optional[Policy] = None, config: Optional[RunConfig] = None,
                 on_event: Optional[Callable[[dict], None]] = None):
        self.session = session
        self.policy = policy or HeuristicPolicy()
        self.config = config or RunConfig()
        self.on_event = on_event or (lambda e: None)

    def run(self, repo: str | Path, task: str) -> RunResult:
        return asyncio.run(self.arun(repo, task))

    async def arun(self, repo: str | Path, task: str) -> RunResult:
        cfg = self.config
        t0 = time.monotonic()
        usage_before = self.session.meter.snapshot()
        tokens_before = self.session.meter.total_tokens

        env = CodeEnvironment(EnvironmentConfig(task=task, max_steps=cfg.max_steps), repo,
                              test_timeout=cfg.test_timeout, tdd=cfg.tdd)
        self.on_event({"type": "start", "task": task, "baseline": env.state.baseline.to_dict(),
                       "max_steps": cfg.max_steps})
        pool = {a.name: a for a in make_pool(self.session, BASE_POOL)}
        registry = None
        if cfg.use_registry:
            cap_map = dict(CAPABILITY_MAP)
            if not cfg.explore:
                cap_map.pop("needs_exploration")
            registry = AgentRegistry(
                list(pool.values()),
                config=AgentRegistryConfig(check_every_n_steps=1, max_pool_size=14,
                                           min_pool_size=3, enable_pruning=False),
                classifier=CodeTaskClassifier(env, explore=cfg.explore),
                capability_map=cap_map,
                agent_factory=lambda name: make_agent(name, self.session),
            )
        memory = Memory()
        self.policy.begin_episode()
        steps: list[dict] = []

        try:
            while not env.is_done:
                if registry is not None:
                    await registry.update(task, env.current_step, memory)
                    pool = {a.name: a for a in registry.active_agents}
                names = sorted(pool)
                allowed = names if self.policy.bypass_shield else allowed_actions(env, names)
                name = self.policy.select(env, allowed)
                if name not in pool:
                    pool[name] = make_agent(name, self.session)
                agent = pool[name]
                if cfg.snapshot_chars and hasattr(agent, "BUDGET"):
                    agent.BUDGET = cfg.snapshot_chars

                step = env.current_step
                self.on_event({"type": "select", "step": step, "agent": name, "allowed": allowed})
                ts = time.monotonic()
                if inspect.iscoroutinefunction(getattr(agent, "arun", None)):
                    output = await agent.arun(env)
                else:
                    output = await asyncio.to_thread(agent.run, env)
                record = env.transition(name, output.metadata.get("prompt", ""), output)
                memory.add(record)
                self._check_budget(env, tokens_before)

                st = env.state
                info = {
                    "step": step, "agent": name, "allowed": allowed,
                    "model": output.metadata.get("model_used", "none"),
                    "tokens": output.tokens_used, "seconds": round(time.monotonic() - ts, 3),
                    "content": output.content[:600], "success": output.success,
                    "error": output.error, "verdict": output.metadata.get("verdict"),
                    "prompt_chars": output.metadata.get("prompt_chars", 0),
                    "pass_rate": st.last_tests.pass_rate, "best_pass_rate": st.best_pass_rate,
                    "head": env.ws.head(), "rollbacks": len(st.rollbacks),
                    "cost_usd": self.session.meter.cost_usd,
                }
                steps.append(info)
                self.on_event({"type": "step", **info})

            final = env.finalize(cfg.holdout_dir)
            step_rewards, reward_summary = episode_rewards(env, final)
            for info, sr in zip(steps, step_rewards):
                info["reward"] = sr.reward
                info["credit"] = env.state.credit.get(info["step"], 0.0)
            update = self.policy.end_episode([sr.reward for sr in step_rewards])
            reward_summary["policy_update"] = update
            usage_after = self.session.meter.snapshot()
            result = RunResult(
                task=task, repo=str(repo), status=env.status.value, success=final["success"],
                steps=steps, final=final, rewards=reward_summary,
                usage=_usage_delta(usage_before, usage_after),
                policy=self.policy.name, wall_s=round(time.monotonic() - t0, 2),
                injections=list(registry._injection_log) if registry else [],
                config=dataclasses.asdict(cfg),
            )
            self.on_event({"type": "end", "result": result})
            return result
        finally:
            env.close()

    def _check_budget(self, env, tokens_before: int) -> None:
        cfg = self.config
        used = self.session.meter.total_tokens - tokens_before
        if cfg.budget_tokens and used >= cfg.budget_tokens:
            env.state.budget_exceeded = True
        cost = self.session.meter.cost_usd
        if cfg.budget_usd is not None and cost is not None and cost >= cfg.budget_usd:
            env.state.budget_exceeded = True


def _usage_delta(before: dict, after: dict) -> dict:
    out = {"by_model": {}}
    for model, a in after.get("by_model", {}).items():
        b = before.get("by_model", {}).get(model, {})
        d = {k: a[k] - b.get(k, 0) for k in a if isinstance(a[k], (int, float))}
        if any(d.values()):
            out["by_model"][model] = d
    out["total_tokens"] = sum(m.get("prompt_tokens", 0) + m.get("completion_tokens", 0)
                              for m in out["by_model"].values())
    costs = [after.get("cost_usd"), before.get("cost_usd")]
    out["cost_usd"] = None if None in costs else round(costs[0] - costs[1], 6)
    return out
