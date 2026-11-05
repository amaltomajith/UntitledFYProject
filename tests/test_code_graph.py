"""The memory graph: what it stores, what it refuses to store, and that it stays current."""

import sqlite3

import pytest

from puppeteer_code.assistant import codemap
from puppeteer_code.assistant.codemap import FileMap, link_calls, parse, resolve_import, sync
from puppeteer_code.assistant.graph import (MEMORY_CHARS, MEMORY_HEAD, CodeGraph, changed_lines, clean, database_path,
                                            file_id, symbol_id)
from puppeteer_code.assistant.snapshots import Snapshots
from puppeteer_code.assistant.toolbox import Toolbox

needs_tree_sitter = pytest.mark.skipif(not codemap.tree_sitter_ready(), reason="tree-sitter is not installed")


def graph_at(tmp_path) -> CodeGraph:
    return CodeGraph(tmp_path / "home" / "graph.db")


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    graph = graph_at(tmp_path)
    yield folder, graph, Toolbox(folder, lambda event: None, lambda request: True), Snapshots(folder, tmp_path / "home")
    graph.close()


def write(folder, files):
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def rows(graph, sql, *args):
    return [dict(row) for row in graph.db.execute(sql, args).fetchall()]


def ids(graph, kind):
    return {row["id"] for row in rows(graph, "SELECT id FROM nodes WHERE kind = ?", kind)}


def edges(graph, kind):
    return {(row["src"], row["dst"]) for row in rows(graph, "SELECT src, dst FROM edges WHERE kind = ?", kind)}


def symbols(found):
    return {s.qualname: (s.kind, s.lineno, s.end_lineno) for s in found.symbols}


# ── reading code ─────────────────────────────────────────────────────────────

def test_python_symbols_lines_and_imports():
    found = parse("pkg/a.py", "import os\nfrom . import b\nfrom .c import d\n\nclass K:\n    def m(self):\n"
                              "        return helper()\n\ndef helper():\n    pass\n")
    assert symbols(found) == {"K": ("class", 5, 7), "K.m": ("method", 6, 7), "helper": ("function", 9, 10)}
    assert {"os", ".", ".b", ".c", ".c.d"} <= set(found.imports)
    assert next(s for s in found.symbols if s.qualname == "K.m").calls == {"helper"}


def test_a_python_syntax_error_is_still_a_file():
    found = parse("bad.py", "def broken(:\n")
    assert found.partial and found.symbols == [] and found.language == "python"


def test_null_bytes_in_python_are_handled():
    assert parse("nul.py", "a = 1\0").partial


@needs_tree_sitter
def test_javascript_symbols_and_imports():
    source = ("import a from './a'\nconst b = require('./b')\nexport function f(x) {\n  return g(x)\n}\n"
              "const g = (x) => x\nclass C {\n  m() { this.n() }\n}\n")
    found = parse("src/app.js", source)
    assert symbols(found) == {"f": ("function", 3, 5), "g": ("function", 6, 6), "C": ("class", 7, 9),
                              "C.m": ("method", 8, 8)}
    assert found.imports == ["./a", "./b"]
    assert next(s for s in found.symbols if s.qualname == "f").calls == {"g"}
    assert next(s for s in found.symbols if s.qualname == "C.m").calls == {"n"}


@needs_tree_sitter
def test_typescript_types_and_signatures():
    found = parse("src/app.ts", "export interface I { a: string }\nexport type T = number\n"
                                "export function f(x: number): string { return String(x) }\n"
                                "export * from './more'\n")
    assert symbols(found) == {"I": ("type", 1, 1), "T": ("type", 2, 2), "f": ("function", 3, 3)}
    assert next(s for s in found.symbols if s.qualname == "f").signature == "(x: number): string"
    assert found.imports == ["./more"]


@needs_tree_sitter
def test_tsx_components_count_as_calls():
    found = parse("App.tsx", "import { B } from './B'\nexport function A() { return <div><B /></div> }\n")
    assert next(s for s in found.symbols if s.name == "A").calls == {"B"}


