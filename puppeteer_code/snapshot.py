"""
puppeteer_code/snapshot.py
==========================
Bounded, Markovian context for agents.

Agents never see a transcript of previous agent messages.  Each activation
receives a fresh ContextSnapshot of the *current reality*: the task, the test
digest, the exact current source of the relevant symbols, callers, rolled-back
attempts, the cumulative diff, and short notes (plan, review, docs).

`render(budget_chars, order)` fills sections in priority order until the
character budget is used, so step 10 costs no more context than step 2 —
the diff *is* the accumulated work, not the conversation that produced it.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

TRUNC = "\n... [truncated to fit context budget]"


@dataclasses.dataclass
class ContextSnapshot:
    task: str
    step: int
    max_steps: int
    test_status: str = ""
    code: list[str] = dataclasses.field(default_factory=list)          # current source blocks
    callers: list[str] = dataclasses.field(default_factory=list)
    rollbacks: list[str] = dataclasses.field(default_factory=list)
    plan: str = ""
    docs: list[str] = dataclasses.field(default_factory=list)
    diff: str = ""
    review: str = ""
    lint: str = ""

    SECTIONS = {
        "task":      "TASK",
        "status":    "TEST STATUS",
        "code":      "CODE (current file contents — copy SEARCH text from here)",
        "callers":   "CALLERS OF THESE SYMBOLS (blast radius — keep them working)",
        "rollbacks": "ROLLED-BACK ATTEMPTS (these failed; take a different approach)",
        "plan":      "PLAN",
        "docs":      "DOCUMENTATION NOTES",
        "diff":      "CURRENT DIFF (vs original code)",
        "review":    "REVIEW NOTES",
        "lint":      "LINT",
    }

    def _body(self, key: str) -> str:
        if key == "task":
            return self.task.strip()[:2000]
        if key == "status":
            return self.test_status.strip()
        if key == "code":
            return "\n\n".join(self.code)
        if key == "callers":
            return "\n".join(self.callers)
        if key == "rollbacks":
            return "\n".join(f"- {r}" for r in self.rollbacks[-3:])
        if key == "plan":
            return self.plan.strip()[:1500]
        if key == "docs":
            return "\n".join(self.docs[-3:])
        if key == "diff":
            return self.diff.strip() or "(no changes yet)"
        if key == "review":
            return self.review.strip()[:1200]
        if key == "lint":
            return self.lint.strip()[:800]
        raise KeyError(key)

    def render(self, budget_chars: int = 8000, order: Optional[list[str]] = None) -> str:
        order = order or ["task", "status", "code", "rollbacks", "callers", "plan", "docs", "diff"]
        header = f"[step {self.step + 1}/{self.max_steps}]"
        parts = [header]
        used = len(header)
        for key in order:
            body = self._body(key)
            if not body:
                continue
            block = f"\n\n## {self.SECTIONS[key]}\n{body}"
            remaining = budget_chars - used
            if remaining <= len(TRUNC) + 40:
                break
            if len(block) > remaining:
                block = block[: remaining - len(TRUNC)] + TRUNC
            parts.append(block)
            used += len(block)
        return "".join(parts)
