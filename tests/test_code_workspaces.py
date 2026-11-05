"""Several folders in one IDE: each a hub of its own, one token, one GPU. Offline."""

import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from puppeteer_code.assistant.ide import MAX_FOLDERS, IdeHub, LocalGate
from puppeteer_code.providers import ProviderSession
from puppeteer_code.server import create_app

TOKEN = "t" * 32
HEADERS = {"host": "127.0.0.1:8765", "x-pcode-token": TOKEN}


def provider():
    return ProviderSession("mock", responder=lambda model, messages: "Done.")


@pytest.fixture
def ide(tmp_path):
    hub = IdeHub(TOKEN, lambda name: provider(), home=tmp_path / "home", provider="mock")
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub)
    client = TestClient(app, base_url="http://127.0.0.1:8765", headers=HEADERS)
    yield client, hub
    hub.close()


def folder(tmp_path, name):
    path = tmp_path / name
    path.mkdir(exist_ok=True)
    (path / f"{name}.txt").write_text(name)
    return str(path)


def open_folder(client, tmp_path, name):
    reply = client.post("/api/ide/workspaces", json={"path": folder(tmp_path, name)})
    assert reply.status_code == 201
    return reply.json()["id"]


def on(workspace):
    return {"x-pcode-workspace": workspace}


def test_a_client_that_names_no_folder_gets_the_first(ide, tmp_path):
    client, hub = ide
    hub.open(folder(tmp_path, "one"))
    assert client.get("/api/ide/state").json()["project"].endswith("one")


def test_each_tab_sees_its_own_folder(ide, tmp_path):
    client, hub = ide
    hub.open(folder(tmp_path, "one"))
    two = open_folder(client, tmp_path, "two")
    assert client.get("/api/ide/tree").json()["files"] == ["one.txt"]
    assert client.get("/api/ide/tree", headers=on(two)).json()["files"] == ["two.txt"]


def test_a_folder_that_is_not_open_is_refused(ide):
    client, _ = ide
    reply = client.get("/api/ide/state", headers=on("nope"))
    assert reply.status_code == 404 and reply.json()["detail"]["code"] == "no_workspace"


def test_every_folder_route_needs_the_token(ide, tmp_path):
    client, hub = ide
    two = open_folder(client, tmp_path, "two")
    bad = {"x-pcode-token": "wrong", **on(two)}
    assert client.get("/api/ide/tree", headers=bad).status_code == 401
    assert client.get("/api/ide/workspaces", headers={"x-pcode-token": "wrong"}).status_code == 401
    assert client.post("/api/ide/workspaces", json={}, headers={"x-pcode-token": "wrong"}).status_code == 401
    assert client.delete(f"/api/ide/workspaces/{two}", headers={"x-pcode-token": "wrong"}).status_code == 401


def test_the_same_folder_uses_its_tab(ide, tmp_path):
    client, hub = ide
    two = open_folder(client, tmp_path, "two")
    assert open_folder(client, tmp_path, "two") == two
    assert len(client.get("/api/ide/workspaces").json()["folders"]) == 2


def test_opening_a_folder_held_by_another_tab_is_refused(ide, tmp_path):
    client, hub = ide
    open_folder(client, tmp_path, "two")
    reply = client.post("/api/ide/open", json={"path": folder(tmp_path, "two")})
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "already_open"


def test_there_is_a_limit(ide, tmp_path):
    client, _ = ide
    for n in range(MAX_FOLDERS - 1):
        open_folder(client, tmp_path, f"f{n}")
    reply = client.post("/api/ide/workspaces", json={"path": folder(tmp_path, "last")})
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "too_many"


def test_a_folder_that_fails_to_open_leaves_no_tab(ide, tmp_path):
    client, _ = ide
    reply = client.post("/api/ide/workspaces", json={"path": str(tmp_path / "missing")})
    assert reply.status_code == 404
    assert len(client.get("/api/ide/workspaces").json()["folders"]) == 1


def test_an_empty_tab_can_pick_its_folder(ide, tmp_path):
    client, _ = ide
    empty = client.post("/api/ide/workspaces", json={}).json()["id"]
    client.post("/api/ide/open", json={"path": folder(tmp_path, "later")}, headers=on(empty))
    assert client.get("/api/ide/state", headers=on(empty)).json()["project"].endswith("later")


def test_closing_a_busy_folder_is_refused(ide, tmp_path):
    client, hub = ide
    two = open_folder(client, tmp_path, "two")
    held = threading.Event()
    target = hub.spaces.hubs[two]
    target.queued = True
    assert client.delete(f"/api/ide/workspaces/{two}").status_code == 409
    target.queued = False
    held.set()
    assert client.delete(f"/api/ide/workspaces/{two}").status_code == 200
    assert client.get("/api/ide/state", headers=on(two)).status_code == 404


def test_closing_the_last_folder_leaves_an_empty_tab(ide, tmp_path):
    client, hub = ide
    hub.open(folder(tmp_path, "one"))
    only = client.get("/api/ide/workspaces").json()["folders"][0]["id"]
    left = client.delete(f"/api/ide/workspaces/{only}").json()
    assert left["folders"] == [{"id": only, "project": None}] and left["saved"] == []


def test_events_stay_in_their_folder(ide, tmp_path):
    client, hub = ide
    hub.open(folder(tmp_path, "one"))
    two = hub.spaces.hubs[open_folder(client, tmp_path, "two")]
    hub.publish({"type": "notice", "text": "for one"})
    assert not [e for e in two.log if e.get("text") == "for one"]


