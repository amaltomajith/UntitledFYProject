"""
puppeteer_code/assistant/ide.py
===============================
The local backend for the browser IDE (`pcode ide`). REST serves files; a WebSocket
carries session events and approvals; one more WebSocket per terminal carries a real shell.

Because this can run commands, every route needs the per-launch token as well as
passing the LocalOnly guard. The token travels in the URL fragment, so it never
reaches a server log or a Referer header.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import string
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from puppeteer_code.assistant.activity import TRANSIENT
from puppeteer_code.assistant.effort import LEVELS
from puppeteer_code.assistant.permissions import REFUSE, classify_command, is_protected
from puppeteer_code.assistant.session import AssistantSession
from puppeteer_code.assistant.setup import (KEY_HELP, is_private, key_status, load_keys, pick_provider,
                                             remember_private, save_key)
from puppeteer_code.assistant.snapshots import pcode_home
from puppeteer_code.assistant.terminals import TerminalLimit, TerminalManager, bridge, pty_missing, windows_build
from puppeteer_code.assistant.toolbox import Toolbox
from puppeteer_code.bench import is_quota_error
from puppeteer_code.cli import apply_diff
from puppeteer_code.privacy import OUTSIDE, PrivacyError, private_claim
from puppeteer_code.sandbox import DirectRunner
from puppeteer_code.server import RunBusy, RunRequest
from puppeteer_code.workspace import IGNORE_DIRS, scrub, scrub_obj, scrubbed_env

MAX_FOLDERS = 6
MAX_TREE = 2000
MAX_FILE = 1_000_000
MAX_LOG = 5000
AUTH_TIMEOUT = 5
AUTH_FAILED = 4401
NO_TERMINAL = 4404
NO_WORKSPACE = 4405
GIT_TIMEOUT = 10
MAX_GIT_FILES = 500
HIT = re.compile(r"^(.+?):(\d+): (.*)$")
FIX_LABELS = {"RepoScoutAgent": "Finding the code", "TestRunnerAgent": "Running the tests",
              "PlannerAgent": "Planning", "CoderAgent": "Fixing", "DebuggerAgent": "Fixing",
              "ExplorerAgent": "Trying different fixes", "ReviewerAgent": "Reviewing", "LinterAgent": "Checking style",
              "TestWriterAgent": "Writing a test", "ConcluderAgent": "Finishing", "TerminatorAgent": "Finishing",
              "DocsSearchAgent": "Reading the docs"}


class KeyRequest(BaseModel):
    provider: str
    key: str = Field(min_length=1, max_length=500)


class OpenRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class CreateRequest(BaseModel):
    parent: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=100)


class FileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=MAX_FILE)


class ModeRequest(BaseModel):
    mode: str


class EffortRequest(BaseModel):
    level: str


class PrivateRequest(BaseModel):
    on: bool


class ModelRequest(BaseModel):
    tier: str
    model: str = Field(min_length=1, max_length=200, pattern=r"^[\w./:-]+$")


class ResumeRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z-]+$")


class ApplyFixRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4000)


class TerminalRequest(BaseModel):
    shell: Optional[str] = Field(default=None, max_length=100)
    cols: int = Field(default=80, ge=1, le=1000)
    rows: int = Field(default=24, ge=1, le=1000)


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def drives() -> list[str]:
    if os.name != "nt":
        return ["/"]
    return [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]


def git_status(project: Path) -> dict:
    """Read-only. A cloned repo's config can name a command to run, so fsmonitor is forced off."""
    env = {**scrubbed_env(), "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args: str) -> Optional[str]:
        try:
            done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(project), *args], env=env,
                                  stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                                  errors="replace", timeout=GIT_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None

    prefix = git("rev-parse", "--show-prefix")
    report = None if prefix is None else git("status", "--porcelain=v1", "--branch", "-z", "--", ".")
    if report is None:
        return {"repo": False, "branch": "", "files": []}
    prefix = prefix.strip()
    rows = iter(report.split("\0"))
    branch, files = "", []
    for row in rows:
        if row.startswith("## "):
            branch = re.sub(r"^No commits yet on ", "", row[3:]).split("...")[0]
        elif len(row) > 3:
            code, path = row[:2], row[3:]
            if "R" in code or "C" in code:
                next(rows, None)        # -z puts the old name of a rename in its own field
            rel = path[len(prefix):] if path.startswith(prefix) else path
            if not is_protected(rel) and len(files) < MAX_GIT_FILES:
                files.append({"path": rel, "status": "U" if code == "??" else code.strip()[0]})
    return {"repo": True, "branch": branch, "files": files}


