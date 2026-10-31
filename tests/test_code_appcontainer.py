"""The OS block for Private. The honesty rules run everywhere; the live ones need Windows and `pcode private-setup`."""

import subprocess
import sys
import tempfile
from pathlib import Path

import psutil
import pytest

from puppeteer_code import appcontainer, sandbox
from puppeteer_code.assistant.session import BLOCK_LOST, AssistantSession
from puppeteer_code.assistant.toolbox import Toolbox
from puppeteer_code.assistant.verify import run_checks
from puppeteer_code.privacy import private_claim
from puppeteer_code.providers import ProviderSession
from puppeteer_code.sandbox import BLOCKED, AppContainerRunner, DirectRunner, private_runner

STATE = {"sid": "S-1-15-2-1", "folder": "C:\\ac", "python": "python.exe", "node": "C:\\ac\\tools\\nodejs", "granted": []}
CLEAN = {"container": True, "tcp": BLOCKED, "tcp6": 10051, "loopback": "closed", "reached": False,
         "udp_control": True, "udp_reached": False}


def runner_with(monkeypatch, report, file_system="exFAT"):
    monkeypatch.setattr(appcontainer, "filesystem", lambda path: file_system, raising=False)
    monkeypatch.setattr(AppContainerRunner, "prepare", lambda self, path: None)
    monkeypatch.setattr(AppContainerRunner, "probe", lambda self: report)
    return AppContainerRunner(STATE, ".")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows jobs")
def test_a_tied_process_ends_even_if_its_parent_is_killed():
    """Private's Ollama outlived a killed IDE, because atexit never ran. The base Python, since a venv's is a launcher."""
    parent = (f"import subprocess, sys, time\nsys.path.insert(0, {str(Path(appcontainer.__file__).parent)!r})\n"
              "import appcontainer\n"
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL)\n"
              "assert appcontainer.tie_to_this_process(child)\n"
              "print(child.pid, flush=True)\n"
              "time.sleep(60)\n")
    proc = subprocess.Popen([sys._base_executable, "-I", "-c", parent], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, text=True)
    with proc.stdout:
        child = psutil.Process(int(proc.stdout.readline()))
    proc.kill()
    proc.wait()
    child.wait(timeout=10)
    assert not child.is_running()


# ── nothing claims a block it has not proved ─────────────────────────────────

def test_a_new_runner_is_not_contained_until_the_probe_passes(monkeypatch):
    runner = runner_with(monkeypatch, CLEAN)
    assert runner.contained is False and "not sandboxed" in private_claim(runner)
    assert runner.verify() and "no network" in private_claim(runner)


@pytest.mark.parametrize("change", [
    {"container": False}, {"tcp": "open"}, {"tcp": 10060}, {"udp_reached": True}, {"tcp6": "open"},
    {"loopback": "open"}, {"reached": True}, {"udp_control": False},
])
def test_any_sign_of_a_way_out_fails_the_probe(monkeypatch, change):
    runner = runner_with(monkeypatch, {**CLEAN, **change})
    assert runner.verify() is False and "not sandboxed" in private_claim(runner)


def test_a_probe_that_reports_nothing_fails(monkeypatch):
    assert runner_with(monkeypatch, {"container": False}).verify() is False


def test_no_setup_means_no_block(tmp_path):
    runner = private_runner(tmp_path, tmp_path / "home")
    assert runner.name == "direct" and runner.contained is False


def test_a_fake_runner_never_earns_the_wording():
    assert "not sandboxed" in private_claim(DirectRunner())


def test_losing_the_block_mid_session_is_said_and_turns_auto_off(tmp_path, monkeypatch):
    runner = runner_with(monkeypatch, CLEAN)
    runner.verify()
    monkeypatch.setattr(sandbox, "private_runner", lambda *a: None)
    session = AssistantSession(tmp_path / "project", ProviderSession("mock"), home=tmp_path / "home", policy="direct")
    session.tools.runner = runner
    session.private = True
    session.tools.mode = "auto"
    monkeypatch.setattr(AppContainerRunner, "probe", lambda self: {**CLEAN, "tcp": "open"})
    events = list(session.send("what is this?"))
    assert [e for e in events if e["type"] == "notice"][0]["text"] == BLOCK_LOST
    assert session.tools.mode == "ask" and "not sandboxed" in private_claim(runner)


# ── how a command is wrapped ─────────────────────────────────────────────────

