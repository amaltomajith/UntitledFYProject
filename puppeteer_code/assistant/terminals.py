"""
puppeteer_code/assistant/terminals.py
=====================================
Real shells for the browser IDE: PowerShell, cmd, Git Bash, WSL, bash, zsh and so on.

Each terminal owns a pseudo-terminal and a reader thread. Output goes into a ring buffer
with an absolute byte offset, so a page reload reattaches and replays instead of killing
the shell. One browser tab is attached at a time; the last to attach wins.

A shell runs as the user with the user's permissions. Nothing here is a sandbox, and the
assistant's permission rules do not apply to what the user types.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from starlette.websockets import WebSocketDisconnect

from puppeteer_code.assistant.setup import PROVIDER_KEYS

MAX_TERMINALS = 8
MAX_RING = 1_000_000
HIGH_WATER = 100_000
LOW_WATER = 5_000
BATCH_SECONDS = 0.005
MAX_FRAME = 64_000
LIST_TIMEOUT = 3
MIN_COLS, MAX_COLS = 2, 500
MIN_ROWS, MAX_ROWS = 1, 200
TAKEN_OVER = 4409
SKIP_DISTROS = ("docker-desktop",)
INSTALL_HINT = {"nt": "pip install pywinpty psutil", "posix": "pip install ptyprocess psutil"}


@dataclass(frozen=True)
class Shell:
    id: str
    name: str
    icon: str
    argv: tuple[str, ...]
    default: bool = False

    def public(self) -> dict:
        row = asdict(self)
        del row["argv"]     # the browser picks a shell by id and never supplies a command line
        return row


# ── shell detection ──────────────────────────────────────────────────────────

def _first(paths, exists) -> Optional[str]:
    return next((p for p in paths if exists(p)), None)


def _wsl_distros(wsl: str, env: dict, run) -> list[str]:
    """`wsl -l -q` prints UTF-16 unless told otherwise, and an unconfigured WSL can hang."""
    try:
        done = run([wsl, "-l", "-q"], env={**env, "WSL_UTF8": "0"}, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    names = done.stdout.decode("utf-16-le", "ignore").replace("\ufeff", "").replace("\0", "").split()
    return [n for n in names if not n.startswith(SKIP_DISTROS)]


def windows_shells(env: dict, which=shutil.which, exists=os.path.isfile, run=subprocess.run) -> list[Shell]:
    env = {k.upper(): v for k, v in env.items()}        # os.environ upper-cases its keys on Windows
    system = Path(env.get("SYSTEMROOT") or r"C:\Windows") / "System32"
    programs = [env.get(k) for k in ("PROGRAMW6432", "PROGRAMFILES", "PROGRAMFILES(X86)")]
    programs = [p for p in dict.fromkeys(programs) if p]
    shells = []

    pwsh = which("pwsh.exe") or _first([str(Path(p) / "PowerShell" / "7" / "pwsh.exe") for p in programs], exists)
    if pwsh:
        shells.append(Shell("pwsh", "PowerShell 7", "powershell", (pwsh, "-NoLogo")))
    legacy = str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    if exists(legacy):
        shells.append(Shell("powershell", "PowerShell", "powershell", (legacy, "-NoLogo")))
    cmd = str(system / "cmd.exe")
    if exists(cmd):
        shells.append(Shell("cmd", "Command Prompt", "cmd", (cmd,)))

    git = which("git.exe")
    # git.exe sits in Git\cmd, or in Git\mingw64\bin when started from Git Bash.
    roots = [str(Path(git).parents[i]) for i in (1, 2)] if git else []
    roots += [str(Path(p) / "Git") for p in programs]
    if env.get("LOCALAPPDATA"):
        roots.append(str(Path(env["LOCALAPPDATA"]) / "Programs" / "Git"))
    bash = _first([str(Path(r) / "bin" / "bash.exe") for r in roots], exists)
    if bash:
        shells.append(Shell("git-bash", "Git Bash", "git-bash", (bash, "--login", "-i")))

    wsl = str(system / "wsl.exe")
    if exists(wsl):
        for distro in _wsl_distros(wsl, env, run):
            shells.append(Shell(f"wsl-{distro}", f"{distro} (WSL)", "linux", (wsl, "-d", distro)))

    return [Shell(s.id, s.name, s.icon, s.argv, i == 0) for i, s in enumerate(shells)]


def unix_shells(env: dict, listing: str, exists=os.path.isfile) -> list[Shell]:
    """`listing` is the text of /etc/shells."""
    paths = [line.strip() for line in listing.splitlines() if line.strip().startswith("/")]
    login = env.get("SHELL")
    if login and login not in paths:
        paths.insert(0, login)
    seen, shells = set(), []
    for path in paths:
        name = Path(path).name
        if name in seen or name in ("nologin", "false", "git-shell") or not exists(path):
            continue
        seen.add(name)
        icon = name if name in ("bash", "zsh", "fish") else "terminal"
        shells.append(Shell(name, name, icon, (path, "-l") if icon != "terminal" else (path,), path == login))
    if shells and not any(s.default for s in shells):
        shells[0] = Shell(shells[0].id, shells[0].name, shells[0].icon, shells[0].argv, True)
    return shells


def detect_shells() -> list[Shell]:
    if os.name == "nt":
        return windows_shells(dict(os.environ))
    try:
        listing = Path("/etc/shells").read_text(encoding="utf-8", errors="replace")
    except OSError:
        listing = ""
    return unix_shells(dict(os.environ), listing)


def shell_env(environ: Optional[dict] = None) -> dict:
    """The user's own environment without the keys pcode loaded. Hygiene, not a boundary.

    Not `scrubbed_env()`: that is for code nobody has read, and it also drops `GITHUB_TOKEN`
    and the like, which the user's own tools need in their own terminal.
    """
    blocked = {name.upper() for name in PROVIDER_KEYS.values()}
    env = {k: v for k, v in (os.environ if environ is None else environ).items()
           if not (k.upper() in blocked or k.upper().endswith("_API_KEY") or k.upper().startswith("PCODE"))}
    env.update(TERM="xterm-256color", COLORTERM="truecolor")
    return env


# ── pseudo-terminals ─────────────────────────────────────────────────────────

def _kill_tree(pid: int) -> None:
    import psutil

    try:
        root = psutil.Process(pid)
        family = root.children(recursive=True) + [root]
    except psutil.Error:
        return
    for proc in family:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(family, timeout=2)


class WindowsPty:
    def __init__(self, argv, cwd, env, rows, cols):
        import ctypes

        from winpty import PtyProcess

        # A server started in the background passes "ignore Ctrl+C" on to its children, so a
        # shell could never interrupt anything.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
        self.proc = PtyProcess.spawn(list(argv), cwd=cwd, env=env, dimensions=(rows, cols))
        self.pid = self.proc.pid

    def read(self) -> bytes:
        return self.proc.read(MAX_FRAME).encode("utf-8", "replace")

    def write(self, data: bytes) -> None:
        self.proc.write(data.decode("utf-8", "replace"))

    def resize(self, rows: int, cols: int) -> None:
        self.proc.setwinsize(rows, cols)

    def alive(self) -> bool:
        return self.proc.isalive()

    def exit_code(self) -> Optional[int]:
        return self.proc.exitstatus

    def kill(self) -> None:
        _kill_tree(self.pid)

    def close(self) -> None:
        self.proc.close(force=True)


class UnixPty:
    def __init__(self, argv, cwd, env, rows, cols):
        from ptyprocess import PtyProcess

        self.proc = PtyProcess.spawn(list(argv), cwd=cwd, env=env, dimensions=(rows, cols))
        self.pid = self.proc.pid

    def read(self) -> bytes:
        return self.proc.read(MAX_FRAME)

    def write(self, data: bytes) -> None:
        self.proc.write(data)

    def resize(self, rows: int, cols: int) -> None:
        self.proc.setwinsize(rows, cols)

    def alive(self) -> bool:
        return self.proc.isalive()

    def exit_code(self) -> Optional[int]:
        return self.proc.exitstatus

    def kill(self) -> None:
        import signal

        for sig in (signal.SIGHUP, signal.SIGCONT):
            try:
                os.killpg(self.pid, sig)
            except OSError:
                return
        deadline = time.monotonic() + 1
        while self.alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        _kill_tree(self.pid)

    def close(self) -> None:
        self.proc.close(force=True)


def spawn_pty(argv, cwd, env, rows, cols):
    return (WindowsPty if os.name == "nt" else UnixPty)(argv, cwd, env, rows, cols)


def pty_missing() -> Optional[str]:
    """The install command when the optional packages are absent, otherwise None."""
    try:
        import psutil  # noqa: F401
        if os.name == "nt":
            import winpty  # noqa: F401
        else:
            import ptyprocess  # noqa: F401
    except ImportError:
        return INSTALL_HINT.get(os.name, INSTALL_HINT["posix"])
    return None


class Terminal:
    def __init__(self, shell: Shell, pty, cols: int, rows: int):
        self.id = uuid.uuid4().hex[:12]
        self.shell = shell
        self.name = shell.name
        self.pty = pty
        self.cols = cols
        self.rows = rows
        self.ring = bytearray()
        self.total = 0                      # bytes ever produced; ring[-1] is byte total - 1
        self.exit_code: Optional[int] = None
        self.exited = threading.Event()
        self.reader_done = threading.Event()
        self.lock = threading.Lock()
        self.sink: Optional[Callable] = None
        self.unacked = 0
        self.can_read = threading.Event()
        self.can_read.set()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "shell": self.shell.id, "icon": self.shell.icon,
                "cols": self.cols, "rows": self.rows, "alive": not self.exited.is_set(),
                "exit_code": self.exit_code}

    def _read(self) -> None:
        while True:
            self.can_read.wait()
            try:
                data = self.pty.read()
            except (EOFError, OSError):
                break
            if data:
                self._take(data)
        self.reader_done.set()

    def _take(self, data: bytes) -> None:
        with self.lock:
            self.ring += data
            del self.ring[:-MAX_RING]
            self.total += len(data)
            if self.sink:
                self.sink(data)
                self.unacked += len(data)
                if self.unacked > HIGH_WATER:
                    self.can_read.clear()

    def _watch(self) -> None:
        # ConPTY does not always report EOF, so the process itself is watched as well.
        while self.pty.alive():
            time.sleep(0.2)
        self.can_read.set()
        self.reader_done.wait(0.5)
        try:
            self.pty.close()
        except (OSError, EOFError):
            pass
        with self.lock:
            self.exit_code = self.pty.exit_code()
            self.exited.set()
            if self.sink:
                self.sink({"t": "exit", "code": self.exit_code})

    def attach(self, sink: Callable, offset: int = 0) -> tuple[dict, bytes]:
        """Returns the header and the bytes from `offset`. A newer attach replaces the old one."""
        with self.lock:
            if self.sink:
                self.sink({"t": "taken"})
            self.sink = sink
            self.unacked = 0
            self.can_read.set()
            start = self.total - len(self.ring)
            at = max(start, min(offset, self.total))
            replay = bytes(self.ring[at - start:])
            header = {"t": "attached", "offset": at, "reset": at != offset, "replay": len(replay),
                      "cols": self.cols, "rows": self.rows}
            if self.exited.is_set():
                sink({"t": "exit", "code": self.exit_code})
            return header, replay

    def detach(self, sink: Callable) -> None:
        with self.lock:
            if self.sink is sink:
                self.sink = None
                self.unacked = 0
                self.can_read.set()

    def ack(self, count: int) -> None:
        with self.lock:
            self.unacked = max(0, self.unacked - count)
            if self.unacked < LOW_WATER:
                self.can_read.set()

    def write(self, data: bytes) -> None:
        if not self.exited.is_set():
            try:
                self.pty.write(data)
            except (EOFError, OSError):
                pass

    def resize(self, cols: int, rows: int) -> None:
        cols = min(MAX_COLS, max(MIN_COLS, int(cols)))
        rows = min(MAX_ROWS, max(MIN_ROWS, int(rows)))
        if (cols, rows) == (self.cols, self.rows) or self.exited.is_set():
            return
        self.cols, self.rows = cols, rows
        try:
            self.pty.resize(rows, cols)
        except (EOFError, OSError):
            pass

    def kill(self) -> None:
        self.can_read.set()         # keep draining: closing a full ConPTY can hang
        if not self.exited.is_set():
            self.pty.kill()
        self.exited.wait(3)


class TerminalLimit(Exception):
    pass


class TerminalManager:
    def __init__(self, spawn=spawn_pty, shells=detect_shells):
        self.spawn = spawn
        self.find_shells = shells
        self.terminals: dict[str, Terminal] = {}
        self.lock = threading.Lock()
        self._shells: Optional[list[Shell]] = None

    def shells(self) -> list[Shell]:
        if self._shells is None:        # detection starts wsl.exe, so it runs once
            self._shells = self.find_shells()
        return self._shells

    def create(self, shell_id: Optional[str], cwd: Path, cols: int = 80, rows: int = 24) -> Terminal:
        shells = self.shells()
        shell = next((s for s in shells if s.id == shell_id), None) if shell_id else None
        if shell_id and shell is None:
            raise KeyError(shell_id)
        shell = shell or next((s for s in shells if s.default), None)
        if shell is None:
            raise KeyError("default")
        with self.lock:
            for done in [t for t in self.terminals.values() if t.exited.is_set()]:
                del self.terminals[done.id]
            if len(self.terminals) >= MAX_TERMINALS:
                raise TerminalLimit()
            cols = min(MAX_COLS, max(MIN_COLS, cols))
            rows = min(MAX_ROWS, max(MIN_ROWS, rows))
            argv = shell.argv + ("--cd", str(cwd)) if shell.id.startswith("wsl-") else shell.argv
            terminal = Terminal(shell, self.spawn(argv, str(cwd), shell_env(), rows, cols), cols, rows)
            self.terminals[terminal.id] = terminal
            return terminal

    def get(self, terminal_id: str) -> Optional[Terminal]:
        return self.terminals.get(terminal_id)

    def list(self) -> list[dict]:
        return [t.public() for t in list(self.terminals.values())]

    def remove(self, terminal_id: str) -> bool:
        with self.lock:
            terminal = self.terminals.pop(terminal_id, None)
        if terminal is None:
            return False
        terminal.kill()
        return True

    def close_all(self) -> None:
        for terminal_id in list(self.terminals):
            self.remove(terminal_id)


async def bridge(ws, terminal: Terminal, offset: int) -> None:
    """Binary frames carry terminal bytes both ways; text frames carry resize, ack and exit."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sink = lambda item: loop.call_soon_threadsafe(queue.put_nowait, item)   # noqa: E731
    header, replay = terminal.attach(sink, offset)

    async def writer():
        await ws.send_text(json.dumps(header))
        if replay:
            await ws.send_bytes(replay)
        held = None
        while True:
            item = held if held is not None else await queue.get()
            held = None
            if isinstance(item, dict):
                if item["t"] == "taken":
                    await ws.close(TAKEN_OVER)
                    return
                await ws.send_text(json.dumps(item))
                continue
            chunk = bytearray(item)
            await asyncio.sleep(BATCH_SECONDS)
            while len(chunk) < MAX_FRAME and not queue.empty():
                following = queue.get_nowait()
                if isinstance(following, dict):
                    held = following
                    break
                chunk += following
            await ws.send_bytes(bytes(chunk))

    sender = asyncio.create_task(writer())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await loop.run_in_executor(None, terminal.write, message["bytes"])
            elif message.get("text"):
                _control(terminal, message["text"])
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        terminal.detach(sink)


def _control(terminal: Terminal, text: str) -> None:
    try:
        message = json.loads(text)
        if message.get("t") == "resize":
            terminal.resize(message["cols"], message["rows"])
        elif message.get("t") == "ack":
            terminal.ack(int(message["n"]))
    except (ValueError, KeyError, TypeError, AttributeError):
        pass


def windows_build() -> Optional[int]:
    return sys.getwindowsversion().build if os.name == "nt" else None
