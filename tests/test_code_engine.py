"""
Offline tests for Puppeteer-Code P2/P3: CodeEnvironment, agents, shield,
policies, rewards and the engine loop, driven by the role-aware mock.
No API key or network needed.
"""

import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from code_fixtures import CALC_BUGGY, CALC_FIX_BLOCK, CALC_TESTS, _write, make_calc_repo
from core.environment import EnvironmentConfig
from puppeteer_code.agents import DebuggerAgent, EDIT_FORMAT
from puppeteer_code.engine import CodeOrchestrator, RunConfig
from puppeteer_code.environment import CodeEnvironment
from puppeteer_code.mock import scripted_responder
from puppeteer_code.policy import AGENTS, FixedPolicy, LinearPolicy, LLMSelector, features
from puppeteer_code.providers import ProviderSession
from puppeteer_code.shield import allowed_actions
from puppeteer_code.snapshot import ContextSnapshot

TASK = "mean() returns the wrong value"

BREAK_ADD = """\
FILE: calc.py
<<<<<<< SEARCH
    return a + b
=======
    return a - b
>>>>>>> REPLACE
"""

EDIT_TESTS = """\
FILE: tests/test_calc.py
<<<<<<< SEARCH
    assert mean([2, 4, 6]) == 4
=======
    assert True
>>>>>>> REPLACE
"""

SYNTAX_ERROR = """\
FILE: calc.py
<<<<<<< SEARCH
    return sum(values) / (len(values) - 1)
=======
    return sum(values) / (len(values)
>>>>>>> REPLACE
"""

NEUTRAL = """\
FILE: calc.py
<<<<<<< SEARCH
    \"\"\"Add two numbers.\"\"\"
=======
    \"\"\"Add two numbers together.\"\"\"
>>>>>>> REPLACE
"""


def run(repo, replies=None, solution=None, policy=None, **cfg):
    responder = scripted_responder(replies, solution)
    session = ProviderSession("mock", responder=responder)
    events = []
    orch = CodeOrchestrator(session, policy=policy, config=RunConfig(**{"max_steps": 10, **cfg}),
                            on_event=events.append)
    result = orch.run(repo, TASK)
    return result, responder, events


def agents_of(result):
    return [s["agent"] for s in result.steps]


# ── end to end ───────────────────────────────────────────────────────────────

