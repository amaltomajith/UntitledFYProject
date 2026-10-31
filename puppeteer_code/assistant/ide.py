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

MAX_TREE = 2000
MAX_FILE = 1_000_000
MAX_LOG = 5000
AUTH_TIMEOUT = 5
AUTH_FAILED = 4401
NO_TERMINAL = 4404
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


class IdeHub:
    """One project and one session at a time, with any number of browser tabs watching."""

    def __init__(self, token: str, make_provider: Callable[[str], object], home: Optional[Path] = None,
                 provider: Optional[str] = None, mode: str = "ask", effort: str = "normal",
                 private: bool = False):
        self.token = token
        self.make_provider = make_provider
        self.home = Path(home or pcode_home())
        self.provider_name = provider
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

    def publish(self, event: dict) -> None:
        with self.lock:
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
        return bool(self.session and self.session.busy) or self.running_command or self.fix_run is not None

    def active_provider(self) -> Optional[str]:
        """Private beats every key that is set, and never falls back to one."""
        return "local" if self.private else self.provider_name or pick_provider()

    def state(self) -> dict:
        session = self.session
        return {
            "keys": key_status(), "key_help": KEY_HELP, "provider": self.active_provider(),
            "project": str(session.project) if session else None,
            "session_id": session.id if session else None,
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
        return AssistantSession(folder, models, mode=self.mode, home=self.home, effort=self.effort)

    def _adopt(self, session: AssistantSession) -> None:
        if self.session:
            self.session.close()
        self.session = session
        self.shell = Toolbox(session.project, self.publish, lambda request: True)
        if self.private:
            remember_private(session.project, True, self.home)
        self.log = []
        self.publish({"type": "opened", "project": str(session.project)})

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
            self.publish({"type": "user", "text": text})
            if message.get("mode") == "fix":
                threading.Thread(target=self._fix, args=(session, text), daemon=True).start()
                return
            effort = message.get("effort") if message.get("effort") in LEVELS else None
            threading.Thread(target=self._run, args=(session.send(text, effort=effort),), daemon=True).start()
        elif kind == "answer":
            request_id, approved = str(message.get("id")), bool(message.get("approved"))
            if session.answer(request_id, approved):
                self.publish({"type": "approval_answered", "id": request_id, "approved": approved})
        elif kind == "cancel":
            session.cancel()
            if self.fix_run:
                self.fix_run.cancel.set()

    def _run(self, stream) -> None:
        for event in stream:
            self.publish(event)

    def approve_plan(self) -> dict:
        session = self.need_session()
        if self.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        try:
            stream = session.approve_plan()
        except ValueError as e:
            raise HTTPException(409, {"code": "no_plan", "message": str(e)})
        self.mode = session.tools.mode
        self.publish({"type": "user", "text": "Go ahead with the plan."})
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


def register_ide(app: FastAPI, hub: IdeHub) -> None:
    hub.manager = app.state.manager

    def auth(x_pcode_token: Optional[str] = Header(default=None)) -> None:
        if not hub.check(x_pcode_token):
            raise HTTPException(401, {"code": "bad_token", "message": "Open the link printed by pcode ide."})

    guard = [Depends(auth)]

    @app.get("/api/ide/state", dependencies=guard)
    def state():
        return hub.state()

    @app.post("/api/ide/keys", dependencies=guard)
    def keys(body: KeyRequest):
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
    def open_project(body: OpenRequest):
        return hub.open(body.path)

    @app.post("/api/ide/create", dependencies=guard)
    def create(body: CreateRequest):
        if not re.fullmatch(r"[\w .-]+", body.name) or body.name.strip(". ") == "":
            raise HTTPException(422, {"code": "bad_name", "message": "Use letters, numbers, spaces, dots or dashes."})
        folder = Path(body.parent).expanduser() / body.name
        if folder.exists():
            raise HTTPException(409, {"code": "exists", "message": "A folder with that name already exists."})
        folder.mkdir(parents=True)
        return hub.open(str(folder))

    @app.get("/api/ide/tree", dependencies=guard)
    def tree():
        session = hub.need_session()
        files = []
        for rel in session.tools._walk(session.project):
            files.append(rel)
            if len(files) >= MAX_TREE:
                break
        return {"root": str(session.project), "files": files, "truncated": len(files) >= MAX_TREE}

    @app.get("/api/ide/file", dependencies=guard)
    def read_file(path: str):
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
    def write_file(body: FileRequest):
        target = hub._resolve(body.path)
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "The assistant is working. Try again when it stops."})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content, encoding="utf-8", newline="")
        return {"saved": True}

    @app.get("/api/ide/search", dependencies=guard)
    def search(q: str = Query(min_length=1, max_length=200)):
        hits = hub.need_session().tools._grep(f"(?i){re.escape(q)}")
        rows = [m.groups() for m in map(HIT.match, hits.splitlines()) if m]
        return scrub_obj({"hits": [{"path": path, "line": int(line), "text": text} for path, line, text in rows],
                          "truncated": hits.endswith("(more matches not shown)")})

    @app.get("/api/ide/git", dependencies=guard)
    def git():
        return git_status(hub.need_session().project)

    @app.get("/api/ide/changes", dependencies=guard)
    def changes():
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
    def undo():
        try:
            result = hub.need_session().undo()
        except RuntimeError as e:
            raise HTTPException(409, {"code": "busy", "message": str(e)})
        hub.publish({"type": "undone", **result})
        return {**result, "can_undo": bool(hub.session.undo_stack)}

    @app.post("/api/ide/mode", dependencies=guard)
    def mode(body: ModeRequest):
        try:
            hub.need_session().set_mode(body.mode)
        except ValueError as e:
            raise HTTPException(422, {"code": "bad_mode", "message": str(e)})
        except RuntimeError as e:
            raise HTTPException(409, {"code": "busy", "message": str(e)})
        hub.mode = body.mode
        return hub.state()

    @app.post("/api/ide/effort", dependencies=guard)
    def effort(body: EffortRequest):
        try:
            hub.need_session().set_effort(body.level)
        except ValueError as e:
            raise HTTPException(422, {"code": "bad_effort", "message": str(e)})
        hub.effort = body.level
        return hub.state()

    @app.post("/api/ide/private", dependencies=guard)
    def private(body: PrivateRequest):
        return hub.set_private(body.on)

    @app.post("/api/ide/plan/approve", dependencies=guard, status_code=202)
    def approve_plan():
        return hub.approve_plan()

    @app.post("/api/ide/model", dependencies=guard)
    def model(body: ModelRequest):
        session = hub.need_session()
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        if body.tier not in session.provider.tiers:
            raise HTTPException(422, {"code": "bad_tier", "message": "Use fast or quality."})
        session.provider.tiers[body.tier] = body.model
        return hub.state()

    @app.post("/api/ide/clear", dependencies=guard)
    def clear():
        session = hub.need_session()
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        session.clear()
        hub.publish({"type": "cleared"})
        return hub.state()

    @app.get("/api/ide/sessions", dependencies=guard)
    def sessions():
        return {"sessions": AssistantSession.list_sessions(hub.need_session().project, hub.home)}

    @app.post("/api/ide/resume", dependencies=guard)
    def resume(body: ResumeRequest):
        session = hub.need_session()
        if hub.busy:
            raise HTTPException(409, {"code": "busy", "message": "Wait for the current request to finish."})
        try:
            session.adopt(body.id)
        except OSError:
            raise HTTPException(404, {"code": "no_session", "message": "That conversation is gone."})
        except ValueError as e:
            raise HTTPException(409, {"code": "private_session", "message": str(e)})
        hub.publish({"type": "resumed", "history": session.history})
        return {"history": session.history}

    @app.post("/api/ide/fix/apply", dependencies=guard)
    def apply_fix(body: ApplyFixRequest):
        return hub.apply_fix(body.run_id)

    @app.post("/api/ide/command", dependencies=guard, status_code=202)
    def command(body: CommandRequest):
        hub.run_command(body.command)
        return {"started": True}

    @app.get("/api/ide/shells", dependencies=guard)
    def shells():
        missing = pty_missing()
        return {"shells": [] if missing else [s.public() for s in hub.terminals.shells()],
                "install": missing, "windows_build": windows_build()}

    @app.get("/api/ide/terminals", dependencies=guard)
    def terminals():
        return {"terminals": hub.terminals.list()}

    @app.post("/api/ide/terminals", dependencies=guard, status_code=201)
    def new_terminal(body: TerminalRequest):
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
    def kill_terminal(terminal_id: str):
        if not hub.terminals.remove(terminal_id):
            raise HTTPException(404, {"code": "no_terminal", "message": "That terminal is gone."})
        return {"killed": True}

    @app.post("/api/ide/terminals/{terminal_id}/rename", dependencies=guard)
    def rename_terminal(terminal_id: str, body: RenameRequest):
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
        if not isinstance(first, dict) or not hub.check(first.get("token")):
            await ws.close(AUTH_FAILED)
            return None
        return first

    @app.websocket("/api/ide/terminals/{terminal_id}/ws")
    async def terminal_socket(ws: WebSocket, terminal_id: str):
        first = await signed_in(ws)
        if first is None:
            return
        terminal = hub.terminals.get(terminal_id)
        if terminal is None:
            await ws.close(NO_TERMINAL)
            return
        await bridge(ws, terminal, max(0, int(first.get("offset") or 0)))

    @app.websocket("/api/ide/ws")
    async def socket(ws: WebSocket):
        first = await signed_in(ws)
        if first is None:
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
