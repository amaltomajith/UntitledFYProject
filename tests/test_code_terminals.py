"""The IDE's real terminals. All offline; the round trips start a real shell on this OS."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.websockets import WebSocketDisconnect  # noqa: E402

from puppeteer_code.assistant import terminals as T  # noqa: E402
from puppeteer_code.assistant.terminals import Shell, Terminal, TerminalLimit, TerminalManager  # noqa: E402
from puppeteer_code.assistant.ide import IdeHub  # noqa: E402
from puppeteer_code.providers import ProviderSession  # noqa: E402
from puppeteer_code.server import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "test-token-123"
LOCAL = {"host": "127.0.0.1:8765"}
AUTH = {**LOCAL, "x-pcode-token": TOKEN}
FAKE = Shell("fake", "Fake", "terminal", ("fake",), True)
needs_pty = pytest.mark.skipif(T.pty_missing() is not None, reason="pty packages not installed")


class FakePty:
    """Output is fed by the test. `stop()` ends the process."""

    pid = 1

    def __init__(self, *args):
        self.spawned = args
        self.out: list = []
        self.more = threading.Event()
        self.running = True
        self.written = []
        self.sizes = []

    def feed(self, data: bytes):
        self.out.append(data)
        self.more.set()

    def read(self):
        while not self.out:
            if not self.running:
                raise EOFError
            self.more.wait(0.02)
            self.more.clear()
        return self.out.pop(0)

    def write(self, data):
        self.written.append(data)

    def resize(self, rows, cols):
        self.sizes.append((rows, cols))

    def alive(self):
        return self.running

    def exit_code(self):
        return 3

    def kill(self):
        self.running = False

    stop = kill

    def close(self):
        pass


def wait_for(check, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return False


def fake_terminal():
    pty = FakePty()
    terminal = Terminal(FAKE, pty, 80, 24)
    return terminal, pty


# ── detection ────────────────────────────────────────────────────────────────

class Ran:
    def __init__(self, stdout=b"", code=0, error=None):
        self.stdout, self.returncode, self.error = stdout, code, error
        self.env = None
        self.stdin = None

    def __call__(self, argv, env=None, stdin=None, **kwargs):
        self.env, self.stdin = env, stdin
        if self.error:
            raise self.error
        return self


WINDOWS = {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"}
SYSTEM = {r"C:\Windows\System32\cmd.exe", r"C:\Windows\System32\wsl.exe",
          r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
          r"C:\Program Files\Git\bin\bash.exe"}


def windows(run, env=WINDOWS, files=SYSTEM):
    normal = {str(Path(f)) for f in files}
    return T.windows_shells(dict(env), which=lambda name: None, exists=lambda p: p in normal, run=run)


def test_wsl_distros_are_read_as_utf16_and_docker_is_skipped():
    listing = "\ufeffUbuntu\r\ndocker-desktop\r\ndocker-desktop-data\r\nDebian\r\n".encode("utf-16-le")
    run = Ran(listing)
    shells = windows(run)
    assert [s.id for s in shells] == ["powershell", "cmd", "git-bash", "wsl-Ubuntu", "wsl-Debian"]
    assert run.env["WSL_UTF8"] == "0"
    assert run.stdin == subprocess.DEVNULL


@pytest.mark.parametrize("run", [Ran(error=subprocess.TimeoutExpired("wsl", 3)), Ran(error=OSError()),
                                 Ran("error".encode("utf-16-le"), code=1)])
def test_a_broken_wsl_gives_no_distros_and_no_error(run):
    assert [s.id for s in windows(run)] == ["powershell", "cmd", "git-bash"]


def test_exactly_one_default_shell_and_pwsh_comes_first():
    shells = T.windows_shells(dict(WINDOWS), which=lambda n: r"C:\pwsh\pwsh.exe" if n == "pwsh.exe" else None,
                              exists=lambda p: p in {str(Path(f)) for f in SYSTEM}, run=Ran(b""))
    assert [s.id for s in shells if s.default] == ["pwsh"]


def test_git_bash_is_found_from_git_on_path():
    git = str(Path(r"D:\tools\Git\cmd\git.exe"))
    bash = str(Path(r"D:\tools\Git\bin\bash.exe"))
    shells = T.windows_shells(dict(WINDOWS), which=lambda n: git if n == "git.exe" else None,
                              exists=lambda p: p == bash, run=Ran(b""))
    assert [s.argv[0] for s in shells] == [bash]


def test_upper_case_environment_keys_still_find_program_files():
    env = {"SYSTEMROOT": r"C:\Windows", "PROGRAMFILES": r"C:\Program Files"}
    assert "git-bash" in [s.id for s in windows(Ran(b""), env=env)]


def test_git_bash_is_found_when_git_runs_from_mingw():
    git = str(Path(r"D:\tools\Git\mingw64\bin\git.exe"))
    bash = str(Path(r"D:\tools\Git\bin\bash.exe"))
    shells = T.windows_shells(dict(WINDOWS), which=lambda n: git if n == "git.exe" else None,
                              exists=lambda p: p == bash, run=Ran(b""))
    assert [s.argv[0] for s in shells] == [bash]


def test_unix_shells_come_from_etc_shells_with_the_login_shell_as_default():
    listing = "# comment\n/bin/sh\n/bin/bash\n/usr/bin/bash\n/usr/bin/zsh\n/usr/sbin/nologin\n"
    shells = T.unix_shells({"SHELL": "/usr/bin/zsh"}, listing, exists=lambda p: True)
    assert [s.id for s in shells] == ["sh", "bash", "zsh"]
    assert [s.id for s in shells if s.default] == ["zsh"]


def test_the_browser_is_never_given_a_command_line():
    assert "argv" not in FAKE.public()


# ── environment ──────────────────────────────────────────────────────────────

def test_shell_env_drops_keys_and_keeps_the_users_own_tokens():
    env = T.shell_env({"NEBIUS_API_KEY": "x", "groq_api_key": "x", "OTHER_API_KEY": "x", "PCODE_THING": "x",
                       "GITHUB_TOKEN": "keep", "PATH": "/bin", "SystemRoot": "C:\\Windows"})
    assert set(env) == {"GITHUB_TOKEN", "PATH", "SystemRoot", "TERM", "COLORTERM"}
    assert env["TERM"] == "xterm-256color"


# ── buffer, attach, flow control (fake pty) ──────────────────────────────────

def test_ring_keeps_the_newest_bytes_with_an_absolute_offset():
    terminal, pty = fake_terminal()
    pty.feed(b"a" * (T.MAX_RING + 10))
    assert wait_for(lambda: terminal.total == T.MAX_RING + 10)
    header, replay = terminal.attach(lambda item: None, 0)
    assert header["offset"] == 10 and header["reset"] is True
    assert len(replay) == T.MAX_RING
    pty.stop()


def test_attach_replays_only_what_the_client_has_not_seen():
    terminal, pty = fake_terminal()
    pty.feed(b"hello ")
    pty.feed(b"world")
    assert wait_for(lambda: terminal.total == 11)
    header, replay = terminal.attach(lambda item: None, 6)
    assert (header["offset"], header["reset"], replay) == (6, False, b"world")
    pty.stop()


def test_a_newer_attach_takes_over():
    terminal, pty = fake_terminal()
    first, second = [], []
    terminal.attach(first.append)
    terminal.attach(second.append)
    assert first == [{"t": "taken"}]
    pty.feed(b"x")
    assert wait_for(lambda: second == [b"x"])
    terminal.detach(first.append)
    assert terminal.sink is not None
    pty.stop()


def test_reading_pauses_above_the_high_mark_and_resumes_after_acks():
    terminal, pty = fake_terminal()
    sink = []
    terminal.attach(sink.append)
    pty.feed(b"a" * (T.HIGH_WATER + 1))
    assert wait_for(lambda: not terminal.can_read.is_set())
    pty.feed(b"held back")
    time.sleep(0.1)
    assert terminal.total == T.HIGH_WATER + 1
    terminal.ack(T.HIGH_WATER)
    assert wait_for(lambda: terminal.total > T.HIGH_WATER + 1)
    pty.stop()


def test_a_detached_terminal_keeps_reading():
    terminal, pty = fake_terminal()
    sink = []
    terminal.attach(sink.append)
    pty.feed(b"a" * (T.HIGH_WATER + 1))
    assert wait_for(lambda: not terminal.can_read.is_set())
    terminal.detach(terminal.sink)
    assert terminal.can_read.is_set()
    pty.stop()


def test_exit_reaches_the_client_and_a_late_attach():
    terminal, pty = fake_terminal()
    sink = []
    terminal.attach(sink.append)
    pty.stop()
    assert wait_for(lambda: {"t": "exit", "code": 3} in sink)
    late = []
    terminal.attach(late.append)
    assert {"t": "exit", "code": 3} in late


def test_resize_is_clamped_and_repeats_are_dropped():
    terminal, pty = fake_terminal()
    terminal.resize(100000, 0)
    terminal.resize(T.MAX_COLS, T.MIN_ROWS)
    assert pty.sizes == [(T.MIN_ROWS, T.MAX_COLS)]
    pty.stop()


def test_bad_control_messages_are_ignored():
    terminal, pty = fake_terminal()
    for text in ["not json", "[]", '{"t":"resize"}', '{"t":"resize","cols":"x","rows":1}', '{"t":"ack"}']:
        T._control(terminal, text)
    assert pty.sizes == []
    pty.stop()


def test_terminal_limit_and_finished_terminals_do_not_count(tmp_path):
    made = []

    def spawn(*args):
        made.append(FakePty(*args))
        return made[-1]

    manager = TerminalManager(spawn=spawn, shells=lambda: [FAKE])
    for _ in range(T.MAX_TERMINALS):
        manager.create(None, tmp_path)
    with pytest.raises(TerminalLimit):
        manager.create(None, tmp_path)
    made[0].stop()
    assert wait_for(lambda: list(manager.terminals.values())[0].exited.is_set())
    manager.create(None, tmp_path)
    manager.close_all()
    assert manager.list() == []
    assert not any(p.running for p in made)


def test_unknown_shell_is_refused(tmp_path):
    with pytest.raises(KeyError):
        TerminalManager(spawn=FakePty, shells=lambda: [FAKE]).create("nope", tmp_path)


def test_wsl_starts_in_the_project_even_with_spaces_in_the_path(tmp_path):
    wsl = Shell("wsl-Ubuntu", "Ubuntu (WSL)", "linux", ("wsl.exe", "-d", "Ubuntu"), True)
    folder = tmp_path / "a folder with spaces"
    manager = TerminalManager(spawn=FakePty, shells=lambda: [wsl])
    terminal = manager.create(None, folder)
    assert terminal.pty.spawned[0] == ("wsl.exe", "-d", "Ubuntu", "--cd", str(folder))
    manager.close_all()


# ── routes and the socket ────────────────────────────────────────────────────

def client_for(tmp_path, spawn=FakePty, shells=lambda: [FAKE]):
    hub = IdeHub(TOKEN, lambda name: ProviderSession("mock", responder=lambda m, msgs: "done"),
                 home=tmp_path / "home", provider="mock")
    hub.terminals = TerminalManager(spawn=spawn, shells=shells)
    client = TestClient(create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub),
                        base_url="http://127.0.0.1:8765")
    project = tmp_path / "project"
    project.mkdir()
    assert client.post("/api/ide/open", json={"path": str(project)}, headers=AUTH).status_code == 200
    return client, hub


def path_for(terminal_id):
    return f"/api/ide/terminals/{terminal_id}/ws"


@pytest.mark.parametrize("method,url", [("get", "/api/ide/shells"), ("get", "/api/ide/terminals"),
                                        ("post", "/api/ide/terminals"), ("delete", "/api/ide/terminals/x"),
                                        ("post", "/api/ide/terminals/x/rename")])
def test_terminal_routes_need_the_token(tmp_path, method, url):
    client, _ = client_for(tmp_path)
    assert getattr(client, method)(url, headers=LOCAL).status_code == 401


def test_terminal_routes_refuse_other_sites(tmp_path):
    client, _ = client_for(tmp_path)
    bad = {**AUTH, "origin": "https://evil.example"}
    assert client.post("/api/ide/terminals", json={}, headers=bad).status_code == 403


@needs_pty
def test_create_list_rename_and_kill(tmp_path):
    client, hub = client_for(tmp_path)
    made = client.post("/api/ide/terminals", json={}, headers=AUTH)
    assert made.status_code == 201 and made.json()["shell"] == "fake"
    tid = made.json()["id"]
    renamed = client.post(f"/api/ide/terminals/{tid}/rename", json={"name": "server"}, headers=AUTH)
    assert renamed.json()["name"] == "server"
    assert [t["id"] for t in client.get("/api/ide/terminals", headers=AUTH).json()["terminals"]] == [tid]
    assert client.delete(f"/api/ide/terminals/{tid}", headers=AUTH).json() == {"killed": True}
    assert client.delete(f"/api/ide/terminals/{tid}", headers=AUTH).status_code == 404


@needs_pty
def test_a_shell_id_the_machine_lacks_is_refused(tmp_path):
    client, _ = client_for(tmp_path)
    assert client.post("/api/ide/terminals", json={"shell": "cmd.exe /c evil"}, headers=AUTH).status_code == 422


def test_opening_a_folder_and_closing_end_every_terminal(tmp_path):
    client, hub = client_for(tmp_path)
    terminal = hub.terminals.create(None, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    client.post("/api/ide/open", json={"path": str(other)}, headers=AUTH)
    assert not terminal.pty.running and hub.terminals.list() == []
    terminal = hub.terminals.create(None, tmp_path)
    hub.close()
    assert not terminal.pty.running


def test_socket_refuses_a_bad_token_before_sending_anything(tmp_path):
    client, hub = client_for(tmp_path)
    terminal = hub.terminals.create(None, tmp_path)
    terminal.pty.feed(b"secret output")
    assert wait_for(lambda: terminal.total > 0)
    with client.websocket_connect(path_for(terminal.id), headers=LOCAL) as ws:
        ws.send_json({"token": "wrong"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 4401
    assert terminal.sink is None
    hub.close()


def test_socket_refuses_a_late_token(tmp_path, monkeypatch):
    monkeypatch.setattr("puppeteer_code.assistant.ide.AUTH_TIMEOUT", 0.05)
    client, hub = client_for(tmp_path)
    terminal = hub.terminals.create(None, tmp_path)
    with client.websocket_connect(path_for(terminal.id), headers=LOCAL) as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 4401
    hub.close()


def test_socket_refuses_a_foreign_origin(tmp_path):
    client, hub = client_for(tmp_path)
    terminal = hub.terminals.create(None, tmp_path)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(path_for(terminal.id), headers={**LOCAL, "origin": "https://evil.example"}) as ws:
            ws.send_json({"token": TOKEN})
            ws.receive_bytes()
    assert closed.value.code == 4403
    hub.close()


def test_socket_for_an_unknown_terminal(tmp_path):
    client, hub = client_for(tmp_path)
    with client.websocket_connect(path_for("nope"), headers=LOCAL) as ws:
        ws.send_json({"token": TOKEN})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 4404


def test_socket_replays_streams_and_takes_input(tmp_path):
    client, hub = client_for(tmp_path)
    terminal = hub.terminals.create(None, tmp_path)
    terminal.pty.feed(b"before")
    assert wait_for(lambda: terminal.total == 6)
    with client.websocket_connect(path_for(terminal.id), headers=LOCAL) as ws:
        ws.send_json({"token": TOKEN})
        assert ws.receive_json()["t"] == "attached"
        assert ws.receive_bytes() == b"before"
        terminal.pty.feed(b"after")
        assert ws.receive_bytes() == b"after"
        ws.send_bytes(b"typed")
        assert wait_for(lambda: terminal.pty.written == [b"typed"])
        ws.send_json({"t": "resize", "cols": 120, "rows": 40})
        assert wait_for(lambda: terminal.pty.sizes == [(40, 120)])
        terminal.pty.stop()
        assert ws.receive_json() == {"t": "exit", "code": 3}
    hub.close()


# ── a real shell on this OS ──────────────────────────────────────────────────

REAL = T.Shell("real", "Real", "terminal", (os.environ.get("ComSpec", "cmd.exe"),) if os.name == "nt" else ("/bin/sh",), True)


def real_manager():
    return TerminalManager(shells=lambda: [REAL])


def output(terminal):
    return bytes(terminal.ring).decode("utf-8", "replace")


@needs_pty
def test_real_shell_runs_a_command_in_the_project_and_reports_its_size(tmp_path):
    manager = real_manager()
    folder = tmp_path / "a folder"
    folder.mkdir()
    terminal = manager.create(None, folder, 100, 30)
    try:
        assert wait_for(lambda: terminal.total > 0, 15)
        terminal.write(b"echo pcode-%CD%\r\n" if os.name == "nt" else b"echo pcode-$PWD\n")
        assert wait_for(lambda: f"pcode-{folder}" in output(terminal).replace("\r\n", ""), 15)
    finally:
        manager.close_all()


@needs_pty
def test_real_shell_exit_code_is_reported(tmp_path):
    manager = real_manager()
    terminal = manager.create(None, tmp_path)
    assert wait_for(lambda: terminal.total > 0, 15)
    terminal.write(b"exit 7\r\n" if os.name == "nt" else b"exit 7\n")
    assert wait_for(terminal.exited.is_set, 15)
    assert terminal.exit_code == 7
    manager.close_all()


@needs_pty
def test_real_shell_has_no_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "must-not-leak")
    monkeypatch.setenv("KEEP_ME", "still-here")
    manager = real_manager()
    terminal = manager.create(None, tmp_path)
    try:
        assert wait_for(lambda: terminal.total > 0, 15)
        terminal.write(b"echo [%NEBIUS_API_KEY%][%KEEP_ME%]\r\n" if os.name == "nt" else b'echo "[$NEBIUS_API_KEY][$KEEP_ME]"\n')
        assert wait_for(lambda: "still-here]" in output(terminal), 15)
        assert "must-not-leak" not in output(terminal)
    finally:
        manager.close_all()


@needs_pty
def test_killing_a_terminal_kills_what_it_started(tmp_path):
    import psutil

    manager = real_manager()
    terminal = manager.create(None, tmp_path)
    assert wait_for(lambda: terminal.total > 0, 15)
    terminal.write(b"ping -t 127.0.0.1\r\n" if os.name == "nt" else b"sleep 300\n")
    root = psutil.Process(terminal.pty.pid)
    assert wait_for(lambda: root.children(recursive=True), 15)
    family = root.children(recursive=True) + [root]
    manager.close_all()
    assert wait_for(lambda: not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in family), 5)


@needs_pty
def test_ctrl_c_interrupts_a_program_even_if_the_server_was_started_ignoring_it(tmp_path):
    import psutil

    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)    # what a background launch leaves behind
    manager = real_manager()
    terminal = manager.create(None, tmp_path)
    try:
        assert wait_for(lambda: terminal.total > 0, 15)
        terminal.write(b"ping -t 127.0.0.1\r\n" if os.name == "nt" else b"sleep 300\n")
        root = psutil.Process(terminal.pty.pid)
        assert wait_for(lambda: root.children(recursive=True), 15)
        if os.name == "nt":
            assert wait_for(lambda: output(terminal).count("127.0.0.1") >= 3, 15)
        terminal.write(b"\x03")
        assert wait_for(lambda: not root.children(recursive=True), 10)
    finally:
        manager.close_all()
        if os.name == "nt":
            ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
