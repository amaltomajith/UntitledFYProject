"""Private mode end to end, on a fake local model: what must never leave, and what must stay honest."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from puppeteer_code import ollama
from puppeteer_code.agents import DocsSearchAgent
from puppeteer_code.assistant.ide import IdeHub
from puppeteer_code.assistant.session import NOTHING_DONE, PRIVATE_ONLY, AssistantSession
from puppeteer_code.assistant.shield import RequestState
from puppeteer_code.assistant.verify import Check
from puppeteer_code.ollama import LocalServer, OllamaClient
from puppeteer_code.privacy import PrivacyError, private_claim
from puppeteer_code.providers import ProviderSession, load_config
from puppeteer_code.sandbox import DirectRunner
from puppeteer_code.server import REAL_PROVIDERS, create_app

TOKEN = "t" * 32
LOCAL = {"host": "127.0.0.1:8765"}


class FakeClient(OllamaClient):
    """The real client with the network replaced, so guards and tiers still run."""

    def __init__(self, url, num_ctx=16384, keep_alive="10m", models=("qwen3:8b",), replies=()):
        super().__init__(url, num_ctx, keep_alive)
        self.models = list(models)
        self.replies = list(replies)
        self.sent = []

    def _json(self, method, path, body=None):
        if path == "/api/tags":
            return {"models": [{"name": n} for n in self.models]}
        return {"capabilities": ["completion", "tools"]}

    def _stream(self, body):
        self.sent.append(body)
        text = self.replies.pop(0) if self.replies else "Done."
        yield {"message": {"content": text}, "done": True, "done_reason": "stop",
               "prompt_eval_count": 10, "eval_count": 5}


class FakeServer:
    url = "http://127.0.0.1:11435"

    def __init__(self, notices=()):
        self.notices = list(notices)

    def warnings(self):
        return self.notices


def local_provider(monkeypatch, models=("qwen3:8b",), replies=(), notices=(), **options):
    monkeypatch.setattr(ollama, "shared_server", lambda spec: FakeServer(notices))
    monkeypatch.setattr(ollama, "OllamaClient", lambda *a: FakeClient(*a, models=models, replies=replies))
    return ProviderSession("local", **options)


def session_for(tmp_path, monkeypatch, **options):
    replies = options.pop("replies", ())
    provider = local_provider(monkeypatch, replies=replies)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return AssistantSession(project, provider, home=tmp_path / "home", **options)


def of(events, name):
    return [e for e in events if e["type"] == name]


# ── model choice ─────────────────────────────────────────────────────────────

def test_the_first_installed_candidate_goes_in_both_tiers(monkeypatch):
    provider = local_provider(monkeypatch, models=("granite4:latest", "qwen3:8b"))
    assert provider.tiers == {"fast": "qwen3:8b", "quality": "qwen3:8b"}


def test_any_installed_model_is_used_when_no_candidate_is(monkeypatch):
    assert local_provider(monkeypatch, models=("mine:7b",)).tiers["quality"] == "mine:7b"


def test_no_model_says_how_to_get_one_and_pulls_nothing(monkeypatch):
    with pytest.raises(PrivacyError, match="ollama pull"):
        local_provider(monkeypatch, models=())


def test_a_cloud_model_is_never_picked(monkeypatch):
    with pytest.raises(PrivacyError):
        local_provider(monkeypatch, models=("gpt-oss:20b-cloud",))


def test_private_refuses_the_response_cache(tmp_path, monkeypatch):
    with pytest.raises(PrivacyError, match="cache"):
        local_provider(monkeypatch, cache_dir=tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_local_shows_no_price():
    assert load_config()["providers"]["local"]["prices"] == {}


def test_local_runs_never_reach_the_public_site():
    assert "local" not in REAL_PROVIDERS


# ── the session ──────────────────────────────────────────────────────────────

def test_a_private_request_runs_on_the_local_client(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch, replies=("It adds numbers.",))
    events = list(session.send("what does this do?"))
    assert of(events, "done")[0]["status"] == "done"
    assert session.provider.client.inner.sent[0]["model"] == "qwen3:8b"


def test_setting_a_cloud_model_is_refused_before_sending(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch)
    session.provider.tiers["fast"] = "gpt-oss:20b-cloud"
    events = list(session.send("what does this do?"))
    assert of(events, "done")[0]["status"] == "error"
    assert "cloud model" in of(events, "done")[0]["reason"]
    assert session.provider.client.inner.sent == []


def test_quota_fallback_is_off(tmp_path, monkeypatch):
    assert session_for(tmp_path, monkeypatch).fallback is False


def reply_to(session, write=True, **state):
    before = session.snapshots.snapshot("before")
    if write:
        (session.project / "stats.py").write_text("x = 1\n")
    session._started, session._request_start = time.monotonic(), 0
    return session._respond(RequestState(request="make stats", start_sha=before, **state))


def passed():
    return Check(kind="tests", ok=True, summary="Tests: 3 passed.")


def test_the_reply_in_private_comes_from_code_not_the_model(tmp_path, monkeypatch):
    """A real local model denied creating a file that existed, with the file list in its prompt."""
    session = session_for(tmp_path, monkeypatch, replies=("I could not create stats.py.",))
    reply = reply_to(session, built=True, check=passed())
    assert reply == "Changed: stats.py.\n\nTests: 3 passed."
    assert session.provider.client.inner.sent == []


def test_a_private_reply_with_no_work_says_so(tmp_path, monkeypatch):
    reply = reply_to(session_for(tmp_path, monkeypatch), write=False)
    assert reply.startswith("No files were changed.") and NOTHING_DONE in reply


def test_a_private_reply_keeps_the_failure_and_the_concerns(tmp_path, monkeypatch):
    check = Check(kind="tests", ok=False, summary="Tests: 1 of 3 failed.")
    reply = reply_to(session_for(tmp_path, monkeypatch), built=True, check=check, review="median is wrong")
    assert "It does not fully work yet. Tests: 1 of 3 failed." in reply and "median is wrong" in reply


def test_a_cloud_reply_still_comes_from_the_model(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch, replies=("All done.",))
    session.private = False
    assert reply_to(session, built=True, check=passed()) == "All done."
    assert "stats.py" not in session.provider.client.inner.sent[-1]["messages"][-1]["content"]


def test_private_has_more_room_than_a_cloud_request(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch)
    assert session.token_budget == 150000 and session.max_seconds == 1800


def test_a_request_that_runs_too_long_stops(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch)
    session.max_seconds = 0.000001
    done = of(list(session.send("what does this do?")), "done")[0]
    assert done["status"] == "stopped" and "minutes" in done["reason"]


def test_start_server_is_not_offered_or_callable(tmp_path, monkeypatch):
    reply = json.dumps({"name": "start_server", "arguments": {"command": "python -m http.server"}})
    session = session_for(tmp_path, monkeypatch, replies=("STACK: python\n1. Serve it", reply, "Done."))
    (session.project / "a.py").write_text("x = 1\n")
    events = list(session.send("start the web server for this project please"))
    sent = session.provider.client.inner.sent
    offered = [t["function"]["name"] for body in sent for t in body.get("tools", [])]
    assert "start_server" not in offered and "run_command" in offered
    assert not of(events, "preview")


def test_the_model_is_told_it_cannot_start_servers(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch)
    list(session.send("what does this do?"))
    assert "cannot start servers" in session.provider.client.inner.sent[0]["messages"][0]["content"]


def test_the_model_is_only_told_the_network_is_off_when_it_is(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch)
    list(session.send("what does this do?"))
    assert "no network" not in session.provider.client.inner.sent[0]["messages"][0]["content"]


def test_auto_drops_to_ask_while_commands_are_not_sandboxed(tmp_path, monkeypatch):
    session = session_for(tmp_path, monkeypatch, mode="auto")
    assert session.tools.mode == "ask"
    session.set_mode("auto")
    assert session.tools.mode == "ask"


def test_the_claim_follows_the_runner():
    class Contained(DirectRunner):
        contained = True
    assert "not sandboxed" in private_claim(DirectRunner())
    assert "no network" in private_claim(Contained())


def test_server_warnings_reach_the_user_once(tmp_path, monkeypatch):
    provider = local_provider(monkeypatch, notices=("Quit the tray app.",))
    project = tmp_path / "project"
    project.mkdir()
    session = AssistantSession(project, provider, home=tmp_path / "home")
    assert of(list(session.send("what is this?")), "notice")[0]["text"] == "Quit the tray app."
    assert not of(list(session.send("what is that?")), "notice")


# ── conversations ────────────────────────────────────────────────────────────

def test_a_cloud_session_cannot_continue_a_private_conversation(tmp_path, monkeypatch):
    private = session_for(tmp_path, monkeypatch)
    list(private.send("what does the secret plan do?"))
    cloud = AssistantSession(private.project, ProviderSession("mock"), home=private.home)
    with pytest.raises(ValueError, match=PRIVATE_ONLY):
        cloud.adopt(private.id)
    with pytest.raises(ValueError, match=PRIVATE_ONLY):
        AssistantSession.resume(private.id, ProviderSession("mock"), home=private.home)
    assert cloud.history == []


def test_a_private_session_can_continue_its_own_conversation(tmp_path, monkeypatch):
    private = session_for(tmp_path, monkeypatch)
    list(private.send("what is this?"))
    again = AssistantSession.resume(private.id, private.provider, home=private.home)
    assert len(again.history) == 2


# ── Fix mode ─────────────────────────────────────────────────────────────────

def provider_named(name, monkeypatch):
    return local_provider(monkeypatch) if name == "local" else ProviderSession("mock")


def hub_for(tmp_path, monkeypatch, **options):
    monkeypatch.setenv("NEBIUS_API_KEY", "present")
    hub = IdeHub(TOKEN, lambda name: provider_named(name, monkeypatch), home=tmp_path / "home", **options)
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub)
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={"x-pcode-token": TOKEN}), hub


def test_private_beats_a_key_that_is_set(tmp_path, monkeypatch):
    _, hub = hub_for(tmp_path, monkeypatch, private=True)
    assert hub.active_provider() == "local"


def test_fix_mode_stays_on_the_session_provider(tmp_path, monkeypatch):
    _, hub = hub_for(tmp_path, monkeypatch, private=True)
    (tmp_path / "project").mkdir()
    hub.open(str(tmp_path / "project"))
    seen = {}

    class Manager:
        def start(self, request):
            seen["provider"] = request.provider
            raise ValueError("stop here")
    hub.manager = Manager()
    hub._fix(hub.session, "fix it")
    assert seen["provider"] == "local"


class Env:
    class state:
        tests_run = 1
        task = "fix"
        docs_notes = []

        class last_tests:
            cases = {}
            failing = []


def test_fix_mode_docs_search_stays_home(monkeypatch):
    provider = local_provider(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEY", "present")
    agent = DocsSearchAgent(provider)
    monkeypatch.setattr(agent, "_tavily", lambda q: pytest.fail("searched the web"))
    monkeypatch.setattr(agent, "_pydoc", lambda b: pytest.fail("imported a module"))
    agent.run(Env)


def test_private_turns_off_parallel_exploration(tmp_path, monkeypatch):
    from puppeteer_code import server

    seen = {}
    monkeypatch.setattr(server, "RunConfig", lambda **k: seen.update(k))
    monkeypatch.setattr(server, "make_policy", lambda *a, **k: object())
    monkeypatch.setattr(server.RunManager, "_work", lambda *a: None)
    manager = server.RunManager(tmp_path, lambda name: local_provider(monkeypatch))
    (tmp_path / "repo").mkdir()
    manager.start(server.RunRequest(task="x", repo=str(tmp_path / "repo"), provider="local"))
    assert seen["explore"] is False


# ── the IDE ──────────────────────────────────────────────────────────────────

def open_folder(client, tmp_path):
    (tmp_path / "project").mkdir(exist_ok=True)
    assert client.post("/api/ide/open", json={"path": str(tmp_path / "project")}, headers=LOCAL).status_code == 200


def toggle(client, on):
    return client.post("/api/ide/private", json={"on": on}, headers=LOCAL)


def test_the_toggle_starts_a_new_conversation_on_the_local_model(tmp_path, monkeypatch):
    client, hub = hub_for(tmp_path, monkeypatch, provider="mock")
    open_folder(client, tmp_path)
    list(hub.session.send("what is this?"))
    state = toggle(client, True).json()
    assert state["private"]["on"] and state["provider"] == "local" and hub.session.history == []
    assert "not sandboxed" in state["private"]["claim"]


def test_private_is_remembered_per_project_and_only_there(tmp_path, monkeypatch):
    client, hub = hub_for(tmp_path, monkeypatch, provider="mock")
    open_folder(client, tmp_path)
    toggle(client, True)
    other = tmp_path / "other"
    other.mkdir()
    hub.keep_private = False
    assert client.post("/api/ide/open", json={"path": str(other)}, headers=LOCAL).json()["private"]["on"] is False
    assert client.post("/api/ide/open", json={"path": str(tmp_path / "project")},
                       headers=LOCAL).json()["private"]["on"] is True


def test_a_repo_cannot_turn_private_off_or_on(tmp_path, monkeypatch):
    client, _ = hub_for(tmp_path, monkeypatch, provider="mock")
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "settings.json").write_text('{"private": true}')
    open_folder(client, tmp_path)
    assert client.get("/api/ide/state", headers=LOCAL).json()["private"]["on"] is False


def test_turning_it_off_goes_back_to_the_cloud_provider(tmp_path, monkeypatch):
    client, hub = hub_for(tmp_path, monkeypatch, provider="mock")
    open_folder(client, tmp_path)
    toggle(client, True)
    assert toggle(client, False).json()["provider"] == "mock"
    assert hub.session.private is False


def test_a_failed_switch_changes_nothing(tmp_path, monkeypatch):
    client, hub = hub_for(tmp_path, monkeypatch, provider="mock")
    open_folder(client, tmp_path)
    before = hub.session

    def fail(name):
        raise PrivacyError("No local model is installed.")
    hub.make_provider = fail
    response = toggle(client, True)
    assert response.status_code == 409 and "No local model" in response.json()["detail"]["message"]
    assert hub.session is before and hub.private is False


def test_private_can_be_chosen_with_no_key_and_no_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client, hub = hub_for(tmp_path, monkeypatch)
    monkeypatch.delenv("NEBIUS_API_KEY")
    assert toggle(client, True).json()["private"]["on"]
    open_folder(client, tmp_path)
    assert hub.session.private


def test_no_toggle_while_working(tmp_path, monkeypatch):
    client, hub = hub_for(tmp_path, monkeypatch, provider="mock")
    open_folder(client, tmp_path)
    hub.running_command = True
    assert toggle(client, True).status_code == 409


def test_plan_mode_refuses_fix_and_apply_in_the_ide(tmp_path, monkeypatch):
    client, hub = hub_for(tmp_path, monkeypatch, provider="mock")
    open_folder(client, tmp_path)
    assert client.post("/api/ide/mode", json={"mode": "plan"}, headers=LOCAL).json()["mode"] == "plan"
    assert client.post("/api/ide/fix/apply", json={"run_id": "fix-1"}, headers=LOCAL).status_code == 409
    hub.handle({"type": "send", "message": "fix it", "mode": "fix"})
    assert hub.log[-1] == {"type": "error", "message": "Fix mode changes code, so it is off in plan mode.",
                           "seq": hub.seq}


def test_the_ide_can_approve_a_plan_and_set_effort(tmp_path, monkeypatch):
    client, hub = hub_for(tmp_path, monkeypatch, provider="mock")
    open_folder(client, tmp_path)
    assert client.post("/api/ide/plan/approve", headers=LOCAL).status_code == 409
    assert client.post("/api/ide/effort", json={"level": "think"}, headers=LOCAL).json()["effort"] == "think"
    assert client.post("/api/ide/effort", json={"level": "ultra"}, headers=LOCAL).status_code == 422
    assert client.post("/api/ide/mode", json={"mode": "other"}, headers=LOCAL).status_code == 422


# ── the server ───────────────────────────────────────────────────────────────

def serve(tmp_path, statuses):
    server = LocalServer("http://127.0.0.1:1", tmp_path / "log", binary=sys.executable)
    spawned = []
    server._spawn = lambda: spawned.append(True)
    answers = iter(statuses)
    server._status = lambda *a, **k: next(answers)
    return server, spawned


def test_a_server_that_refuses_cloud_is_used(tmp_path):
    server, spawned = serve(tmp_path, [None, 200, 403])
    server.start()
    assert spawned == [True]


def test_a_server_already_running_is_joined(tmp_path):
    server, spawned = serve(tmp_path, [200, 403])
    server.start()
    assert spawned == []


def test_a_server_that_allows_cloud_is_refused(tmp_path):
    server, _ = serve(tmp_path, [200, 404])
    with pytest.raises(PrivacyError, match="did not refuse cloud"):
        server.start()


def test_a_server_that_dies_is_reported_and_nothing_falls_back(tmp_path):
    server = LocalServer("http://127.0.0.1:1", tmp_path / "log", binary=sys.executable)
    server._status = lambda *a, **k: None

    def spawn():
        server.proc = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        server.proc.wait()
    server._spawn = spawn
    with pytest.raises(PrivacyError, match="did not start"):
        server.start()


def test_missing_ollama_says_where_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ollama.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(PrivacyError, match="ollama.com/download"):
        LocalServer("http://127.0.0.1:1", tmp_path / "log")._find_binary()


def test_the_server_starts_with_cloud_off_and_no_secrets(tmp_path, monkeypatch):
    seen = {}

    class Popen:
        def __init__(self, command, env, **kwargs):
            seen.update(command=command, env=env)
    tied = []
    monkeypatch.setattr(ollama.subprocess, "Popen", Popen)
    monkeypatch.setattr(ollama.appcontainer, "tie_to_this_process", tied.append)
    monkeypatch.setenv("NEBIUS_API_KEY", "present")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:1")
    LocalServer("http://127.0.0.1:11435", tmp_path / "log", binary="ollama")._spawn()
    env = seen["env"]
    assert len(tied) == (sys.platform == "win32")
    assert seen["command"] == ["ollama", "serve"]
    assert env["OLLAMA_NO_CLOUD"] == "1" and env["OLLAMA_HOST"] == "127.0.0.1:11435"
    assert "NEBIUS_API_KEY" not in env and "HTTPS_PROXY" not in env


def test_only_one_server_starts(tmp_path, monkeypatch):
    starts = []
    monkeypatch.setattr(ollama, "_server", None)
    monkeypatch.setattr(ollama.LocalServer, "start", lambda self: starts.append(self))
    monkeypatch.setattr(ollama.atexit, "register", lambda f: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    threads = [threading.Thread(target=ollama.shared_server, args=({"base_url": "http://127.0.0.1:1"},))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(starts) == 1
    assert Path(os.fspath(tmp_path)).exists()
