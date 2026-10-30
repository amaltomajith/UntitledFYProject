"""
Offline tests for Puppeteer-Code P4: parallel worktree exploration.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from code_fixtures import CALC_BUGGY, CALC_FIX_BLOCK, make_calc_repo
from puppeteer_code.engine import CodeOrchestrator, RunConfig
from puppeteer_code.explore import Candidate, pick_best
from puppeteer_code.mock import scripted_responder
from puppeteer_code.providers import ProviderSession
from puppeteer_code.workspace import TestCase, TestResult
from test_code_engine import BREAK_ADD, NEUTRAL

FIX_WITH_NOISE = CALC_FIX_BLOCK + """
FILE: calc.py
<<<<<<< SEARCH
    \"\"\"Add two numbers.\"\"\"
=======
    \"\"\"Add two numbers (noise).\"\"\"
>>>>>>> REPLACE
"""


def _result(passed: int, total: int) -> TestResult:
    cases = {f"t::{i}": TestCase(f"t::{i}", "passed" if i < passed else "failed") for i in range(total)}
    return TestResult(cases, 0, 0.1)


class TestPickBest:
    def test_prefers_pass_rate_then_small_diff(self):
        a = Candidate("a", "m", applied=True, result=_result(3, 3), diff_lines=10)
        b = Candidate("b", "m", applied=True, result=_result(3, 3), diff_lines=2)
        c = Candidate("c", "m", applied=True, result=_result(2, 3), diff_lines=1)
        assert pick_best([a, b, c], best_pass_rate=1 / 3, best_green=False).variant == "b"

    def test_rejects_regressions_violations_and_non_improvements(self):
        reg = Candidate("reg", "m", applied=True, result=_result(3, 3), regressions=["x"])
        bad = Candidate("bad", "m", applied=True, result=_result(3, 3), violations=["skip"])
        same = Candidate("same", "m", applied=True, result=_result(1, 3))
        none = Candidate("none", "m", error="SEARCH not found")
        assert pick_best([reg, bad, same, none], best_pass_rate=1 / 3, best_green=False) is None


def test_stall_triggers_parallel_exploration_and_promotes_smallest_fix(tmp_path):
    scripted = scripted_responder({"CoderAgent": BREAK_ADD, "DebuggerAgent": NEUTRAL})
    explorer_replies = {"VARIANT 1": NEUTRAL, "VARIANT 2": CALC_FIX_BLOCK, "VARIANT 3": FIX_WITH_NOISE}

    def responder(model, messages):
        if messages[0]["content"].startswith("You are ExplorerAgent"):
            for key, reply in explorer_replies.items():
                if key in messages[-1]["content"]:
                    return reply
        return scripted(model, messages)

    session = ProviderSession("mock", responder=responder)
    repo = make_calc_repo(tmp_path)
    result = CodeOrchestrator(session, config=RunConfig(max_steps=14)).run(
        repo, "mean() returns the wrong value")

    names = [s["agent"] for s in result.steps]
    assert "ExplorerAgent" in names, names
    assert any(e["agent_name"] == "ExplorerAgent" for e in result.injections)
    explorer = next(s for s in result.steps if s["agent"] == "ExplorerAgent")
    assert "promoted 'alternative'" in explorer["content"]
    assert explorer["credit"] > 0
    assert result.success
    assert "noise" not in result.diff and "len(values)" in result.diff
    assert (repo / "calc.py").read_text(encoding="utf-8") == CALC_BUGGY
    # both quality and fast tiers were used by the fan-out
    assert {"mock-quality", "mock-fast"} <= set(result.usage["by_model"])
