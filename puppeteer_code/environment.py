"""
puppeteer_code/environment.py
=============================
CodeEnvironment: the Puppeteer++ Environment specialised for coding.

The global state S_t is no longer just a list of agent outputs.  It is the
sandbox itself plus a structured CodeState: test results, the high-water
mark checkpoint, rollback history, scout results, and per-step credit.

Transactional rules (applied whenever tests run after an edit):

  integrity violation        → rollback to the high-water mark
  syntax error (on edit)     → rollback immediately, before testing
  regression (a test that passed at the high-water mark now fails)
                             → rollback
  pass rate improves         → new checkpoint (high-water mark)
  pass rate unchanged        → rollback (keeps the diff minimal and makes
                               every surviving line attributable to a gain)
  pass rate drops            → rollback
  TestWriterAgent edit (TDD) → accepted: new failing tests become targets

Every rollback is recorded with the agent and reason, and shown to the next
editing agent so it takes a different angle.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Optional

from core.environment import Environment, EnvironmentConfig, EpisodeStatus, StepRecord
from puppeteer_code.index import SymbolIndex
from puppeteer_code.integrity import check_test_count, scan_diff
from puppeteer_code.snapshot import ContextSnapshot
from puppeteer_code.workspace import LintResult, TestResult, Workspace, WorkspaceError

logger = logging.getLogger(__name__)

EDITING_AGENTS = {"CoderAgent", "DebuggerAgent", "TestWriterAgent", "ExplorerAgent"}


@dataclasses.dataclass
class CodeState:
    task: str
    ws: Workspace
    index: SymbolIndex
    baseline: TestResult
    last_tests: TestResult
    best_tests: TestResult
    best_sha: str
    baseline_total: int
    target_tests: set[str]

    edits_since_test: bool = False
    pending_edit: Optional[tuple[int, str]] = None   # (step, agent) awaiting a test run
    edit_attempts: int = 0
    rejected_edits: int = 0
    last_edit_rejected: bool = False
    tests_run: int = 0
    stall: int = 0                                   # edit→test cycles with no gain

    scout_keys: list[str] = dataclasses.field(default_factory=list)
    test_keys: list[str] = dataclasses.field(default_factory=list)
    scouted: int = 0
    scouted_at_tests: int = -1
    planned: bool = False
    plan: str = ""
    review: str = ""
    reviewed_since_edit: bool = False
    lint: Optional[LintResult] = None
    linted_since_edit: bool = False
    docs_notes: list[str] = dataclasses.field(default_factory=list)
    docs_at_tests: int = -1
    rollbacks: list[dict] = dataclasses.field(default_factory=list)
    integrity_violations: list[str] = dataclasses.field(default_factory=list)
    credit: dict[int, float] = dataclasses.field(default_factory=dict)   # step -> signed credit
    used: Counter = dataclasses.field(default_factory=Counter)
    last_agent: str = ""
    tdd: bool = False
    explore_events: list[dict] = dataclasses.field(default_factory=list)
    budget_exceeded: bool = False

    @property
    def best_pass_rate(self) -> float:
        return self.best_tests.pass_rate

    @property
    def best_passing(self) -> set[str]:
        return self.best_tests.passed

    @property
    def all_green(self) -> bool:
        return not self.edits_since_test and self.last_tests.all_green

    @property
    def improved(self) -> bool:
        return self.best_pass_rate > self.baseline.pass_rate or (
            self.best_tests.all_green and not self.baseline.all_green)


class CodeEnvironment(Environment):
    """Environment whose state is a git sandbox + CodeState."""

    def __init__(self, config: EnvironmentConfig, repo: str | Path, test_timeout: int = 120,
                 tdd: bool = False):
        super().__init__(config)
        self.repo = Path(repo)
        self.test_timeout = test_timeout
        self.ws = Workspace.create(repo)
        self.index = SymbolIndex(self.ws).build()
        baseline = self._baseline_tests(test_timeout)
        targets = set(baseline.failing)
        self.state = CodeState(
            task=config.task, ws=self.ws, index=self.index,
            baseline=baseline, last_tests=baseline, best_tests=baseline,
            best_sha=self.ws.base_sha, baseline_total=baseline.total,
            target_tests=targets, tdd=tdd,
        )
        logger.info("Baseline: %d/%d passing", len(baseline.passed), baseline.total)

    def _baseline_tests(self, timeout: int) -> TestResult:
        """
        The baseline defines the targets, so it must be trustworthy: a timeout or
        crash would look like "no failing tests" and silently turn a bug fix into
        a TDD task.  Retry once, then refuse to start.  ("no tests collected" is a
        legitimate baseline: a repository without tests.)
        """
        for _ in range(2):
            result = self.ws.run_tests(timeout=timeout)
            if not result.error or result.error == "no tests collected":
                return result
            logger.warning("Baseline test run failed (%s); retrying", result.error)
        self.ws.cleanup()
        raise WorkspaceError(f"baseline test run failed twice: {result.error}\n{result.raw_tail[-800:]}")

    # ── stopping rules ────────────────────────────────────────────────────────

    def _check_stop(self, record: StepRecord) -> Optional[EpisodeStatus]:
        self.state.used[record.agent_name] += 1
        self.state.last_agent = record.agent_name
        return super()._check_stop(record)

    # ── edit lifecycle ────────────────────────────────────────────────────────

    def after_edit(self, agent: str, step: int, files: list[str]) -> Optional[str]:
        """
        Called by editing agents after a successful apply.  Runs the instant
        syntax check; on failure rolls back immediately and returns the error.
        """
        st = self.state
        st.edit_attempts += 1
        st.last_edit_rejected = False
        errors = self.ws.syntax_errors(files)
        if errors:
            self._rollback(agent, step, "syntax error: " + "; ".join(errors)[:300])
            st.credit[step] = -1.0
            return errors[0]
        st.edits_since_test = True
        st.pending_edit = (step, agent)
        st.reviewed_since_edit = False
        st.linted_since_edit = False
        return None

    def edit_rejected(self, agent: str, step: int, errors: list[str]) -> None:
        st = self.state
        st.edit_attempts += 1
        st.rejected_edits += 1
        st.last_edit_rejected = True
        st.credit[step] = -0.5
        st.stall += 1
        st.rollbacks.append({"step": step, "agent": agent,
                             "reason": "edit not applied: " + "; ".join(errors)[:300]})

    def record_test_result(self, result: TestResult, step: int, promoted: bool = False) -> dict:
        """
        Apply the transactional rules after a test run.  Returns a verdict:
        {action: checkpoint|rollback|accepted|baseline, delta, reason}.
        """
        st = self.state
        st.tests_run += 1
        prev_best = st.best_pass_rate
        edit_step, edit_agent = st.pending_edit if st.pending_edit else (step, "TestRunnerAgent")
        verdict = {"action": "baseline", "delta": 0.0, "reason": ""}

        if not st.edits_since_test and not promoted:
            st.last_tests = result
            return verdict

        diff = self.ws.diff()
        violations = scan_diff(diff) + check_test_count(st.baseline_total, result.total)
        regressions = sorted(st.best_passing - result.passed)
        delta = result.pass_rate - prev_best

        if violations:
            st.integrity_violations.extend(violations)
            self._rollback(edit_agent, edit_step, "integrity: " + "; ".join(violations)[:300])
            verdict = {"action": "rollback", "delta": -1.0, "reason": "integrity violation"}
            st.credit[edit_step] = -1.0
        elif edit_agent == "TestWriterAgent":
            new_failing = result.failing - st.best_tests.failing
            st.target_tests |= new_failing
            st.baseline_total = max(st.baseline_total, result.total)
            st.best_sha = self.ws.checkpoint(f"step {edit_step}: {edit_agent} added tests")
            st.best_tests = result
            st.last_tests = result
            st.scout_keys = []          # re-scout against the new failing tests
            verdict = {"action": "accepted", "delta": 0.0,
                       "reason": f"{len(new_failing)} new reproducing test(s)"}
            st.credit[edit_step] = 0.5 if new_failing else -0.25
        elif regressions:
            self._rollback(edit_agent, edit_step,
                           f"regression: broke {', '.join(r.split('::')[-1] for r in regressions[:4])}",
                           result)
            verdict = {"action": "rollback", "delta": delta, "reason": "regression"}
            st.credit[edit_step] = max(-1.0, min(-0.25, 2 * delta))
        elif result.pass_rate > prev_best or (result.all_green and not st.best_tests.all_green):
            st.best_sha = self.ws.checkpoint(
                f"step {edit_step}: {edit_agent} {len(result.passed)}/{result.total}")
            st.best_tests = result
            st.last_tests = result
            st.stall = 0
            verdict = {"action": "checkpoint", "delta": delta, "reason": "pass rate improved"}
            st.credit[edit_step] = min(1.0, max(0.25, 2 * delta))
        else:
            reason = "no improvement" if delta == 0 else "pass rate dropped"
            self._rollback(edit_agent, edit_step, reason, result)
            verdict = {"action": "rollback", "delta": delta, "reason": reason}
            st.credit[edit_step] = max(-1.0, min(-0.1, 2 * delta))

        st.edits_since_test = False
        st.pending_edit = None
        return verdict

    def _rollback(self, agent: str, step: int, reason: str, result: Optional[TestResult] = None) -> None:
        st = self.state
        self.ws.rollback(st.best_sha)
        st.edits_since_test = False
        st.pending_edit = None
        st.last_tests = st.best_tests
        st.stall += 1
        note = {"step": step, "agent": agent, "reason": reason}
        if result is not None:
            first = sorted(result.failing)[:1]
            if first:
                note["failure"] = result.cases[first[0]].message.strip()[:200]
        st.rollbacks.append(note)
        logger.info("Rollback at step %d (%s): %s", step, agent, reason)

    # ── snapshot ──────────────────────────────────────────────────────────────

    def refresh_index(self) -> None:
        self.index.build()

    def snapshot(self, for_agent: str) -> ContextSnapshot:
        st = self.state
        self.refresh_index()
        code_blocks, callers = [], []
        for key in st.scout_keys:
            sym = self.index.symbols.get(key)
            if sym is None:
                continue
            code_blocks.append(f"# {sym.header()}\n{self.index.source(sym, pad=2)}")
            for c in self.index.callers(sym, limit=4):
                line = f"{c.file}:{c.lineno}  {c.qualname}{c.signature}"
                if line not in callers:
                    callers.append(line)
        for key in st.test_keys:
            sym = self.index.symbols.get(key)
            if sym is not None:
                code_blocks.append(f"# {sym.header()}  (test — read only)\n{self.index.source(sym, pad=0)}")

        rollbacks = []
        for r in st.rollbacks[-3:]:
            text = f"step {r['step'] + 1} {r['agent']}: {r['reason']}"
            if r.get("failure"):
                text += f" (first failure: {r['failure']})"
            rollbacks.append(text)

        status = st.last_tests.digest()
        if st.edits_since_test:
            status = "Edits applied but NOT yet tested.\n" + status
        lint = ""
        if st.lint is not None:
            lint = "clean" if st.lint.clean else "\n".join(st.lint.issues[:10])
        return ContextSnapshot(
            task=st.task, step=self.current_step, max_steps=self.config.max_steps,
            test_status=status, code=code_blocks, callers=callers[:8], rollbacks=rollbacks,
            plan=st.plan, docs=st.docs_notes, diff=self.ws.diff(), review=st.review, lint=lint,
        )

    # ── final evaluation ──────────────────────────────────────────────────────

    def finalize(self, holdout_dir: Optional[str | Path] = None) -> dict:
        """Final truth: run the full suite (and optional hidden holdout tests)."""
        st = self.state
        if st.edits_since_test:
            self.record_test_result(self.ws.run_tests(timeout=self.test_timeout), self.current_step)
        final = self.ws.run_tests(timeout=self.test_timeout)
        diff = self.ws.diff()
        regressions = sorted(st.baseline.passed - final.passed)
        out = {
            "final_tests": final.to_dict(),
            "baseline_tests": st.baseline.to_dict(),
            "targets_fixed": sorted(st.target_tests & final.passed),
            "targets_total": len(st.target_tests),
            "regressions": regressions,
            "integrity_violations": sorted(set(st.integrity_violations)),
            "diff": diff,
            "diff_stat": self.ws.diff_stat(),
        }
        if holdout_dir:
            holdout = Path(holdout_dir)
            dest = "tests/_holdout"
            copied = []
            for f in holdout.glob("*.py"):
                rel = f"{dest}/{f.name}"
                self.ws.write(rel, f.read_text(encoding="utf-8"))
                copied.append(rel)
            if copied:
                hres = self.ws.run_tests(copied, timeout=self.test_timeout)
                out["holdout_tests"] = hres.to_dict()
                shutil.rmtree(self.ws.resolve(dest), ignore_errors=True)
        lint = self.ws.run_lint(self.ws.changed_files())
        out["lint_clean"] = bool(diff.strip()) and lint.clean
        out["lint_issues"] = lint.issues[:20]
        out["success"] = (
            final.all_green
            and not regressions
            and not out["integrity_violations"]
            and out.get("holdout_tests", {"pass_rate": 1.0})["pass_rate"] == 1.0
            and bool(diff.strip() or not st.baseline.failing)
        )
        return out

    def close(self) -> None:
        self.ws.cleanup()
