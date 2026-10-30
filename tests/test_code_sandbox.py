"""
Offline tests for Puppeteer-Code P1: sandbox, integrity guard, edit tools,
and the AST symbol index.  No API key or network needed.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from code_fixtures import CALC_BUGGY, CALC_FIX_BLOCK, make_calc_repo
from puppeteer_code.index import SymbolIndex
from puppeteer_code.integrity import check_test_count, is_test_path, scan_diff
from puppeteer_code.tools import EditBlock, apply_block, apply_edits, grep, parse_edit_blocks
from puppeteer_code.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    repo = make_calc_repo(tmp_path)
    w = Workspace.create(repo)
    yield w
    w.cleanup()


# ── workspace ────────────────────────────────────────────────────────────────

class TestWorkspace:
    def test_copies_sources_but_not_secrets_or_caches(self, ws):
        files = set(ws.git("ls-files").split())
        assert {"calc.py", "tests/test_calc.py", "conftest.py"} <= files
        assert ".env" not in files
        assert not any("__pycache__" in f for f in files)

    def test_user_repo_is_never_modified(self, tmp_path):
        repo = make_calc_repo(tmp_path)
        with Workspace.create(repo) as w:
            w.write("calc.py", "broken")
        assert (repo / "calc.py").read_text(encoding="utf-8") == CALC_BUGGY

    @pytest.mark.parametrize("bad", ["../escape.py", "/etc/passwd", "C:/Windows/x.py", ".git/config"])
    def test_path_guard(self, ws, bad):
        with pytest.raises(PermissionError):
            ws.resolve(bad)

    def test_checkpoint_and_rollback(self, ws):
        good = ws.checkpoint("good")
        ws.write("calc.py", "def add(:\n")
        ws.write("new_file.py", "x = 1\n")
        ws.rollback(good)
        assert ws.read("calc.py") == CALC_BUGGY
        assert not ws.exists("new_file.py")

    def test_diff_and_stat(self, ws):
        ws.write("calc.py", CALC_BUGGY.replace("(len(values) - 1)", "len(values)"))
        assert "-    return sum(values) / (len(values) - 1)" in ws.diff()
        assert ws.diff_stat() == (1, 1, 1)
        assert ws.changed_files() == ["calc.py"]

    def test_run_tests_parses_outcomes_and_frames(self, ws):
        res = ws.run_tests()
        assert res.total == 3
        assert len(res.passed) == 1
        assert res.pass_rate == pytest.approx(1 / 3)
        assert not res.all_green
        assert any(f.endswith("calc.py") for f, _ in res.frames())
        assert "1/3 tests passing" in res.digest()

    def test_tests_run_without_secrets(self, ws, monkeypatch):
        monkeypatch.setenv("NEBIUS_API_KEY", "should-not-leak")
        ws.write("tests/test_env.py",
                 "import os\ndef test_no_key():\n    assert 'NEBIUS_API_KEY' not in os.environ\n")
        res = ws.run_tests(["tests/test_env.py"])
        assert res.all_green

    def test_syntax_errors(self, ws):
        ws.write("calc.py", "def broken(:\n    pass\n")
        assert ws.syntax_errors(["calc.py"])[0].startswith("calc.py:1")

    def test_fork_and_promote(self, ws):
        fork = ws.fork()
        try:
            fork.write("calc.py", CALC_BUGGY.replace("(len(values) - 1)", "len(values)"))
            sha = fork.checkpoint("candidate")
            assert ws.run_tests().pass_rate < 1.0         # main untouched
            ws.rollback(sha)                               # promote shared commit
            assert ws.run_tests().all_green
        finally:
            fork.cleanup()


# ── integrity ────────────────────────────────────────────────────────────────

class TestIntegrity:
    @pytest.mark.parametrize("path,expected", [
        ("tests/test_calc.py", True), ("test_x.py", True), ("pkg/x_test.py", True),
        ("conftest.py", True), ("src/calc.py", False), ("pkg/testing/helpers.py", True),
    ])
    def test_is_test_path(self, path, expected):
        assert is_test_path(path) is expected

    def test_scan_diff_flags_tampering(self):
        diff = (
            "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -1,3 +1,3 @@\n"
            "-    assert mean([2, 4, 6]) == 4\n+    pytest.skip('flaky')\n"
            "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n"
            "+    if 'PYTEST_CURRENT_TEST' in os.environ: return 4\n"
        )
        v = scan_diff(diff)
        assert any("removes an assertion" in x for x in v)
        assert any("skip" in x for x in v)
        assert any("detects the test harness" in x for x in v)

    def test_clean_diff_has_no_violations(self):
        diff = "--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-    return x - 1\n+    return x\n"
        assert scan_diff(diff) == []

    def test_count_guard(self):
        assert check_test_count(5, 4)
        assert not check_test_count(5, 5)


# ── edit tools ───────────────────────────────────────────────────────────────

class TestEdits:
    def test_parse_plain_block(self):
        blocks = parse_edit_blocks(CALC_FIX_BLOCK)
        assert len(blocks) == 1 and blocks[0].file == "calc.py"
        assert "len(values) - 1" in blocks[0].search

    def test_parse_fenced_bare_path(self):
        text = ("Here is the fix:\n\n`calc.py`\n```python\n<<<<<<< SEARCH\nold\n=======\nnew\n"
                ">>>>>>> REPLACE\n```\n")
        blocks = parse_edit_blocks(text)
        assert blocks == [EditBlock("calc.py", "old", "new")]

    def test_apply_exact(self, ws):
        res = apply_edits(ws, parse_edit_blocks(CALC_FIX_BLOCK))
        assert res.ok and res.files == ["calc.py"]
        assert ws.run_tests().all_green

    def test_trailing_whitespace_tolerant(self):
        new, note = apply_block("a = 1   \nb = 2\n", EditBlock("f.py", "a = 1\nb = 2", "a = 3\nb = 2"))
        assert new.startswith("a = 3") and "tolerant" in note

    def test_indentation_tolerant(self):
        content = "def f():\n    x = 1\n    return x\n"
        new, note = apply_block(content, EditBlock("f.py", "x = 1\nreturn x", "x = 2\nreturn x"))
        assert new == "def f():\n    x = 2\n    return x\n" and "indentation" in note

    def test_ambiguous_match_is_an_error(self):
        new, note = apply_block("x = 1\nx = 1\n", EditBlock("f.py", "x = 1", "x = 2"))
        assert new is None and "2 places" in note

    def test_test_files_are_protected(self, ws):
        block = EditBlock("tests/test_calc.py", "assert mean([2, 4, 6]) == 4", "assert True")
        res = apply_edits(ws, [block])
        assert not res.ok and "not allowed" in res.errors[0]
        assert apply_edits(ws, [block], may_edit_tests=True).ok

    def test_all_or_nothing(self, ws):
        good = parse_edit_blocks(CALC_FIX_BLOCK)[0]
        bad = EditBlock("calc.py", "this text does not exist", "x")
        res = apply_edits(ws, [good, bad])
        assert not res.ok
        assert ws.read("calc.py") == CALC_BUGGY

    def test_crlf_preserved(self, ws):
        ws.write("crlf.py", "a = 1\r\nb = 2\r\n")
        assert apply_edits(ws, [EditBlock("crlf.py", "a = 1", "a = 9")]).ok
        assert ws.read("crlf.py") == "a = 9\r\nb = 2\r\n"

    def test_create_new_file(self, ws):
        assert apply_edits(ws, [EditBlock("pkg/new.py", "", "VALUE = 1")]).ok
        assert ws.read("pkg/new.py") == "VALUE = 1\n"

    def test_grep(self, ws):
        assert any("calc.py" in h for h in grep(ws, r"def mean"))


# ── AST index ────────────────────────────────────────────────────────────────

class TestIndex:
    def test_symbols_and_lookup(self, ws):
        idx = SymbolIndex(ws).build()
        mean = idx.by_name["mean"][0]
        assert mean.kind == "function" and mean.signature == "(values)"
        assert idx.at("calc.py", mean.lineno + 1).qualname == "mean"
        assert idx.at("/abs/prefix/calc.py", mean.lineno + 1).qualname == "mean"

    def test_callers_blast_radius(self, ws):
        idx = SymbolIndex(ws).build()
        callers = {s.qualname for s in idx.callers(idx.by_name["mean"][0])}
        assert "describe" in callers

    def test_source_is_verbatim(self, ws):
        idx = SymbolIndex(ws).build()
        src = idx.source(idx.by_name["mean"][0], pad=0)
        assert src in ws.read("calc.py")

    def test_rank_finds_buggy_function_from_traceback(self, ws):
        res = ws.run_tests()
        idx = SymbolIndex(ws).build()
        ranked = idx.rank("mean of a list is wrong", res.frames(), res.failing)
        assert ranked[0][0].qualname == "mean"
        assert all(not s.is_test for s, _, _ in ranked)
