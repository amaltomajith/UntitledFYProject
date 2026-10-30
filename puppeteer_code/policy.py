"""
puppeteer_code/policy.py
========================
Policies π(a | S_t) for choosing the next agent.

  features()       normalised, zero-token state vector built from CodeState
  HeuristicPolicy  hand-written preference order (strong baseline, no training)
  FixedPolicy      a fixed pipeline that ignores state (the "pipeline" baseline)
  LinearPolicy     softmax(W·x) over the shielded action set, trained with true
                   REINFORCE + a running baseline (Williams, 1992).  Decisions
                   take microseconds and cost zero tokens.
  LLMSelector      asks a FAST model to pick from the shielded set (the
                   original Puppeteer++ orchestrator style); falls back to the
                   heuristic if the reply is not an allowed agent.

Every policy exposes select(env, allowed) -> agent name, plus
begin_episode() / end_episode(returns) so the engine can train any of them.
"""

from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path
from typing import Optional

import numpy as np

from core.orchestrator import normalize_agent_name
from puppeteer_code.agents import ALL_AGENTS

logger = logging.getLogger(__name__)

AGENTS = ALL_AGENTS
FEATURE_NAMES = [
    "bias", "progress", "pass_rate", "best_pass_rate", "all_green", "baseline_green",
    "edits_pending", "scouted", "planned", "edit_attempts", "rollbacks", "stall",
    "rejected_last", "linted", "reviewed", "has_docs", "has_targets", "improved",
    "near_end", "tokens_used",
] + [f"last_{a}" for a in AGENTS]


def features(env, token_budget: int = 20000) -> np.ndarray:
    st = env.state
    step, max_steps = env.current_step, max(1, env.config.max_steps)
    x = [
        1.0,
        step / max_steps,
        st.last_tests.pass_rate,
        st.best_pass_rate,
        float(st.last_tests.all_green and not st.edits_since_test),
        float(st.baseline.all_green),
        float(st.edits_since_test),
        float(st.scouted > 0),
        float(st.planned),
        min(st.edit_attempts, 5) / 5,
        min(len(st.rollbacks), 5) / 5,
        min(st.stall, 3) / 3,
        float(st.last_edit_rejected),
        float(st.linted_since_edit),
        float(st.reviewed_since_edit),
        float(bool(st.docs_notes)),
        float(bool(st.target_tests)),
        float(st.improved),
        float(step >= max_steps - 3),
        min(env.total_tokens / max(1, token_budget), 1.0),
    ]
    x += [float(st.last_agent == a) for a in AGENTS]
    return np.asarray(x, dtype=np.float64)


class Policy:
    name = "base"
    bypass_shield = False

    def select(self, env, allowed: list[str]) -> str:
        raise NotImplementedError

    def begin_episode(self) -> None:
        pass

    def end_episode(self, returns: list[float]) -> dict:
        return {}


