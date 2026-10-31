"""Plan mode and effort levels: what the host enforces, on a scripted model."""

import json

import pytest

from puppeteer_code.assistant.effort import describe, reasons, think_option, tier_for
from puppeteer_code.assistant.session import NO_PLAN, AssistantSession
from puppeteer_code.assistant.shield import RequestState, allowed
from puppeteer_code.assistant.toolbox import READ_ONLY, SPECS as TOOLS, Toolbox
from puppeteer_code.providers import ProviderSession

PLAN = "STACK: python\n1. Write app.py\n2. Add a test"
WRITE = {"content": "", "tool_calls": [{"id": "c1", "name": "write_file",
                                        "arguments": json.dumps({"path": "app.py", "content": "print(1)\n"})}]}


def script(*replies):
    queue = list(replies)
    seen = []

    def responder(model, messages):
        seen.append((model, list(messages)))
        return queue.pop(0) if queue else "All done."
    responder.seen = seen
    return responder


def session_for(tmp_path, *replies, values=None, **options):
    responder = script(*replies)
    provider = ProviderSession("mock", responder=responder)
    if values is not None:
        provider.client.inner.think_values = lambda model: values
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    options.setdefault("policy", "heuristic")
    return AssistantSession(project, provider, home=tmp_path / "home", **options), responder


def of(events, name):
    return [e for e in events if e["type"] == name]


# ── plan mode ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", sorted(set(TOOLS) - set(READ_ONLY)))
def test_plan_mode_refuses_every_tool_that_changes_something(tmp_path, tool):
    events = []
    tools = Toolbox(tmp_path, events.append, lambda r: True, "plan")
    args = {"path": "a.txt", "content": "x", "old": "x", "new": "y", "command": "echo hi"}
    assert tools.call(tool, json.dumps(args), list(TOOLS)).startswith("ERROR")
    assert not (tmp_path / "a.txt").exists() and not of(events, "tool_start")


def test_plan_mode_still_reads(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    tools = Toolbox(tmp_path, lambda e: None, lambda r: True, "plan")
    assert tools.call("read_file", '{"path": "a.txt"}', list(TOOLS)) == "hello"


def test_plan_mode_writes_nothing_even_if_the_model_tries(tmp_path):
    session, _ = session_for(tmp_path, WRITE, PLAN, mode="plan")
    events = list(session.send("build a tip calculator with a test suite"))
    assert not (session.project / "app.py").exists()
    assert of(events, "done")[0]["status"] == "planned"
    assert of(events, "plan")[0]["pending"] is True
    assert session.pending_plan["steps"] == ["Write app.py", "Add a test"]
    assert not of(events, "text_delta")


def test_plan_mode_runs_no_checks(tmp_path):
    session, _ = session_for(tmp_path, PLAN, mode="plan")
    (session.project / "test_a.py").write_text("def test_a():\n    assert True\n")
    assert not of(list(session.send("add a feature that does something useful")), "verify")


def test_questions_in_plan_mode_are_answered_and_keep_the_plan(tmp_path):
    session, _ = session_for(tmp_path, PLAN, "It is a calculator.", mode="plan")
    list(session.send("build a tip calculator with a test suite"))
    events = list(session.send("what does this project do?"))
    assert of(events, "done")[0]["status"] == "done" and session.pending_plan


def test_a_new_message_replaces_the_plan(tmp_path):
    session, _ = session_for(tmp_path, PLAN, "STACK: node\n1. Something else", mode="plan")
    list(session.send("build a tip calculator with a test suite"))
    list(session.send("make a to-do list instead of that"))
    assert session.pending_plan["request"] == "make a to-do list instead of that"
    assert session.pending_plan["stack"] == "node"


def test_a_reply_with_no_steps_leaves_nothing_waiting(tmp_path):
    session, _ = session_for(tmp_path, "I would rather chat.", mode="plan")
    done = of(list(session.send("build a tip calculator with a test suite")), "done")[0]
    assert done["status"] == "failed" and done["reason"] == NO_PLAN and session.pending_plan is None


def test_nothing_to_approve_is_an_error(tmp_path):
    session, _ = session_for(tmp_path)
    with pytest.raises(ValueError):
        session.approve_plan()


def test_approval_builds_without_planning_again_and_restores_the_mode(tmp_path):
    session, _ = session_for(tmp_path, PLAN, WRITE, "Done writing.")
    (session.project / "a.py").write_text("x = 1\n")
    session.set_mode("auto")
    session.set_mode("plan")
    list(session.send("write me an app"))         # short, so without a plan it would count as small
    events = list(session.approve_plan())
    assert session.tools.mode == "auto"
    assert (session.project / "app.py").exists()
    assert session.pending_plan is None
    assert "Planner" not in [e["agent"] for e in of(events, "agent")]
    assert "Reviewer" in [e["agent"] for e in of(events, "agent")]      # so it was not treated as small


def test_approved_text_that_looks_like_a_question_still_builds(tmp_path):
    session, _ = session_for(tmp_path, WRITE, "Done.", mode="plan")
    session.pending_plan = {"request": "what about a small app?", "steps": ["Write it"], "stack": "python",
                            "sha": session.snapshots.snapshot("x"), "return_mode": "ask"}
    assert "Builder" in [e["agent"] for e in of(list(session.approve_plan()), "agent")]


def test_approval_warns_when_files_changed_after_planning(tmp_path):
    session, _ = session_for(tmp_path, PLAN, "Done.", mode="plan")
    list(session.send("build a tip calculator with a test suite"))
    (session.project / "mine.txt").write_text("changed by hand")
    notices = of(list(session.approve_plan()), "notice")
    assert any("mine.txt" in n["text"] for n in notices)


def test_a_waiting_plan_survives_a_restart(tmp_path):
    session, _ = session_for(tmp_path, PLAN, mode="plan")
    list(session.send("build a tip calculator with a test suite"))
    again = AssistantSession.resume(session.id, session.provider, home=session.home)
    assert again.pending_plan["steps"] == ["Write app.py", "Add a test"]


def test_the_shield_skips_the_planner_after_approval():
    assert "Planner" not in allowed(RequestState(request="x", planned=True))


def test_plan_mode_cannot_be_entered_mid_request(tmp_path):
    session, _ = session_for(tmp_path)
    session._busy = True
    with pytest.raises(RuntimeError):
        session.set_mode("plan")
    session._busy = False


# ── effort ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("level,agent,values,expected", [
    ("normal", "Planner", [False, True], {"think": True}),
    ("normal", "Builder", [False, True], {"think": False}),
    ("think", "Builder", [False, True], {"think": True}),
    ("think", "Responder", [False, True], {"think": False}),
    ("fast", "Debugger", [False, True], {"think": False}),
    ("deep", "Builder", ["low", "medium", "high"], {"think": "high"}),
    ("deep", "Builder", ["high", "low"], {"think": "high"}),
    ("fast", "Builder", ["low", "medium", "high"], {"think": "low"}),
    ("normal", "Planner", ["low", "medium", "high"], {"think": "medium"}),
    ("think", "Builder", None, {}),
    ("normal", "Builder", None, {}),
])
def test_think_option(level, agent, values, expected):
    assert think_option(level, agent, values) == expected


