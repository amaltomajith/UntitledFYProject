"""
puppeteer_code/sandbox.py
=========================
How commands written by a model are started. Everything that runs project code
goes through a runner, so private mode can swap in one with no network.

The block stops sending, not reading: on a drive with no permissions (exFAT) a
sandboxed command can still read and delete everything on that drive. Nothing is
called contained until the probe has passed, inside the container, on this machine.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from puppeteer_code import appcontainer

LAUNCHER = str(Path(appcontainer.__file__).resolve())
STATE_FILE = "private.json"
PROBE_SECONDS = 20
READ = "(RX)"
READ_TREE = "(OI)(CI)(RX)"
CHANGE_TREE = "(OI)(CI)(M)"
_PYTHON_FILES = (".exe", ".dll", ".zip", ".cfg", ".txt")
_PYTHON_TREES = ("DLLs", "Lib", r"Library\bin")
BLOCKED = 10013         # WSAEACCES: Windows itself refused the socket

# Runs inside the container and reports what it could reach. It must not import anything from a project.
PROBE = r"""
import ctypes, json, socket, sys
from ctypes import wintypes as w
k = ctypes.WinDLL("kernel32"); a = ctypes.WinDLL("advapi32")
k.GetCurrentProcess.restype = w.HANDLE
a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
a.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]
t = w.HANDLE(); f = w.DWORD(); n = w.DWORD()
a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(t))
a.GetTokenInformation(t, 29, ctypes.byref(f), 4, ctypes.byref(n))
def tcp(host, port, family=socket.AF_INET):
    s = socket.socket(family); s.settimeout(1)
    try:
        s.connect((host, port)); return "open"
    except OSError as e:
        return getattr(e, "winerror", None) or "closed"
    finally:
        s.close()
def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(b"inside", (sys.argv[2], int(sys.argv[3])))
    except OSError:
        pass
    finally:
        s.close()
udp()
print(json.dumps({"container": bool(f.value), "tcp": tcp("192.0.2.1", 80),
                  "tcp6": tcp("2001:db8::1", 80, socket.AF_INET6), "loopback": tcp("127.0.0.1", int(sys.argv[1]))}))
"""


def _lan_address() -> str:
    """This machine's address on its network. Connecting a UDP socket picks it and sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 53))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _heard_from_inside(canary: socket.socket) -> bool:
    try:
        while True:
            if canary.recv(16) == b"inside":
                return True
    except OSError:
        return False