class LocalGate:
    """One model on one GPU: a Private request in any folder waits for the one before it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.holder = ""

    def enter(self, name: str, stop: threading.Event, waiting: Callable[[str], None]) -> bool:
        while not self._lock.acquire(timeout=1):
            if stop.is_set():
                return False
            waiting(self.holder)
        self.holder = name
        return True

    def leave(self) -> None:
        self.holder = ""
        self._lock.release()


class IdeHub:
    """One project and one session at a time, with any number of browser tabs watching."""

    def __init__(self, token: str, make_provider: Callable[[str], object], home: Optional[Path] = None,
                 provider: Optional[str] = None, mode: str = "ask", effort: str = "normal",
                 private: bool = False, gate: Optional[LocalGate] = None):
        self.id = secrets.token_hex(4)
        self.gate = gate or LocalGate()
        self.spaces: Optional["Workspaces"] = None
        self.queued = False
        self.stop_waiting = threading.Event()
        self.token = token
        self.make_provider = make_provider
        self.home = Path(home or pcode_home())
        self.provider_name = None if provider == "local" else provider  # `--private` says local; off needs a real one
        self.mode = mode
        self.effort = effort
        self.private = private
        self.keep_private = private         # `--private`, or chosen before a folder was open
        self.session: Optional[AssistantSession] = None
        self.shell: Optional[Toolbox] = None
        self.log: list[dict] = []
        self.seq = 0
        self.subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self.lock = threading.Lock()
        self.running_command = False
        self.manager = None                 # Fix mode runs through the server's RunManager
        self.fix_run = None
        self.fix_tokens = 0                 # Fix mode meters its own session
        self.terminals = TerminalManager()

    def check(self, token: object) -> bool:
        return isinstance(token, str) and secrets.compare_digest(token.encode(), self.token.encode())

    def publish(self, event: dict, keep: bool = True) -> None:
        with self.lock:
            if keep:
                self.seq += 1
                event = {**event, "seq": self.seq}
                self.log = (self.log + [event])[-MAX_LOG:]
            targets = list(self.subscribers)
        for loop, queue in targets:
            loop.call_soon_threadsafe(queue.put_nowait, event)

    def subscribe(self, loop, queue, since: int) -> list[dict]:
        with self.lock:
            self.subscribers.append((loop, queue))
            return [e for e in self.log if e["seq"] > since]

    def unsubscribe(self, queue) -> None:
        with self.lock:
            self.subscribers = [s for s in self.subscribers if s[1] is not queue]

    @property
    def busy(self) -> bool:
        return bool(self.session and self.session.busy) or self.running_command or self.fix_run is not None or self.queued

    def active_provider(self) -> Optional[str]:
        """Private beats every key that is set, and never falls back to one."""
        return "local" if self.private else self.provider_name or pick_provider()

    def state(self) -> dict:
        session = self.session
        return {
            "keys": key_status(), "key_help": KEY_HELP, "provider": self.active_provider(),
            "project": str(session.project) if session else None,
            "session_id": session.id if session else None,
            "session_title": session.display_title if session else "",
            "mode": session.tools.mode if session else self.mode,
            "effort": session.effort if session else self.effort,
            "pending_plan": bool(session and session.pending_plan),
            "private": {"on": self.private, "claim": private_claim(session.tools.runner if session else DirectRunner()),
                        "outside": list(OUTSIDE), "warning": session.tools.runner.warning() if session else "",
                        "setup": self.private and bool(session) and session.tools.runner.name == "direct"},
            "busy": self.busy,
            "models": dict(session.provider.tiers) if session else {},
            "can_undo": bool(session and session.undo_stack),
            "tokens": (session.provider.meter.total_tokens if session else 0) + self.fix_tokens,
            "seq": self.seq,
        }

    def open(self, path: str) -> dict:
        if self.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise HTTPException(404, {"code": "no_folder", "message": "That folder does not exist."})
        if self.spaces and self.spaces.holding(folder) not in (None, self):
            raise HTTPException(409, {"code": "already_open", "message": "That folder is already open in another tab."})
        previous = self.private
        self.private = self.keep_private or is_private(folder, self.home)
        try:
            session = self._new_session(folder)
        except HTTPException:
            self.private = previous
            raise
        self.terminals.close_all()
        self._adopt(session)
        return self.state()

    def _new_session(self, folder: Path) -> AssistantSession:
        provider = self.active_provider()
        if not provider:
            raise HTTPException(409, {"code": "no_key", "message": "Add an API key first."})
        try:
            models = self.make_provider(provider)
        except PrivacyError as e:
            raise HTTPException(409, {"code": "no_local", "message": str(e)})
        return AssistantSession(folder, models, mode=self.mode, home=self.home, effort=self.effort, memory=True, live=True)

    def _adopt(self, session: AssistantSession) -> None:
        if self.session:
            self.session.close()
        self.session = session
        self.shell = Toolbox(session.project, self.publish, lambda request: True)
        if self.private:
            remember_private(session.project, True, self.home)
        self.log = []
        self.publish({"type": "opened", "project": str(session.project)})
        if self.spaces:
            self.spaces.remember(session.project)

    def set_private(self, on: bool) -> dict:
        """A new session, with no history: a conversation does not cross between cloud and Private."""
        if self.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        previous = self.private, self.keep_private
        self.private = self.keep_private = on
        if self.session is None:
            return self.state()
        try:
            session = self._new_session(self.session.project)
        except HTTPException:
            self.private, self.keep_private = previous
            raise
        self._adopt(session)
        remember_private(session.project, on, self.home)
        return self.state()

    def need_session(self) -> AssistantSession:
        if self.session is None:
            raise HTTPException(409, {"code": "no_project", "message": "Open a folder first."})
        return self.session

    def _resolve(self, rel: str) -> Path:
        try:
            return self.need_session().tools.resolve(rel)
        except PermissionError as e:
            raise HTTPException(403, {"code": "protected", "message": str(e)})

    def handle(self, message: dict) -> None:
        kind = message.get("type")
        session = self.session
        if session is None:
            self.publish({"type": "error", "message": "Open a folder first."})
        elif kind == "send":
            text = str(message.get("message") or "").strip()[:8000]
            if not text:
                return
            if self.busy:
                self.publish({"type": "error", "message": "Still working on the last request."})
                return
            if message.get("mode") == "fix" and session.tools.mode == "plan":
                self.publish({"type": "error", "message": "Fix mode changes code, so it is off in plan mode."})
                return
            fix = message.get("mode") == "fix"
            self.publish({"type": "user", "text": text, "turn": None if fix else len(session.turns) + 1})
            if fix:
                threading.Thread(target=self._turn, args=(lambda: self._fix(session, text),), daemon=True).start()
                return
            effort = message.get("effort") if message.get("effort") in LEVELS else None
            threading.Thread(target=self._run, args=(session.send(text, effort=effort),), daemon=True).start()
        elif kind == "answer":
            request_id, approved = str(message.get("id")), bool(message.get("approved"))
            if session.answer(request_id, approved):
                self.publish({"type": "approval_answered", "id": request_id, "approved": approved})
        elif kind == "cancel":
            self.stop_waiting.set()
            session.cancel()
            if self.fix_run:
                self.fix_run.cancel.set()

    def _run(self, stream) -> None:
        def work() -> None:
            for event in stream:
                self.publish(event, keep=event["type"] not in TRANSIENT)    # a long run must not push real events out
        self._turn(work)

    def _turn(self, work: Callable[[], None]) -> None:
        """Runs `work`, once the GPU is free if this folder is Private."""
        private = self.private
        if private and not self._wait_for_gate():
            return
        try:
            work()
        finally:
            if private:
                self.gate.leave()

    def _wait_for_gate(self) -> bool:
        started = time.monotonic()
        name = str(self.session.project.name) if self.session else ""
        self.stop_waiting.clear()
        self.queued = True

        def waiting(holder: str) -> None:
            self.publish({"type": "activity", "agent": "", "label": "", "phase": "queued", "path": holder,
                          "seconds": int(time.monotonic() - started), "silent": 0, "rate": None}, keep=False)
        entered = self.gate.enter(name, self.stop_waiting, waiting)
        self.queued = False
        if not entered:
            self.publish({"type": "done", "status": "cancelled", "reason": "Stopped by you.", "files": [], "verified": False,
                          "can_undo": bool(self.session and self.session.undo_stack)})
        return entered

    def approve_plan(self) -> dict:
        session = self.need_session()
        if self.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        try:
            stream = session.approve_plan()
        except ValueError as e:
            raise HTTPException(409, {"code": "no_plan", "message": str(e)})
        self.mode = session.tools.mode
        self.publish({"type": "user", "text": "Go ahead with the plan.", "turn": len(session.turns) + 1})
        threading.Thread(target=self._run, args=(stream,), daemon=True).start()
        return self.state()

    def _fix(self, session: AssistantSession, text: str) -> None:
        """Fix mode: the sandboxed engine from `pcode fix`. Nothing reaches the folder until Apply."""
        try:
            run = self.manager.start(RunRequest(task=text, repo=str(session.project),
                                                provider=session.provider.provider))
        except (RunBusy, ValueError) as e:
            self.publish({"type": "error", "message": str(e) or "A fix is already running."})
            self.publish({"type": "done", "status": "error", "reason": "", "files": [], "verified": False})
            return
        self.fix_run = run
        seen = 0
        while True:
            finished = run.done
            for event in run.events[seen:]:
                self._translate(event)
            seen = len(run.events)
            if finished:
                break
            time.sleep(0.1)
        self.fix_run = None
        self._fix_done(run)

    def _translate(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "select":
            self.publish({"type": "agent", "agent": event["agent"],
                          "label": FIX_LABELS.get(event["agent"], event["agent"])})
        elif kind == "step" and event.get("rollback_reason"):
            self.publish({"type": "rollback", "reason": str(event["rollback_reason"])})
        elif kind == "error":
            self.publish({"type": "error", "message": event["message"]})

    def _fix_done(self, run) -> None:
        result = run.result
        if result is None:
            status = "quota" if is_quota_error(run.error or "") else "error"
            self.publish({"type": "done", "status": status, "reason": "", "files": [], "verified": False})
            return
        tokens = (result.get("usage") or {}).get("total_tokens") or 0
        self.fix_tokens += tokens
        self.publish({"type": "usage", "tokens": tokens, "total": self.state()["tokens"]})
        tests = (result.get("final") or {}).get("final_tests") or {}
        diff = (result.get("final") or {}).get("diff") or ""
        success = bool(result.get("success"))
        summary = f"Tests: {tests.get('passed', 0)} of {tests.get('total', 0)} pass in the sandbox."
        self.publish({"type": "verify", "ok": success, "verified": True, "summary": summary})
        if result.get("status") == "cancelled":
            status = "cancelled"
        else:
            status = "done" if success else "failed"
        can_apply = success and bool(diff.strip())
        self.publish({"type": "done", "status": status, "reason": "" if success else summary, "files": [],
                      "verified": success, "fix_run": run.id if can_apply else None})

    def apply_fix(self, run_id: str) -> dict:
        session = self.need_session()
        if self.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        if session.tools.mode == "plan":
            raise HTTPException(409, {"code": "plan_mode", "message": "Leave plan mode to apply a fix."})
        data = self.manager.load(run_id)
        if data is None:
            raise HTTPException(404, {"code": "not_found", "message": "That fix is gone."})
        diff = (data.get("final") or {}).get("diff") or ""
        if not data.get("success") or not diff.strip() or Path(str(data.get("repo"))) != session.project:
            raise HTTPException(409, {"code": "not_verified", "message": "Only a verified fix for this folder can be applied."})
        before = session.snapshots.snapshot("before: apply fix")
        ok, error = apply_diff(session.project, diff)
        if not ok:
            raise HTTPException(409, {"code": "apply_failed", "message": scrub(error)[:500]})
        files = session.snapshots.changed_since(before)
        after = session.snapshots.snapshot("after: apply fix")
        session.undo_stack.append({"before": before, "after": after, "files": files, "request": "Apply fix"})
        session.save()
        self.publish({"type": "applied", "files": files})
        return {"files": files, "can_undo": True}

    def run_command(self, command: str) -> None:
        if self.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        decision, reason = classify_command(command)
        if decision == REFUSE:
            raise HTTPException(403, {"code": "refused", "message": f"Refused: this command {reason}."})
        self.need_session()
        self.running_command = True

        def work():
            self.publish({"type": "command_start", "command": command})
            try:
                code, _ = self.shell.run(command, 300)
                self.publish({"type": "command_end", "code": code})
            finally:
                self.running_command = False

        threading.Thread(target=work, daemon=True).start()

    def close(self) -> None:
        self.terminals.close_all()
        if self.session:
            self.session.close()
        if self.spaces:
            self.spaces.close_others(self)


class Workspaces:
    """The folders open in one IDE: a hub each, one token. Nothing crosses between them but the gate."""

    def __init__(self, first: IdeHub):
        self.hubs: dict[str, IdeHub] = {first.id: first}
        self.lock = threading.Lock()
        self.path = first.home / "ide-workspaces.json"
        self.saved = self._read()
        first.spaces = self
        if first.session:
            self.remember(first.session.project)

    @property
    def first(self) -> IdeHub:
        return next(iter(self.hubs.values()))

    def _read(self) -> list[str]:
        try:
            folders = json.loads(self.path.read_text(encoding="utf-8")).get("folders", [])
        except (OSError, ValueError, AttributeError):
            return []
        return [str(f) for f in folders if isinstance(f, str) and Path(f).is_dir()][:MAX_FOLDERS]

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"folders": self.saved}), encoding="utf-8")
        except OSError:
            pass                    # the tabs are simply not back next time

    @staticmethod
    def _same(folder: str | Path) -> str:
        return os.path.normcase(str(Path(folder).expanduser().resolve()))

    def holding(self, folder: str | Path) -> Optional[IdeHub]:
        wanted = self._same(folder)
        return next((hub for hub in self.hubs.values() if hub.session and self._same(hub.session.project) == wanted), None)

    def remember(self, folder: Path) -> None:
        with self.lock:
            if self._same(folder) not in map(self._same, self.saved):
                self.saved = [*self.saved, str(folder)][-MAX_FOLDERS:]
            self._write()

    def forget(self, folder: str) -> None:
        with self.lock:
            self.saved = [f for f in self.saved if self._same(f) != self._same(folder)]
            self._write()

    def add(self) -> IdeHub:
        with self.lock:
            if len(self.hubs) >= MAX_FOLDERS:
                raise HTTPException(409, {"code": "too_many", "message": f"Close a folder first. {MAX_FOLDERS} can be open."})
            model = self.first
            hub = IdeHub(model.token, model.make_provider, model.home, provider=model.provider_name, mode=model.mode,
                         effort=model.effort, private=model.keep_private, gate=model.gate)
            hub.manager, hub.spaces = model.manager, self
            self.hubs[hub.id] = hub
            return hub

    def remove(self, hub: IdeHub) -> None:
        with self.lock:
            del self.hubs[hub.id]
            hub.spaces = None
        if hub.session:
            self.forget(str(hub.session.project))
        hub.close()
        hub.session, hub.spaces = None, self    # shutdown closes the launch's hub, even after its tab has gone
        if not self.hubs:
            with self.lock:
                self.hubs[hub.id] = fresh = IdeHub(hub.token, hub.make_provider, hub.home, provider=hub.provider_name,
                                                   mode=hub.mode, effort=hub.effort, gate=hub.gate)
                fresh.id, fresh.manager, fresh.spaces = hub.id, hub.manager, self

    def close_others(self, closing: IdeHub) -> None:
        for hub in [h for h in self.hubs.values() if h is not closing]:
            hub.spaces = None
            hub.close()

    def public(self) -> dict:
        folders = [{"id": hub.id, "project": str(hub.session.project) if hub.session else None} for hub in self.hubs.values()]
        return {"folders": folders, "saved": [f for f in self.saved if self.holding(f) is None]}


class FolderRequest(BaseModel):
    path: Optional[str] = None


def register_ide(app: FastAPI, first: IdeHub) -> None:
    first.manager = app.state.manager
    spaces = Workspaces(first)

    def auth(x_pcode_token: Optional[str] = Header(default=None)) -> None:
        if not spaces.first.check(x_pcode_token):
            raise HTTPException(401, {"code": "bad_token", "message": "Open the link printed by pcode ide."})

    def current(x_pcode_workspace: Optional[str] = Header(default=None)) -> IdeHub:
        """Each folder's tab names its own hub. A client that names none means the first."""
        if not x_pcode_workspace:
            return spaces.first
        hub = spaces.hubs.get(x_pcode_workspace)
        if hub is None:
            raise HTTPException(404, {"code": "no_workspace", "message": "That folder is no longer open."})
        return hub

    guard = [Depends(auth)]

    @app.get("/api/ide/workspaces", dependencies=guard)
    def workspaces():
        return spaces.public()

    @app.post("/api/ide/workspaces", dependencies=guard, status_code=201)
    def open_workspace(body: FolderRequest):
        held = spaces.holding(body.path) if body.path else None
        if held:
            return {"id": held.id, **spaces.public()}
        hub = spaces.add()
        if body.path:
            try:
                hub.open(body.path)
            except HTTPException as e:
                spaces.remove(hub)
                if e.status_code == 404:        # a missing key is no reason to lose the tab
                    spaces.forget(body.path)
                raise
        return {"id": hub.id, **spaces.public()}

    @app.delete("/api/ide/workspaces/{workspace}", dependencies=guard)
    def close_workspace(workspace: str):
        hub = spaces.hubs.get(workspace)
        if hub is None:
            raise HTTPException(404, {"code": "no_workspace", "message": "That folder is no longer open."})
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "Stop the request in this folder first."})
        spaces.remove(hub)
        return spaces.public()

    @app.post("/api/ide/workspaces/forget", dependencies=guard)
    def forget_workspace(body: FolderRequest):
        spaces.forget(body.path or "")
        return spaces.public()

    @app.get("/api/ide/state", dependencies=guard)
    def state(hub: IdeHub = Depends(current)):
        return hub.state()

    @app.post("/api/ide/keys", dependencies=guard)
    def keys(body: KeyRequest, hub: IdeHub = Depends(current)):
        if body.provider not in KEY_HELP:
            raise HTTPException(422, {"code": "bad_provider", "message": "Unknown provider."})
        try:
            save_key(body.provider, body.key, hub.home)
        except ValueError as e:
            raise HTTPException(422, {"code": "bad_key", "message": str(e)})
        hub.provider_name = hub.provider_name or body.provider
        return hub.state()

    @app.get("/api/ide/browse", dependencies=guard)
    def browse(path: str = ""):
        if not path:
            return {"path": "", "parent": None, "folders": [str(Path.home()), *drives()]}
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise HTTPException(404, {"code": "no_folder", "message": "That folder does not exist."})
        try:
            names = sorted((e.name for e in os.scandir(folder) if e.is_dir(follow_symlinks=False)), key=str.lower)
        except OSError:
            raise HTTPException(403, {"code": "no_access", "message": "That folder cannot be opened."})
        names = [n for n in names if not n.startswith(".") and n not in IGNORE_DIRS]
        parent = None if folder.parent == folder else str(folder.parent)
        return {"path": str(folder), "parent": parent or "", "folders": [str(folder / n) for n in names]}

    @app.post("/api/ide/open", dependencies=guard)
    def open_project(body: OpenRequest, hub: IdeHub = Depends(current)):
        return hub.open(body.path)

    @app.post("/api/ide/create", dependencies=guard)
    def create(body: CreateRequest, hub: IdeHub = Depends(current)):
        if not re.fullmatch(r"[\w .-]+", body.name) or body.name.strip(". ") == "":
            raise HTTPException(422, {"code": "bad_name", "message": "Use letters, numbers, spaces, dots or dashes."})
        folder = Path(body.parent).expanduser() / body.name
        if folder.exists():
            raise HTTPException(409, {"code": "exists", "message": "A folder with that name already exists."})
        folder.mkdir(parents=True)
        return hub.open(str(folder))

    @app.get("/api/ide/tree", dependencies=guard)
    def tree(hub: IdeHub = Depends(current)):
        session = hub.need_session()
        files = []
        for rel in session.tools._walk(session.project):
            files.append(rel)
            if len(files) >= MAX_TREE:
                break
        return {"root": str(session.project), "files": files, "truncated": len(files) >= MAX_TREE}

    @app.get("/api/ide/file", dependencies=guard)
    def read_file(path: str, hub: IdeHub = Depends(current)):
        target = hub._resolve(path)
        if not target.is_file():
            raise HTTPException(404, {"code": "no_file", "message": "That file does not exist."})
        size = target.stat().st_size
        if size > MAX_FILE:
            return {"path": path, "content": None, "reason": "too_large", "size": size}
        try:
            return {"path": path, "content": target.read_bytes().decode("utf-8"), "size": size}
        except UnicodeDecodeError:
            return {"path": path, "content": None, "reason": "binary", "size": size}

    @app.put("/api/ide/file", dependencies=guard)
    def write_file(body: FileRequest, hub: IdeHub = Depends(current)):
        target = hub._resolve(body.path)
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "The assistant is working. Try again when it stops."})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content, encoding="utf-8", newline="")
        return {"saved": True}

    @app.get("/api/ide/search", dependencies=guard)
    def search(q: str = Query(min_length=1, max_length=200), hub: IdeHub = Depends(current)):
        hits = hub.need_session().tools._grep(f"(?i){re.escape(q)}")
        rows = [m.groups() for m in map(HIT.match, hits.splitlines()) if m]
        return scrub_obj({"hits": [{"path": path, "line": int(line), "text": text} for path, line, text in rows],
                          "truncated": hits.endswith("(more matches not shown)")})

    @app.get("/api/ide/git", dependencies=guard)
    def git(hub: IdeHub = Depends(current)):
        return git_status(hub.need_session().project)

    @app.get("/api/ide/changes", dependencies=guard)
    def changes(hub: IdeHub = Depends(current)):
        session = hub.need_session()
        if not session.undo_stack:
            return {"request": None, "files": []}
        entry = session.undo_stack[-1]
        files = []
        for rel in entry["files"][:50]:
            before = session.snapshots.read(entry["before"], rel)
            after = session.snapshots.read(entry["after"], rel)
            files.append({"path": rel, "before": before, "after": after})
        return scrub_obj({"request": entry["request"], "files": files})

    @app.post("/api/ide/undo", dependencies=guard)
    def undo(hub: IdeHub = Depends(current)):
        try:
            result = hub.need_session().undo()
        except RuntimeError as e:
            raise HTTPException(409, {"code": "busy", "message": str(e)})
        hub.publish({"type": "undone", **result})
        return {**result, "can_undo": bool(hub.session.undo_stack)}

    @app.post("/api/ide/mode", dependencies=guard)
    def mode(body: ModeRequest, hub: IdeHub = Depends(current)):
        try:
            hub.need_session().set_mode(body.mode)
        except ValueError as e:
            raise HTTPException(422, {"code": "bad_mode", "message": str(e)})
        except RuntimeError as e:
            raise HTTPException(409, {"code": "busy", "message": str(e)})
        hub.mode = body.mode
        return hub.state()

    @app.post("/api/ide/effort", dependencies=guard)
    def effort(body: EffortRequest, hub: IdeHub = Depends(current)):
        try:
            hub.need_session().set_effort(body.level)
        except ValueError as e:
            raise HTTPException(422, {"code": "bad_effort", "message": str(e)})
        hub.effort = body.level
        return hub.state()

    @app.post("/api/ide/private", dependencies=guard)
    def private(body: PrivateRequest, hub: IdeHub = Depends(current)):
        return hub.set_private(body.on)

    @app.post("/api/ide/plan/approve", dependencies=guard, status_code=202)
    def approve_plan(hub: IdeHub = Depends(current)):
        return hub.approve_plan()

    @app.post("/api/ide/model", dependencies=guard)
    def model(body: ModelRequest, hub: IdeHub = Depends(current)):
        session = hub.need_session()
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        if body.tier not in session.provider.tiers:
            raise HTTPException(422, {"code": "bad_tier", "message": "Use fast or quality."})
        session.provider.tiers[body.tier] = body.model
        return hub.state()

    @app.post("/api/ide/clear", dependencies=guard)
    def clear(hub: IdeHub = Depends(current)):
        session = hub.need_session()
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        session.clear()
        hub.publish({"type": "cleared"})
        return hub.state()

    @app.get("/api/ide/sessions", dependencies=guard)
    def sessions(hub: IdeHub = Depends(current)):
        session = hub.need_session()
        return {"sessions": AssistantSession.list_sessions(session.project, hub.home, session.private)}

    def edit_session(hub: IdeHub, change: Callable[[AssistantSession], None]) -> AssistantSession:
        session = hub.need_session()
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        try:
            change(session)
        except OSError:
            raise HTTPException(404, {"code": "no_session", "message": "That conversation is gone."})
        except ValueError as e:
            raise HTTPException(409, {"code": "private_session", "message": str(e)})
        return session

    @app.post("/api/ide/resume", dependencies=guard)
    def resume(body: ResumeRequest, hub: IdeHub = Depends(current)):
        session = edit_session(hub, lambda s: s.adopt(body.id))
        hub.publish({"type": "resumed", "history": session.history, "turns": session.turns})
        return {"history": session.history}

    @app.post("/api/ide/sessions/{session_id}/rename", dependencies=guard)
    def rename_session(session_id: str, body: RenameRequest, hub: IdeHub = Depends(current)):
        edit_session(hub, lambda s: s.rename(session_id, body.name))
        return hub.state()

    @app.get("/api/ide/graph", dependencies=guard)
    def graph(scope: str = Query("session", pattern="^(session|project)$"), q: str = Query("", max_length=200),
              types: str = Query("", max_length=60), limit: int = Query(300, ge=1, le=2000), hub: IdeHub = Depends(current)):
        found = hub.need_session().graph_view(scope, q, [kind for kind in types.split(",") if kind], limit)
        if found is None:
            raise HTTPException(503, {"code": "memory_off", "message": "Memory is not available right now."})
        return found

    @app.get("/api/ide/graph/node/{node_id:path}", dependencies=guard)
    def graph_node(node_id: str, hub: IdeHub = Depends(current)):
        node = hub.need_session().graph_node(node_id)
        if node is None:
            raise HTTPException(404, {"code": "no_node", "message": "That item is no longer in the graph."})
        return node

    @app.delete("/api/ide/sessions/{session_id}", dependencies=guard)
    def delete_session(session_id: str, hub: IdeHub = Depends(current)):
        active = hub.need_session().id == session_id
        edit_session(hub, lambda s: s.delete(session_id))
        if active:
            hub.publish({"type": "cleared"})
        return hub.state()

    @app.post("/api/ide/fix/apply", dependencies=guard)
    def apply_fix(body: ApplyFixRequest, hub: IdeHub = Depends(current)):
        return hub.apply_fix(body.run_id)

    @app.post("/api/ide/command", dependencies=guard, status_code=202)
    def command(body: CommandRequest, hub: IdeHub = Depends(current)):
        hub.run_command(body.command)
        return {"started": True}

    @app.get("/api/ide/shells", dependencies=guard)
    def shells(hub: IdeHub = Depends(current)):
        missing = pty_missing()
        return {"shells": [] if missing else [s.public() for s in hub.terminals.shells()],
                "install": missing, "windows_build": windows_build()}

    @app.get("/api/ide/terminals", dependencies=guard)
    def terminals(hub: IdeHub = Depends(current)):
        return {"terminals": hub.terminals.list()}

    @app.post("/api/ide/terminals", dependencies=guard, status_code=201)
    def new_terminal(body: TerminalRequest, hub: IdeHub = Depends(current)):
        session = hub.need_session()
        missing = pty_missing()
        if missing:
            raise HTTPException(503, {"code": "no_pty", "message": f"Terminals need one more package: {missing}"})
        try:
            return hub.terminals.create(body.shell, session.project, body.cols, body.rows).public()
        except KeyError:
            raise HTTPException(422, {"code": "bad_shell", "message": "That shell is not on this machine."})
        except TerminalLimit:
            raise HTTPException(409, {"code": "too_many", "message": "Close a terminal first."})
        except OSError as e:
            raise HTTPException(500, {"code": "no_start", "message": f"That shell would not start: {scrub(str(e))[:200]}"})

    @app.delete("/api/ide/terminals/{terminal_id}", dependencies=guard)
    def kill_terminal(terminal_id: str, hub: IdeHub = Depends(current)):
        if not hub.terminals.remove(terminal_id):
            raise HTTPException(404, {"code": "no_terminal", "message": "That terminal is gone."})
        return {"killed": True}

    @app.post("/api/ide/terminals/{terminal_id}/rename", dependencies=guard)
    def rename_terminal(terminal_id: str, body: RenameRequest, hub: IdeHub = Depends(current)):
        terminal = hub.terminals.get(terminal_id)
        if terminal is None:
            raise HTTPException(404, {"code": "no_terminal", "message": "That terminal is gone."})
        terminal.name = body.name.strip() or terminal.name
        return terminal.public()

    async def signed_in(ws: WebSocket) -> Optional[dict]:
        """Nothing is sent or started until the first message carries the token."""
        await ws.accept()
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT))
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, KeyError):
            await ws.close(AUTH_FAILED)
            return None
        if not isinstance(first, dict) or not spaces.first.check(first.get("token")):
            await ws.close(AUTH_FAILED)
            return None
        return first

    def named(message: dict) -> Optional[IdeHub]:
        return spaces.hubs.get(message.get("workspace")) if message.get("workspace") else spaces.first

    @app.websocket("/api/ide/terminals/{terminal_id}/ws")
    async def terminal_socket(ws: WebSocket, terminal_id: str):
        first = await signed_in(ws)
        if first is None:
            return
        hub = named(first)
        terminal = hub.terminals.get(terminal_id) if hub else None
        if terminal is None:
            await ws.close(NO_TERMINAL)
            return
        await bridge(ws, terminal, max(0, int(first.get("offset") or 0)))

    @app.websocket("/api/ide/ws")
    async def socket(ws: WebSocket):
        first = await signed_in(ws)
        if first is None:
            return
        hub = named(first)
        if hub is None:
            await ws.close(NO_WORKSPACE)
            return
        queue: asyncio.Queue = asyncio.Queue()
        backlog = hub.subscribe(asyncio.get_running_loop(), queue, int(first.get("since") or 0))

        async def pump():
            await ws.send_json({"type": "hello", "state": hub.state()})
            for event in backlog:
                await ws.send_json(event)
            while True:
                await ws.send_json(await queue.get())

        sender = asyncio.create_task(pump())
        try:
            while True:
                try:
                    message = json.loads(await ws.receive_text())
                except ValueError:
                    continue
                if isinstance(message, dict):
                    hub.handle(message)
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            hub.unsubscribe(queue)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def make_hub(provider: Optional[str] = None, mode: str = "ask", cache: Optional[str] = None,
             effort: str = "normal", private: bool = False) -> IdeHub:
    from puppeteer_code.providers import ProviderSession

    load_keys()
    return IdeHub(new_token(), lambda name: ProviderSession(name, cache_dir=None if name == "local" else cache),
                  provider=provider, mode=mode, effort=effort, private=private)


__all__ = ["IdeHub", "register_ide", "make_hub", "new_token", "scrub"]
