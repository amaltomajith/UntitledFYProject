"""Offline tests for benchmarks/reasoning: checkers, token counting (policy included), runner, statistics."""

import argparse
import json

import pytest
from openai import RateLimitError

from benchmarks.reasoning import checkers, datasets, methods, run, stats
from benchmarks.reasoning.client import CountingClient


# ── checkers ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reply,gold,ok", [
    ("work\nFINAL ANSWER: 18", "18", True),
    ("FINAL ANSWER: $1,234", "1234", True),
    ("FINAL ANSWER: **18** dollars", "18", True),
    ("FINAL ANSWER: \\boxed{42}", "42", True),
    ("FINAL ANSWER: 19", "18", False),
    ("The total is 3 then 18.", "18", True),                 # no tag: last number
    ("<think>FINAL ANSWER: 7</think>FINAL ANSWER: 18", "18", True),
    ("FINAL ANSWER: 278081807", "278081800", False),        # large integers are exact
    ("FINAL ANSWER: 4.33", "4.333333333333333", True),      # non-integer gold, rounded
    ("FINAL ANSWER: 4.4", "4.333333333333333", False),
    ("FINAL ANSWER: 9/2", "4.5", True),
    ("FINAL ANSWER: -9867630", "-9867630", True),
    ("FINAL ANSWER: −9867630", "-9867630", True),           # Unicode minus
])
def test_number(reply, gold, ok):
    assert checkers.check("number", reply, gold) is ok


@pytest.mark.parametrize("reply,gold,ok", [
    ("FINAL ANSWER: C", "C", True),
    ("FINAL ANSWER: (C)", "C", True),
    ("FINAL ANSWER: C. 42 m/s", "C", True),
    ("FINAL ANSWER: Option J", "J", True),
    ("FINAL ANSWER: B", "C", False),
    ("I think the answer is (D).", "D", True),
    ("no answer here", "A", False),
    ("D", "D", True),                                       # a bare one-line reply is the answer
    ("I", "I", True),
])
def test_letter(reply, gold, ok):
    assert checkers.check("letter", reply, gold) is ok


@pytest.mark.parametrize("reply,gold,ok", [
    ("FINAL ANSWER: (B)", "(B)", True),
    ("FINAL ANSWER: B", "(B)", True),
    ("FINAL ANSWER: (B) 12/25/1937", "(B)", True),
    ("FINAL ANSWER: (A)", "(B)", False),
    ("FINAL ANSWER: True.", "True", True),
    ("FINAL ANSWER: (True)", "True", True),
    ("True", "True", True),
    ("FINAL ANSWER: false", "True", False),
    ("FINAL ANSWER: invalid", "valid", False),
    ("FINAL ANSWER: 12", "12", True),
    ("FINAL ANSWER: ] ) ]", "] ) ]", True),
    ("FINAL ANSWER: apple banana cherry", "apple banana cherry", True),
])
def test_exact(reply, gold, ok):
    assert checkers.check("exact", reply, gold) is ok


@pytest.mark.parametrize("reply,gold,ok", [
    ("FINAL ANSWER: 1,000", "1000", True),
    ("FINAL ANSWER: Paris", "paris", True),
    ("FINAL ANSWER: a, b, c", "a,b,c", True),
    ("FINAL ANSWER: a, b", "a,b,c", False),
])
def test_gaia(reply, gold, ok):
    assert checkers.check("gaia", reply, gold) is ok


def test_frozen_tasks_check_clean():
    assert datasets.check() == []
    counts = json.loads((datasets.TASKS_DIR / "MANIFEST.json").read_text())["counts"]
    assert counts == {"gsm8k": 50, "gsm-hard": 50, "mmlu-pro": 70, "bbh": 54}


def test_smoke_tasks_are_outside_the_subsets():
    main = {t["id"] for t in datasets.load_tasks(["gsm8k", "gsm-hard", "mmlu-pro", "bbh"])}
    smoke = {t["id"] for t in datasets.load_tasks(["gsm8k", "gsm-hard", "mmlu-pro", "bbh"], smoke=True)}
    assert smoke and not (main & smoke)


# ── a scripted model ─────────────────────────────────────────────────────────

