"""
puppeteer_code/explore.py
=========================
Parallel worktree exploration (requirement E).

When the sequential Code ⇄ Test ⇄ Debug loop stalls, the registry injects
ExplorerAgent.  One activation:

  1. forks K linked git worktrees from the high-water-mark checkpoint
     (`git worktree add --detach`; forks share the object store),
  2. asks K differently-configured models for a fix concurrently
     (asyncio.gather + Semaphore for provider rate limits); each variant
     differs in model tier, temperature and strategy hint,
  3. applies each candidate in its own fork and runs the tests there,
  4. promotes the best candidate:  no integrity violation, no regression,
     highest pass rate, then the smallest diff footprint.  The winner's
     commit is promoted into the main sandbox with `reset --hard` (no copy),
     and the usual checkpoint rules credit the ExplorerAgent step.
  5. removes every fork.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from typing import Optional

from puppeteer_code.agents import EDIT_FORMAT, CodeAgent, _output
from puppeteer_code.integrity import check_test_count, scan_diff
from puppeteer_code.tools import apply_edits, parse_edit_blocks
from puppeteer_code.workspace import TestResult

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Variant:
    label: str
    tier: str
    temperature: float
    hint: str


DEFAULT_VARIANTS = [
    Variant("minimal", "quality", 0.2,
            "Make the most conservative, minimal fix of the most likely root cause."),
    Variant("alternative", "quality", 0.8,
            "The obvious fix was already tried and rolled back. Take a genuinely different approach."),
    Variant("rethink", "fast", 0.5,
            "Re-derive the root cause from the traceback alone before changing anything."),
]


@dataclasses.dataclass
class Candidate:
    variant: str
    model: str
    tokens: int = 0
    applied: bool = False
    error: str = ""
    result: Optional[TestResult] = None
    sha: str = ""
    diff_lines: int = 0
    violations: list = dataclasses.field(default_factory=list)
    regressions: list = dataclasses.field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.result.pass_rate if self.result else -1.0

    @property
    def eligible(self) -> bool:
        return self.applied and self.result is not None and not self.violations and not self.regressions

    def summary(self) -> dict:
        return {"variant": self.variant, "model": self.model, "tokens": self.tokens,
                "applied": self.applied, "error": self.error[:200], "pass_rate": self.pass_rate,
                "diff_lines": self.diff_lines, "violations": self.violations,
                "regressions": self.regressions}


def pick_best(candidates: list[Candidate], best_pass_rate: float, best_green: bool) -> Optional[Candidate]:
    """Highest pass rate, then smallest diff; must strictly improve on the high-water mark."""
    eligible = [c for c in candidates if c.eligible and (
        c.pass_rate > best_pass_rate or (c.result.all_green and not best_green))]
    if not eligible:
        return None
    return sorted(eligible, key=lambda c: (-c.pass_rate, c.diff_lines, c.variant))[0]


class ExplorerAgent(CodeAgent):
    ROLE = ("You are ExplorerAgent, an expert Python engineer working on one of several parallel "
            "attempts to fix a stubborn bug with SEARCH/REPLACE edits.")
    DESCRIPTION = "QUALITY models in parallel worktrees: K alternative fixes, promotes the best."
    TOOLS = ["git_worktree"]
    BUDGET = 9000
    ORDER = ["task", "status", "rollbacks", "code", "callers", "docs", "diff"]
    K = 3
    CONCURRENCY = 3

    def __init__(self, session, variants: Optional[list[Variant]] = None):
        super().__init__(session)
        self.variants = (variants or DEFAULT_VARIANTS)[: self.K]

    def run(self, env):
        return asyncio.run(self.arun(env))

    async def arun(self, env):
        st = env.state
        step = env.current_step
        t0 = time.monotonic()
        base_prompt = env.snapshot(self.name).render(self.BUDGET, self.ORDER)
        sem = asyncio.Semaphore(self.CONCURRENCY)
        # Create worktrees up front: concurrent `git worktree add` contends for locks.
        forks = [env.ws.fork(st.best_sha) for _ in self.variants]

        async def attempt(i: int, v: Variant) -> Candidate:
            fork = forks[i]
            model = self.session.model_for(self.name, tier_override=v.tier)
            cand = Candidate(variant=v.label, model=model)
            prompt = (f"{base_prompt}\n\n## YOUR JOB (VARIANT {i + 1}: {v.label})\n{v.hint}\n\n"
                      f"{EDIT_FORMAT}")
            async with sem:
                try:
                    resp = await asyncio.to_thread(
                        self.groq_client.chat, model=model, temperature=v.temperature,
                        max_tokens=self.config.max_tokens,
                        messages=[{"role": "system", "content": self.config.role_prompt},
                                  {"role": "user", "content": prompt}])
                except Exception as e:
                    cand.error = f"LLM call failed: {e}"
                    return cand
                cand.tokens = resp["usage"]["total_tokens"]
                res = apply_edits(fork, parse_edit_blocks(resp["content"] or ""))
                if not res.ok:
                    cand.error = "; ".join(res.errors)
                    return cand
                errs = fork.syntax_errors(res.files)
                if errs:
                    cand.error = "syntax error: " + errs[0]
                    return cand
                cand.applied = True
                cand.result = await asyncio.to_thread(fork.run_tests, None, env.test_timeout)
                cand.sha = fork.checkpoint(f"explorer {v.label}")
                diff = fork.diff()
                _, added, removed = fork.diff_stat()
                cand.diff_lines = added + removed
                cand.violations = scan_diff(diff) + check_test_count(st.baseline_total, cand.result.total)
                cand.regressions = sorted(st.best_passing - cand.result.passed)
                return cand

        try:
            candidates = await asyncio.gather(*(attempt(i, v) for i, v in enumerate(self.variants)))
        finally:
            for f in forks:
                f.cleanup()
            env.ws.git("worktree", "prune", check=False)

        tokens = sum(c.tokens for c in candidates)
        best = pick_best(candidates, st.best_pass_rate, st.best_tests.all_green)
        event = {"step": step, "candidates": [c.summary() for c in candidates],
                 "winner": best.variant if best else None}
        st.explore_events.append(event)
        st.edit_attempts += 1

        if best is not None:
            env.ws.rollback(best.sha)                     # promote the shared commit
            st.edits_since_test = True
            st.pending_edit = (step, self.name)
            verdict = env.record_test_result(best.result, step, promoted=True)
            content = (f"promoted '{best.variant}' ({best.model}): {len(best.result.passed)}/"
                       f"{best.result.total} passing, {best.diff_lines} diff lines "
                       f"[{verdict['action']}]")
        else:
            st.stall += 1
            st.credit[step] = -0.25
            reasons = ", ".join(f"{c.variant}: {c.error or f'pass {c.pass_rate:.2f}'}"[:120]
                                for c in candidates)
            st.rollbacks.append({"step": step, "agent": self.name,
                                 "reason": f"{len(candidates)} parallel candidates, none improved ({reasons})"})
            content = f"no candidate improved on {st.best_pass_rate:.2f} ({reasons})"
        return _output(self.name, content, tokens=tokens,
                       model=",".join(sorted({c.model for c in candidates})),
                       latency_ms=(time.monotonic() - t0) * 1000,
                       prompt_chars=len(base_prompt), explore=event)
