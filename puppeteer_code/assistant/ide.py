"""
puppeteer_code/assistant/ide.py
===============================
The local backend for the browser IDE (`pcode ide`). REST serves files; a WebSocket
carries session events and approvals.

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
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from puppeteer_code.assistant.permissions import REFUSE, classify_command
from puppeteer_code.assistant.session import AssistantSession
from puppeteer_code.assistant.setup import KEY_HELP, key_status, load_keys, pick_provider, save_key
from puppeteer_code.assistant.snapshots import pcode_home
from puppeteer_code.assistant.toolbox import Toolbox
from puppeteer_code.bench import is_quota_error
from puppeteer_code.cli import apply_diff
from puppeteer_code.server import RunBusy, RunRequest
from puppeteer_code.workspace import IGNORE_DIRS, scrub, scrub_obj

MAX_TREE = 2000
MAX_FILE = 1_000_000
MAX_LOG = 5000
AUTH_TIMEOUT = 5
AUTH_FAILED = 4401
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


class ResumeRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z-]+$")


class ApplyFixRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=4000)


def drives() -> list[str]:
    if os.name != "nt":
        return ["/"]
    return [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]


class IdeHub:
    """One project and one session at a time, with any number of browser tabs watching."""

    def __init__(self, token: str, make_provider: Callable[[str], object], home: Optional[Path] = None,
                 provider: Optional[str] = None, mode: str = "ask"):
        self.token = token
        self.make_provider = make_provider
        self.home = Path(home or pcode_home())
        self.provider_name = provider
        self.mode = mode
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

    def state(self) -> dict:
        session = self.session
        return {
            "keys": key_status(), "key_help": KEY_HELP, "provider": self.provider_name or pick_provider(),
            "project": str(session.project) if session else None,
            "session_id": session.id if session else None,
            "mode": session.tools.mode if session else self.mode,
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
        provider = self.provider_name or pick_provider()
        if not provider:
            raise HTTPException(409, {"code": "no_key", "message": "Add an API key first."})
        if self.session:
            self.session.close()
        self.session = AssistantSession(folder, self.make_provider(provider), mode=self.mode, home=self.home)
        self.shell = Toolbox(self.session.project, self.publish, lambda request: True)
        self.log = []
        self.publish({"type": "opened", "project": str(self.session.project)})
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
            self.publish({"type": "user", "text": text})
            work = self._fix if message.get("mode") == "fix" else self._run
            threading.Thread(target=work, args=(session, text), daemon=True).start()
        elif kind == "answer":
            request_id, approved = str(message.get("id")), bool(message.get("approved"))
            if session.answer(request_id, approved):
                self.publish({"type": "approval_answered", "id": request_id, "approved": approved})
        elif kind == "cancel":
            session.cancel()
            if self.fix_run:
                self.fix_run.cancel.set()

    def _run(self, session: AssistantSession, text: str) -> None:
        for event in session.send(text):
            self.publish(event)

    def _fix(self, session: AssistantSession, text: str) -> None:
        """Fix mode: the sandboxed engine from `pcode fix`. Nothing reaches the folder until Apply."""
        try:
            run = self.manager.start(RunRequest(task=text, repo=str(session.project),
                                                provider=self.provider_name or pick_provider()))
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
        hub.mode = body.mode
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
        return {"history": session.history}

    @app.post("/api/ide/fix/apply", dependencies=guard)
    def apply_fix(body: ApplyFixRequest):
        return hub.apply_fix(body.run_id)

    @app.post("/api/ide/command", dependencies=guard, status_code=202)
    def command(body: CommandRequest):
        hub.run_command(body.command)
        return {"started": True}

    @app.websocket("/api/ide/ws")
    async def socket(ws: WebSocket):
        await ws.accept()
        try:
            hello = await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT)
            first = json.loads(hello)
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
            await ws.close(AUTH_FAILED)
            return
        if not isinstance(first, dict) or not hub.check(first.get("token")):
            await ws.close(AUTH_FAILED)
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


def make_hub(provider: Optional[str] = None, mode: str = "ask", cache: Optional[str] = None) -> IdeHub:
    from puppeteer_code.providers import ProviderSession

    load_keys()
    return IdeHub(new_token(), lambda name: ProviderSession(name, cache_dir=cache), provider=provider, mode=mode)


__all__ = ["IdeHub", "register_ide", "make_hub", "new_token", "scrub"]
