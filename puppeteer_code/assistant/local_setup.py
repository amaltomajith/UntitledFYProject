"""
puppeteer_code/assistant/local_setup.py
=======================================
Setting up a model on this computer from the IDE, so CompactCode works with no API key:
find Ollama (or install it), see what this computer can run, download a model, choose it.

Every step here starts from a click. Nothing runs during a request:
- Install runs Ollama's official installer on Windows, with its output shown live. On
  macOS and Linux the installer may ask for a password, so the IDE shows the command
  for the person to run in their own terminal, then checks again.
- Download goes through pcode's own Ollama server (cloud off) and reports progress.
  It is refused while a request runs, for a cloud name, and when the disk is too full.
Wording stays inside the allowed claim: the model comes from ollama.com, and the
person's code is not sent. Never "no data leaves".
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from puppeteer_code import hardware, local_models
from puppeteer_code.ollama import Cancelled, OllamaClient, find_ollama, ollama_version, shared_server
from puppeteer_code.privacy import PrivacyError, full_name, is_cloud_name
from puppeteer_code.workspace import scrub, scrubbed_env

WINDOWS_INSTALL = "irm https://ollama.com/install.ps1 | iex"
UNIX_INSTALL = "curl -fsSL https://ollama.com/install.sh | sh"
MODEL_NAME = re.compile(r"^[\w.\-/]+(:[\w.\-]+)?$")
PROGRESS_EVERY = 0.25           # seconds between progress events
MAX_LINES = 200
DOWNLOADING = "Downloading the model from ollama.com. Your code is not sent."

Publish = Callable[..., None]


def install_command() -> dict:
    if sys.platform == "win32":
        return {"command": WINDOWS_INSTALL, "runs_here": True, "shell": "PowerShell"}
    return {"command": UNIX_INSTALL, "runs_here": False, "shell": "Terminal"}


def _default_client() -> OllamaClient:
    from puppeteer_code.providers import load_config
    spec = load_config()["providers"]["local"]
    return OllamaClient(shared_server(spec).url, spec.get("num_ctx", 16384), spec.get("keep_alive", "10m"))


def _default_installer() -> subprocess.Popen:
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    return subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", WINDOWS_INSTALL],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=scrubbed_env(), text=True, encoding="utf-8", errors="replace", **flags)


class LocalSetup:
    def __init__(self, home: Path, publish: Publish, busy: Callable[[], bool] = lambda: False,
                 make_client: Callable[[], OllamaClient] = _default_client,
                 start_installer: Callable[[], subprocess.Popen] = _default_installer,
                 detect: Callable[[], dict] = hardware.detect, find: Callable[[], Optional[str]] = find_ollama,
                 models_folder: Optional[Path] = None):
        self.home = Path(home)
        self.publish = publish
        self.busy = busy
        self.make_client = make_client
        self.start_installer = start_installer
        self.detect = detect
        self.find = find
        self.folder = models_folder
        self.lock = threading.Lock()
        self.pull: Optional[dict] = None        # model, status, done, total, state, message
        self.install: Optional[dict] = None     # state, lines, code
        self._cancel: Optional[threading.Event] = None
        self._hardware: Optional[dict] = None

    # ── what is here ─────────────────────────────────────────────────────────

    def status(self, refresh: bool = False) -> dict:
        if self._hardware is None or refresh:
            self._hardware = self.detect()
        binary = self.find()
        have = local_models.installed(self.folder)
        return {
            "ollama": {"found": bool(binary), "version": ollama_version(binary) if binary else None},
            "install": {**install_command(), **(self.install or {"state": "idle", "lines": []})},
            "hardware": self._hardware,
            "models": local_models.offers(self._hardware, have),
            "installed": have,
            "chosen": local_models.chosen(self.home),
            "pull": self.pull,
            "downloading_note": DOWNLOADING,
        }

    # ── install Ollama ───────────────────────────────────────────────────────

    def run_install(self) -> dict:
        if not install_command()["runs_here"]:
            raise PermissionError("Run the command in your own terminal, then check again.")
        with self.lock:
            if self.install and self.install["state"] == "running":
                raise RuntimeError("The installer is already running.")
            self.install = {"state": "running", "lines": [], "code": None}
            started = {**self.install, "lines": []}
        threading.Thread(target=self._install, daemon=True).start()
        return started

    def _install(self) -> None:
        try:
            proc = self.start_installer()
            for line in proc.stdout:
                line = scrub(line.rstrip())
                if not line:
                    continue
                self.install["lines"] = (self.install["lines"] + [line])[-MAX_LINES:]
                self.publish({"type": "local_install", "line": line}, keep=False)
            code = proc.wait()
        except OSError as e:
            code, self.install["lines"] = -1, self.install["lines"] + [f"The installer would not start: {e}"]
        found = bool(self.find())
        self.install.update(state="done" if code == 0 and found else "failed", code=code)
        self.publish({"type": "local_install", "state": self.install["state"], "code": code, "found": found})

    # ── download a model ─────────────────────────────────────────────────────

    def start_pull(self, model: str) -> dict:
        model = model.strip()
        if is_cloud_name(model):
            raise PrivacyError(f"{model} is a cloud model. Private mode only runs models on this computer.")
        if not MODEL_NAME.match(model):
            raise ValueError("That is not a model name.")
        if self.busy():
            raise RuntimeError("Wait for the current request to finish. Downloads never run during one.")
        if not self.find():
            raise PrivacyError("Install Ollama first.")
        known = local_models.BY_NAME.get(model)
        free = hardware.free_disk(self.folder)
        if known and free is not None and free < known.size * hardware.GB:
            raise OSError(f"Not enough free disk: {model} needs about {known.size:g} GB and "
                          f"{free / hardware.GB:.1f} GB is free.")
        with self.lock:
            if self.pull and self.pull["state"] == "running":
                raise RuntimeError("A download is already running.")
            self._cancel = threading.Event()
            self.pull = {"model": model, "status": "starting", "done": 0, "total": 0, "state": "running",
                         "message": DOWNLOADING}
            started = dict(self.pull)
        threading.Thread(target=self._pull, args=(model, self._cancel), daemon=True).start()
        return started

    def _pull(self, model: str, cancel: threading.Event) -> None:
        last = 0.0

        def progress(status: str, done: int, total: int) -> None:
            nonlocal last
            self.pull.update(status=status, done=done, total=total)
            now = time.monotonic()
            if now - last >= PROGRESS_EVERY:
                last = now
                self.publish({"type": "local_pull", **self.pull}, keep=False)

        try:
            client = self.make_client()
            client.cancel = cancel
            client.pull(model, progress)
            self.pull.update(state="done", status="success", message=f"{model} is ready.")
            if not local_models.chosen(self.home):
                local_models.choose(self.home, model)       # the first model downloaded is the one used
        except Cancelled:
            self.pull.update(state="cancelled", message="Download stopped. The part already downloaded is kept.")
        except Exception as e:      # no server, no network, a name ollama.com does not have
            self.pull.update(state="failed", message=scrub(str(e))[:300])
        self.publish({"type": "local_pull", **self.pull})

    def cancel_pull(self) -> bool:
        if self._cancel is None or not self.pull or self.pull["state"] != "running":
            return False
        self._cancel.set()
        return True

    # ── choose ───────────────────────────────────────────────────────────────

    def choose(self, model: str) -> None:
        have = local_models.installed(self.folder)
        if model not in have and full_name(model) not in have:
            raise LookupError("Download that model first.")
        local_models.choose(self.home, model)


__all__ = ["LocalSetup", "install_command", "DOWNLOADING"]
