"""Memory in a real session: what agents see, what Undo removes, and what stays apart."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from puppeteer_code.assistant import ide
from puppeteer_code.assistant.context import FOLDED, KEEP_RECENT, MAX_PROMPT_CHARS, fold, relevant_code, saved_chars
from puppeteer_code.assistant.graph import MEMORY_CHARS, MEMORY_HEAD, CodeGraph, database_path, file_id, symbol_id
from puppeteer_code.assistant.ide import IdeHub
from puppeteer_code.assistant.session import AssistantSession
from puppeteer_code.assistant.toolbox import Toolbox
from puppeteer_code.providers import ProviderSession
from puppeteer_code.server import create_app

TOKEN = "test-token"
AUTH = {"host": "127.0.0.1:8765", "x-pcode-token": TOKEN}
GREET = "def greet():\n    return 'hi'\n"


def call(name, _id="c1", **args):
    return {"id": _id, "name": name, "arguments": json.dumps(args)}


def calls(*items, content=""):
    return {"content": content, "tool_calls": list(items)}


def script(*replies):
    queue = list(replies)
    seen = []

    def responder(model, messages):
        seen.append(list(messages))
        return queue.pop(0) if queue else "OK"
    responder.seen = seen
    return responder


def session_for(tmp_path, *replies, memory=True, **options):
    responder = script(*replies)
    folder = tmp_path / "project"
    folder.mkdir(exist_ok=True)
    (folder / "greet.py").write_text(GREET, encoding="utf-8")
    session = AssistantSession(folder, ProviderSession("mock", responder=responder), home=tmp_path / "home",
                               mode="auto", memory=memory, **options)
    return session, responder


def run(session, text):
    return list(session.send(text))


def edit(text="def greet():\n    return 'hello'\n", path="greet.py"):
    return calls(call("write_file", path=path, content=text))


def prompts(responder):
    return ["\n".join(str(m.get("content")) for m in messages) for messages in responder.seen]


def read_db(session, sql, *args):
    return [dict(row) for row in session.graph.db.execute(sql, args).fetchall()]


def episodes(session):
    return read_db(session, "SELECT id, data FROM nodes WHERE kind = 'episode'")


# ── what agents see ──────────────────────────────────────────────────────────

def test_a_follow_up_reaches_the_builder_with_the_first_turn(tmp_path):
    session, responder = session_for(tmp_path, edit())
    run(session, "change the greeting to hello")
    responder.seen.clear()
    run(session, "now make it louder")
    builder = next(p for p in prompts(responder) if "Request: now make it louder" in p)
    assert MEMORY_HEAD in builder
    assert 'Turn 1: "change the greeting to hello"' in builder and "Changed greet.py." in builder


def test_the_block_stays_inside_its_cap_and_prompts_stay_bounded(tmp_path):
    session, responder = session_for(tmp_path)
    for turn in range(1, 8):
        session.history += [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    graph = CodeGraph(database_path(session.project, session.home, False))
    for turn in range(1, 8):
        graph.add_episode(session.id, turn, "t", "greet " * 200, ["Builder"], "done", "3 passed",
                          [f"file{i}.py" for i in range(30)], "a", "b", 1)
    graph.close()
    run(session, "change the greeting again")
    block = next(p for p in prompts(responder) if MEMORY_HEAD in p)
    start = block.index(MEMORY_HEAD)
    end = block.index("\n\nFiles:\n", start)
    assert end - start <= MEMORY_CHARS


def test_a_memory_prompt_chooses_files_from_the_graph(tmp_path, monkeypatch):
    session, responder = session_for(tmp_path)
    (session.project / "other.py").write_text("x = 'greet greet greet'\n", encoding="utf-8")
    run(session, "change greet please")
    assert any("--- greet.py" in p for p in prompts(responder))


def test_a_new_session_keeps_structure_but_not_episodes(tmp_path):
    session, responder = session_for(tmp_path, edit())
    run(session, "change the greeting to hello")
    session.clear()
    responder.seen.clear()
    run(session, "change greet again")
    joined = "\n".join(prompts(responder))
    assert "Turn 1" not in joined and "greet.py:1-2 function greet" in joined


def test_a_resumed_session_gets_its_own_episodes_back(tmp_path):
    session, responder = session_for(tmp_path, edit())
    run(session, "change the greeting to hello")
    first = session.id
    session.clear()
    session.adopt(first)
    responder.seen.clear()
    run(session, "and again")
    assert any("Turn 1" in p for p in prompts(responder))


def test_a_read_only_question_changes_nothing_and_still_leaves_an_episode(tmp_path):
    session, _ = session_for(tmp_path)
    run(session, "what does this do?")
    assert (session.project / "greet.py").read_text(encoding="utf-8") == GREET
    data = json.loads(episodes(session)[0]["data"])
    assert data["before"] is None and data["files"] == []


def test_a_plan_draft_is_an_episode_and_sees_memory(tmp_path):
    session, responder = session_for(tmp_path, edit())
    run(session, "change the greeting to hello")
    session.set_mode("plan")
    responder.seen.clear()
    session.provider.client.responder = script("STACK: python\n1. Do it")
    run(session, "plan a bigger change")
    data = sorted(json.loads(row["data"])["turn"] for row in episodes(session))
    assert data == [1, 2]


# ── what is stored ───────────────────────────────────────────────────────────

def test_an_episode_holds_only_what_code_observed(tmp_path):
    session, _ = session_for(tmp_path, edit(), "I rewrote the universe.")
    run(session, "change the greeting to hello")
    data = json.loads(episodes(session)[0]["data"])
    assert data["files"] == ["greet.py"] and data["before"] and data["after"]
    assert "Builder" in data["agents"]
    assert "universe" not in str(read_db(session, "SELECT * FROM nodes"))


def test_a_key_in_a_prompt_never_reaches_the_graph(tmp_path):
    session, _ = session_for(tmp_path)
    run(session, "why does gsk_abcdefghij1234567890 fail here?")
    assert "abcdefghij" not in str(read_db(session, "SELECT * FROM nodes"))


def test_memory_stays_out_of_the_project_folder(tmp_path):
    session, _ = session_for(tmp_path, edit())
    run(session, "change the greeting to hello")
    assert sorted(p.name for p in session.project.iterdir()) == ["greet.py"]


# ── off by default ───────────────────────────────────────────────────────────

def test_with_memory_off_nothing_is_stored_or_added(tmp_path):
    session, responder = session_for(tmp_path, edit(), memory=False)
    run(session, "change the greeting to hello")
    run(session, "now make it louder")
    assert session.graph is None
    assert not database_path(session.project, session.home, False).exists()
    assert not any(MEMORY_HEAD in p for p in prompts(responder))


def test_relevant_code_is_unchanged_without_a_pick(tmp_path):
    tools = Toolbox(tmp_path, lambda e: None, lambda r: True)
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("beta\n", encoding="utf-8")
    assert relevant_code(tools, "alpha") == relevant_code(tools, "alpha", None) == "--- a.py\nalpha\n"
    assert relevant_code(tools, "alpha", ["b.py"]) == "--- b.py\nbeta\n"
    assert relevant_code(tools, "alpha", ["gone.py"]) == "--- a.py\nalpha\n"


# ── Undo and delete ──────────────────────────────────────────────────────────

def test_undo_removes_the_episode_and_reindexes(tmp_path):
    new = "def greet():\n    return 'hello'\n\ndef wave():\n    pass\n"
    session, _ = session_for(tmp_path, edit(new))
    run(session, "add a wave function")
    assert episodes(session) and read_db(session, "SELECT id FROM nodes WHERE id = ?", symbol_id("greet.py", "wave"))
    session.undo()
    assert episodes(session) == []
    assert read_db(session, "SELECT * FROM edges WHERE kind = 'MODIFIED'") == []
    assert read_db(session, "SELECT id FROM nodes WHERE id = ?", symbol_id("greet.py", "wave")) == []


def test_undo_keeps_an_episode_for_a_file_the_user_changed_afterwards(tmp_path):
    session, _ = session_for(tmp_path, calls(call("write_file", path="greet.py", content="a = 1\n"),
                                             call("write_file", "c2", path="new.py", content="b = 2\n")))
    run(session, "write both files now")
    (session.project / "new.py").write_text("mine = 3\n", encoding="utf-8")
    assert session.undo()["kept"] == ["new.py"]
    data = json.loads(episodes(session)[0]["data"])
    assert data["files"] == ["new.py"]
    assert [row["dst"] for row in read_db(session, "SELECT dst FROM edges WHERE kind = 'MODIFIED'")] == [file_id("new.py")]


def test_an_undo_entry_with_no_episode_still_works(tmp_path):
    session, _ = session_for(tmp_path, edit())
    run(session, "change the greeting to hello")
    del session.undo_stack[-1]["episode"]
    assert session.undo()["restored"] == ["greet.py"]
    assert len(episodes(session)) == 1


def test_undo_with_memory_off_is_unchanged(tmp_path):
    session, _ = session_for(tmp_path, edit(), memory=False)
    run(session, "change the greeting to hello")
    assert session.undo()["restored"] == ["greet.py"]


def test_deleting_a_session_removes_its_episodes(tmp_path):
    session, _ = session_for(tmp_path)
    run(session, "what does this do?")
    session.delete(session.id)
    assert episodes(session) == []
    assert read_db(session, "SELECT id FROM nodes WHERE kind = 'file'")


# ── keeping Private apart ────────────────────────────────────────────────────

def test_private_and_cloud_never_share_memory(tmp_path):
    secret, _ = session_for(tmp_path, edit())
    secret.private = True
    run(secret, "change the greeting to hello")
    assert database_path(secret.project, secret.home, True).exists()
    assert not database_path(secret.project, secret.home, False).exists()
    secret.close()

    cloud, responder = session_for(tmp_path)
    run(cloud, "change the greeting again")
    assert not any("hello" in p for p in prompts(responder) if MEMORY_HEAD in p)
    view = cloud.graph_view("project", "", [], 300)
    assert not [node for node in view["nodes"] if node["kind"] == "episode" and node["session"] == secret.id]
    with sqlite3.connect(database_path(cloud.project, cloud.home, False)) as db:
        assert "change the greeting to hello" not in str(db.execute("SELECT * FROM nodes").fetchall())


# ── failure ──────────────────────────────────────────────────────────────────

def test_a_broken_store_costs_one_notice_and_never_the_request(tmp_path):
    session, _ = session_for(tmp_path, edit())
    path = database_path(session.project, session.home, False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a database" * 200)
    events = run(session, "change the greeting to hello")
    assert [e for e in events if e["type"] == "done"][0]["status"] == "done"
    assert len([e for e in events if e["type"] == "notice"]) == 1
    assert "hello" in (session.project / "greet.py").read_text(encoding="utf-8")


def test_an_index_failure_is_survived(tmp_path, monkeypatch):
    from puppeteer_code.assistant import session as module
    monkeypatch.setattr(module, "sync", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    session, _ = session_for(tmp_path, edit())
    events = run(session, "change the greeting to hello")
    assert [e for e in events if e["type"] == "done"][0]["status"] == "done"


# ── folding ──────────────────────────────────────────────────────────────────

def conversation(*steps):
    messages = [{"role": "user", "content": "go"}]
    for i, (name, path, result) in enumerate(steps):
        args = json.dumps({"path": path})
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": args}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": result})
    return messages


LONG = "def alpha():\n    pass\n" + "# filler\n" * 200
PADDING = [("list_files", "x", "a"), ("list_files", "y", "b"), ("list_files", "z", "c")]


def test_a_read_made_stale_by_an_edit_folds_with_its_symbols():
    messages = conversation(("read_file", "a.py", LONG), ("edit_file", "a.py", "edited"), *PADDING)
    folded = fold(messages)
    assert folded[0]["path"] == "a.py" and folded[0]["chars"] > 1000
    assert messages[2]["content"].startswith(FOLDED + "a.py, ") and "alpha 1-2" in messages[2]["content"]
    assert "Read it again" in messages[2]["content"]


def test_a_read_repeated_later_folds_and_the_latest_stays():
    messages = conversation(("read_file", "a.py", LONG), ("read_file", "a.py", LONG), *PADDING)
    fold(messages)
    assert messages[2]["content"].startswith(FOLDED) and messages[4]["content"] == LONG


def test_the_latest_read_with_no_edit_after_it_stays():
    messages = conversation(("read_file", "a.py", LONG), *PADDING)
    assert fold(messages) == [] and messages[2]["content"] == LONG


def test_a_read_after_the_edit_stays():
    messages = conversation(("edit_file", "a.py", "edited"), ("read_file", "a.py", LONG), *PADDING)
    assert fold(messages) == []


def test_the_last_results_and_errors_stay():
    recent = conversation(("read_file", "a.py", LONG), ("read_file", "a.py", LONG), ("edit_file", "a.py", "e"))
    assert len(recent) == 1 + 2 * KEEP_RECENT and fold(recent) == []
    error = conversation(("read_file", "a.py", "ERROR: " + "x" * 500), ("edit_file", "a.py", "e"), *PADDING)
    assert fold(error) == []


def test_folding_twice_changes_nothing():
    messages = conversation(("read_file", "a.py", LONG), ("edit_file", "a.py", "e"), *PADDING)
    fold(messages)
    assert fold(messages) == []


def test_a_short_read_is_left_alone():
    assert fold(conversation(("read_file", "a.py", "tiny"), ("edit_file", "a.py", "e"), *PADDING)) == []


def rereads():
    """Reading the same files in rotation: stuck-looking, but each old read gets folded away."""
    names = ["a.py", "b.py", "c.py", "d.py"]
    return [calls(call("read_file", f"c{i}", path=names[i % 4])) for i in range(14)] + ["OK"]


def test_rereading_a_folded_file_is_not_a_stuck_loop(tmp_path):
    session, _ = session_for(tmp_path, *rereads(), max_turns=30)
    for name in "abcd":
        (session.project / f"{name}.py").write_text("x = 1\n" * 50, encoding="utf-8")
    done = [e for e in run(session, "what does this do?") if e["type"] == "done"][0]
    assert done["status"] == "done"


def test_with_memory_off_the_same_reads_hit_the_repeat_limit(tmp_path):
    session, _ = session_for(tmp_path, *rereads(), memory=False, max_turns=30)
    for name in "abcd":
        (session.project / f"{name}.py").write_text("x = 1\n" * 50, encoding="utf-8")
    done = [e for e in run(session, "what does this do?") if e["type"] == "done"][0]
    assert done["status"] == "stopped"


# ── tokens saved ─────────────────────────────────────────────────────────────

def test_saved_is_net_of_the_memory_block():
    messages = [{"role": "user", "content": "x" * 100}]
    assert saved_chars(messages, 500, 120) == 380


def test_saved_does_not_count_what_trim_would_have_cut():
    messages = [{"role": "user", "content": "x" * (MAX_PROMPT_CHARS - 100)}]
    assert saved_chars(messages, 5000, 0) == 100
    assert saved_chars([{"role": "user", "content": "x" * MAX_PROMPT_CHARS}], 5000, 0) == 0


def test_a_re_read_counts_against_the_saving():
    assert saved_chars([{"role": "user", "content": "x"}], -300, 10) == -310


def test_a_session_reports_its_estimate_and_never_goes_below_zero(tmp_path):
    session, _ = session_for(tmp_path, edit())
    events = run(session, "change the greeting to hello")
    delta = [e for e in events if e["type"] == "graph_delta"][-1]
    assert delta["saved"] == 0      # one short edit folds nothing, and the block costs a little


# ── the routes ───────────────────────────────────────────────────────────────

def opened(tmp_path, responder=None):
    hub = IdeHub(TOKEN, lambda name: ProviderSession("mock", responder=responder or script()),
                 home=tmp_path / "home", provider="mock")
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "greet.py").write_text(GREET, encoding="utf-8")
    assert client.post("/api/ide/open", json={"path": str(folder)}, headers=AUTH).status_code == 200
    return client, hub


def test_the_ide_turns_memory_on_and_serves_the_graph(tmp_path):
    client, hub = opened(tmp_path)
    assert hub.session.memory
    run(hub.session, "what does this do?")
    body = client.get("/api/ide/graph?scope=project", headers=AUTH).json()
    assert {node["kind"] for node in body["nodes"]} == {"session", "episode", "file", "symbol"}
    assert body["truncated"] is False and "scripts" in body and "saved" in body


def test_the_graph_route_filters_and_limits(tmp_path):
    client, hub = opened(tmp_path)
    run(hub.session, "what does this do?")
    only = client.get("/api/ide/graph?scope=project&types=symbol", headers=AUTH).json()
    assert {node["kind"] for node in only["nodes"]} == {"symbol"}
    assert client.get("/api/ide/graph?scope=everything", headers=AUTH).status_code == 422
    assert client.get("/api/ide/graph?limit=0", headers=AUTH).status_code == 422


def test_a_node_id_with_slashes_reaches_the_route(tmp_path):
    client, hub = opened(tmp_path)
    (hub.session.project / "pkg").mkdir()
    (hub.session.project / "pkg" / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    run(hub.session, "what does this do?")
    node = client.get("/api/ide/graph/node/symbol%3Apkg%2Fmod.py%3A%3Af", headers=AUTH).json()
    assert node["file"] == "pkg/mod.py" and node["line"] == 1 and node["end"] == 2


def test_a_missing_node_is_a_404_and_a_stored_bad_path_is_not_served(tmp_path):
    client, hub = opened(tmp_path)
    run(hub.session, "what does this do?")
    assert client.get("/api/ide/graph/node/nothing", headers=AUTH).status_code == 404
    graph = hub.session.graph
    with graph.lock, graph.db:
        graph.db.execute("UPDATE nodes SET file = '../outside.py' WHERE id = ?", (file_id("greet.py"),))
    assert client.get(f"/api/ide/graph/node/{file_id('greet.py')}", headers=AUTH).json()["file"] is None


def test_private_off_leaves_the_local_model_after_starting_with_the_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(ide, "pick_provider", lambda: "mock")
    names = []

    def make(name):
        names.append(name)
        return ProviderSession("mock", responder=script())

    folder = tmp_path / "project"
    folder.mkdir()
    hub = IdeHub(TOKEN, make, home=tmp_path / "home", provider="local", private=True)
    hub.open(str(folder))
    hub.set_private(False)
    assert names == ["local", "mock"]
    assert hub.state()["provider"] == "mock" and hub.state()["private"]["on"] is False


def test_the_routes_need_the_token(tmp_path):
    client, _ = opened(tmp_path)
    assert client.get("/api/ide/graph", headers={"host": "127.0.0.1:8765"}).status_code == 401


def test_a_broken_store_is_a_clear_503(tmp_path):
    client, hub = opened(tmp_path)
    hub.session._memory_failed = True
    assert client.get("/api/ide/graph", headers=AUTH).status_code == 503


@pytest.mark.parametrize("scope", ["session", "project"])
def test_the_view_of_a_new_session_is_valid(tmp_path, scope):
    client, hub = opened(tmp_path)
    run(hub.session, "what does this do?")
    hub.session.clear()
    for query in (f"scope={scope}", f"scope={scope}&types=session,episode,file,symbol"):
        assert client.get(f"/api/ide/graph?{query}", headers=AUTH).status_code == 200
    assert not hub.session._memory_failed