class HeuristicPolicy(Policy):
    """Encodes the intended behaviour: scout, code, test, debug, conclude when green."""

    name = "heuristic"

    def select(self, env, allowed: list[str]) -> str:
        st = env.state
        a = set(allowed)
        failing_msgs = " ".join(st.last_tests.cases[n].message for n in st.last_tests.failing)
        order = [
            ("TestRunnerAgent", True),
            ("ConcluderAgent", st.last_tests.all_green and not st.edits_since_test),
            ("RepoScoutAgent", not st.scout_keys),
            ("TestWriterAgent", not st.target_tests),
            ("CoderAgent", st.used["CoderAgent"] == 0),
            ("DocsSearchAgent", bool(re.search(r"ImportError|ModuleNotFound|has no attribute",
                                               failing_msgs)) and not st.docs_notes),
            # ask the Terminator once per 4 failed cycles (it may say CONTINUE)
            ("TerminatorAgent", st.stall >= 4 and st.used["TerminatorAgent"] < st.stall // 4),
            ("ExplorerAgent", st.used["ExplorerAgent"] < 2),
            ("RepoScoutAgent", st.stall >= 1),
            ("DebuggerAgent", True),
            ("CoderAgent", True),
            ("ConcluderAgent", True),
            ("TerminatorAgent", True),
            ("LinterAgent", True),
            ("ReviewerAgent", True),
            ("PlannerAgent", True),
        ]
        for name, cond in order:
            if cond and name in a:
                return name
        return allowed[0]


class FixedPolicy(Policy):
    """A static pipeline; ignores state and the shield (baseline)."""

    name = "fixed"
    bypass_shield = True
    DEFAULT = ["RepoScoutAgent", "PlannerAgent", "CoderAgent", "TestRunnerAgent",
               "DebuggerAgent", "TestRunnerAgent", "DebuggerAgent", "TestRunnerAgent",
               "ConcluderAgent"]

    def __init__(self, sequence: Optional[list[str]] = None):
        self.sequence = sequence or list(self.DEFAULT)
        self.i = 0

    def begin_episode(self) -> None:
        self.i = 0

    def select(self, env, allowed: list[str]) -> str:
        last = env.current_step >= env.config.max_steps - 1
        name = "ConcluderAgent" if last else self.sequence[min(self.i, len(self.sequence) - 1)]
        self.i += 1
        return name


class LinearPolicy(Policy):
    """
    π(a|x) = softmax over allowed a of  W[a]·x.

    REINFORCE with baseline:  W ← W + α · (G_t − b) · ∇ log π(a_t|x_t)
    where ∇_W log π = (onehot(a_t) − π(·|x_t)) ⊗ x_t  restricted to the
    allowed actions, and b is an exponential moving average of returns.
    """

    name = "linear"

    def __init__(self, lr: float = 0.05, temperature: float = 1.0, greedy: bool = False,
                 seed: Optional[int] = None, baseline_decay: float = 0.9, l2: float = 1e-4):
        self.W = np.zeros((len(AGENTS), len(FEATURE_NAMES)))
        self.lr = lr
        self.temperature = temperature
        self.greedy = greedy
        self.rng = np.random.default_rng(seed)
        self.baseline: Optional[float] = None
        self.baseline_decay = baseline_decay
        self.l2 = l2
        self.trace: list[tuple[np.ndarray, np.ndarray, int]] = []
        self.episodes = 0
        self.token_budget = 20000

    def probs(self, x: np.ndarray, allowed: list[str]) -> tuple[np.ndarray, np.ndarray]:
        mask = np.array([a in allowed for a in AGENTS])
        logits = self.W @ x / max(self.temperature, 1e-6)
        logits = np.where(mask, logits, -np.inf)
        logits -= logits[mask].max()
        p = np.exp(logits)
        p[~mask] = 0.0
        return p / p.sum(), mask

    def select(self, env, allowed: list[str]) -> str:
        x = features(env, self.token_budget)
        p, mask = self.probs(x, allowed)
        idx = int(np.argmax(p)) if self.greedy else int(self.rng.choice(len(AGENTS), p=p))
        self.trace.append((x, mask, idx))
        return AGENTS[idx]

    def begin_episode(self) -> None:
        self.trace = []

    def end_episode(self, returns: list[float]) -> dict:
        """returns[t] = discounted return G_t for the decision at step t."""
        n = min(len(returns), len(self.trace))
        if n == 0:
            return {"updated": False}
        G = np.asarray(returns[:n], dtype=np.float64)
        if self.baseline is None:
            self.baseline = float(G.mean())
        adv = G - self.baseline
        grad = np.zeros_like(self.W)
        for t in range(n):
            x, mask, a = self.trace[t]
            logits = self.W @ x / max(self.temperature, 1e-6)
            logits = np.where(mask, logits, -np.inf)
            logits -= logits[mask].max()
            p = np.exp(logits)
            p[~mask] = 0.0
            p /= p.sum()
            g = -p
            g[a] += 1.0
            grad += adv[t] * np.outer(g, x)
        self.W += self.lr * (grad / n - self.l2 * self.W)
        self.baseline = self.baseline_decay * self.baseline + (1 - self.baseline_decay) * float(G.mean())
        self.episodes += 1
        self.trace = []
        return {"updated": True, "mean_return": float(G.mean()), "baseline": self.baseline,
                "grad_norm": float(np.linalg.norm(grad / n))}

    # ── persistence ─────────────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "agents": AGENTS, "features": FEATURE_NAMES, "W": self.W.tolist(),
            "baseline": self.baseline, "episodes": self.episodes, "lr": self.lr,
        }, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, **kw) -> "LinearPolicy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        pol = cls(lr=data.get("lr", 0.05), **kw)
        W = np.zeros_like(pol.W)
        for i, a in enumerate(data["agents"]):
            if a in AGENTS:
                for j, f in enumerate(data["features"]):
                    if f in FEATURE_NAMES:
                        W[AGENTS.index(a), FEATURE_NAMES.index(f)] = data["W"][i][j]
        pol.W = W
        pol.baseline = data.get("baseline")
        pol.episodes = data.get("episodes", 0)
        return pol

    def top_weights(self, k: int = 3) -> dict[str, list[tuple[str, float]]]:
        out = {}
        for i, a in enumerate(AGENTS):
            order = np.argsort(-np.abs(self.W[i]))[:k]
            out[a] = [(FEATURE_NAMES[j], round(float(self.W[i, j]), 3)) for j in order]
        return out


