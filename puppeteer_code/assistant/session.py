"""
puppeteer_code/assistant/session.py
===================================
One conversation on one project. `send()` yields events; the terminal and the IDE
both read this same stream, so there is one engine.

The work happens on a worker thread so that an approval can pause it: the front
end sees `approval_request` and calls `answer()`.

Events: text_delta, notice, plan, agent, tool_start, tool_end, file_changed,
command_output, preview, approval_request, verify, checkpoint, rollback, usage,
done, error.
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
from typing import Iterator, Optional, Union

from puppeteer_code.assistant.agents import BUILDER_ONLY, INSTRUCTIONS, SPECS, UPDATE_TESTS, AgentSpec
from puppeteer_code.assistant.context import conversation, file_map, task_for, trim
from puppeteer_code.assistant.policy import DirectPolicy, make_policy, reward
from puppeteer_code.assistant.shield import RequestState, allowed
from puppeteer_code.assistant.snapshots import Snapshots, pcode_home
from puppeteer_code.assistant.toolbox import Toolbox, is_test_file
from puppeteer_code.assistant.verify import Check, detect, improved, regressed, run_checks, testable
from puppeteer_code.bench import is_quota_error
from puppeteer_code.integrity import scan_diff
from puppeteer_code.policy import Policy
from puppeteer_code.workspace import scrub_obj

MAX_STEPS = 12
TOKEN_BUDGET = 60000
REPEAT_LIMIT = 3
DIFF_CHARS = 6000
SMALL_WORDS = 12
MAX_PLAN_STEPS = 6
TOOL_FAILED = ("That tool call was malformed and did not run, so nothing changed. Call one of the offered "
               "tools by its exact name, with valid JSON arguments.")
NOTHING_DONE = "I did not manage to change anything, so nothing was built."
GAVE_UP = "I could not do this without breaking what worked, so I put the code back as it was."

_ACTION = re.compile(r"\b(add|build|change|create|delete|fix|implement|make|move|refactor|remove|"
                     r"rename|replace|update|write|generate|install|set up|setup|convert|run)\b", re.I)
_QUESTION = re.compile(r"^(what|why|how|where|when|who|which|does|do|is|are|can|could|should|"
                       r"explain|describe|show me|tell me)\b", re.I)


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
                 max_steps: int = MAX_STEPS, token_budget: int = TOKEN_BUDGET, session_id: Optional[str] = None,
                 policy: Union[str, Policy] = "heuristic", max_turns: Optional[int] = None,
                 fallback: bool = True):
        self.project = Path(project).resolve()
        self.project.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.home = Path(home or pcode_home())
        self.max_steps = max_steps
        self.max_turns = max_turns      # overrides each agent's own limit
        self.token_budget = token_budget
        self.fallback = fallback        # benchmarks turn this off so a row used the models it names
        self.policy = make_policy(policy) if isinstance(policy, str) else policy
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.history: list[dict] = []
        self.undo_stack: list[dict] = []
        self.snapshots = Snapshots(self.project, self.home)
        self.tools = Toolbox(self.project, self._emit, self._approve, mode)
        self._events: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._lock = threading.Lock()
        self._busy = False
        self._cancel = threading.Event()
        self._pending: dict[str, dict] = {}
        self._request_start = 0

    # ── front-end API ────────────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._busy

    def send(self, message: str) -> Iterator[dict]:
        with self._lock:
            if self._busy:
                raise RuntimeError("the assistant is already working on a request")
            self._busy = True
        self._cancel.clear()
        self._events = queue.Queue()
        worker = threading.Thread(target=self._work, args=(message,), daemon=True)
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
        if mode not in ("ask", "auto"):
            raise ValueError("mode must be ask or auto")
        self.tools.mode = mode

    def undo(self) -> dict:
        """Undo the latest request. A file the user changed afterwards is kept and reported."""
        if self._busy:
            raise RuntimeError("wait for the current request to finish")
        if not self.undo_stack:
            return {"restored": [], "kept": []}
        entry = self.undo_stack.pop()
        kept = sorted(set(entry["files"]) & set(self.snapshots.changed_since(entry["after"])))
        restored = self.snapshots.restore(entry["before"], [f for f in entry["files"] if f not in kept])
        self.save()
        return {"restored": restored, "kept": kept}

    def close(self) -> None:
        self.cancel()
        self.tools.close()

    # ── saving ───────────────────────────────────────────────────────────────

    def _path(self) -> Path:
        return self.home / "sessions" / f"{self.id}.json"

    def save(self) -> None:
        self._path().parent.mkdir(parents=True, exist_ok=True)
        data = {"id": self.id, "project": str(self.project), "saved": time.time(),
                "history": self.history, "undo": self.undo_stack}
        self._path().write_text(json.dumps(scrub_obj(data), indent=1), encoding="utf-8")

    @classmethod
    def resume(cls, session_id: str, provider, home: Optional[Path] = None, **options) -> "AssistantSession":
        path = Path(home or pcode_home()) / "sessions" / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        session = cls(data["project"], provider, home=home, session_id=session_id, **options)
        session.history = data["history"]
        session.undo_stack = data["undo"]
        return session

    def adopt(self, session_id: str) -> None:
        """Continue an earlier conversation on this same project."""
        data = json.loads((self.home / "sessions" / f"{session_id}.json").read_text(encoding="utf-8"))
        self.id, self.history, self.undo_stack = session_id, data["history"], data["undo"]

    @staticmethod
    def list_sessions(project: str | Path, home: Optional[Path] = None) -> list[dict]:
        folder = Path(home or pcode_home()) / "sessions"
        target = str(Path(project).resolve())
        rows = []
        for path in folder.glob("*.json") if folder.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("project") == target:
                first = next((m["content"] for m in data["history"] if m["role"] == "user"), "")
                rows.append({"id": data["id"], "saved": data["saved"], "title": first[:80]})
        return sorted(rows, key=lambda r: r["saved"], reverse=True)

    # ── internals ────────────────────────────────────────────────────────────

    def _emit(self, event: dict) -> None:
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

    def _work(self, message: str) -> None:
        try:
            self._request(message)
        except Exception as e:  # never leave a front end waiting on a dead worker
            self._emit({"type": "error", "message": str(e)[:300]})
            self._emit({"type": "done", "status": "error", "reason": str(e)[:300], "files": [],
                        "can_undo": bool(self.undo_stack)})
        finally:
            self._busy = False
            self._events.put(None)

    def _request(self, message: str) -> None:
        self._request_start = self.provider.meter.total_tokens
        before, st = None, None
        status, reason, reply = "done", "", ""
        self.tools.touched.clear()
        self.tools.scope = "any"
        try:
            if is_question(message):
                reply = self._loop(SPECS["Answerer"], message)
            elif isinstance(self.policy, DirectPolicy):
                before = self.snapshots.snapshot("before: " + message[:60])
                reply = self._loop(BUILDER_ONLY, message)
            else:
                before = self.snapshots.snapshot("before: " + message[:60])
                st = RequestState(request=message, max_steps=self.max_steps, token_budget=self.token_budget,
                                  start_sha=before, best_sha=before)
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
        files = self._finish(before, message, reply or reason)
        if st is not None:
            self._learn(st, files)
        self._emit({"type": "usage", "tokens": self._spent(), "total": self.provider.meter.total_tokens})
        check = st.check if st else None
        self._emit({"type": "done", "status": status, "reason": reason, "files": files,
                    "can_undo": bool(self.undo_stack), "verified": bool(check and check.verified and check.ok),
                    "question": before is None and status == "done"})

    def _finish(self, before: Optional[str], message: str, reply: str) -> list[str]:
        files: list[str] = []
        if before:
            files = self.snapshots.changed_since(before)
            if files:
                after = self.snapshots.snapshot("after: " + message[:60])
                self.undo_stack.append({"before": before, "after": after, "files": files, "request": message[:200]})
        self.history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
        self.save()
        return files

    # ── the puppeteer ────────────────────────────────────────────────────────

    def _puppeteer(self, st: RequestState) -> str:
        """One agent per step: the shield filters, the policy picks, the checks decide what stands."""
        empty = file_map(self.tools) == "(the folder is empty)"
        st.new_project = empty
        st.small = not empty and len(st.request.split()) <= SMALL_WORDS
        st.testable = testable(self.project)
        if detect(self.project):
            st.baseline = st.best = st.check = self._verify()
        self.policy.begin_episode()
        while True:
            self._limits()
            st.tokens = self._spent()
            action = self.policy.select(st, allowed(st))
            if action == "Responder":
                break
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

    def _step(self, action: str, st: RequestState) -> None:
        if action == "Planner":
            self._plan(st)
        elif action == "Reviewer":
            self._review(st)
        else:
            self._edit_step(action, st)

    def _plan(self, st: RequestState) -> None:
        text = self._once(SPECS["Planner"], f"Request: {st.request}\n\nFiles:\n{file_map(self.tools)}")
        stack = re.search(r"STACK:\s*(\w+)", text, re.I)
        st.stack = stack.group(1).lower() if stack else ""
        st.plan = [m.strip() for m in re.findall(r"^\s*\d+[.)]\s*(.+)$", text, re.M)][:MAX_PLAN_STEPS]
        if st.stack:
            st.testable = testable(self.project, st.stack)
        if st.plan:
            self._emit({"type": "plan", "steps": st.plan})

    def _edit_step(self, action: str, st: RequestState) -> None:
        spec = SPECS[action]
        instruction = INSTRUCTIONS[action]
        if action == "TestWriter" and st.built:
            instruction = UPDATE_TESTS
        scope = spec.scope if action == "TestWriter" or st.has_tests else "any"
        self.tools.scope = scope
        before = self.snapshots.snapshot(f"step {st.step}: {action}")
        commands = self.tools.commands
        note = self._loop(spec, task_for(st, self.tools, instruction), history=False)
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

    def _once(self, spec: AgentSpec, task: str, stream: bool = False) -> str:
        """One model call with no tools."""
        self._emit({"type": "agent", "agent": spec.name, "label": spec.label,
                    "model": self.provider.model_for(spec.name)})
        on_delta = (lambda text: self._emit({"type": "text_delta", "text": text})) if stream else None
        reply = self._chat(spec, [{"role": "system", "content": spec.prompt}, {"role": "user", "content": task}],
                           on_delta=on_delta)
        return reply.get("content") or ""

    def _chat(self, spec: AgentSpec, messages: list[dict], **extra) -> dict:
        """When the strong model is out of quota, carry on with the fast one instead of stopping."""
        settings = self.provider.settings_for(spec.name)
        model = self.provider.model_for(spec.name)
        options = {"messages": messages, "temperature": settings.temperature,
                   "max_tokens": settings.max_tokens, **extra}
        try:
            reply = self.provider.client.chat(model=model, **options)
        except Exception as e:
            fast = self.provider.tiers.get("fast")
            if not self.fallback or not is_quota_error(str(e)) or model == fast:
                raise
            self.provider.tiers["quality"] = fast
            self._emit({"type": "notice", "text": "The stronger model hit its usage limit, so I switched to "
                                                  "the faster one. Results may be weaker."})
            reply = self.provider.client.chat(model=fast, **options)
        self._emit({"type": "usage", "tokens": self._spent(), "total": self.provider.meter.total_tokens})
        return reply

    def _loop(self, spec: AgentSpec, task: str, history: bool = True) -> str:
        tools = list(spec.tools)
        max_turns = self.max_turns or spec.max_turns
        self._emit({"type": "agent", "agent": spec.name, "label": spec.label,
                    "model": self.provider.model_for(spec.name)})
        messages = [{"role": "system", "content": spec.prompt}, *(self._context() if history else []),
                    {"role": "user", "content": task}]
        repeats: Counter = Counter()
        for _ in range(max_turns):
            self._limits()
            reply = self._chat(spec, trim(messages), tools=self.tools.specs(tools))
            calls = reply.get("tool_calls") or []
            text = reply.get("content") or ""
            if reply.get("tool_failed"):        # a malformed call is not a finished agent
                messages.append({"role": "user", "content": TOOL_FAILED})
                continue
            if not calls:
                if history:
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
        raise Stop("stopped", f"Stopped after {max_turns} model turns without finishing.")
