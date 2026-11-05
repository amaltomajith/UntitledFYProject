"""Sessions: every chat can be listed, named, resumed and deleted, and stays inside its project and mode."""

import json

import pytest
from fastapi.testclient import TestClient

from puppeteer_code.assistant.ide import IdeHub
from puppeteer_code.assistant.session import CLOUD_ONLY, PRIVATE_ONLY, TITLE_CHARS, AssistantSession
from puppeteer_code.providers import ProviderSession
from puppeteer_code.server import create_app

TOKEN = "test-token"
LOCAL = {"host": "127.0.0.1:8765"}
AUTH = {**LOCAL, "x-pcode-token": TOKEN}


def provider():
    return ProviderSession("mock", responder=lambda messages, tools: "It reads a file.")


def session_for(tmp_path, name="project", **options):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return AssistantSession(folder, provider(), home=tmp_path / "home", policy="direct", **options)


def ask(session, text="what is here?"):
    return list(session.send(text))


def private(session):
    """A saved conversation marked private, with no local server involved."""
    path = session._path()
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "private": True}), encoding="utf-8")


# ── the list ─────────────────────────────────────────────────────────────────

def test_a_session_is_titled_from_its_first_prompt_and_counts_turns(tmp_path):
    session = session_for(tmp_path)
    ask(session, "what   does\nthis do? " + "x" * 100)
    ask(session, "and that?")
    row = AssistantSession.list_sessions(session.project, session.home)[0]
    assert row["title"].startswith("what does this do? ") and len(row["title"]) == TITLE_CHARS
    assert row["turns"] == 2


def test_the_newest_session_comes_first(tmp_path):
    session = session_for(tmp_path)
    ask(session)
    first = session.id
    session.clear()
    ask(session, "a second chat?")
    rows = AssistantSession.list_sessions(session.project, session.home)
    assert [row["id"] for row in rows] == [session.id, first]


def test_a_new_session_is_listed_only_once_it_has_a_turn(tmp_path):
    session = session_for(tmp_path)
    assert AssistantSession.list_sessions(session.project, session.home) == []


def test_the_list_keeps_private_and_cloud_apart(tmp_path):
    session = session_for(tmp_path)
    ask(session, "a private question?")
    secret = session.id
    private(session)
    session.clear()
    ask(session, "a cloud question?")
    home, project = session.home, session.project
    assert [r["id"] for r in AssistantSession.list_sessions(project, home, False)] == [session.id]
    assert [r["id"] for r in AssistantSession.list_sessions(project, home, True)] == [secret]


# ── resuming ─────────────────────────────────────────────────────────────────

def test_resume_restores_turns_title_and_plan(tmp_path):
    session = session_for(tmp_path)
    ask(session)
    session.rename(session.id, "My chat")
    session.pending_plan = {"request": "x", "steps": ["a"], "stack": "", "sha": "s", "return_mode": "ask"}
    session.save()
    first = session.id
    session.clear()
    assert session.turns == [] and session.title == ""
    session.adopt(first)
    assert session.title == "My chat" and session.pending_plan["steps"] == ["a"]
    assert session.turns[0]["status"] == "done" and session.turns[0]["question"] is True


def test_a_conversation_saved_before_turns_existed_still_opens(tmp_path):
    session = session_for(tmp_path)
    ask(session)
    path = session._path()
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["turns"]
    path.write_text(json.dumps(data), encoding="utf-8")
    first = session.id
    session.clear()
    session.adopt(first)
    assert session.turns == [{}] and session.history
    ask(session, "and now?")
    assert session.turns[0] == {} and session.turns[1]["status"] == "done"


