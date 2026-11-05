"""
Update checks and one-click updates.

    state()           what is known now, from the cache (no network)
    check(force)      ask GitHub for the latest release; automatic checks at most once a day
    notice()          one line for the terminal, from the cache; refreshes a stale cache in the background
    apply(...)        download and verify the new wheel, then hand over to a helper that installs it after this
                      process exits (Windows locks a running compactcode.exe), and relaunches CompactCode

Nothing is ever installed without the user asking: the IDE's "Update and restart" or `compactcode update`.

What leaves the machine: one HTTPS GET to the GitHub releases API (and, on update, downloads from GitHub), with a
User-Agent naming the CompactCode version. No project data, keys or identifiers. Automatic checks are off in Private
mode, and `COMPACTCODE_NO_UPDATE_CHECK=1` turns them off everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from puppeteer_code import __version__, paths

REPO = "amaltomajith/UntitledFYProject"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600
TIMEOUT = 3.0
ENV_URL = "COMPACTCODE_UPDATE_URL"          # tests and staging: a URL serving GitHub-release-shaped JSON
ENV_OFF = "COMPACTCODE_NO_UPDATE_CHECK"
UPDATE_COMMAND = "compactcode update"


class UpdateError(Exception):
    """A refusal or failure with a message fit to show the user as is."""


# ── versions ─────────────────────────────────────────────────────────────────

def parse_version(text: str) -> Optional[tuple[int, ...]]:
    """'v0.2.0' -> (0, 2, 0). Pre-releases ('0.3.0rc1', '0.3.0-beta') return None and are never offered."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# ── cache ────────────────────────────────────────────────────────────────────

def _home(home: Optional[Path]) -> Path:
    return Path(home) if home else Path.home() / ".pcode"


def _cache_path(home: Optional[Path]) -> Path:
    return _home(home) / "update.json"


def _load(home: Optional[Path]) -> dict:
    try:
        data = json.loads(_cache_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict, home: Optional[Path]) -> None:
    try:
        path = _cache_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass                                   # a check that cannot be cached is simply repeated later


def auto_enabled(home: Optional[Path] = None) -> bool:
    return not os.environ.get(ENV_OFF) and _load(home).get("auto", True) is not False


def set_auto(on: bool, home: Optional[Path] = None) -> dict:
    data = _load(home)
    data["auto"] = bool(on)
    _save(data, home)
    return state(home)


# ── what kind of install is this ─────────────────────────────────────────────

def install_kind(prefix: Optional[str] = None, root: Path = paths.ROOT) -> str:
    """'source' (a git checkout: update with git pull), 'uv-tool' (the installer's), or 'pip'."""
    if paths.is_source_checkout(root):
        return "source"
    parts = [p.lower() for p in Path(prefix or sys.prefix).parts]
    if "uv" in parts and "tools" in parts:
        return "uv-tool"
    tool_dir = os.environ.get("UV_TOOL_DIR")
    if tool_dir and Path(prefix or sys.prefix).resolve().is_relative_to(Path(tool_dir).resolve()):
        return "uv-tool"
    return "pip"


# ── the release feed ─────────────────────────────────────────────────────────