def test_tiers_by_effort():
    assert tier_for("normal", "Planner") is None and tier_for("normal", "Builder") is None
    assert tier_for("fast", "Builder") == "fast"
    assert tier_for("think", "Planner") == "quality" and tier_for("think", "Builder") is None


def test_reasoning_pass_only_where_it_is_needed():
    assert reasons("think", "Builder", None) and reasons("deep", "Builder", [False, True])
    assert not reasons("think", "Builder", [False, True]) and not reasons("normal", "Builder", None)
    assert not reasons("deep", "Responder", None)


@pytest.mark.parametrize("level", ["fast", "normal", "think", "deep"])
@pytest.mark.parametrize("values", [None, [False, True]])
def test_every_level_can_be_described(level, values):
    assert describe(level, values)


def calls_at(tmp_path, effort):
    session, _ = session_for(tmp_path, effort=effort, policy="direct")
    list(session.send("write something small"))
    return session.provider.client.inner.calls


def test_normal_effort_sends_what_it_always_sent(tmp_path):
    calls = calls_at(tmp_path, "normal")
    assert calls[0]["model"] == "mock-quality" and len(calls) == 1


def test_fast_effort_uses_the_fast_model_and_no_review(tmp_path):
    assert calls_at(tmp_path, "fast")[0]["model"] == "mock-fast"
    assert "Reviewer" not in allowed(RequestState(request="x", built=True, no_review=True))


def test_reasoning_notes_reach_the_task_but_not_history(tmp_path):
    session, responder = session_for(tmp_path, "note to self", "Done.", effort="think", policy="direct")
    list(session.send("write something small"))
    assert responder.seen[0][1][0]["content"].startswith("Think through")
    assert "note to self" in responder.seen[1][1][-1]["content"]
    assert "note to self" not in json.dumps(session.history)


def test_a_model_with_native_thinking_gets_no_reasoning_pass(tmp_path):
    session, responder = session_for(tmp_path, values=[False, True], effort="think", policy="direct")
    captured = {}
    inner = session.provider.client.inner
    original = inner.chat

    def chat(think=None, **kwargs):
        captured["think"] = think
        return original(**kwargs)
    inner.chat = chat
    events = list(session.send("write something small"))
    assert captured["think"] is True and len(responder.seen) == 1
    assert of(events, "effort")[0]["how"] == "The model thinks before it acts."


def test_one_request_can_use_more_effort(tmp_path):
    session, responder = session_for(tmp_path, "notes", "Done.", policy="direct")
    list(session.send("write something small", effort="think"))
    assert len(responder.seen) == 2 and session.effort == "normal"
    list(session.send("write something else small"))
    assert len(responder.seen) == 3


def test_deep_warns_about_spend_and_always_plans(tmp_path):
    session, _ = session_for(tmp_path, "notes", PLAN, "Done.", effort="deep")
    (session.project / "a.py").write_text("x = 1\n")
    events = list(session.send("add a thing"))
    assert any("more tokens" in n["text"] for n in of(events, "notice"))
    assert "Planner" in [e["agent"] for e in of(events, "agent")]


def test_unknown_effort_is_refused(tmp_path):
    session, _ = session_for(tmp_path)
    with pytest.raises(ValueError):
        session.set_effort("ultra")