@pytest.mark.parametrize("bad", ["../secrets", "..\\x", "a/b", "a.b", ""])
def test_an_id_must_be_a_plain_name(tmp_path, bad):
    session = session_for(tmp_path)
    (tmp_path / "secrets.json").write_text(json.dumps({"history": [], "undo": []}), encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        session.adopt(bad)
    with pytest.raises(FileNotFoundError):
        AssistantSession.resume(bad, provider(), home=session.home)


def test_another_projects_session_cannot_be_adopted_renamed_or_deleted(tmp_path):
    other = session_for(tmp_path, "other")
    ask(other)
    session = session_for(tmp_path)
    for change in (lambda: session.adopt(other.id), lambda: session.rename(other.id, "Mine"),
                   lambda: session.delete(other.id)):
        with pytest.raises(FileNotFoundError):
            change()
    assert other._path().exists() and "title" not in json.loads(other._path().read_text(encoding="utf-8"))


def test_private_and_cloud_refuse_each_others_conversations(tmp_path):
    session = session_for(tmp_path)
    ask(session)
    cloud_id = session.id
    local = provider()
    local.private = True
    with pytest.raises(ValueError, match=CLOUD_ONLY):
        AssistantSession.resume(cloud_id, local, home=session.home)
    private(session)
    with pytest.raises(ValueError, match=PRIVATE_ONLY):
        session_for(tmp_path).adopt(cloud_id)


# ── naming and deleting ──────────────────────────────────────────────────────

def test_rename_a_saved_session_and_keep_it(tmp_path):
    session = session_for(tmp_path)
    ask(session)
    first = session.id
    session.clear()
    session.rename(first, "  Fix   the\nlogin  ")
    assert AssistantSession.list_sessions(session.project, session.home)[0]["title"] == "Fix the login"
    assert json.loads(session._file(first).read_text(encoding="utf-8"))["history"]


def test_a_name_cannot_be_empty_and_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_value_that_must_not_leak")
    session = session_for(tmp_path)
    with pytest.raises(ValueError):
        session.rename(session.id, "   ")
    session.rename(session.id, "key gsk_test_value_that_must_not_leak")
    assert session.title == "key [hidden]"


def test_deleting_an_older_session_leaves_this_one_and_the_files(tmp_path):
    session = session_for(tmp_path)
    ask(session)
    first = session.id
    session.clear()
    ask(session, "another?")
    (session.project / "keep.txt").write_text("mine", encoding="utf-8")
    session.delete(first)
    assert [r["id"] for r in AssistantSession.list_sessions(session.project, session.home)] == [session.id]
    assert (session.project / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_deleting_the_active_session_starts_a_new_one(tmp_path):
    session = session_for(tmp_path)
    ask(session)
    old = session.id
    session.delete(old)
    assert session.id != old and session.history == [] and session.turns == []
    assert AssistantSession.list_sessions(session.project, session.home) == []


def test_deleting_waits_for_the_request(tmp_path):
    session = session_for(tmp_path)
    session._busy = True
    with pytest.raises(RuntimeError):
        session.delete(session.id)


# ── the routes ───────────────────────────────────────────────────────────────

def opened(tmp_path):
    home = tmp_path / "home"
    hub = IdeHub(TOKEN, lambda name: provider(), home=home, provider="mock")
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    folder = tmp_path / "project"
    folder.mkdir()
    assert client.post("/api/ide/open", json={"path": str(folder)}, headers=AUTH).status_code == 200
    return client, hub


def test_routes_rename_list_and_delete(tmp_path):
    client, hub = opened(tmp_path)
    ask(hub.session)
    sid = hub.session.id
    state = client.post(f"/api/ide/sessions/{sid}/rename", json={"name": "Named"}, headers=AUTH).json()
    assert state["session_title"] == "Named"
    rows = client.get("/api/ide/sessions", headers=AUTH).json()["sessions"]
    assert rows[0] == {**rows[0], "title": "Named", "turns": 1}
    state = client.delete(f"/api/ide/sessions/{sid}", headers=AUTH).json()
    assert state["session_id"] != sid and client.get("/api/ide/sessions", headers=AUTH).json()["sessions"] == []


def test_routes_need_the_token(tmp_path):
    client, hub = opened(tmp_path)
    assert client.delete(f"/api/ide/sessions/{hub.session.id}", headers=LOCAL).status_code == 401
    assert client.post("/api/ide/sessions/x/rename", json={"name": "a"}, headers=LOCAL).status_code == 401


def test_routes_report_a_missing_session_and_a_busy_one(tmp_path):
    client, hub = opened(tmp_path)
    assert client.delete("/api/ide/sessions/gone", headers=AUTH).status_code == 404
    assert client.post("/api/ide/sessions/gone/rename", json={"name": "a"}, headers=AUTH).status_code == 404
    hub.running_command = True
    assert client.delete("/api/ide/sessions/gone", headers=AUTH).status_code == 409


def test_a_traversal_id_never_reaches_a_file(tmp_path):
    client, _ = opened(tmp_path)
    (tmp_path / "home" / "secret.json").write_text("{}", encoding="utf-8")
    assert client.delete("/api/ide/sessions/..%5Csecret", headers=AUTH).status_code in (404, 405)
    assert (tmp_path / "home" / "secret.json").exists()


def test_resume_sends_the_turns_too(tmp_path):
    client, hub = opened(tmp_path)
    ask(hub.session)
    first = hub.session.id
    client.post("/api/ide/clear", headers=AUTH)
    client.post("/api/ide/resume", json={"id": first}, headers=AUTH)
    resumed = [e for e in hub.log if e["type"] == "resumed"][-1]
    assert resumed["turns"][0]["status"] == "done" and len(resumed["history"]) == 2
