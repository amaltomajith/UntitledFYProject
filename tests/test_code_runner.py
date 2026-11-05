"""Command runners: everything that runs project code must go through one."""

import ast
import hashlib
import json
from pathlib import Path

from puppeteer_code.assistant.toolbox import Toolbox
from puppeteer_code.providers import CachingClient, MockClient
from puppeteer_code.sandbox import DirectRunner
from puppeteer_code.workspace import Workspace

PACKAGE = Path(__file__).resolve().parent.parent / "puppeteer_code"

# Places allowed to start a process directly. None of these run code the model wrote: they run git on
# the shadow repo, kill a process, or score a benchmark. Add here only after reading what the call runs.
DIRECT = {
    "cli.py": {"apply_diff"},
    "ollama.py": {"_spawn", "stop"},
    "sandbox.py": {"popen", "run", "_grant", "_revoke", "_python_bases"},
    "workspace.py": {"_list_source_files", "git"},
    "assistant/snapshots.py": {"_git", "_git_bytes", "read"},
    "assistant/toolbox.py": {"_kill"},
    "assistant/ide.py": {"git"},
    "assistant/buildbench.py": None,
}


def direct_calls(path: Path) -> list[tuple[str, int]]:
    """(enclosing function, line) of each subprocess.run or subprocess.Popen call in a file."""
    found = []

    def visit(node, owner):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("run", "Popen")
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"):
            found.append((owner, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(ast.parse(path.read_text(encoding="utf-8")), "")
    return found


def test_project_code_only_runs_through_a_runner():
    strays = []
    for path in PACKAGE.rglob("*.py"):
        rel = path.relative_to(PACKAGE).as_posix()
        for owner, line in direct_calls(path):
            allowed = DIRECT.get(rel, set())
            if allowed is not None and owner not in allowed:
                strays.append(f"{rel}:{line} in {owner}")
    assert strays == []


class Recording(DirectRunner):
    def __init__(self):
        self.calls = []

    def popen(self, command, **kwargs):
        self.calls.append(command)
        return super().popen(command, **kwargs)

    def run(self, command, **kwargs):
        self.calls.append(command)
        return super().run(command, **kwargs)


def test_toolbox_commands_use_its_runner(tmp_path):
    runner = Recording()
    tools = Toolbox(tmp_path, lambda e: None, lambda a: True, runner=runner)
    code, out = tools.run("echo hello")
    assert code == 0 and "hello" in out
    assert runner.calls == ["echo hello"]


def test_toolbox_defaults_to_the_direct_runner(tmp_path):
    assert Toolbox(tmp_path, lambda e: None, lambda a: True).runner.name == "direct"


def workspace(tmp_path, runner):
    src = tmp_path / "src"
    src.mkdir()
    (src / "test_a.py").write_text("def test_a():\n    assert True\n")
    return Workspace.create(src, tmp_parent=str(tmp_path), runner=runner)


def test_workspace_tests_and_forks_use_its_runner(tmp_path):
    runner = Recording()
    with workspace(tmp_path, runner) as ws:
        assert ws.run_tests().all_green
        assert len(runner.calls) == 1
        fork = ws.fork()
        assert fork.runner is runner
        fork.cleanup()


def old_key(model, messages, temperature, max_tokens):
    """The cache key before options existed. Old caches must keep working."""
    blob = json.dumps([model, messages, temperature, max_tokens], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest() + ".json"


def test_cache_keys_do_not_change_without_options(tmp_path):
    cache = CachingClient(MockClient(lambda *a, **k: "x"), tmp_path)
    messages = [{"role": "user", "content": "hi"}]
    assert cache._key("m", messages, 0.2, 10).name == old_key("m", messages, 0.2, 10)


def test_cache_keys_differ_by_options(tmp_path):
    cache = CachingClient(MockClient(lambda *a, **k: "x"), tmp_path)
    messages = [{"role": "user", "content": "hi"}]
    plain = cache._key("m", messages, 0.2, 10, None, {})
    thinking = cache._key("m", messages, 0.2, 10, None, {"think": True})
    assert plain != thinking


def test_cache_passes_options_to_the_client(tmp_path):
    seen = {}

    class Client:
        def chat(self, **kwargs):
            seen.update(kwargs)
            return {"content": "ok", "usage": {}}

    CachingClient(Client(), tmp_path).chat("m", [], think=True)
    assert seen["think"] is True


def test_preview_server_stays_on_this_computer():
    from puppeteer_code.assistant.agents import SPECS
    assert "--bind 127.0.0.1" in SPECS["Builder"].prompt