@needs_tree_sitter
def test_a_script_syntax_error_keeps_what_it_can():
    found = parse("bad.js", "function ok() {}\nfunction (((\n")
    assert found.partial and "ok" in symbols(found)


def test_other_languages_are_files_with_no_symbols():
    found = parse("main.go", "package main\nfunc main() {}\n")
    assert found.symbols == [] and found.language is None


def test_scripts_without_tree_sitter_are_files(monkeypatch):
    monkeypatch.setattr(codemap, "_parser", lambda language: None)
    assert parse("a.js", "function f() {}").symbols == []
    assert parse("a.py", "def f(): pass").symbols


def test_nothing_from_the_project_runs(tmp_path):
    marker = tmp_path / "ran"
    parse("a.py", f"open({str(marker)!r}, 'w').close()\ndef f(): pass\n")
    assert not marker.exists()


# ── linking ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, name, target", [
    ("pkg/a.py", ".b", "pkg/b.py"),
    ("pkg/sub/a.py", "..b", "pkg/b.py"),
    ("pkg/a.py", "pkg.b", "pkg/b.py"),
    ("pkg/a.py", "pkg", "pkg/__init__.py"),
    ("main.py", "os", None),
    ("web/a.ts", "./c", "web/c.ts"),
    ("web/a.ts", "./c.js", "web/c.ts"),
    ("web/a.ts", "./d", "web/d/index.tsx"),
    ("web/sub/a.ts", "../c", "web/c.ts"),
    ("x.ts", "@/lib/e", "src/lib/e.ts"),
    ("web/a.ts", "react", None),
])
def test_imports_resolve_to_project_files(path, name, target):
    files = {"pkg/b.py", "pkg/__init__.py", "web/c.ts", "web/d/index.tsx", "src/lib/e.ts", path}
    assert resolve_import(path, name, files) == target


def symbol(name, calls=(), kind="function"):
    return {"id": f"symbol:{name}", "name": name, "kind": kind, "calls": list(calls)}


def test_a_call_links_only_when_the_name_is_unique():
    found = link_calls([symbol("caller", ["alone", "twice"]), symbol("alone"), symbol("twice"),
                        {**symbol("twice"), "id": "symbol:other"}])
    assert found == [("symbol:caller", "symbol:alone")]


def test_a_common_name_never_links():
    assert link_calls([symbol("caller", ["get"]), symbol("get", kind="method")]) == []


def test_a_call_to_itself_is_not_an_edge():
    assert link_calls([symbol("again", ["again"])]) == []


# ── the index ────────────────────────────────────────────────────────────────

def test_first_sync_builds_files_symbols_and_edges(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "from b import work\n\ndef run_all():\n    return work()\n", "b.py": "def work():\n    pass\n",
                   "notes.md": "hello\n"})
    result = sync(graph, tools, snaps)
    assert file_id("notes.md") in result["added"]
    assert ids(graph, "file") == {file_id("a.py"), file_id("b.py"), file_id("notes.md")}
    assert (file_id("a.py"), symbol_id("a.py", "run_all")) in edges(graph, "DEFINES")
    assert (file_id("a.py"), file_id("b.py")) in edges(graph, "IMPORTS")
    assert (symbol_id("a.py", "run_all"), symbol_id("b.py", "work")) in edges(graph, "CALLS")


def test_a_method_is_defined_by_its_class(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "class K:\n    def m(self):\n        pass\n"})
    sync(graph, tools, snaps)
    assert (symbol_id("a.py", "K"), symbol_id("a.py", "K.m")) in edges(graph, "DEFINES")


def test_edits_made_outside_the_assistant_are_found(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def old():\n    pass\n"})
    sync(graph, tools, snaps)
    write(folder, {"a.py": "def new():\n    pass\n"})
    result = sync(graph, tools, snaps)
    assert result == {"added": [symbol_id("a.py", "new")], "removed": [symbol_id("a.py", "old")]}


def test_a_deleted_file_leaves_no_nodes_or_edges(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "import b\n", "b.py": "def f():\n    pass\n"})
    sync(graph, tools, snaps)
    (folder / "b.py").unlink()
    sync(graph, tools, snaps)
    assert ids(graph, "file") == {file_id("a.py")} and ids(graph, "symbol") == set()
    assert edges(graph, "IMPORTS") == set() and edges(graph, "DEFINES") == set()


