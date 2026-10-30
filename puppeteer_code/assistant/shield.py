"""
puppeteer_code/assistant/shield.py
==================================
The state of one request, and the hard rules on which agent may act next. The
policy only ever chooses from this list, so a bad or untrained policy cannot skip
planning, finish on failing checks, or spin forever.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from typing import Optional

from puppeteer_code.assistant.verify import Check

ACTIONS = ["Planner", "TestWriter", "Builder", "Debugger", "Reviewer", "Responder"]
MAX_USES = {"Planner": 1, "TestWriter": 2, "Builder": 3, "Debugger": 4, "Reviewer": 1}
STALL_LIMIT = 3


@dataclasses.dataclass
class RequestState:
    request: str
    small: bool = False
    new_project: bool = False
    testable: bool = False
    stack: str = ""
    plan: list[str] = dataclasses.field(default_factory=list)
    step: int = 0
    max_steps: int = 12
    used: Counter = dataclasses.field(default_factory=Counter)
    last_agent: str = ""
    built: bool = False
    baseline: Optional[Check] = None
    best: Optional[Check] = None
    check: Optional[Check] = None
    start_sha: str = ""
    best_sha: str = ""
    failure: str = ""
    notes: list[str] = dataclasses.field(default_factory=list)
    review: str = ""
    review_handled: bool = False
    stall: int = 0
    regressions: int = 0
    rollbacks: int = 0
    tokens: int = 0
    token_budget: int = 60000

    @property
    def has_tests(self) -> bool:
        return bool(self.check and self.check.kind == "tests")

    @property
    def failing(self) -> bool:
        return bool(self.check and self.check.kind != "none" and not self.check.ok)

    @property
    def wants_tests(self) -> bool:
        return not self.small and (self.new_project or len(self.plan) >= 3)


def allowed(st: RequestState) -> list[str]:
    out: list[str] = []
    room = lambda name: st.used[name] < MAX_USES[name]
    stuck = st.stall >= STALL_LIMIT or st.step >= st.max_steps - 1

    if not st.small and not st.built and st.step == 0 and room("Planner"):
        out.append("Planner")
    if not stuck:
        if not st.built and room("Builder"):
            out.append("Builder")
        if room("TestWriter") and ((not st.built and st.testable and st.wants_tests and not st.has_tests)
                                   or (st.built and st.failing and st.has_tests and st.used["Debugger"] >= 1)):
            out.append("TestWriter")
        needs_fix = st.failing or (st.review and not st.review_handled)
        if st.built and needs_fix and room("Debugger"):
            out.append("Debugger")
        if st.built and not st.failing and not st.small and room("Reviewer") and not st.review:
            out.append("Reviewer")
    if not out or (st.built and not st.failing and "Debugger" not in out):
        out.append("Responder")
    return out
