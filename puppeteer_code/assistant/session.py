"""
puppeteer_code/assistant/session.py
===================================
One conversation on one project. `send()` yields events; the terminal and the IDE
both read this same stream, so there is one engine.

The work happens on a worker thread so that an approval can pause it: the front
end sees `approval_request` and calls `answer()`.

Events: text_delta, notice, plan, agent, tool_start, tool_end, file_changed,
command_output, preview, approval_request, verify, checkpoint, rollback, usage,
effort, done, error.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar, Union

from puppeteer_code.assistant.activity import Activity
from puppeteer_code.assistant.agents import BUILDER_ONLY, INSTRUCTIONS, PLANNER_READ, SPECS, UPDATE_TESTS, AgentSpec
from puppeteer_code.assistant.codemap import sync, tree_sitter_ready
from puppeteer_code.assistant.context import (RELEVANT_FILES, conversation, file_map, fold, path_of, saved_chars,
                                              task_for, trim)
from puppeteer_code.assistant.effort import (LEVELS, MAX_NOTES, MAX_REASONING_TOKENS, REASONING_PROMPT, describe,
                                             reasons, think_option, tier_for)
from puppeteer_code.assistant.graph import CodeGraph, database_path
from puppeteer_code.assistant.policy import DirectPolicy, make_policy, reward
from puppeteer_code.assistant.shield import RequestState, allowed
from puppeteer_code.assistant.snapshots import Snapshots, pcode_home
from puppeteer_code.assistant.toolbox import Toolbox, is_test_file
from puppeteer_code.assistant.verify import Check, detect, improved, regressed, run_checks, testable
from puppeteer_code.bench import is_quota_error
from puppeteer_code.integrity import scan_diff
from puppeteer_code.ollama import Cancelled
from puppeteer_code.policy import Policy
from puppeteer_code.sandbox import private_runner
from puppeteer_code.workspace import scrub, scrub_obj

T = TypeVar("T")

MAX_STEPS = 12
TOKEN_BUDGET = 60000
REPEAT_LIMIT = 3
DIFF_CHARS = 6000
SMALL_WORDS = 12
MAX_PLAN_STEPS = 6
MODES = ("plan", "ask", "auto")
TITLE_CHARS = 60
CHARS_PER_TOKEN = 4
MEMORY_OFF = "Memory is off for this request because its store could not be used. Your work is not affected."
TOOL_FAILED = ("That tool call was malformed and did not run, so nothing changed. Call one of the offered "
               "tools by its exact name, with valid JSON arguments.")
MAX_CUT_OFFS = 2
WRITERS = ("Builder", "Debugger", "TestWriter")
WRITING = ("write_file", "edit_file", "delete_file")
CUT_OFF = ("Your reply was too long and was cut off, so nothing ran. Do not write a whole file again. Use edit_file to "
           "change one small part now, and do the rest in later turns. If the file is new, write a short first version. "
           "Keep your reasoning brief.")
CUT_OFF_NOTICE = "That reply was too long to finish, so nothing from it was applied. Trying again with smaller edits."
CUT_OFF_STOP = ("The model kept running out of room for its reply, so I stopped. Nothing from those replies was applied. "
                "Try asking for one smaller change at a time.")
NOTHING_DONE = "I did not manage to change anything, so nothing was built."
GAVE_UP = "I could not do this without breaking what worked, so I put the code back as it was."
NO_PLAN = "I could not write a plan for this. Try saying more about what you want."
DEEP_SPEND = "Deep effort makes extra model calls, so this request will use more tokens."
PRIVATE_NOTE = ("This runs only on the user's computer. You cannot start servers or previews: give the user the "
                "command to run instead.")
NO_NETWORK = ("Commands you run have no network, so nothing can be downloaded. If a package is missing, ask the "
              "user to install it.")
BLOCK_LOST = ("The check that commands have no network did not pass just now, so they may not be blocked. "
              "Auto is off, and I will ask before commands.")
PRIVATE_ONLY = "That conversation is private. Open it in Private mode."
CLOUD_ONLY = "That conversation used a cloud model. Turn Private off to open it."

_SESSION_ID = re.compile(r"[\w-]+")
_ACTION = re.compile(r"\b(add|build|change|create|delete|fix|implement|make|move|refactor|remove|"
                     r"rename|replace|update|write|generate|install|set up|setup|convert|run)\b", re.I)
_QUESTION = re.compile(r"^(what|why|how|where|when|who|which|does|do|is|are|can|could|should|"
                       r"explain|describe|show me|tell me)\b", re.I)


def parse_plan(text: str) -> tuple[str, list[str]]:
    """The stack and the checklist from a Planner reply."""
    stack = re.search(r"STACK:\s*(\w+)", text, re.I)
    steps = [m.strip() for m in re.findall(r"^\s*\d+[.)]\s*(.+)$", text, re.M)][:MAX_PLAN_STEPS]
    return (stack.group(1).lower() if stack else ""), steps


def is_question(message: str) -> bool:
    """Cheap rule, no tokens spent. A wrong guess only costs a follow-up, because questions cannot edit."""
    text = message.strip()
    if _ACTION.search(text) and not re.match(r"^(what|why|how|where|which)\b", text, re.I):
        return False
    return text.endswith("?") or bool(_QUESTION.match(text))


class Stop(Exception):
    """Ends a request early with a status and a plain reason for the user."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class AssistantSession:
    def __init__(self, project: str | Path, provider, mode: str = "ask", home: Optional[Path] = None,
                 max_steps: int = MAX_STEPS, token_budget: Optional[int] = None, session_id: Optional[str] = None,
                 policy: Union[str, Policy] = "heuristic", max_turns: Optional[int] = None,
                 fallback: bool = True, effort: str = "normal", memory: bool = False, live: bool = False):
        self.project = Path(project).resolve()
        self.project.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.home = Path(home or pcode_home())
        self.max_steps = max_steps
        self.max_turns = max_turns      # overrides each agent's own limit
        self.private = provider.private
        self.token_budget = token_budget or provider.spec.get("token_budget", TOKEN_BUDGET)
        self.max_seconds = provider.spec.get("max_seconds")
        self.fallback = fallback and not self.private   # benchmarks turn this off so a row used the models it names
        self.policy = make_policy(policy) if isinstance(policy, str) else policy
        self.id = session_id or self._new_id()
        self.history: list[dict] = []
        self.turns: list[dict] = []     # one result per request, so a resumed feed shows more than text
        self.title = ""                 # a name the user chose
        self.memory = memory            # off by default, so benchmarks send what they always sent
        self.activity = Activity(self._emit) if live else None      # off too: a live reply is a streamed call
        self._at = 0                    # the step in progress
        self.graph: Optional[CodeGraph] = None
        self._memory_failed = False
        self._brief = ""
        self._agents: list[str] = []
        self._saved_chars = 0
        self.undo_stack: list[dict] = []
        self.pending_plan: Optional[dict] = None
        self.set_effort(effort)
        self._level = effort            # the effort of the request in progress
        self._return_mode = "ask"       # where leaving plan mode goes
        self.snapshots = Snapshots(self.project, self.home)
        self.tools = Toolbox(self.project, self._emit, self._approve, mode,
                             runner=private_runner(self.project, self.home) if self.private else None)
        self.tools.diffs = live
        self.withheld = ("start_server",) if self.private else ()
        self._events: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._lock = threading.Lock()
        self._busy = False
        self._cancel = threading.Event()
        if self.private:
            provider.client.inner.cancel = self._cancel     # Stop reaches a reply in progress
            self.tools.mode = self._safe_mode(mode)
        self._pending: dict[str, dict] = {}
        self._request_start = 0
        self._started = 0.0

    # ── front-end API ────────────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._busy

    def send(self, message: str, plan: Optional[dict] = None, effort: Optional[str] = None) -> Iterator[dict]:
        """`plan` carries an approved plan. `effort` applies to this one request."""
        with self._lock:
            if self._busy:
                raise RuntimeError("the assistant is already working on a request")
            self._busy = True
        self._cancel.clear()
        self._events = queue.Queue()
        self._level = effort or self.effort
        worker = threading.Thread(target=self._work, args=(message, plan), daemon=True)
        worker.start()
        try:
            while True:
                try:
                    event = self._events.get(timeout=0.2)   # a timeout keeps Ctrl+C working on Windows
                except queue.Empty:
                    continue
                except KeyboardInterrupt:
                    self._cancel.set()
                    continue
                if event is None:
                    return
                yield event
        finally:
            self._cancel.set()
            worker.join(5)

    def answer(self, request_id: str, approved: bool) -> bool:
        pending = self._pending.get(request_id)
        if pending is None:
            return False
        pending["approved"] = bool(approved)
        pending["event"].set()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("mode must be plan, ask or auto")
        if self._busy and "plan" in (mode, self.tools.mode):
            raise RuntimeError("wait for the current request to finish")
        if mode == "plan" and self.tools.mode != "plan":
            self._return_mode = self.tools.mode
        self.tools.mode = self._safe_mode(mode)

    def _safe_mode(self, mode: str) -> str:
        return "ask" if mode == "auto" and self.private and not self.tools.runner.contained else mode

    def set_effort(self, level: str) -> None:
        if level not in LEVELS:
            raise ValueError("effort must be fast, normal, think or deep")
        self.effort = level

    def approve_plan(self) -> Iterator[dict]:
        """Run the waiting plan. Only the front ends call this: no tool or agent can reach it."""
        plan = self.pending_plan
        if plan is None:
            raise ValueError("no plan is waiting for approval")
        self.set_mode(plan["return_mode"])
        return self.send(plan["request"], plan=plan)

    def undo(self) -> dict:
        """Undo the latest request. A file the user changed afterwards is kept and reported."""
        if self._busy:
            raise RuntimeError("wait for the current request to finish")
        if not self.undo_stack:
            return {"restored": [], "kept": []}
        entry = self.undo_stack.pop()
        kept = sorted(set(entry["files"]) & set(self.snapshots.changed_since(entry["after"])))
        restored = self.snapshots.restore(entry["before"], [f for f in entry["files"] if f not in kept])
        self._remember(lambda graph: self._undo_memory(graph, entry.get("episode"), kept))
        self.save()
        return {"restored": restored, "kept": kept}

    def clear(self) -> None:
        """Start a new conversation. The old one stays resumable, and Undo still works."""
        self.id, self.history, self.pending_plan = self._new_id(), [], None
        self.turns, self.title = [], ""

    def rename(self, session_id: str, title: str) -> None:
        title = scrub(" ".join(title.split()))[:TITLE_CHARS]
        if not title:
            raise ValueError("A session needs a name.")
        if session_id == self.id:
            self.title = title
            if self.history:
                self.save()
                self._remember(lambda graph: graph.touch_session(session_id, title))
            return
        data = self._own(session_id)
        data["title"] = title
        self._file(session_id).write_text(json.dumps(data, indent=1), encoding="utf-8")
        self._remember(lambda graph: graph.touch_session(session_id, title))

    def delete(self, session_id: str) -> None:
        """Forget a conversation. Project files and snapshots stay, so this changes no code."""
        if self._busy:
            raise RuntimeError("wait for the current request to finish")
        if session_id == self.id:
            self._path().unlink(missing_ok=True)
            self.clear()
        else:
            self._own(session_id)
            self._file(session_id).unlink()
        self._remember(lambda graph: graph.forget_session(session_id))

    def close(self) -> None:
        self.cancel()
        self.tools.close()
        if self.graph:
            self.graph.close()
            self.graph = None

    # ── saving ───────────────────────────────────────────────────────────────

    @staticmethod
    def _new_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def _path(self) -> Path:
        return self._file(self.id)

    def _file(self, session_id: str) -> Path:
        return self.home / "sessions" / f"{session_id}.json"

    def save(self) -> None:
        self._path().parent.mkdir(parents=True, exist_ok=True)
        data = {"id": self.id, "project": str(self.project), "saved": time.time(),
                "history": self.history, "turns": self.turns, "undo": self.undo_stack}
        if self.title:
            data["title"] = self.title
        if self.pending_plan:
            data["pending_plan"] = self.pending_plan
        if self.private:
            data["private"] = True
        self._path().write_text(json.dumps(scrub_obj(data), indent=1), encoding="utf-8")

    @staticmethod
    def _load(home: Path, session_id: str, private: bool) -> dict:
        """A saved conversation of this mode. The id comes from a request, so it must be a plain name."""
        if not _SESSION_ID.fullmatch(session_id):
            raise FileNotFoundError(session_id)
        data = json.loads((home / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))
        if bool(data.get("private")) != bool(private):
            raise ValueError(PRIVATE_ONLY if data.get("private") else CLOUD_ONLY)
        return data

    def _own(self, session_id: str) -> dict:
        """As `_load`, for this project: another project's undo stack means nothing here."""
        data = self._load(self.home, session_id, self.private)
        if data.get("project") != str(self.project):
            raise FileNotFoundError(session_id)
        return data

    def _restore(self, data: dict) -> None:
        self.history, self.undo_stack = data["history"], data["undo"]
        self.title = data.get("title", "")
        self.pending_plan = data.get("pending_plan")
        turns = data.get("turns", [])
        asked = sum(m["role"] == "user" for m in self.history)
        self.turns = [{} for _ in range(asked - len(turns))] + turns    # older files kept no results

    @classmethod
    def resume(cls, session_id: str, provider, home: Optional[Path] = None, **options) -> "AssistantSession":
        data = cls._load(Path(home or pcode_home()), session_id, provider.private)
        session = cls(data["project"], provider, home=home, session_id=session_id, **options)
        session._restore(data)
        return session

    def adopt(self, session_id: str) -> None:
        """Continue an earlier conversation on this same project."""
        self._restore(self._own(session_id))
        self.id = session_id

    @staticmethod
    def _title(data: dict) -> str:
        first = next((m["content"] for m in data["history"] if m["role"] == "user"), "")
        return data.get("title") or " ".join(first.split())[:TITLE_CHARS]

    @property
    def display_title(self) -> str:
        return self._title({"title": self.title, "history": self.history})

    @staticmethod
    def list_sessions(project: str | Path, home: Optional[Path] = None, private: Optional[bool] = None) -> list[dict]:
        """Newest first. `private` keeps one mode's conversations out of the other's list."""
        folder = Path(home or pcode_home()) / "sessions"
        target = str(Path(project).resolve())
        rows = []
        for path in folder.glob("*.json") if folder.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("project") == target and private in (None, bool(data.get("private"))):
                rows.append({"id": data["id"], "saved": data["saved"], "title": AssistantSession._title(data),
                             "turns": sum(m["role"] == "user" for m in data["history"])})
        return sorted(rows, key=lambda r: r["saved"], reverse=True)

    # ── internals ────────────────────────────────────────────────────────────

    def _emit(self, event: dict) -> None:
        if self.activity is not None:
            self.activity.see(event)
        self._events.put(scrub_obj(event))

    def _approve(self, request: dict) -> bool:
        request_id = uuid.uuid4().hex[:12]
        pending = {"event": threading.Event(), "approved": False}
        self._pending[request_id] = pending
        self._emit({"type": "approval_request", "id": request_id, **request})
        while not pending["event"].wait(0.1):
            if self._cancel.is_set():
                break
        self._pending.pop(request_id, None)
        return pending["approved"]

    def _spent(self) -> int:
        return self.provider.meter.total_tokens - self._request_start

    # ── memory ───────────────────────────────────────────────────────────────

    def _remember(self, work: Callable[[CodeGraph], T]) -> Optional[T]:
        """Memory never fails a request: one notice, then off until the next one."""
        if not self.memory or self._memory_failed:
            return None
        try:
            if self.graph is None:
                self.graph = CodeGraph(database_path(self.project, self.home, self.private))
            return work(self.graph)
        except Exception as e:
            self._memory_failed = True
            self._emit({"type": "notice", "text": f"{MEMORY_OFF} ({type(e).__name__})"})
            return None

    def graph_view(self, scope: str, q: str, kinds: list[str], limit: int) -> Optional[dict]:
        found = self._remember(lambda graph: graph.view(scope, self.id, q, kinds, limit))
        return {**found, "scripts": tree_sitter_ready()} if found else None

    def graph_node(self, node_id: str) -> Optional[dict]:
        node = self._remember(lambda graph: graph.detail(node_id))
        if node and node.get("file"):
            try:
                self.tools.resolve(node["file"])    # a stored path is not trusted to stay inside the project
            except (PermissionError, ValueError, OSError):
                node["file"] = None
        return node

    def _prepare_memory(self, graph: CodeGraph, message: str) -> Optional[list[str]]:
        first = graph.get_meta("indexed_sha") is None
        change = sync(graph, self.tools, self.snapshots)
        if first:
            self._emit({"type": "memory", "text": f"Mapped this project for memory: {len(change['added'])} items."})
        if change["added"] or change["removed"]:
            self._emit({"type": "graph_delta", **change, "saved": graph.saved(self.id)})
        self._brief = graph.brief(self.id, message)
        return graph.related_files(message, RELEVANT_FILES) or None

    def _record(self, graph: CodeGraph, before: Optional[str], after: Optional[str], message: str,
                turn: dict, files: list[str]) -> str:
        """Everything in an episode was seen by code. A model's account of its own work is never stored."""
        change = sync(graph, self.tools, self.snapshots)
        diff = self.snapshots.diff(before, files) if before and files else ""
        node = graph.add_episode(self.id, len(self.turns), self.display_title, message, self._agents, turn["status"],
                                 turn["check"], files, before, after, turn["tokens"], diff)
        saved = graph.add_saved(self.id, self._saved_chars // CHARS_PER_TOKEN)
        self._emit({"type": "graph_delta", "added": [*change["added"], node], "removed": change["removed"],
                    "saved": saved})
        return node

    def _undo_memory(self, graph: CodeGraph, episode: Optional[str], kept: list[str]) -> None:
        """An undo entry from before memory, or from Fix mode, has no episode. The code still re-indexes."""
        if episode and kept:
            graph.keep_only(episode, kept)
        elif episode:
            graph.drop_episode(episode)
        sync(graph, self.tools, self.snapshots)

    def _work(self, message: str, plan: Optional[dict]) -> None:
        try:
            if self.activity is not None:
                self.activity.begin()
            self._request(message, plan)
        except Exception as e:  # never leave a front end waiting on a dead worker
            self._emit({"type": "error", "message": str(e)[:300]})
            self._emit({"type": "done", "status": "error", "reason": str(e)[:300], "files": [],
                        "can_undo": bool(self.undo_stack)})
        finally:
            if self.activity is not None:
                self.activity.end()
            self._busy = False
            self._events.put(None)

    def _request(self, message: str, plan: Optional[dict] = None) -> None:
        self._request_start = self.provider.meter.total_tokens
        self._started = time.monotonic()
        self._at = 0
        while self.provider.notices:
            self._emit({"type": "notice", "text": self.provider.notices.pop(0)})
        before, st = None, None
        status, reason, reply = "done", "", ""
        self.tools.touched.clear()
        self.tools.scope = "any"
        self._memory_failed, self._brief, self._agents, self._saved_chars = False, "", [], 0
        related = self._remember(lambda graph: self._prepare_memory(graph, message))
        question = plan is None and is_question(message)
        if not question:
            self.pending_plan = None            # a new request replaces a waiting plan
        try:
            self._check_block()
            self._announce_effort()
            if plan:
                self._warn_if_changed(plan)
            if question:
                reply = self._loop(SPECS["Answerer"], message)
            elif plan is None and self.tools.mode == "plan":
                status, reason, reply = self._draft_plan(message)
            elif isinstance(self.policy, DirectPolicy):
                before = self.snapshots.snapshot("before: " + message[:60])
                steps = "\n".join(plan["steps"]) if plan else ""
                task = f"{message}\n\nFollow this plan:\n{steps}" if plan else message
                reply = self._loop(BUILDER_ONLY, f"{self._brief}\n\n{task}" if self._brief else task,
                                   memory_chars=len(self._brief))
            else:
                before = self.snapshots.snapshot("before: " + message[:60])
                st = RequestState(request=message, max_steps=self.max_steps, token_budget=self.token_budget,
                                  start_sha=before, best_sha=before, no_review=self._level == "fast",
                                  memory=self._brief, related=related)
                if plan:
                    st.planned, st.plan, st.stack = True, plan["steps"], plan["stack"]
                reply = self._puppeteer(st)
                if st.failing:
                    status, reason = "failed", st.check.summary
                elif self._gave_up(st):
                    status, reason = "failed", GAVE_UP
                elif not st.built:
                    status, reason = "failed", NOTHING_DONE
        except Stop as stop:
            status, reason = stop.status, stop.reason
        except Exception as e:  # a provider failure must reach the user, not lose the work so far
            status = "quota" if is_quota_error(str(e)) else "error"
            reason = ("The model's usage limit was reached. Your work so far is saved; try again later."
                      if status == "quota" else str(e)[:300])
            self._emit({"type": "error", "message": reason})
        check = st.check if st else None
        turn = {"status": status, "reason": reason, "verified": bool(check and check.verified and check.ok),
                "question": before is None and status == "done", "tokens": self._spent(),
                "check": check.summary if check else ""}
        files = self._finish(before, message, reply or reason, turn)
        if st is not None:
            self._learn(st, files)
        self._emit({"type": "usage", "tokens": self._spent(), "total": self.provider.meter.total_tokens})
        self._emit({"type": "done", "status": status, "reason": reason, "files": files,
                    "can_undo": bool(self.undo_stack), "verified": turn["verified"], "question": turn["question"]})

    def _finish(self, before: Optional[str], message: str, reply: str, turn: dict) -> list[str]:
        files: list[str] = []
        after = None
        if before:
            files = self.snapshots.changed_since(before)
            if files:
                after = self.snapshots.snapshot("after: " + message[:60])
                self.undo_stack.append({"before": before, "after": after, "files": files, "request": message[:200]})
        self.history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        self.turns.append({**turn, "files": files})
        episode = self._remember(lambda graph: self._record(graph, before, after, message, turn, files))
        if episode and files:
            self.undo_stack[-1]["episode"] = episode
        self.save()
        return files

    # ── the puppeteer ────────────────────────────────────────────────────────

    def _puppeteer(self, st: RequestState) -> str:
        """One agent per step: the shield filters, the policy picks, the checks decide what stands."""
        empty = file_map(self.tools) == "(the folder is empty)"
        st.new_project = empty
        st.small = (not empty and len(st.request.split()) <= SMALL_WORDS and not st.planned
                    and self._level != "deep")
        st.testable = testable(self.project, st.stack)
        if detect(self.project):
            st.baseline = st.best = st.check = self._verify()
        self.policy.begin_episode()
        while True:
            self._limits()
            st.tokens = self._spent()
            action = self.policy.select(st, allowed(st))
            if action == "Responder":
                break
            self._at = st.step
            self._step(action, st)
            st.used[action] += 1
            st.last_agent = action
            st.step += 1
        return self._respond(st)

    def _limits(self) -> None:
        if self._cancel.is_set():
            raise Stop("cancelled", "Stopped by you.")
        if self._spent() >= self.token_budget:
            raise Stop("stopped", f"Stopped at the limit of {self.token_budget} tokens for one request.")
        if self.max_seconds and time.monotonic() - self._started >= self.max_seconds:
            raise Stop("stopped", f"Stopped at the limit of {self.max_seconds // 60} minutes for one request.")

    def _step(self, action: str, st: RequestState) -> None:
        if action == "Planner":
            self._plan(st)
        elif action == "Reviewer":
            self._review(st)
        else:
            self._edit_step(action, st)

    def _plan(self, st: RequestState) -> None:
        self._saved_chars -= len(self._brief)
        text = self._once(SPECS["Planner"], f"Request: {st.request}\n\n{self._memory_block()}Files:\n{file_map(self.tools)}")
        st.stack, st.plan = parse_plan(text)
        if st.stack:
            st.testable = testable(self.project, st.stack)
        if st.plan:
            self._emit({"type": "plan", "steps": st.plan})

    def _draft_plan(self, message: str) -> tuple[str, str, str]:
        """Plan mode: read the project and propose a plan. Nothing runs until the user approves it."""
        sha = self.snapshots.snapshot("plan: " + message[:60])
        text = self._loop(PLANNER_READ, f"Request: {message}\n\n{self._memory_block()}Files:\n{file_map(self.tools)}",
                          quiet=True, memory_chars=len(self._brief))
        stack, steps = parse_plan(text)
        if not steps:
            return "failed", NO_PLAN, NO_PLAN
        self.pending_plan = {"request": message, "steps": steps, "stack": stack, "sha": sha,
                             "return_mode": self._return_mode}
        self._emit({"type": "plan", "steps": steps, "pending": True})
        return "planned", "", "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))

    def _memory_block(self) -> str:
        return f"{self._brief}\n\n" if self._brief else ""

    def _warn_if_changed(self, plan: dict) -> None:
        changed = self.snapshots.changed_since(plan["sha"])
        if changed:
            self._emit({"type": "notice", "text": f"Files changed since this plan was written: {', '.join(changed[:5])}."})

    def _check_block(self) -> None:
        """Before every request, inside the container. A failure is always said out loud."""
        runner = self.tools.runner
        if runner.name != "direct" and runner.contained and not runner.verify():
            self.tools.mode = self._safe_mode(self.tools.mode)
            self._emit({"type": "notice", "text": BLOCK_LOST})

    def _announce_effort(self) -> None:
        if self._level == "normal":
            return
        values = self.provider.thinking_values(self._model(SPECS["Builder"]))
        self._emit({"type": "effort", "level": self._level, "how": describe(self._level, values)})
        if self._level == "deep" and self.provider.provider != "local":
            self._emit({"type": "notice", "text": DEEP_SPEND})

    def _edit_step(self, action: str, st: RequestState) -> None:
        spec = SPECS[action]
        instruction = INSTRUCTIONS[action]
        if action == "TestWriter" and st.built:
            instruction = UPDATE_TESTS
        scope = spec.scope if action == "TestWriter" or st.has_tests else "any"
        self.tools.scope = scope
        before = self.snapshots.snapshot(f"step {st.step}: {action}")
        commands = self.tools.commands
        note = self._loop(spec, task_for(st, self.tools, instruction), history=False, memory_chars=len(st.memory))
        self.tools.scope = "any"
        st.notes.append(f"{spec.label}: {note[:200]}")
        changed = self.snapshots.changed_since(before)
        if action == "Builder" and (changed or self.tools.commands > commands):
            st.built = True                     # a reply alone has built nothing
        if action == "Debugger" and st.review:
            st.review_handled = True
        if changed or st.check is None:
            self._judge(action, st)
        else:
            st.stall += 1

    def _verify(self) -> Check:
        if self.activity is not None:
            self.activity.set_phase("checking")
        check = run_checks(self.project, self.tools)
        self._emit({"type": "verify", "ok": check.ok, "kind": check.kind, "summary": check.summary,
                    "verified": check.verified})
        return check

    def _judge(self, action: str, st: RequestState) -> None:
        """A pass checkpoints. A regression gets one repair attempt, then goes back to the last good state."""
        now = self._verify()
        st.check = now
        st.failure = "" if now.ok else now.output
        if action == "TestWriter" or improved(st.best, now) or (now.ok and not regressed(st.best, now)):
            if action != "TestWriter" and not improved(st.best, now):
                st.stall += 1
            else:
                st.stall = 0
            st.best, st.regressions = now, 0
            st.best_sha = self.snapshots.snapshot(f"checkpoint: {now.summary}")
            self._emit({"type": "checkpoint", "summary": now.summary})
            return
        st.stall += 1
        if regressed(st.best, now):
            st.regressions += 1
            if st.regressions >= 2:
                self.snapshots.restore(st.best_sha)
                st.rollbacks += 1
                st.regressions = 0
                st.check = st.best
                st.built = False        # try once more from the good state; the failure is still in view
                self._emit({"type": "rollback", "reason": f"that change made things worse. {now.summary}"})

    def _review(self, st: RequestState) -> None:
        diff = self.snapshots.diff(st.start_sha)
        text = self._once(SPECS["Reviewer"], f"Request: {st.request}\n\nChange:\n{diff[:DIFF_CHARS]}")
        st.review = "" if re.match(r"\W*ok\b", text.strip(), re.I) else text.strip()
        st.review_handled = not st.review
        if not st.review:
            st.review = "OK"

    def _tampering(self, files: list[str], before: str) -> list[str]:
        tests = [f for f in files if is_test_file(f)]
        return scan_diff(self.snapshots.diff(before, tests)) if tests and before else []

    def _respond(self, st: RequestState) -> str:
        """The Responder sees facts from the checks. The note on what was verified is added by code."""
        self._limits()
        if self.private:
            return self._report(st)
        check = st.check
        facts = [f"Request: {st.request}", f"Check result: {check.summary if check else 'nothing was checked'}"]
        if st.notes:
            facts.append("Work:\n" + "\n".join(st.notes[-4:]))
        if st.rollbacks:
            facts.append(f"{st.rollbacks} change(s) were undone because they made things worse.")
        if st.review not in ("", "OK") and st.review_handled is False:
            facts.append(f"Unresolved review concerns: {st.review[:400]}")
        text = self._once(SPECS["Responder"], "\n\n".join(facts), stream=True)
        note = self._honesty_note(st)
        if note:
            self._emit({"type": "text_delta", "text": f"\n\n{note}"})
            text = f"{text}\n\n{note}"
        return text

    def _report(self, st: RequestState) -> str:
        """Private's reply, written by code. A real local model denied finished work with the file list in its prompt."""
        changed = self.snapshots.changed_since(st.start_sha)
        lines = [f"Changed: {', '.join(changed[:20])}." if changed else "No files were changed."]
        if st.rollbacks:
            lines.append(f"{st.rollbacks} change(s) were undone because they made things worse.")
        if st.review not in ("", "OK") and st.review_handled is False:
            lines.append(f"Unresolved review concerns: {st.review[:400]}")
        note = self._honesty_note(st)
        lines.append(note or st.check.summary)
        text = "\n\n".join(lines)
        self._emit({"type": "text_delta", "text": text})
        return text

    @staticmethod
    def _gave_up(st: RequestState) -> bool:
        return bool(st.rollbacks) and not improved(st.baseline, st.check)

    def _honesty_note(self, st: RequestState) -> str:
        check = st.check
        if self._gave_up(st):
            return GAVE_UP
        if not st.built:
            return NOTHING_DONE
        if check is None or check.kind == "none":
            return "I could not check this automatically, so please try it yourself."
        if not check.ok:
            return f"It does not fully work yet. {check.summary}"
        if not check.verified:
            return f"{check.summary} Please try it yourself."
        return ""

    def _learn(self, st: RequestState, files: list[str]) -> None:
        tampered = bool(self._tampering(files, self.undo_stack[-1]["before"])) if files else False
        if tampered:
            self._emit({"type": "text_delta", "text": "\n\nWarning: tests were weakened in this change. Check them."})
        decisions = st.step + 1
        self.policy.end_episode([reward(st, tampered)] * decisions)

    # ── model calls ──────────────────────────────────────────────────────────

    def _context(self) -> list[dict]:
        return conversation(self.history)

    def _system(self, spec: AgentSpec) -> str:
        if not self.private:
            return spec.prompt
        return " ".join([spec.prompt, PRIVATE_NOTE, *([NO_NETWORK] if self.tools.runner.contained else [])])

    def _model(self, spec: AgentSpec) -> str:
        return self.provider.model_for(spec.name, tier_for(self._level, spec.name))

    def _reasoned(self, spec: AgentSpec, task: str) -> str:
        """A reasoning pass, when this effort calls for one. Its notes are never saved to history."""
        if not reasons(self._level, spec.name, self.provider.thinking_values(self._model(spec))):
            return task
        reply = self._chat(spec, trim([{"role": "system", "content": REASONING_PROMPT},
                                       {"role": "user", "content": task}]), max_tokens=MAX_REASONING_TOKENS)
        notes = (reply.get("content") or "").strip()[:MAX_NOTES]
        return f"{task}\n\nYour notes from thinking this through:\n{notes}" if notes else task

    def _agent_event(self, spec: AgentSpec) -> dict:
        return {"type": "agent", "agent": spec.name, "label": spec.label, "model": self._model(spec),
                "step": self._at + 1, "max_steps": self.max_steps}

    def _once(self, spec: AgentSpec, task: str, stream: bool = False) -> str:
        """One model call with no tools."""
        self._emit(self._agent_event(spec))
        self._agents.append(spec.name)
        task = self._reasoned(spec, task)
        on_delta = (lambda text: self._emit({"type": "text_delta", "text": text})) if stream else None
        reply = self._chat(spec, [{"role": "system", "content": self._system(spec)},
                                  {"role": "user", "content": task}], on_delta=on_delta)
        return reply.get("content") or ""

    def _chat(self, spec: AgentSpec, messages: list[dict], **extra) -> dict:
        """When the strong model is out of quota, carry on with the fast one instead of stopping."""
        settings = self.provider.settings_for(spec.name)
        model = self._model(spec)
        room = self.provider.spec.get("reply_tokens") if spec.name in WRITERS else None
        options = {"messages": messages, "temperature": settings.temperature, "max_tokens": room or settings.max_tokens,
                   **think_option(self._level, spec.name, self.provider.thinking_values(model)), **extra}
        if self.activity is not None:
            options["on_progress"] = self.activity.progress
            self.activity.reply_started()
        try:
            reply = self.provider.client.chat(model=model, **options)
        except Cancelled:
            raise Stop("cancelled", "Stopped by you.")
        except Exception as e:
            fast = self.provider.tiers.get("fast")
            if not self.fallback or not is_quota_error(str(e)) or model == fast:
                raise
            self.provider.tiers["quality"] = fast
            self._emit({"type": "notice", "text": "The stronger model hit its usage limit, so I switched to "
                                                  "the faster one. Results may be weaker."})
            reply = self.provider.client.chat(model=fast, **options)
        if self.activity is not None:
            self.activity.reply_ended()
        self._emit({"type": "usage", "tokens": self._spent(), "total": self.provider.meter.total_tokens})
        return reply

    def _loop(self, spec: AgentSpec, task: str, history: bool = True, quiet: bool = False,
              memory_chars: int = 0) -> str:
        tools = [name for name in spec.tools if name not in self.withheld]
        max_turns = self.max_turns or spec.max_turns
        self._emit(self._agent_event(spec))
        self._agents.append(spec.name)
        task = self._reasoned(spec, task)
        messages = [{"role": "system", "content": self._system(spec)}, *(self._context() if history else []),
                    {"role": "user", "content": task}]
        repeats: Counter = Counter()
        folded_paths: set[str] = set()
        removed = 0
        cut_offs = 0
        for _ in range(max_turns):
            self._limits()
            if self.memory:
                for item in fold(messages):
                    repeats.pop(item["key"], None)      # reading a folded file again is not a stuck loop
                    folded_paths.add(item["path"])
                    removed += item["chars"]
            prompt = trim(messages)
            if self.memory:
                self._saved_chars += saved_chars(prompt, removed, memory_chars)
            reply = self._chat(spec, prompt, tools=self.tools.specs(tools))
            calls = reply.get("tool_calls") or []
            text = reply.get("content") or ""
            if reply.get("cut_off"):            # the same request would only be cut off again
                cut_offs += 1
                if cut_offs > MAX_CUT_OFFS:
                    raise Stop("stopped", CUT_OFF_STOP)
                self._emit({"type": "notice", "text": CUT_OFF_NOTICE})
                messages.append({"role": "user", "content": CUT_OFF})
                continue
            if reply.get("tool_failed"):        # a malformed call is not a finished agent
                messages.append({"role": "user", "content": TOOL_FAILED})
                continue
            if not calls:
                if history and not quiet:
                    self._emit({"type": "text_delta", "text": text})
                return text
            messages.append({"role": "assistant", "content": text, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in calls]})
            for call in calls:
                repeats[(call["name"], call["arguments"])] += 1
                if repeats[(call["name"], call["arguments"])] > REPEAT_LIMIT:
                    raise Stop("stopped", "Stopped because the same step kept repeating without progress.")
                if self._cancel.is_set():
                    raise Stop("cancelled", "Stopped by you.")
                result = self.tools.call(call["name"], call["arguments"], tools)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                if call["name"] in WRITING and not result.startswith("ERROR"):
                    repeats.clear()             # the files changed, so the same step can find something new
                if call["name"] == "read_file" and path_of(call["arguments"]) in folded_paths:
                    removed -= len(result)
        raise Stop("stopped", f"Stopped after {max_turns} model turns without finishing.")