def _get(url: str, timeout: float = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"compactcode/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 (fixed https or test URL)
        return r.read()


def fetch(url: Optional[str] = None, timeout: float = TIMEOUT) -> dict:
    """The latest release, as {version, notes, url, wheel_url, sums_url, published}."""
    d = json.loads(_get(url or os.environ.get(ENV_URL) or RELEASES_API, timeout).decode("utf-8"))
    if d.get("draft") or d.get("prerelease"):
        raise UpdateError("The latest release is not a final release.")
    version = str(d.get("tag_name", "")).lstrip("v")
    if parse_version(version) is None:
        raise UpdateError(f"The release has an unreadable version: {d.get('tag_name')!r}.")
    assets = {a.get("name", ""): a.get("browser_download_url", "") for a in d.get("assets") or []}
    wheel = next((u for n, u in assets.items() if n.startswith("compactcode-") and n.endswith(".whl")), None)
    return {"version": version, "notes": (d.get("body") or "")[:4000], "url": d.get("html_url") or "",
            "wheel_url": wheel, "sums_url": assets.get("SHA256SUMS"), "published": d.get("published_at")}


def state(home: Optional[Path] = None, private: bool = False) -> dict:
    data = _load(home)
    latest = data.get("latest")
    return {
        "current": __version__,
        "latest": latest,
        "available": bool(latest and is_newer(latest)),
        "notes": data.get("notes", ""),
        "url": data.get("url", ""),
        "checked_at": data.get("checked_at"),
        "error": data.get("error"),
        "auto": auto_enabled(home),
        "auto_blocked": "private" if private else ("env" if os.environ.get(ENV_OFF) else None),
        "kind": install_kind(),
    }


def _stale(data: dict, now: float) -> bool:
    checked = data.get("checked_at")
    return not isinstance(checked, (int, float)) or now - checked >= CHECK_EVERY or now < checked


def check(force: bool = False, home: Optional[Path] = None, private: bool = False,
          now: Optional[float] = None) -> dict:
    """Refresh from the release feed when asked (force) or when an automatic check is due and allowed."""
    now = time.time() if now is None else now
    data = _load(home)
    if not force and (private or not auto_enabled(home) or not _stale(data, now)):
        return state(home, private)
    try:
        rel = fetch()
        data.update(latest=rel["version"], notes=rel["notes"], url=rel["url"], wheel_url=rel["wheel_url"],
                    sums_url=rel["sums_url"], published=rel["published"], error=None)
    except UpdateError as e:
        data["error"] = str(e)
    except Exception as e:                     # offline, rate-limited, DNS: try again at the next due check
        data["error"] = f"Could not reach GitHub ({type(e).__name__})."
    data["checked_at"] = now
    _save(data, home)
    return state(home, private)


_refreshing = threading.Lock()


def refresh_in_background(home: Optional[Path] = None, private: bool = False, done=None) -> bool:
    """Start a due automatic check on a thread, unless one is running. `done(state)` runs after it."""
    if private or not auto_enabled(home) or not _stale(_load(home), time.time()):
        return False
    if not _refreshing.acquire(False):
        return False

    def run():
        try:
            s = check(home=home, private=private)
        finally:
            _refreshing.release()
        if done:
            done(s)
    threading.Thread(target=run, daemon=True, name="update-check").start()
    return True


def notice(home: Optional[Path] = None, private: bool = False) -> Optional[str]:
    """One line for the terminal from the cache. A due automatic check runs in the background, never blocking."""
    refresh_in_background(home, private)
    s = state(home, private)
    if not s["available"]:
        return None
    how = "git pull" if s["kind"] == "source" else UPDATE_COMMAND
    return f"CompactCode {s['latest']} is available (you have {s['current']}). Update: {how}"


# ── applying an update ───────────────────────────────────────────────────────

def download(home: Optional[Path] = None) -> Path:
    """Download the cached release's wheel and check it against SHA256SUMS. Returns the local wheel."""
    data = _load(home)
    url, sums_url = data.get("wheel_url"), data.get("sums_url")
    if not url or not sums_url:
        raise UpdateError("The release has no wheel or no SHA256SUMS, so it cannot be verified. Nothing was changed.")
    name = url.rsplit("/", 1)[-1]
    if not re.fullmatch(r"compactcode-[\w.]+-py3-none-any\.whl", name):
        raise UpdateError(f"Unexpected file name in the release: {name}.")
    sums = _get(sums_url, timeout=30).decode("utf-8", "replace")
    want = next((line.split()[0].lower() for line in sums.splitlines()
                 if line.strip().endswith(name) and line.split()), None)
    if not want:
        raise UpdateError(f"SHA256SUMS does not list {name}. Nothing was changed.")
    blob = _get(url, timeout=120)
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise UpdateError("The download does not match its published checksum. Nothing was changed.")
    folder = _home(home) / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(blob)
    return path


def find_uv() -> Optional[str]:
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"),
                      Path.home() / ".cargo" / "bin" / ("uv.exe" if os.name == "nt" else "uv")):
        if candidate.exists():
            return str(candidate)
    return None


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _sh_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def install_args(kind: str, wheel: Path, uv: Optional[str], python: str) -> list[str]:
    spec = f"compactcode[web] @ {wheel.resolve().as_uri()}"
    if kind == "uv-tool":
        if not uv:
            raise UpdateError("uv was not found, so the update cannot be installed. Rerun the installer.")
        return [uv, "tool", "install", "--force", "--python", "3.12", spec]
    return [python, "-m", "pip", "install", "--upgrade", spec]