def test_a_name_that_stops_being_unique_loses_its_edge(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def caller():\n    return work()\n", "b.py": "def work():\n    pass\n"})
    sync(graph, tools, snaps)
    assert edges(graph, "CALLS")
    write(folder, {"c.py": "def work():\n    pass\n"})
    sync(graph, tools, snaps)
    assert edges(graph, "CALLS") == set()


def test_no_change_means_no_work(project, monkeypatch):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "x = 1\n"})
    sync(graph, tools, snaps)
    monkeypatch.setattr(codemap, "parse", lambda *a: pytest.fail("parsed again"))
    assert sync(graph, tools, snaps) == {"added": [], "removed": []}


def test_a_lost_snapshot_store_rebuilds(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def f():\n    pass\n"})
    sync(graph, tools, snaps)
    graph.set_meta("indexed_sha", "0" * 40)
    write(folder, {"b.py": "def g():\n    pass\n"})
    assert file_id("b.py") in sync(graph, tools, snaps)["added"]


def test_a_large_or_binary_file_has_no_symbols(project):
    folder, graph, tools, snaps = project
    write(folder, {"big.py": "def f():\n    pass\n" + "#" * codemap.MAX_FILE_BYTES})
    (folder / "blob.py").write_bytes(b"\xff\xfe\x00def f(): pass")
    sync(graph, tools, snaps)
    assert file_id("big.py") in ids(graph, "file") and file_id("blob.py") in ids(graph, "file")
    assert ids(graph, "symbol") == set()


def test_ignored_folders_stay_out(project):
    folder, graph, tools, snaps = project
    write(folder, {"node_modules/x/index.js": "function f() {}\n", "a.py": "x = 1\n"})
    sync(graph, tools, snaps)
    assert ids(graph, "file") == {file_id("a.py")}


def test_the_projects_gitignore_and_generated_folders_stay_out(project):
    folder, graph, tools, snaps = project
    write(folder, {".gitignore": "output/\n", "output/a.js": "function f() {}\n", ".next/b.js": "function g() {}\n",
                   "c.py": "x = 1\n"})
    sync(graph, tools, snaps)
    assert ids(graph, "file") == {file_id(".gitignore"), file_id("c.py")}


def test_a_minified_bundle_is_a_file_with_no_symbols(project):
    folder, graph, tools, snaps = project
    write(folder, {"app.min.js": "function a(){}\n", "bundle.js": "function b(){}" * 100})
    sync(graph, tools, snaps)
    assert len(ids(graph, "file")) == 2 and ids(graph, "symbol") == set()


def test_a_type_in_a_signature_stays_readable(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def f(token: str, password='swordfish'):\n    pass\n"})
    sync(graph, tools, snaps)
    signature = graph.detail(symbol_id("a.py", "f"))["signature"]
    assert "token: str" in signature and "swordfish" not in signature


# ── secrets ──────────────────────────────────────────────────────────────────

def test_secret_files_are_never_indexed(project):
    folder, graph, tools, snaps = project
    write(folder, {".env": "API_KEY=abc123\n", ".env.local": "X=1\n", "secrets.pem": "x", "a.py": "x = 1\n"})
    sync(graph, tools, snaps)
    assert ids(graph, "file") == {file_id("a.py")}
    dump = "\n".join(str(row) for row in rows(graph, "SELECT * FROM nodes"))
    assert "abc123" not in dump and ".env" not in dump


def test_a_protected_path_gets_no_node_even_if_asked(project):
    _, graph, _, _ = project
    assert not codemap.indexable(".env")
    graph.put_file(FileMap(".env", digest="x"))
    assert ids(graph, "file") == set()


def test_a_key_in_a_prompt_or_a_default_argument_is_hidden(project, monkeypatch):
    folder, graph, tools, snaps = project
    monkeypatch.setenv("GROQ_API_KEY", "value_from_the_environment")
    write(folder, {"a.py": "def f(key='gsk_abcdefghij1234567890'):\n    pass\n"})
    sync(graph, tools, snaps)
    graph.add_episode("s", 1, "t", "use value_from_the_environment and sk-abcdefghij1234567890 and api_key=hunter2",
                      ["Builder"], "done", "", [], None, None, 10)
    dump = "\n".join(str(row) for row in rows(graph, "SELECT * FROM nodes"))
    for secret in ("value_from_the_environment", "gsk_abcdefghij", "sk-abcdefghij", "hunter2"):
        assert secret not in dump
    assert clean("password: swordfish") == "password: [hidden]"


def test_no_source_text_is_stored(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def f():\n    marker_only_in_the_body = 1\n"})
    sync(graph, tools, snaps)
    assert "marker_only_in_the_body" not in "\n".join(str(r) for r in rows(graph, "SELECT * FROM nodes"))


# ── episodes and the brief ───────────────────────────────────────────────────

def episode(graph, session="s1", turn=1, prompt="add a login page", files=("a.py",), diff="", status="done"):
    return graph.add_episode(session, turn, "title", prompt, ["Builder"], status, "3 passed", list(files),
                             "before", "after", 100, diff)


def test_an_episode_links_its_session_files_and_touched_symbols(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def one():\n    pass\n\ndef two():\n    pass\n"})
    sync(graph, tools, snaps)
    node = episode(graph, diff="--- a/a.py\n+++ b/a.py\n@@ -4,2 +4,2 @@\n def two():\n-    pass\n+    return 1\n")
    assert (node, "session:s1") in edges(graph, "IN_SESSION")
    modified = {dst for src, dst in edges(graph, "MODIFIED") if src == node}
    assert modified == {file_id("a.py"), symbol_id("a.py", "two")}


def test_changed_lines_reads_a_unified_diff():
    assert changed_lines("+++ b/x.py\n@@ -1 +1,3 @@\n@@ -9,0 +12 @@\n+++ /dev/null\n@@ -1 +0,0 @@\n") == \
        {"x.py": [(1, 3), (12, 12)]}


def test_the_brief_is_labelled_as_data_and_capped(project):
    folder, graph, tools, snaps = project
    write(folder, {f"m{i}.py": f"def login_step_{i}(a, b):\n    pass\n" for i in range(40)})
    sync(graph, tools, snaps)
    for turn in range(1, 9):
        episode(graph, turn=turn, prompt="login " * 60, files=[f"m{i}.py" for i in range(20)])
    text = graph.brief("s1", "login")
    assert text.startswith(MEMORY_HEAD) and len(text) <= MEMORY_CHARS
    assert "Turn 8" in text and "Turn 3" not in text


def test_the_brief_holds_only_this_sessions_episodes(project):
    folder, graph, tools, snaps = project
    episode(graph, session="mine", prompt="make the header blue")
    episode(graph, session="theirs", prompt="rotate the logo")
    text = graph.brief("mine", "colours")
    assert "header blue" in text and "rotate" not in text


def test_the_brief_is_the_same_for_the_same_state(project):
    folder, graph, tools, snaps = project
    write(folder, {f"m{i}.py": "def shared_name_a():\n    pass\n" for i in range(12)})
    sync(graph, tools, snaps)
    assert graph.brief("s1", "shared name") == graph.brief("s1", "shared name") != ""


def test_an_empty_graph_gives_no_brief(project):
    _, graph, _, _ = project
    assert graph.brief("s1", "anything at all") == ""


@pytest.mark.parametrize("text", ['"quoted', "NEAR(a b)", "a* OR b", "col:value", "(((", "-x ^y", "' OR 1=1 --"])
def test_search_syntax_in_a_prompt_is_harmless(project, text):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def value():\n    pass\n"})
    sync(graph, tools, snaps)
    graph.brief("s1", text)
    graph.view("project", "s1", q=text)


def test_related_files_follow_the_search(project):
    folder, graph, tools, snaps = project
    write(folder, {"auth.py": "def login():\n    pass\n", "zed.py": "def other():\n    pass\n"})
    sync(graph, tools, snaps)
    assert graph.related_files("fix the login", 2) == ["auth.py"]


def test_keep_only_narrows_an_episode(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    sync(graph, tools, snaps)
    node = episode(graph, files=("a.py", "b.py"))
    graph.keep_only(node, ["b.py"])
    assert {dst for src, dst in edges(graph, "MODIFIED")} == {file_id("b.py")}
    assert graph.detail(node)["files"] == ["b.py"]


def test_forgetting_a_session_removes_its_episodes_and_keeps_the_code(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def f():\n    pass\n"})
    sync(graph, tools, snaps)
    episode(graph)
    graph.forget_session("s1")
    assert ids(graph, "episode") == set() and ids(graph, "session") == set()
    assert ids(graph, "file") == {file_id("a.py")} and edges(graph, "MODIFIED") == set()


# ── the view ─────────────────────────────────────────────────────────────────

def test_session_scope_shows_this_sessions_work(project):
    folder, graph, tools, snaps = project
    write(folder, {"a.py": "def f():\n    pass\n", "b.py": "def g():\n    pass\n"})
    sync(graph, tools, snaps)
    episode(graph, files=("a.py",))
    episode(graph, session="other", files=("b.py",))
    shown = {node["id"] for node in graph.view("session", "s1")["nodes"]}
    assert file_id("a.py") in shown and "session:s1" in shown
    assert file_id("b.py") not in shown and "session:other" not in shown
    assert file_id("b.py") in {node["id"] for node in graph.view("project", "s1")["nodes"]}


def test_a_large_view_is_cut_and_says_so(project):
    folder, graph, tools, snaps = project
    write(folder, {f"m{i}.py": "x = 1\n" for i in range(12)})
    sync(graph, tools, snaps)
    episode(graph, files=("m1.py",))
    view = graph.view("project", "s1", limit=5)
    assert view["truncated"] and view["total"] > 5 and len(view["nodes"]) == 5
    assert view["nodes"][0]["session"] == "s1"
    shown = {node["id"] for node in view["nodes"]}
    assert all(edge["src"] in shown and edge["dst"] in shown for edge in view["edges"])


def test_type_filters_and_search(project):
    folder, graph, tools, snaps = project
    write(folder, {"auth.py": "def login():\n    pass\n", "zed.py": "def other():\n    pass\n"})
    sync(graph, tools, snaps)
    only = graph.view("project", "s1", kinds=["symbol"])
    assert {node["kind"] for node in only["nodes"]} == {"symbol"}
    found = {node["id"] for node in graph.view("project", "s1", q="log")["nodes"]}
    assert symbol_id("auth.py", "login") in found and symbol_id("zed.py", "other") not in found


def test_detail_shows_an_episode(project):
    _, graph, _, _ = project
    node = graph.detail(episode(graph, files=()))
    assert node["prompt"] == "add a login page" and node["turn"] == 1 and node["check"] == "3 passed"
    assert graph.detail("nothing") is None


def test_saved_is_kept_per_session_and_never_below_zero(project):
    _, graph, _, _ = project
    assert graph.add_saved("s1", 10) == 0        # no session node yet
    episode(graph)
    graph.add_saved("s1", 10)
    assert graph.add_saved("s1", -50) == 0


# ── the two files ────────────────────────────────────────────────────────────

def test_private_and_cloud_use_different_files(tmp_path):
    assert database_path(tmp_path, tmp_path, True) != database_path(tmp_path, tmp_path, False)
    assert database_path(tmp_path, tmp_path, True).name == "graph-private.db"


def test_a_bad_database_file_raises_for_the_caller_to_handle(tmp_path):
    path = tmp_path / "graph.db"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        CodeGraph(path)