class Scripted:
    """Replies by role: the policy names agents in turn; agents answer with the gold."""

    def __init__(self, answer="18", policy=("ReasoningAgent", "ConcluderAgent")):
        self.answer, self.policy, self.calls = answer, list(policy), []

    def chat(self, model, messages, temperature=0.7, max_tokens=2048, **kw):
        system = messages[0]["content"]
        self.calls.append(system[:40])
        if "central orchestrator" in system:
            name = self.policy[min(len([c for c in self.calls if c.startswith("You are the central")]) - 1,
                                   len(self.policy) - 1)]
            content, usage = name, (100, 5)
        else:
            content, usage = f"<think>hidden</think>REASONING RESULT: done\nFINAL ANSWER: {self.answer}", (200, 50)
        return {"content": content, "model": model,
                "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)}}


TASK = {"id": "gsm8k-1", "dataset": "gsm8k", "question": "q?", "gold": "18", "kind": "number", "meta": {}}


def test_policy_tokens_are_counted():
    inner = Scripted(policy=("ReasoningAgent", "ConcluderAgent"))
    client = CountingClient(inner)
    res = methods.run_puppeteer(client, "m", TASK)
    assert res.agents == ["ReasoningAgent", "ConcluderAgent"]
    assert res.status == "completed"
    assert len(client.calls) == 4                       # 2 policy calls + 2 agent calls
    assert client.prompt_tokens == 2 * 100 + 2 * 200
    assert checkers.check("number", res.reply, "18")
    assert "<think>" not in res.reply


def test_policy_fallback_recorded():
    client = CountingClient(Scripted(policy=("Nonsense", "ConcluderAgent")))
    res = methods.run_puppeteer(client, "m", TASK)
    assert res.policy_fallbacks == 1
    assert res.agents[0] == "PlannerAgent"              # the engine's fallback: first agent in the pool


def test_fixed_runs_four_agents_and_no_policy():
    client = CountingClient(Scripted())
    res = methods.run_fixed(client, "m", TASK)
    assert res.agents == list(methods.FIXED_ORDER)
    assert not any("central orchestrator" in c for c in client.inner.calls)


def test_one_call_methods():
    for fn in (methods.run_direct, methods.run_cot):
        client = CountingClient(Scripted())
        res = fn(client, "m", TASK)
        assert len(client.calls) == 1 and checkers.check("number", res.reply, "18")


def test_cost_uses_prices():
    client = CountingClient(Scripted())
    methods.run_direct(client, "m", TASK)
    assert client.cost({"m": [1.0, 2.0]}) == pytest.approx(200 / 1e6 + 50 * 2 / 1e6)
    assert client.cost({}) is None


# ── runner ───────────────────────────────────────────────────────────────────

def _args(tmp_path, **kw):
    base = dict(provider="nebius", model="m", datasets="gsm8k", methods="direct,puppeteer", seed=0, limit=2,
                smoke=False, cache=None, out=str(tmp_path / "r.json"), resume=False, thinking="off",
                max_tokens=4096)
    base.update(kw)
    return argparse.Namespace(**base)


def test_runner_writes_rows_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "prices_for", lambda p: {})
    tasks = datasets.load_tasks(["gsm8k"])[:2]
    inner = Scripted(answer=tasks[0]["gold"])
    assert run.run(_args(tmp_path), inner=inner) == 0
    doc = json.loads((tmp_path / "r.json").read_text())
    assert len(doc["rows"]) == 4 and doc["partial"] is False and doc["excluded"] == 0
    assert doc["summary"]["gsm8k"]["methods"]["direct"]["n"] == 2
    assert doc["config"]["max_steps"] == methods.MAX_STEPS


def test_provider_error_excluded_and_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "prices_for", lambda p: {})

    class Flaky(Scripted):
        def chat(self, model, messages, **kw):
            if "central orchestrator" in messages[0]["content"] and self.broken:
                raise RuntimeError("boom")
            return super().chat(model, messages, **kw)
    inner = Flaky()
    inner.broken = True
    run.run(_args(tmp_path), inner=inner)
    doc = json.loads((tmp_path / "r.json").read_text())
    assert doc["excluded"] == 2 and doc["partial"] is True
    assert doc["summary"]["gsm8k"]["methods"].get("puppeteer") is None
    inner.broken = False
    run.run(_args(tmp_path, resume=True), inner=inner)
    doc = json.loads((tmp_path / "r.json").read_text())
    assert doc["excluded"] == 0 and len(doc["rows"]) == 4 and doc["partial"] is False


def test_rescore_uses_stored_replies(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "prices_for", lambda p: {})
    tasks = datasets.load_tasks(["gsm8k"])[:2]
    run.run(_args(tmp_path), inner=Scripted(answer=tasks[0]["gold"]))
    path = tmp_path / "r.json"
    doc = json.loads(path.read_text())
    for r in doc["rows"]:
        r["correct"] = not r["correct"]
    path.write_text(json.dumps(doc))
    n, changed = run.rescore(path)
    assert n == 4 and changed == 4
    assert json.loads(path.read_text())["summary"]["gsm8k"]["methods"]["direct"]["correct"] == 1


def test_quota_stop_saves_and_returns_2(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "prices_for", lambda p: {})

    class Quota(Scripted):
        def chat(self, model, messages, **kw):
            import httpx
            resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
            raise RateLimitError("daily cap", response=resp, body=None)
    assert run.run(_args(tmp_path), inner=Quota()) == 2
    doc = json.loads((tmp_path / "r.json").read_text())
    assert doc["rows"] == []


# ── statistics ───────────────────────────────────────────────────────────────

def test_wilson_known_values():
    lo, hi = stats.wilson(40, 50)
    assert lo == pytest.approx(0.6696, abs=1e-3) and hi == pytest.approx(0.8876, abs=1e-3)
    assert stats.wilson(0, 0) == (0.0, 0.0)


def test_mcnemar_exact():
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(5, 0) == pytest.approx(0.0625)
    assert stats.mcnemar_exact(3, 3) == 1.0


def test_summary_pairs_and_excludes():
    rows = []
    for i in range(4):
        for m, ok in (("puppeteer", i < 3), ("direct", i < 1)):
            rows.append({"dataset": "d", "task": f"t{i}", "method": m, "seed": 0, "correct": ok, "tokens": 10,
                         "prompt_tokens": 8, "completion_tokens": 2, "calls": 1, "cost_usd": 0.1, "tagged": True})
    rows.append({**rows[0], "task": "t9", "provider_error": "x"})
    s = stats.summarize(rows)
    assert s["d"]["methods"]["puppeteer"]["n"] == 4
    assert s["d"]["paired"]["direct"] == {"shared": 4, "ours_only": 2, "theirs_only": 0, "p_mcnemar": 0.5}
    assert s["all"]["methods"]["direct"]["correct"] == 1