class DirectRunner:
    """Runs a command on the machine with the user's own permissions and network."""

    name = "direct"
    contained = False
    scratch: Optional[str] = None       # where a Fix mode copy must live for this runner to reach it

    def popen(self, command, **kwargs) -> subprocess.Popen:
        return subprocess.Popen(command, **kwargs)

    def run(self, command, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(command, **kwargs)

    def adapt(self, command: str) -> str:
        """The form of a check command that works under this runner."""
        return command

    def prepare(self, path: str | Path) -> None:
        """Lets this runner reach a folder."""

    def warning(self) -> str:
        return ""

    def verify(self) -> bool:
        return self.contained


def _grant(path: str, sid: str, spec: str) -> None:
    done = subprocess.run(["icacls", path, "/grant", f"*{sid}:{spec}", "/Q"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, errors="replace")
    if done.returncode != 0:
        raise OSError(f"could not give the sandbox access to {path}: {done.stdout.strip()[-200:]}")


def _revoke(path: str, sid: str) -> None:
    subprocess.run(["icacls", path, "/remove", f"*{sid}", "/Q"], stdin=subprocess.DEVNULL, capture_output=True)


class AppContainerRunner(DirectRunner):
    name = "appcontainer"

    def __init__(self, state: dict, project: str | Path):
        self.state = state
        self.project = Path(project).resolve()
        self.folder = Path(state["folder"])
        self.scratch = str(self.folder / "work")
        self.file_system = appcontainer.filesystem(str(self.project))
        self.prepare(self.project)

    def _env(self, env: Optional[dict]) -> dict:
        from puppeteer_code.workspace import scrubbed_env

        out = dict(scrubbed_env() if env is None else env)
        home = self.folder / "home"
        out.update(PYTHONNOUSERSITE="1", HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "Roaming"),
                   LOCALAPPDATA=str(home / "Local"), npm_config_cache=str(home / "npm"),
                   GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="safe.directory", GIT_CONFIG_VALUE_0="*")
        if self.state.get("node"):
            out["PATH"] = self.state["node"] + os.pathsep + out.get("PATH", "")
            # Node stats every parent folder of a script, and the container may not look at C:\ itself.
            out["NODE_OPTIONS"] = "--preserve-symlinks-main --preserve-symlinks"
        if self.file_system != "NTFS":
            # Python makes temp folders only their owner can open. That is enforced on NTFS, and the
            # container's own temp folder is on NTFS, so temp files go on a drive with no permissions.
            out["TMPDIR"] = str(Path(self.project.anchor) / ".pcode-tmp")
        return out

    def _wrap(self, command, kwargs: dict) -> list[str]:
        shell = kwargs.pop("shell", False)
        kwargs["env"] = self._env(kwargs.get("env"))
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        if shell:
            line = f'cmd.exe /d /s /c "{command}"'
        else:
            line = command if isinstance(command, str) else subprocess.list2cmdline(command)
        return [sys.executable, "-I", LAUNCHER, "--", line]

    def popen(self, command, **kwargs) -> subprocess.Popen:
        return subprocess.Popen(self._wrap(command, kwargs), **kwargs)

    def run(self, command, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(self._wrap(command, kwargs), **kwargs)

    def adapt(self, command: str) -> str:
        # Child processes with stdio pipes hang in a container (Node's named pipes are refused).
        return command.replace("node --test", "node --test --experimental-test-isolation=none")

    def prepare(self, path: str | Path) -> None:
        if appcontainer.filesystem(str(path)) == "NTFS":
            _grant(str(path), self.state["sid"], CHANGE_TREE)
        elif "TMPDIR" not in os.environ:
            (Path(Path(path).anchor) / ".pcode-tmp").mkdir(exist_ok=True)

    def warning(self) -> str:
        if self.file_system == "NTFS":
            return ("pytest cannot start in the sandbox on this drive, because commands may not look at the folders "
                    "above this one, so a failed Python check here may not be a real failure.")
        drive = self.project.anchor.rstrip("\\")
        return (f"{drive} has no file permissions, so commands can read and delete everything on it, "
                "including .env files. They still cannot send it anywhere.")

    def probe(self) -> dict:
        canary = socket.socket()
        canary.bind(("127.0.0.1", 0))
        canary.listen(1)
        canary.settimeout(0.1)
        reached = threading.Event()
        # A UDP send reports success even when the packet is dropped, so only arrival counts. The send
        # from out here proves the listener can hear, or silence from inside would prove nothing.
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind((_lan_address(), 0))
        listener.settimeout(0.5)
        host, port = listener.getsockname()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as outside:
            outside.sendto(b"outside", (host, port))
        try:
            control = listener.recv(16) == b"outside"
        except OSError:
            control = False

        def watch():
            try:
                canary.accept()
                reached.set()
            except OSError:
                pass

        threading.Thread(target=watch, daemon=True).start()
        try:
            done = self.run([self.state["python"], "-I", "-c", PROBE, str(canary.getsockname()[1]), host, str(port)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                            timeout=PROBE_SECONDS, cwd=self.folder)
            report = json.loads(done.stdout.strip().splitlines()[-1])
            return {**report, "reached": reached.is_set(), "udp_control": control,
                    "udp_reached": _heard_from_inside(listener)}
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return {"container": False}
        finally:
            canary.close()
            listener.close()

    def verify(self) -> bool:
        """The token says container, Windows refuses sockets, and a listener on this machine saw nothing."""
        report = self.probe()
        self.contained = (report.get("container") is True and report.get("tcp") == BLOCKED
                          and report.get("udp_control") is True and report.get("udp_reached") is False
                          and report.get("tcp6") != "open"
                          and report.get("loopback") != "open" and not report.get("reached"))
        return self.contained


def _state_path(home: Optional[Path]) -> Path:
    from puppeteer_code.assistant.snapshots import pcode_home
    return Path(home or pcode_home()) / STATE_FILE


def load_state(home: Optional[Path] = None) -> Optional[dict]:
    try:
        return json.loads(_state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def private_runner(project: str | Path, home: Optional[Path] = None) -> DirectRunner:
    """The block when `pcode private-setup` has run and the probe passes; otherwise commands are not sandboxed."""
    state = load_state(home)
    if sys.platform != "win32" or state is None:
        return DirectRunner()
    try:
        runner = AppContainerRunner(state, project)
    except OSError:
        return DirectRunner()
    return runner if runner.verify() else DirectRunner()


def _python_bases() -> list[str]:
    """Where the interpreters commands will use really live, since a venv only points at one."""
    bases = [sys.base_prefix]
    python = shutil.which("python")
    if python:
        done = subprocess.run([python, "-I", "-c", "import sys; print(sys.base_prefix)"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, errors="replace")
        if done.returncode == 0 and done.stdout.strip():
            bases.append(done.stdout.strip())
    return list(dict.fromkeys(os.path.normcase(b) for b in bases))


def _grant_python(base: str, sid: str, say: Callable[[str], None]) -> list[str]:
    """Read access to the runtime only, not to the packages or environments kept beside it."""
    say(f"Giving the sandbox read access to Python in {base}")
    paths = [base]
    _grant(base, sid, READ)
    for entry in os.scandir(base):
        if entry.is_file() and entry.name.lower().endswith(_PYTHON_FILES):
            _grant(entry.path, sid, READ)
            paths.append(entry.path)
    for tree in _PYTHON_TREES:
        full = Path(base) / tree
        if full.is_dir():
            if tree != full.name:
                _grant(str(full.parent), sid, READ)
                paths.append(str(full.parent))
            _grant(str(full), sid, READ_TREE)
            paths.append(str(full))
    return paths


def _copy_node(folder: Path, say: Callable[[str], None]) -> str:
    """Program Files needs an admin to change, so the container gets its own copy of Node."""
    node = shutil.which("node")
    if not node:
        return ""
    target = folder / "tools" / "nodejs"
    if not target.exists():
        say(f"Copying Node into the sandbox from {Path(node).parent}")
        shutil.copytree(Path(node).parent, target)
    return str(target)


def setup_private(home: Optional[Path] = None, say: Callable[[str], None] = lambda text: None) -> dict:
    """Creates the profile and the narrow read grants. No admin needed. Safe to run twice."""
    if sys.platform != "win32":
        raise OSError("The sandbox is Windows only for now. On this system Private says commands are not sandboxed.")
    sid = appcontainer.sid_text()
    folder = Path(appcontainer.container_folder())
    granted: list[str] = []
    for base in _python_bases():
        granted += _grant_python(base, sid, say)
    state = {"sid": sid, "folder": str(folder), "python": str(Path(sys.base_prefix) / "python.exe"),
             "node": _copy_node(folder, say), "granted": granted}
    for name in ("home", "work"):
        (folder / name).mkdir(exist_ok=True)
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    return state


def remove_private(home: Optional[Path] = None, say: Callable[[str], None] = lambda text: None) -> None:
    """Undoes setup. Project folders on NTFS keep their grant until removed by hand."""
    state = load_state(home)
    if state is None:
        return
    for path in state["granted"]:
        _revoke(path, state["sid"])
    say(f"Removed the sandbox's access to {len(state['granted'])} paths")
    appcontainer.delete_profile()
    _state_path(home).unlink()


__all__ = ["AppContainerRunner", "DirectRunner", "load_state", "private_runner", "remove_private", "setup_private"]
