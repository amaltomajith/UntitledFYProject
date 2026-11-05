"""Long local replies: a cut-off stops early, and the user can see the work as it happens. Offline."""

import json
import time

from puppeteer_code.assistant.activity import (MAX_DIFF_CHARS, MAX_DIFF_FILE, QUIET_SECONDS, TRANSIENT, Activity, file_diff,
                                               partial_fields, tool_text)
from puppeteer_code.assistant.ide import IdeHub
from puppeteer_code.assistant.session import CUT_OFF, CUT_OFF_NOTICE, CUT_OFF_STOP, MAX_CUT_OFFS, AssistantSession
from puppeteer_code.providers import ProviderSession
from tests.test_code_assistant import call, calls, script
from tests.test_code_ollama import TOOLS, ask, done, fake  # noqa: F401
from tests.test_code_private import session_for as private_session

CUT = {"content": "", "tool_calls": [], "tool_failed": True, "cut_off": True, "model": "m", "latency_ms": 0.0,
       "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def run(tmp_path, responder):
    session = AssistantSession(tmp_path / "p", ProviderSession("mock", responder=responder), mode="auto", home=tmp_path,
                               policy="direct")
    events = list(session.send("build it please"))
    session.close()
    return events


def texts(messages):
    return [m["content"] for m in messages]


# ── a reply that does not fit ────────────────────────────────────────────────

def test_a_reply_that_hit_the_limit_is_marked_cut_off(fake):  # noqa: F811
    fake.chunks = [done("I will now", reason="length")]
    assert ask(fake, tools=TOOLS)["cut_off"]


def test_only_thinking_until_the_limit_is_cut_off_too(fake):  # noqa: F811
    fake.chunks = [done("", thinking="Let me think", reason="length")]
    assert ask(fake, tools=TOOLS)["cut_off"]


def test_a_malformed_call_that_finished_is_not_cut_off(fake):  # noqa: F811
    fake.chunks = [done('<tool_call>{"name": "read_file"', reason="stop")]
    reply = ask(fake, tools=TOOLS)
    assert reply["tool_failed"] and "cut_off" not in reply


def test_a_cut_off_stops_after_the_retries_instead_of_repeating(tmp_path):
    model = script(*[CUT] * 10)
    events = run(tmp_path, model)
    assert len(model.seen) == MAX_CUT_OFFS + 1
    assert events[-1]["status"] == "stopped" and events[-1]["reason"] == CUT_OFF_STOP


def test_each_retry_asks_for_a_smaller_step_and_says_so(tmp_path):
    model = script(CUT, calls(call("write_file", path="a.py", content="x = 1\n")), "Done.")
    events = run(tmp_path, model)
    assert CUT_OFF in texts(model.seen[1][1])
    assert any(e["type"] == "notice" and e["text"] == CUT_OFF_NOTICE for e in events)
    assert (tmp_path / "p" / "a.py").exists() and events[-1]["status"] == "done"


# ── how long a reply may be ──────────────────────────────────────────────────

def test_a_local_writer_gets_the_longer_reply(tmp_path, monkeypatch):
    session = private_session(tmp_path, monkeypatch, mode="auto", policy="direct")
    list(session.send("build it please"))
    session.close()
    assert session.provider.client.inner.sent[0]["options"]["num_predict"] > 6000


def test_a_local_planner_keeps_its_own_limit(tmp_path, monkeypatch):
    session = private_session(tmp_path, monkeypatch, mode="plan")
    list(session.send("build a calculator with a history and a memory key for me"))
    session.close()
    assert session.provider.client.inner.sent[0]["options"]["num_predict"] == 2048


def test_the_cloud_limit_does_not_move(tmp_path):
    seen = {}
    provider = ProviderSession("mock", responder=script("Done."))
    real = provider.client.chat

    def spy(**options):
        seen.update(options)
        return real(**options)
    provider.client.chat = spy
    session = AssistantSession(tmp_path / "p", provider, mode="auto", home=tmp_path, policy="direct")
    list(session.send("build it please"))
    session.close()
    assert seen["max_tokens"] == provider.settings_for("Builder").max_tokens


# ── text that is still arriving ──────────────────────────────────────────────

def test_a_field_can_be_read_before_it_ends():
    assert partial_fields(r'{"path": "a.py", "content": "line one\nline t') == {"path": "a.py", "content": "line one\nline t"}


def test_a_cut_escape_is_left_out():
    assert partial_fields('{"content": "a\\')["content"] == "a"
    assert partial_fields(r'{"content": "a\u00')["content"] == "a"
    assert partial_fields(r'{"content": "\ud83d')["content"] == ""


def test_whole_unicode_survives():
    assert partial_fields(r'{"content": "\ud83d\ude00 ok')["content"] == "\U0001F600 ok"


def test_a_key_inside_a_value_is_not_a_key():
    fields = partial_fields('{"content": "\\"path\\": \\"fake.py\\"", "path": "real.py"}')
    assert fields["path"] == "real.py"


def test_other_value_types_are_stepped_over():
    assert partial_fields('{"timeout": 5, "command": "ls')["command"] == "ls"


def test_nothing_to_read_is_empty():
    assert partial_fields("") == {} and partial_fields("{") == {} and partial_fields('{"pa') == {}


def test_a_tool_call_written_as_text_is_found():
    name, arguments = tool_text('<tool_call>{"name": "write_file", "arguments": {"path": "a.py", "content": "x')
    assert name == "write_file" and partial_fields(arguments) == {"path": "a.py", "content": "x"}


def test_plain_prose_is_not_a_tool_call():
    assert tool_text("I will now write the file.") is None


# ── what a change did ────────────────────────────────────────────────────────

def test_a_diff_counts_and_locates_the_change():
    event = file_diff("a.py", "edited", "one\ntwo\nthree\n", "one\nTWO\nthree\nfour\n")
    assert (event["added"], event["removed"]) == (2, 1)
    assert "- two" in event["hunks"][0]["lines"] and "+ TWO" in event["hunks"][0]["lines"]
    assert event["hunks"][0]["line"] == 1


def test_a_diff_is_bounded():
    event = file_diff("a.py", "created", "", "\n".join(f"line {i}" for i in range(5000)))
    assert event["added"] == 5000 and event["more"]
    assert len(json.dumps(event)) < MAX_DIFF_CHARS * 2


def test_a_huge_file_sends_counts_only():
    event = file_diff("a.py", "edited", "a\n", "b\n" * (MAX_DIFF_FILE // 2 + 1))
    assert event["hunks"] == [] and event["more"] and event["added"] > 1


def test_a_key_in_a_diff_is_hidden():
    event = file_diff("a.py", "edited", "", 'token = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')
    assert "abcdefghijklmnop" not in json.dumps(event)


# ── the live stream ──────────────────────────────────────────────────────────

def watched(tmp_path, responder, **options):
    session = AssistantSession(tmp_path / "p", ProviderSession("mock", responder=responder), mode="auto", home=tmp_path,
                               policy="direct", live=True, **options)
    events = list(session.send("build it please"))
    session.close()
    return events


def slow(reply):
    def responder(model, messages):
        time.sleep(1.2)
        return reply
    return responder


def test_a_quiet_model_is_still_reported(tmp_path):
    events = watched(tmp_path, slow("Done."))
    rows = [e for e in events if e["type"] == "activity"]
    assert rows and rows[0]["agent"] == "Builder" and rows[0]["phase"] == "waiting" and rows[0]["max_steps"]


def test_a_change_reports_its_lines(tmp_path):
    events = watched(tmp_path, script(calls(call("write_file", path="a.py", content="x = 1\n")), "Done."))
    diff = next(e for e in events if e["type"] == "file_diff")
    assert diff["path"] == "a.py" and diff["action"] == "created" and diff["hunks"][0]["lines"] == ["+ x = 1"]


def test_code_on_its_way_is_shown(tmp_path):
    emitted = []
    activity = Activity(emitted.append)
    activity.begin()
    activity.reply_started()
    activity.progress("tool", '{"path": "a.py", "content": "print(1)', "write_file")
    time.sleep(0.4)
    activity.end()
    assert {"type": "code_delta", "path": "a.py", "text": "print(1)"} in emitted


def test_code_for_a_protected_file_never_leaves(tmp_path):
    emitted = []
    activity = Activity(emitted.append)
    activity.begin()
    activity.reply_started()
    for path in (".env", "./.env", "app/.env.local"):
        activity.progress("tool", json.dumps({"path": path, "content": "API_KEY=abc"}), "write_file")
        time.sleep(0.3)
    activity.end()
    assert not [e for e in emitted if e["type"] == "code_delta"]


def test_thinking_is_shown_and_a_key_in_it_is_hidden(tmp_path):
    emitted = []
    activity = Activity(emitted.append)
    activity.begin()
    activity.reply_started()
    activity.progress("thinking", "the key is sk-abcdefghijklmnopqrstuvwxyz123456 so")
    time.sleep(0.4)
    activity.end()
    text = next(e["text"] for e in emitted if e["type"] == "thinking_delta")
    assert "the key is" in text and "abcdefghijklmnop" not in text


def test_a_model_that_goes_quiet_after_thinking_is_writing(monkeypatch):
    activity = Activity(lambda event: None)
    activity.reply_started()
    activity.progress("thinking", "hm")
    report = activity._report(time.monotonic() + QUIET_SECONDS + 1)
    assert report["phase"] == "writing" and report["silent"] >= QUIET_SECONDS and report["rate"] is None


def test_transient_events_stay_out_of_the_log():
    hub = IdeHub("t" * 32, lambda name: None)
    hub.publish({"type": "activity", "seconds": 1}, keep=False)
    hub.publish({"type": "notice", "text": "kept"})
    assert hub.seq == 1 and [e["type"] for e in hub.log] == ["notice"]


def test_live_off_sends_what_it_always_sent(tmp_path):
    seen = {}
    provider = ProviderSession("mock", responder=script("Done."))
    real = provider.client.chat

    def spy(**options):
        seen.update(options)
        return real(**options)
    provider.client.chat = spy
    session = AssistantSession(tmp_path / "p", provider, mode="auto", home=tmp_path, policy="direct")
    events = list(session.send("build it please"))
    session.close()
    assert "on_progress" not in seen and not [e for e in events if e["type"] in TRANSIENT + ("file_diff",)]


# ── the repeat guard ─────────────────────────────────────────────────────────

def test_reading_a_file_after_changing_it_is_not_repeating(tmp_path):
    read = calls(call("read_file", path="a.py"))
    turns = []
    for n in range(5):
        turns += [read, calls(call("write_file", path="a.py", content=f"x = {n}\n"))]
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.py").write_text("x = 0\n")
    events = run(tmp_path, script(*turns, "Done."))
    assert events[-1]["status"] == "done"


def test_reading_the_same_file_with_no_change_is_still_stopped(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.py").write_text("x = 0\n")
    events = run(tmp_path, script(*[calls(call("read_file", path="a.py"))] * 10))
    assert events[-1]["status"] == "stopped" and "repeating" in events[-1]["reason"]
