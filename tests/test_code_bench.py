"""
Offline tests for Puppeteer-Code P3/P5: tasks, simulated responder,
baselines, training loop, benchmark aggregation, report and CLI.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from code_fixtures import make_calc_repo
from puppeteer_code import baselines
from puppeteer_code.bench import aggregate, run_method, summary_table, train
from puppeteer_code.cli import build_parser, main
from puppeteer_code.index import SymbolIndex
from puppeteer_code.mock import simulated_responder
from puppeteer_code.providers import ProviderSession
from puppeteer_code.report import write_report
from puppeteer_code.tasks import load_tasks
from puppeteer_code.workspace import Workspace


@pytest.fixture(scope="module")
def tasks():
    return load_tasks(split="all")


def test_benchmark_has_train_and_test_splits(tasks):
    assert len(tasks) >= 25
    splits = {t.split for t in tasks}
    assert splits == {"train", "test"}
    assert not {t.module for t in tasks if t.split == "train"} & {t.module for t in tasks if t.split == "test"}
    assert any(t.n_bugs == 2 for t in tasks)
    for t in tasks:
        assert (t.repo / "conftest.py").exists() and (t.holdout / "test_holdout.py").exists()


def test_index_sees_module_level_tables(tasks):
    task = next(t for t in tasks if t.id == "rpn_division")
    ws = Workspace.create(task.repo)
    try:
        idx = SymbolIndex(ws).build()
        ops = idx.by_name["OPS"][0]
        assert ops.kind == "variable"
        assert "evaluate" in {s.qualname for s in idx.callers(ops)}
        res = ws.run_tests()
        ranked = [s.qualname for s, _, _ in idx.rank(task.task, res.frames(), res.failing)]
        assert "OPS" in ranked
    finally:
        ws.cleanup()


def test_simulated_responder_only_fixes_visible_code(tasks):
    task = next(t for t in tasks if t.id == "rpn_two_bugs")
    sim = simulated_responder(task.solution, seed=0, skill={"CoderAgent": 1.0})
    blind = sim("mock-quality", [{"role": "system", "content": "You are CoderAgent"},
                                 {"role": "user", "content": "no code here"}])
    assert "not in this file" in blind
    full = sim("mock-quality", [{"role": "system", "content": "You are CoderAgent"},
                                {"role": "user", "content": (task.repo / "rpn.py").read_text()}])
    assert full.count("<<<<<<< SEARCH") == 2


def test_single_shot_and_react_baselines(tmp_path):
    from code_fixtures import CALC_FIX_BLOCK
    from puppeteer_code.mock import scripted_responder
    repo = make_calc_repo(tmp_path)
    session = ProviderSession("mock", responder=scripted_responder(solution=CALC_FIX_BLOCK))
    single = baselines.single_shot(session, repo, "mean() is wrong")
    assert single["success"] and len(single["steps"]) == 1
    react = baselines.react(ProviderSession("mock", responder=scripted_responder(solution=CALC_FIX_BLOCK)),
                            repo, "mean() is wrong")
    assert react["success"]


def test_react_transcript_grows(tmp_path):
    from puppeteer_code.mock import scripted_responder
    repo = make_calc_repo(tmp_path)
    session = ProviderSession("mock", responder=scripted_responder())     # never fixes anything
    res = baselines.react(session, repo, "mean() is wrong", max_turns=3)
    sizes = [s["prompt_chars"] for s in res["steps"]]
    assert len(sizes) == 3 and sizes[0] < sizes[1] < sizes[2]
    assert not res["success"]


def test_run_method_scores_hidden_tests(tasks):
    task = next(t for t in tasks if t.id == "bank_exact")
    row = run_method("heuristic", task, "oracle", seed=0)
    assert row["success"] and row["holdout_pass_rate"] == 1.0 and row["regressions"] == 0
    assert row["agents"][0] == "RepoScoutAgent" and row["agents"][-1] == "ConcluderAgent"


def test_train_loop_updates_and_saves(tasks, tmp_path):
    subset = [t for t in tasks if t.id in ("bank_exact", "inv_value")]
    curve = train(subset, "sim", episodes=2, out_weights=tmp_path / "w.json",
                  curve_path=tmp_path / "curve.json", seed=1)
    assert len(curve) == 2 and all(r["update"]["updated"] for r in curve)
    saved = json.loads((tmp_path / "curve.json").read_text())
    assert saved["provider"] == "sim" and (tmp_path / "w.json").exists()


def _fake_rows():
    rows = []
    for seed in (0, 1):
        for method, ok, tokens in (("heuristic", True, 1000), ("react", seed == 0, 3000)):
            rows.append({"method": method, "task": "t", "difficulty": "easy", "split": "test", "seed": seed,
                         "success": ok, "status": "completed", "pass_rate": 1.0 if ok else 0.5,
                         "holdout_pass_rate": 1.0 if ok else 0.0, "regressions": 0, "integrity": 0,
                         "tokens": tokens, "cost_usd": None, "steps": 4, "llm_calls": 2,
                         "max_prompt_chars": 2000, "prompt_chars": [0, 1500, 0, 2000],
                         "agents": ["RepoScoutAgent", "CoderAgent", "TestRunnerAgent", "ConcluderAgent"],
                         "wall_s": 1.0, "diff_lines": 2, "return": 1.2})
    return rows


def test_aggregate_mean_std_over_seeds():
    s = aggregate(_fake_rows())
    assert s["heuristic"]["success"]["mean"] == 1.0 and s["heuristic"]["success"]["std"] == 0.0
    assert s["react"]["success"]["mean"] == 0.5 and s["react"]["success"]["std"] == 0.5
    assert s["react"]["tokens_per_success"] == 6000
    assert "| heuristic | 100% ± 0 |" in summary_table(s)


def test_report_is_self_contained(tmp_path):
    rows = _fake_rows()
    bench = {"provider": "sim", "methods": ["heuristic", "react"], "seeds": [0, 1], "tasks": ["t"],
             "rows": rows, "summary": aggregate(rows)}
    curve = {"provider": "sim", "lr": 0.1, "episodes": [dict(r, episode=i) for i, r in enumerate(rows)],
             "top_weights": {"CoderAgent": [["scouted", 0.5]]}}
    (tmp_path / "b.json").write_text(json.dumps(bench))
    (tmp_path / "c.json").write_text(json.dumps(curve))
    out = write_report(tmp_path / "r.html", str(tmp_path / "b.json"), str(tmp_path / "c.json"))
    page = out.read_text(encoding="utf-8")
    assert "Simulated models" in page and "<svg" in page
    assert "http://" not in page and "https://" not in page          # no external assets


def test_cli_parser_and_replay(tmp_path, capsys):
    p = build_parser()
    args = p.parse_args(["fix", "do it", "--repo", ".", "--provider", "mock", "--policy", "linear"])
    assert args.policy == "linear" and args.provider == "mock"
    for cmd in ("train", "bench", "report", "models", "replay"):
        assert cmd in p._subparsers._group_actions[0].choices
    run = {"task": "t", "policy": "heuristic", "status": "completed", "success": True,
           "steps": [{"step": 0, "agent": "CoderAgent", "model": "m", "tokens": 10, "content": "applied",
                      "best_pass_rate": 1.0, "verdict": None}],
           "rewards": {"terminal_reward": 1.6, "total_return": 1.5}, "usage": {"total_tokens": 10},
           "final": {"diff": "+x"}}
    (tmp_path / "run.json").write_text(json.dumps(run))
    assert main(["replay", str(tmp_path / "run.json"), "--diff"]) == 0
    assert "CoderAgent" in capsys.readouterr().out


def test_react_tools_reads_before_editing(tasks):
    task = next(t for t in tasks if t.id == "bank_exact")
    sim = simulated_responder(task.solution, seed=0, skill={"CoderAgent": 1.0})
    res = baselines.react_tools(ProviderSession("mock", responder=sim), task.repo, task.task)
    assert res["success"] and res["policy"] == "react-tools"
    dump = len((task.repo / "bank.py").read_text(encoding="utf-8"))
    assert res["steps"][0]["prompt_chars"] < 3 * dump              # starts from a file list, not a dump


def test_react_tools_read_action_shows_numbered_source(tasks):
    task = next(t for t in tasks if t.id == "bank_exact")
    from puppeteer_code.mock import scripted_responder
    session = ProviderSession("mock", responder=scripted_responder(
        {"CoderAgent": ["READ: bank.py", "DONE"]}))
    res = baselines.react_tools(session, task.repo, task.task)
    assert [s["content"] for s in res["steps"]] == ["READ: bank.py", "DONE"]
    assert res["steps"][1]["prompt_chars"] > res["steps"][0]["prompt_chars"]   # transcript grows
    assert not res["success"]


def test_tier_overrides_reach_the_session(tasks):
    p = build_parser()
    args = p.parse_args(["bench", "--provider", "sim", "--quality-model", "mock-fast"])
    from puppeteer_code.bench import make_task_session, tier_overrides
    models = tier_overrides(args)
    assert models == {"quality": "mock-fast"}
    session = make_task_session("sim", tasks[0], 0, models=models)
    assert session.model_for("CoderAgent") == "mock-fast"


def test_bench_stops_on_quota_and_resumes(tasks, tmp_path, monkeypatch):
    from puppeteer_code import bench as bench_mod
    subset = [t for t in tasks if t.id in ("bank_exact", "inv_value")]
    calls = []

    def fake_run(method, task, provider, seed, **kw):
        calls.append(task.id)
        if task.id == "inv_value" and calls.count("inv_value") == 1:
            return {"method": method, "task": task.id, "difficulty": task.difficulty, "split": task.split,
                    "seed": seed, "success": False, "provider_error": "RateLimitError: 429 tokens per day"}
        return dict(_fake_rows()[0], method=method, task=task.id, seed=seed)

    monkeypatch.setattr(bench_mod, "run_method", fake_run)
    out = tmp_path / "b.json"
    first = bench_mod.bench(subset, ["heuristic"], [0], "groq", out)
    assert first["partial"] and "429" in first["stopped"] and calls == ["bank_exact", "inv_value"]
    second = bench_mod.bench(subset, ["heuristic"], [0], "groq", out, resume=True)
    assert not second["partial"] and calls[2:] == ["inv_value"]       # bank_exact was not re-run
    assert [r["success"] for r in second["rows"]] == [True, True]


def test_repo_variant_cross_module_bug_is_scouted():
    root = os.path.join(ROOT, "benchmarks", "code_tasks", "tasks_repo")
    repo_tasks = load_tasks(root, split="all")
    assert len(repo_tasks) >= 40 and sum(t.id.startswith("repo_x_") for t in repo_tasks) >= 6
    task = next(t for t in repo_tasks if t.id == "repo_x_median")
    assert not (task.repo / "tests" / "test_stats.py").exists()        # the module's own tests are hidden
    ws = Workspace.create(task.repo)
    try:
        res = ws.run_tests()
        assert res.failing == {"tests.test_report::test_describe_numbers"}
        idx = SymbolIndex(ws).build()
        top = [(s.file, s.qualname) for s, _, _ in idx.rank(task.task, res.frames(), res.failing)[:4]]
        assert ("stats.py", "median") in top
    finally:
        ws.cleanup()


def test_react_tools_understands_native_tool_calls():
    call = '{"name": "repo_browser.open_file", "arguments": {"path": "rpn.py", "line_start": 1}}'
    assert baselines._native_tool_call(call) == "READ: rpn.py"
    assert baselines._native_tool_call('{"name": "repo_browser.search", "arguments": {"query": "OPS"}}') == "GREP: OPS"
    assert baselines._native_tool_call("DONE") == "DONE"


def test_groq_tool_use_failed_is_returned_as_text():
    from types import SimpleNamespace
    from core.agent import _failed_generation
    err = SimpleNamespace(body={"error": {"code": "tool_use_failed", "failed_generation": '{"name": "x"}'}})
    assert _failed_generation(err) == '{"name": "x"}'
    assert _failed_generation(SimpleNamespace(body={"code": "tool_use_failed", "failed_generation": "y"})) == "y"
    assert _failed_generation(SimpleNamespace(body={"error": {"code": "invalid_model"}})) is None


def test_react_tools_converts_native_apply_patch():
    patch = ("*** Begin Patch\n*** Update File: calc.py\n@@\n     \"\"\"Arithmetic mean of a non-empty list.\"\"\"\n"
             "-    return sum(values) / (len(values) - 1)\n+    return sum(values) / len(values)\n*** End Patch")
    text = baselines._native_tool_call(json.dumps({"name": "repo_browser.apply_patch", "arguments": {"patch": patch}}))
    from puppeteer_code.tools import parse_edit_blocks
    (block,) = parse_edit_blocks(text)
    assert block.file == "calc.py" and "(len(values) - 1)" in block.search and "/ len(values)" in block.replace