class TestEndToEnd:
    def test_heuristic_fixes_bug_and_leaves_user_repo_untouched(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        result, _, events = run(repo, solution=CALC_FIX_BLOCK)
        assert result.success and result.status == "completed"
        assert agents_of(result) == ["RepoScoutAgent", "CoderAgent", "TestRunnerAgent", "ConcluderAgent"]
        assert "+    return sum(values) / len(values)" in result.diff
        assert (repo / "calc.py").read_text(encoding="utf-8") == CALC_BUGGY
        assert result.final["regressions"] == [] and result.final["final_tests"]["pass_rate"] == 1.0
        assert events[0]["type"] == "start" and events[-1]["type"] == "end"

    def test_tool_agents_cost_zero_tokens(self, tmp_path):
        result, _, _ = run(make_calc_repo(tmp_path), solution=CALC_FIX_BLOCK)
        for s in result.steps:
            if s["agent"] in ("RepoScoutAgent", "TestRunnerAgent", "LinterAgent"):
                assert s["tokens"] == 0 and s["model"] == "none"
        assert set(result.usage["by_model"]) <= {"mock-fast", "mock-quality"}
        assert result.usage["by_model"]["mock-quality"]["calls"] == 1   # Coder only

    def test_credit_goes_to_the_editing_agent(self, tmp_path):
        result, _, _ = run(make_calc_repo(tmp_path), solution=CALC_FIX_BLOCK)
        coder = next(s for s in result.steps if s["agent"] == "CoderAgent")
        runner = next(s for s in result.steps if s["agent"] == "TestRunnerAgent")
        assert coder["credit"] == 1.0 and runner["credit"] == 0.0
        assert result.rewards["terminal_reward"] == pytest.approx(1.6)   # 1.0 + 0.5 success + 0.1 lint

    def test_regression_is_rolled_back_and_shown_to_the_next_editor(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        responder_replies = {"CoderAgent": BREAK_ADD, "DebuggerAgent": CALC_FIX_BLOCK}
        result, responder, _ = run(repo, replies=responder_replies)
        verdicts = [s["verdict"]["action"] for s in result.steps if s["verdict"]]
        assert verdicts[0] == "rollback" and verdicts[-1] == "checkpoint"
        assert result.success
        assert "a - b" not in result.diff
        coder = next(s for s in result.steps if s["agent"] == "CoderAgent")
        assert coder["credit"] < 0

    def test_rollback_note_reaches_debugger_snapshot(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        env = CodeEnvironment(EnvironmentConfig(task=TASK, max_steps=10), repo)
        try:
            from puppeteer_code.tools import apply_edits, parse_edit_blocks
            res = apply_edits(env.ws, parse_edit_blocks(BREAK_ADD))
            env.after_edit("CoderAgent", 1, res.files)
            verdict = env.record_test_result(env.ws.run_tests(), 2)
            assert verdict["action"] == "rollback" and "regression" in verdict["reason"]
            assert env.ws.read("calc.py") == CALC_BUGGY
            text = env.snapshot("DebuggerAgent").render(9000, DebuggerAgent.ORDER)
            assert "ROLLED-BACK ATTEMPTS" in text and "test_add" in text
        finally:
            env.close()

    def test_integrity_guard_rejects_test_edits(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        result, _, _ = run(repo, replies={"CoderAgent": EDIT_TESTS, "DebuggerAgent": EDIT_TESTS},
                           max_steps=6)
        assert not result.success
        assert any("not allowed" in s["content"] for s in result.steps if s["agent"] == "CoderAgent")
        assert "tests/test_calc.py" not in result.diff

    def test_syntax_error_rolls_back_immediately(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        result, _, _ = run(repo, replies={"CoderAgent": SYNTAX_ERROR, "DebuggerAgent": CALC_FIX_BLOCK})
        coder = next(s for s in result.steps if s["agent"] == "CoderAgent")
        assert "syntax error" in coder["content"] and coder["credit"] == -1.0
        i = result.steps.index(coder)
        assert result.steps[i + 1]["agent"] != "TestRunnerAgent"      # nothing left to test
        assert result.success

    def test_neutral_edit_is_not_kept(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        result, _, _ = run(repo, replies={"CoderAgent": NEUTRAL, "DebuggerAgent": CALC_FIX_BLOCK})
        assert result.success
        assert "together" not in result.diff          # only lines that bought a gain survive

    def test_bad_search_gets_one_repair_turn(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        bad = CALC_FIX_BLOCK.replace("(len(values) - 1)", "(len(values) - 2)")
        result, responder, _ = run(repo, replies={"CoderAgent": [bad, CALC_FIX_BLOCK]})
        assert result.success
        assert responder.log.count("CoderAgent") == 2
        assert agents_of(result)[:3] == ["RepoScoutAgent", "CoderAgent", "TestRunnerAgent"]

    def test_unsolvable_task_terminates_or_truncates(self, tmp_path):
        result, _, _ = run(make_calc_repo(tmp_path), replies={"TerminatorAgent": "TERMINATE"},
                           max_steps=8)
        assert not result.success
        assert result.status in ("terminated", "completed", "truncated")
        assert result.diff == ""


# ── TDD / registry ───────────────────────────────────────────────────────────

REPRO_TEST = """\
FILE: tests/test_task_repro.py
<<<<<<< SEARCH
=======
from calc import mean


def test_mean_of_three():
    assert mean([2, 4, 6]) == 4
>>>>>>> REPLACE
"""


def make_green_repo(tmp_path):
    repo = make_calc_repo(tmp_path)
    only_add = CALC_TESTS.split("def test_mean")[0]
    _write(repo / "tests" / "test_calc.py", only_add)
    return repo


class TestTDD:
    def test_testwriter_is_injected_and_its_failing_test_becomes_a_target(self, tmp_path):
        repo = make_green_repo(tmp_path)
        result, _, _ = run(repo, replies={"TestWriterAgent": REPRO_TEST}, solution=CALC_FIX_BLOCK)
        names = agents_of(result)
        assert "TestWriterAgent" in names
        assert any(e["agent_name"] == "TestWriterAgent" for e in result.injections)
        accepted = [s for s in result.steps if s["verdict"] and s["verdict"]["action"] == "accepted"]
        assert accepted, names
        assert result.success
        assert "tests/test_task_repro.py" in result.diff and "len(values)" in result.diff

    def test_testwriter_may_not_edit_source(self, tmp_path):
        repo = make_green_repo(tmp_path)
        result, _, _ = run(repo, replies={"TestWriterAgent": CALC_FIX_BLOCK}, max_steps=5)
        tw = [s for s in result.steps if s["agent"] == "TestWriterAgent"]
        assert tw and "may only edit test files" in tw[0]["content"]


# ── bounded context ──────────────────────────────────────────────────────────

class TestBoundedContext:
    def test_render_respects_budget(self):
        snap = ContextSnapshot(task="t" * 5000, step=9, max_steps=12, test_status="s" * 5000,
                               code=["c" * 5000] * 3, rollbacks=["r" * 500] * 10, diff="d" * 9000)
        for budget in (2000, 5000, 9000):
            assert len(snap.render(budget)) <= budget

    def test_prompt_size_does_not_grow_with_steps(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        # Debugger keeps proposing neutral edits: many steps, many rollbacks.
        result, _, _ = run(repo, replies={"CoderAgent": NEUTRAL, "DebuggerAgent": NEUTRAL},
                           max_steps=14, explore=False)
        llm_editor = [s for s in result.steps if s["agent"] in ("CoderAgent", "DebuggerAgent")]
        assert len(llm_editor) >= 3
        cap = 9000 + len("\n\n## YOUR JOB\n") + len(DebuggerAgent.INSTRUCTIONS)
        assert all(s["prompt_chars"] <= cap for s in llm_editor)
        debug = [s["prompt_chars"] for s in llm_editor if s["agent"] == "DebuggerAgent"]
        # once the rollback log holds its last 3 entries, prompts stop growing
        assert max(debug) - min(debug) < 1500


# ── shield ───────────────────────────────────────────────────────────────────

class TestShield:
    def test_pending_edits_force_testing(self, tmp_path):
        env = CodeEnvironment(EnvironmentConfig(task=TASK, max_steps=10), make_calc_repo(tmp_path))
        try:
            pool = AGENTS
            first = allowed_actions(env, pool)
            assert "CoderAgent" not in first and "RepoScoutAgent" in first   # scout before coding
            env.state.scouted = 1
            env.state.scouted_at_tests = env.state.tests_run
            env.ws.write("calc.py", CALC_BUGGY.replace("(len(values) - 1)", "len(values)"))
            env.after_edit("CoderAgent", 1, ["calc.py"])
            assert set(allowed_actions(env, pool)) == {"TestRunnerAgent", "LinterAgent", "ReviewerAgent"}
            env.record_test_result(env.ws.run_tests(), 2)
            assert "ConcluderAgent" in allowed_actions(env, pool)
            assert "CoderAgent" not in allowed_actions(env, pool)            # green: stop editing
        finally:
            env.close()

    def test_last_step_concludes(self, tmp_path):
        env = CodeEnvironment(EnvironmentConfig(task=TASK, max_steps=1), make_calc_repo(tmp_path))
        try:
            assert allowed_actions(env, AGENTS) == ["ConcluderAgent"]
        finally:
            env.close()


# ── policies ─────────────────────────────────────────────────────────────────

class TestPolicies:
    def test_fixed_pipeline_runs(self, tmp_path):
        result, _, _ = run(make_calc_repo(tmp_path), solution=CALC_FIX_BLOCK, policy=FixedPolicy())
        assert agents_of(result)[0] == "RepoScoutAgent" and agents_of(result)[-1] == "ConcluderAgent"
        assert result.success

    def test_llm_selector_with_mock(self, tmp_path):
        session = ProviderSession("mock", responder=scripted_responder(solution=CALC_FIX_BLOCK))
        pol = LLMSelector(session)
        result = CodeOrchestrator(session, policy=pol, config=RunConfig(max_steps=10)).run(
            make_calc_repo(tmp_path), TASK)
        assert result.success and pol.fallbacks == 0

    def test_linear_policy_runs_and_respects_shield(self, tmp_path):
        pol = LinearPolicy(seed=0)
        result, _, _ = run(make_calc_repo(tmp_path), solution=CALC_FIX_BLOCK, policy=pol)
        for s in result.steps:
            assert s["agent"] in s["allowed"]
        assert result.rewards["policy_update"]["updated"]

    def test_reinforce_learns_preferred_action(self):
        """Toy bandit: action CoderAgent is rewarded, Planner is not."""
        pol = LinearPolicy(lr=0.5, seed=1)
        x = np.zeros(len(pol.W[0]))
        x[0] = 1.0
        allowed = ["CoderAgent", "PlannerAgent"]
        p0 = pol.probs(x, allowed)[0][AGENTS.index("CoderAgent")]
        for _ in range(60):
            pol.begin_episode()
            p, mask = pol.probs(x, allowed)
            idx = int(pol.rng.choice(len(AGENTS), p=p))
            pol.trace.append((x, mask, idx))
            pol.end_episode([1.0 if AGENTS[idx] == "CoderAgent" else 0.0])
        p1 = pol.probs(x, allowed)[0][AGENTS.index("CoderAgent")]
        assert p0 == pytest.approx(0.5) and p1 > 0.8

    def test_linear_policy_save_load(self, tmp_path):
        pol = LinearPolicy(seed=0)
        pol.W[2, 3] = 1.25
        pol.save(tmp_path / "w.json")
        assert LinearPolicy.load(tmp_path / "w.json").W[2, 3] == 1.25

    def test_features_are_normalised(self, tmp_path):
        env = CodeEnvironment(EnvironmentConfig(task=TASK, max_steps=10), make_calc_repo(tmp_path))
        try:
            x = features(env)
            assert x.shape == (len(LinearPolicy().W[0]),)
            assert np.all((x >= 0) & (x <= 1))
        finally:
            env.close()


def test_edit_format_mentions_tests_rule():
    assert "Never edit" in EDIT_FORMAT


def test_baseline_retries_a_transient_failure_then_refuses(tmp_path, monkeypatch):
    from puppeteer_code.workspace import TestResult, Workspace, WorkspaceError
    repo = make_calc_repo(tmp_path)
    real = Workspace.run_tests
    flaky = {"n": 0}

    def once_then_real(self, *a, **kw):
        flaky["n"] += 1
        return TestResult(cases={}, returncode=-1, duration_s=1.0, error="timeout after 1s") if flaky["n"] == 1 else real(self, *a, **kw)

    monkeypatch.setattr(Workspace, "run_tests", once_then_real)
    env = CodeEnvironment(EnvironmentConfig(task="t"), repo)
    try:
        assert env.state.baseline.failing and flaky["n"] == 2       # bug-fix targets, not TDD mode
    finally:
        env.close()

    monkeypatch.setattr(Workspace, "run_tests", lambda self, *a, **kw: TestResult(cases={}, returncode=-1, duration_s=1.0, error="timeout after 1s"))
    with pytest.raises(WorkspaceError, match="baseline test run failed"):
        CodeEnvironment(EnvironmentConfig(task="t"), repo)