def test_terminals_stay_in_their_folder(ide, tmp_path):
    client, hub = ide
    hub.open(folder(tmp_path, "one"))
    two = hub.spaces.hubs[open_folder(client, tmp_path, "two")]
    assert hub.terminals is not two.terminals


def test_a_socket_for_a_closed_folder_is_turned_away(ide):
    client, _ = ide
    with client.websocket_connect("/api/ide/ws", headers=HEADERS) as socket:
        socket.send_json({"token": TOKEN, "workspace": "nope", "since": 0})
        assert socket.receive()["type"] == "websocket.close"


def test_a_socket_reaches_the_folder_it_names(ide, tmp_path):
    client, hub = ide
    two = open_folder(client, tmp_path, "two")
    with client.websocket_connect("/api/ide/ws", headers=HEADERS) as socket:
        socket.send_json({"token": TOKEN, "workspace": two, "since": 0})
        assert socket.receive_json()["state"]["project"].endswith("two")


def test_open_folders_come_back_as_paths_only(tmp_path):
    made = []

    def make(name):
        made.append(name)
        return provider()

    first = IdeHub(TOKEN, make, home=tmp_path / "home", provider="mock")
    client = TestClient(create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=first),
                        base_url="http://127.0.0.1:8765", headers=HEADERS)
    open_folder(client, tmp_path, "one")
    first.close()
    made.clear()

    second = IdeHub(TOKEN, make, home=tmp_path / "home", provider="mock")
    client = TestClient(create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=second),
                        base_url="http://127.0.0.1:8765", headers=HEADERS)
    listing = client.get("/api/ide/workspaces").json()
    assert [p.split("\\")[-1].split("/")[-1] for p in listing["saved"]] == ["one"]
    assert made == []                                   # no model starts until a tab is clicked
    second.close()


def test_a_saved_folder_that_is_gone_is_dropped(ide, tmp_path):
    client, hub = ide
    open_folder(client, tmp_path, "gone")
    hub.spaces.saved = [str(tmp_path / "missing")]
    hub.spaces._write()
    assert hub.spaces._read() == []


def test_a_refused_folder_stays_saved(ide, tmp_path, monkeypatch):
    client, hub = ide
    path = folder(tmp_path, "later")
    hub.spaces.saved = [path]

    def refuse(self, wanted):
        raise HTTPException(409, {"code": "no_key", "message": "Add an API key first."})
    monkeypatch.setattr(IdeHub, "_new_session", refuse)
    assert client.post("/api/ide/workspaces", json={"path": path}).status_code == 409
    assert client.get("/api/ide/workspaces").json()["saved"] == [path]


def test_shutdown_reaches_every_folder_after_the_first_tab_closed(ide, tmp_path):
    client, hub = ide
    other = hub.spaces.hubs[open_folder(client, tmp_path, "two")]
    first = hub.id
    assert client.delete(f"/api/ide/workspaces/{first}").status_code == 200
    closed = []
    monkey = other.close
    other.close = lambda: (closed.append(True), monkey())
    hub.close()
    assert closed == [True]


def test_a_saved_folder_can_be_forgotten(ide, tmp_path):
    client, hub = ide
    path = folder(tmp_path, "old")
    hub.spaces.saved = [path]
    assert client.post("/api/ide/workspaces/forget", json={"path": path}).json()["saved"] == []


# ── one GPU ──────────────────────────────────────────────────────────────────

def test_a_private_request_waits_for_the_one_before_it():
    gate = LocalGate()
    stop = threading.Event()
    assert gate.enter("one", stop, lambda holder: None)
    seen, entered = [], []

    def second():
        entered.append(gate.enter("two", stop, seen.append))
    thread = threading.Thread(target=second)
    thread.start()
    time.sleep(1.2)
    assert seen == ["one"] and not entered
    gate.leave()
    thread.join(timeout=3)
    assert entered == [True] and gate.holder == "two"
    gate.leave()


def test_a_waiting_request_can_be_stopped():
    gate = LocalGate()
    stop = threading.Event()
    gate.enter("one", stop, lambda holder: None)
    stop.set()
    assert not gate.enter("two", stop, lambda holder: None)
    gate.leave()


def test_a_cancelled_wait_ends_the_request(tmp_path):
    hub = IdeHub(TOKEN, lambda name: provider(), home=tmp_path / "home", provider="mock")
    hub.private = True
    stop = threading.Event()
    hub.gate.enter("other", stop, lambda holder: None)
    ran = []
    thread = threading.Thread(target=hub._turn, args=(lambda: ran.append(True),))
    thread.start()
    time.sleep(0.2)
    assert hub.busy
    hub.stop_waiting.set()
    thread.join(timeout=3)
    assert not ran and not hub.busy and hub.log[-1]["status"] == "cancelled"
    hub.gate.leave()


def test_a_cloud_folder_does_not_wait(tmp_path):
    hub = IdeHub(TOKEN, lambda name: provider(), home=tmp_path / "home", provider="mock")
    stop = threading.Event()
    hub.gate.enter("other", stop, lambda holder: None)
    ran = []
    hub._turn(lambda: ran.append(True))
    assert ran == [True]
    hub.gate.leave()


def test_private_and_cloud_folders_keep_their_own_sessions(tmp_path):
    hub = IdeHub(TOKEN, lambda name: provider(), home=tmp_path / "home", provider="mock")
    client = TestClient(create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub),
                        base_url="http://127.0.0.1:8765", headers=HEADERS)
    hub.open(folder(tmp_path, "cloud"))
    two = open_folder(client, tmp_path, "other")
    assert hub.spaces.hubs[two].session is not hub.session
    assert client.get("/api/ide/sessions", headers=on(two)).json()["sessions"] == []
    hub.close()