def test_a_shell_command_starts_inside_through_the_launcher(monkeypatch):
    kwargs = {"shell": True, "env": {"PATH": "x"}, "creationflags": 0}
    line = runner_with(monkeypatch, CLEAN)._wrap("echo hi", kwargs)
    assert line[:4] == [sys.executable, "-I", sandbox.LAUNCHER, "--"]
    assert line[4] == 'cmd.exe /d /s /c "echo hi"'
    assert "shell" not in kwargs and kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_an_argument_list_is_quoted(monkeypatch):
    line = runner_with(monkeypatch, CLEAN)._wrap(["py", "a b.py"], {})
    assert line[4] == 'py "a b.py"'


def test_commands_get_a_redirected_home_and_git_works(monkeypatch):
    env = runner_with(monkeypatch, CLEAN)._env({"PATH": "base"})
    assert env["PYTHONNOUSERSITE"] == "1" and env["HOME"].startswith("C:\\ac") and env["APPDATA"].startswith("C:\\ac")
    assert env["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert env["PATH"].startswith("C:\\ac\\tools\\nodejs")


def test_secrets_are_dropped_when_no_env_is_given(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "present")
    assert "NEBIUS_API_KEY" not in runner_with(monkeypatch, CLEAN)._env(None)


def test_temp_files_move_off_ntfs_rules_only_on_a_drive_without_them(monkeypatch):
    assert "TMPDIR" in runner_with(monkeypatch, CLEAN, "exFAT")._env({})
    assert "TMPDIR" not in runner_with(monkeypatch, CLEAN, "NTFS")._env({})


def test_node_tests_stay_in_one_process(monkeypatch):
    runner = runner_with(monkeypatch, CLEAN)
    assert "--experimental-test-isolation=none" in runner.adapt("node --test")
    assert runner.adapt("npm test") == "npm test"
    assert DirectRunner().adapt("node --test") == "node --test"


def test_a_drive_with_no_permissions_is_named(monkeypatch):
    assert "read and delete everything" in runner_with(monkeypatch, CLEAN, "exFAT").warning()
    assert "pytest cannot start" in runner_with(monkeypatch, CLEAN, "NTFS").warning()


def test_the_launcher_imports_only_the_standard_library():
    source = open(sandbox.LAUNCHER, encoding="utf-8").read()
    assert "puppeteer_code" not in source.split('"""', 2)[2]


# ── live, on a machine that has run `pcode private-setup` ────────────────────

@pytest.fixture
def live(tmp_path):
    state = sandbox.load_state()
    if sys.platform != "win32" or state is None:
        pytest.skip("needs Windows and pcode private-setup")
    return AppContainerRunner(state, tmp_path)


def test_live_probe_passes(live):
    assert live.verify(), live.probe()


def test_live_a_udp_packet_from_outside_arrives_and_one_from_inside_does_not(live):
    report = live.probe()
    assert report["udp_control"] is True and report["udp_reached"] is False


def test_live_output_and_exit_code_come_back(live):
    done = live.run("echo hello& exit /b 3", shell=True, cwd=live.project, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True)
    assert done.returncode == 3 and "hello" in done.stdout


def test_live_python_is_inside_and_has_no_network(live):
    code = "import socket\ntry:\n    socket.create_connection(('1.1.1.1', 443), 3)\n    print('OPEN')\nexcept OSError as e:\n    print('BLOCKED', e.winerror)"
    done = live.run([live.state["python"], "-I", "-c", code], cwd=live.project, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True)
    assert f"BLOCKED {BLOCKED}" in done.stdout, done.stdout + done.stderr


def test_live_kill_takes_the_whole_tree(live):
    from puppeteer_code.assistant.toolbox import Toolbox

    proc = live.popen(f'"{live.state["python"]}" -c "import time; time.sleep(60)"', shell=True, cwd=live.project,
                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.poll() is None
    Toolbox._kill(proc)
    assert proc.wait(10) != 0


@pytest.fixture
def folder(live):
    """On the repo's drive: pytest's own temp folders are locked to their owner, so a container cannot walk them."""
    with tempfile.TemporaryDirectory(dir=Path(sandbox.LAUNCHER).anchor, prefix="pcode-live-") as path:
        yield Path(path)


def check(live, folder):
    runner = AppContainerRunner(live.state, folder)
    return run_checks(folder, Toolbox(folder, lambda e: None, lambda r: True, runner=runner))


def test_live_a_project_check_runs_inside(live, folder):
    (folder / "test_a.py").write_text("def test_a(tmp_path):\n    assert tmp_path.is_dir()\n")
    result = check(live, folder)
    assert result.ok and result.kind == "tests", result.output


def test_live_node_tests_do_not_hang(live, folder):
    (folder / "a.test.js").write_text("const t = require('node:test'); t.test('adds', () => {});\n")
    result = check(live, folder)
    assert result.ok, result.output
