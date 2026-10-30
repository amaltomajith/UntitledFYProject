"""
puppeteer_code/assistant/policy.py
==================================
Who acts next on a request. The designs are the ones from Fix mode: a hand-written
heuristic, and a linear softmax policy trained with REINFORCE on objective outcomes.
`DirectPolicy` is one strong model in a plain tool loop, which is the baseline these
are compared against.
"""

from __future__ import annotations

import numpy as np

from puppeteer_code.assistant.shield import ACTIONS, RequestState
from puppeteer_code.policy import LinearPolicy, Policy

FEATURE_NAMES = [
    "bias", "progress", "small", "new_project", "testable", "planned", "built", "has_tests", "failing",
    "verified", "pass_rate", "stall", "regressions", "rollbacks", "reviewed", "review_open", "tokens_used",
] + [f"last_{a}" for a in ACTIONS]


def features(st: RequestState) -> np.ndarray:
    check = st.check
    x = [
        1.0,
        st.step / max(1, st.max_steps),
        float(st.small),
        float(st.new_project),
        float(st.testable),
        float(bool(st.plan)),
        float(st.built),
        float(st.has_tests),
        float(st.failing),
        float(bool(check and check.verified)),
        check.pass_rate if check else 0.0,
        min(st.stall, 3) / 3,
        min(st.regressions, 3) / 3,
        min(st.rollbacks, 3) / 3,
        float(bool(st.review)),
        float(bool(st.review) and not st.review_handled),
        min(st.tokens / max(1, st.token_budget), 1.0),
    ]
    x += [float(st.last_agent == a) for a in ACTIONS]
    return np.asarray(x, dtype=np.float64)


class HeuristicAssistantPolicy(Policy):
    """Plan, write tests when they can run, build, fix what fails, review, explain."""

    name = "heuristic"
    ORDER = ["Planner", "TestWriter", "Builder", "Debugger", "Reviewer", "Responder"]

    def select(self, st: RequestState, allowed: list[str]) -> str:
        if st.built and st.stall < 2 and "Debugger" in allowed:     # fix the code before doubting the tests
            return "Debugger"
        return next(a for a in self.ORDER if a in allowed)


class DirectPolicy(HeuristicAssistantPolicy):
    """No puppeteer: the session runs one Builder loop. Baseline for comparisons."""

    name = "direct"


class LinearAssistantPolicy(LinearPolicy):
    name = "linear"
    ACTIONS = ACTIONS
    FEATURES = FEATURE_NAMES

    def featurize(self, st: RequestState) -> np.ndarray:
        return features(st)


def make_policy(name: str, weights=None, seed=None) -> Policy:
    if name == "direct":
        return DirectPolicy()
    if name == "heuristic":
        return HeuristicAssistantPolicy()
    if name == "linear":
        if weights:
            return LinearAssistantPolicy.load(weights, greedy=True, seed=seed)
        return LinearAssistantPolicy(seed=seed)
    raise ValueError(f"unknown policy {name!r}")


def reward(st: RequestState, tampered: bool) -> float:
    """Objective only: what the checks say, and what it cost."""
    check = st.check
    success = bool(check and check.ok and check.verified)
    regression = bool(st.baseline and check and check.passed < st.baseline.passed)
    cost = st.tokens / max(1, st.token_budget)
    return (check.pass_rate if check else 0.0) + 0.5 * success - 0.5 * regression - float(tampered) - cost