class LLMSelector(Policy):
    """FAST-model orchestrator over the shielded action set (Puppeteer++ baseline)."""

    name = "llm"

    def __init__(self, session, fallback: Optional[Policy] = None):
        self.session = session
        self.model = session.model_for("PolicyLLM")
        self.settings = session.settings_for("PolicyLLM")
        self.fallback = fallback or HeuristicPolicy()
        self.tokens = 0
        self.fallbacks = 0

    def select(self, env, allowed: list[str]) -> str:
        if len(allowed) == 1:
            return allowed[0]
        from puppeteer_code.agents import _CLASSES
        st = env.state
        lines = []
        for a in allowed:
            desc = getattr(_CLASSES.get(a), "DESCRIPTION", "") or "parallel exploration of alternative fixes"
            lines.append(f"- {a}: {desc}")
        state = (f"step {env.current_step + 1}/{env.config.max_steps}; tests "
                 f"{len(st.last_tests.passed)}/{st.last_tests.total} passing; best "
                 f"{st.best_pass_rate:.2f}; edits pending: {st.edits_since_test}; scouted: "
                 f"{st.scouted > 0}; edit attempts: {st.edit_attempts}; rollbacks: "
                 f"{len(st.rollbacks)}; last agent: {st.last_agent or 'none'}")
        prompt = (f"Task: {st.task[:300]}\nState: {state}\n\nAllowed agents:\n" + "\n".join(lines)
                  + "\n\nReply with the single agent name to activate next.")
        try:
            resp = self.session.client.chat(
                model=self.model, temperature=self.settings.temperature,
                max_tokens=max(self.settings.max_tokens, 16),
                messages=[{"role": "system", "content": "You are the Puppeteer orchestrator. You "
                           "choose which coding agent acts next. Reply with one agent name only."},
                          {"role": "user", "content": prompt}])
            self.tokens += resp["usage"]["total_tokens"]
            text = resp["content"] or ""
            for a in allowed:
                if a.lower() in text.lower():
                    return a
            guess = normalize_agent_name(text.strip().split()[0] if text.strip() else "")
            if guess in allowed:
                return guess
        except Exception as e:
            logger.warning("LLM selector failed (%s); using fallback", e)
        self.fallbacks += 1
        return self.fallback.select(env, allowed)


class RandomPolicy(Policy):
    name = "random"

    def __init__(self, seed: Optional[int] = None):
        self.rng = random.Random(seed)

    def select(self, env, allowed: list[str]) -> str:
        return self.rng.choice(allowed)


def make_policy(name: str, session=None, weights: Optional[str] = None, seed: Optional[int] = None,
                greedy: bool = True) -> Policy:
    if name == "heuristic":
        return HeuristicPolicy()
    if name == "fixed":
        return FixedPolicy()
    if name == "random":
        return RandomPolicy(seed)
    if name == "linear":
        if weights and Path(weights).exists():
            return LinearPolicy.load(weights, greedy=greedy, seed=seed)
        return LinearPolicy(greedy=greedy, seed=seed)
    if name == "llm":
        return LLMSelector(session)
    raise ValueError(f"unknown policy {name!r}")


__all__ = ["features", "FEATURE_NAMES", "AGENTS", "Policy", "HeuristicPolicy", "FixedPolicy",
           "LinearPolicy", "LLMSelector", "RandomPolicy", "make_policy"]