def helper_command(install: list[str], wait_pids: list[int], log: Path, relaunch: Optional[list[str]],
                   windows: bool = os.name == "nt") -> list[str]:
    """A command that waits for these processes to exit, installs, logs, and optionally relaunches."""
    if windows:
        pids = ",".join(str(p) for p in wait_pids)
        run = "& " + " ".join(_ps_quote(a) for a in install)
        lines = [
            "$ErrorActionPreference = 'Continue'",
            f"'helper started, waiting for {pids}' | Out-File -Append -Encoding utf8 {_ps_quote(log)}",
            f"try {{ Wait-Process -Id {pids} -Timeout 120 -ErrorAction SilentlyContinue }} catch {{}}",
            "Start-Sleep -Milliseconds 500",
            # `*>>` would write UTF-16 in Windows PowerShell 5.1; the log is UTF-8.
            f"{run} *>&1 | ForEach-Object {{ \"$_\" }} | Out-File -Append -Encoding utf8 {_ps_quote(log)}",
            f"'exit code ' + $LASTEXITCODE | Out-File -Append -Encoding utf8 {_ps_quote(log)}",
        ]
        if relaunch:
            args = " ".join(_ps_quote(a) for a in relaunch[1:])
            lines.append(f"Start-Process -FilePath {_ps_quote(relaunch[0])}"
                         + (f" -ArgumentList {args}" if args else ""))
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "; ".join(lines)]
    wait = " ".join(f"while kill -0 {p} 2>/dev/null; do sleep 0.3; done;" for p in wait_pids)
    run = " ".join(_sh_quote(a) for a in install)
    script = f"{wait} {run} >> {_sh_quote(log)} 2>&1; echo \"exit code $?\" >> {_sh_quote(log)};"
    if relaunch:
        script += " nohup " + " ".join(_sh_quote(a) for a in relaunch) + f" >> {_sh_quote(log)} 2>&1 &"
    return ["/bin/sh", "-c", script]


BREAKAWAY = 0x01000000        # CREATE_BREAKAWAY_FROM_JOB


def spawn_detached(cmd: list[str], cwd: Path, popen=subprocess.Popen) -> None:
    """Start the helper so it outlives this process, even inside a terminal that kills its job when it closes."""
    kwargs: dict = {"cwd": str(cwd), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name != "nt":
        kwargs["start_new_session"] = True
        popen(cmd, **kwargs)
        return
    # A hidden console, not DETACHED_PROCESS: Windows PowerShell 5.1 with no console at all exits at once, doing
    # nothing, with exit code 0.
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        popen(cmd, creationflags=flags | BREAKAWAY, **kwargs)
    except OSError:              # the job forbids breakaway: start it inside, and hope the job outlives us
        popen(cmd, creationflags=flags, **kwargs)


LAUNCHERS = {"python.exe", "pythonw.exe", "compactcode.exe", "pcode.exe"}


def processes_to_wait_for() -> list[int]:
    """This process and the launchers above it. On Windows uv's compactcode.exe starts the venv's python.exe, which
    starts the real Python; every one of them holds a file the update replaces."""
    pids = [os.getpid()]
    if os.name != "nt":
        return pids
    try:
        import psutil
        proc = psutil.Process()
        for _ in range(3):
            proc = proc.parent()
            if proc is None or proc.name().lower() not in LAUNCHERS:
                break
            pids.append(proc.pid)
            if proc.name().lower() in ("compactcode.exe", "pcode.exe"):
                break
    except Exception:            # no psutil, or a parent that has gone: the direct parent is the best guess
        if os.getppid() not in pids:
            pids.append(os.getppid())
    return pids


def launcher() -> list[str]:
    """How to start CompactCode again after an update."""
    found = shutil.which("compactcode")
    return [found] if found else [sys.executable, "-c", "from puppeteer_code.cli import launch; launch()"]


def apply(busy: bool = False, home: Optional[Path] = None, relaunch: bool = True,
          spawn=spawn_detached) -> dict:
    """Verify and hand over to the helper. The caller must then exit (the IDE stops its server)."""
    s = state(home)
    if s["kind"] == "source":
        raise UpdateError("This copy runs from a git checkout. Update it with git pull.")
    if busy:
        raise UpdateError("Wait for the current request to finish, then update.")
    if not s["available"]:
        raise UpdateError("No newer version is known. Check for updates first.")
    wheel = download(home)
    log = _home(home) / "logs" / "update.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} updating {s['current']} -> {s['latest']} ({s['kind']})\n")
    pids = processes_to_wait_for()
    cmd = helper_command(install_args(s["kind"], wheel, find_uv(), sys.executable), pids, log,
                         launcher() if relaunch else None)
    spawn(cmd, Path.home())
    return {"ok": True, "from": s["current"], "to": s["latest"], "log": str(log)}
