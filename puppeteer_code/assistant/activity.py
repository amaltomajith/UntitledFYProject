"""
puppeteer_code/assistant/activity.py
====================================
What the model is doing, told to the screen while it happens.

Nothing here reaches a model. Ollama sends no text while it generates a tool call (measured on
qwen3.5:9b: 46 s of silence, then the whole call), so a quiet model is reported as quiet, never
as stuck. Only a limit decides that a request has stopped.

`thinking_delta` and `code_delta` carry the latest text, not an addition to it, so a page that
reconnects in the middle of a reply is right again on the next one.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
from typing import Callable, Optional

from puppeteer_code.assistant.graph import clean
from puppeteer_code.assistant.permissions import is_protected

TRANSIENT = ("activity", "thinking_delta", "code_delta")    # shown once, never replayed
TICK_SECONDS = 0.25
TICKS_PER_REPORT = 4
MAX_TAIL = 4000
QUIET_SECONDS = 3
MAX_DIFF_CHARS = 8000
MAX_DIFF_FILE = 200_000
CONTEXT_LINES = 2
WRITING_TOOLS = {"write_file": "content", "edit_file": "new"}

_STRING = re.compile(r'"((?:[^"\\]|\\.)*)("?)', re.S)
_ESCAPE_CUT = re.compile(r"\\(?:u[0-9a-fA-F]{0,3})?$")
_ARGUMENTS = re.compile(r'"arguments"\s*:\s*(?=\{)')
_TOOL_NAME = re.compile(r'"name"\s*:\s*"(\w+)"')


def _decode(raw: str) -> str:
    """A JSON string body, which may stop in the middle of an escape."""
    try:
        text = json.loads(f'"{_ESCAPE_CUT.sub("", raw)}"', strict=False)
    except ValueError:
        return ""
    return text[:-1] if text and "\ud800" <= text[-1] <= "\udbff" else text    # half of a surrogate pair


def partial_fields(arguments: str) -> dict[str, str]:
    """The top-level string fields of a JSON object that is still arriving. The last may be cut short."""
    fields: dict[str, str] = {}
    at = arguments.find("{") + 1
    while 0 < at < len(arguments):
        key = _STRING.search(arguments, at)
        if key is None or not key.group(2):
            break
        at = key.end()
        while at < len(arguments) and arguments[at] in " \t\r\n:":
            at += 1
        if at >= len(arguments):
            break
        if arguments[at] != '"':                    # a number or similar: step over it
            comma = arguments.find(",", at)
            if comma < 0:
                break
            at = comma + 1
            continue
        value = _STRING.match(arguments, at)
        fields[_decode(key.group(1))] = _decode(value.group(1))
        if not value.group(2):
            break
        at = value.end()
    return fields


def tool_text(content: str) -> Optional[tuple[str, str]]:
    """(name, arguments) of a tool call a model is writing as plain text, as some local models do."""
    arguments, name = _ARGUMENTS.search(content), _TOOL_NAME.search(content)
    return (name.group(1), content[arguments.end():]) if arguments and name else None


def file_diff(path: str, action: str, before: str, after: str) -> dict:
    """What a change did, for the Live tab. Bounded, because it stays in the replay log."""
    old, new = before.splitlines(), after.splitlines()
    event = {"type": "file_diff", "path": path, "action": action, "added": 0, "removed": 0, "hunks": [], "more": False}
    if max(len(before), len(after)) > MAX_DIFF_FILE:
        return {**event, "added": len(new), "removed": len(old), "more": True}
    room = MAX_DIFF_CHARS
    matcher = difflib.SequenceMatcher(None, old, new)
    for group in matcher.get_grouped_opcodes(CONTEXT_LINES):
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            gone, come = ([], []) if tag == "equal" else (old[i1:i2], new[j1:j2])
            event["removed"] += len(gone)
            event["added"] += len(come)
            piece = [f"  {line}" for line in old[i1:i2]] if tag == "equal" else [
                *(f"- {line}" for line in gone), *(f"+ {line}" for line in come)]
            for line in piece:
                room -= len(line) + 1
                if room >= 0:
                    lines.append(clean(line))
        event["more"] = event["more"] or room < 0
        if lines:
            event["hunks"].append({"line": group[0][3] + 1, "end": group[-1][4], "lines": lines})
    return event


class Activity:
    """Holds what is known about the work in progress and reports it on a clock, quiet model or not."""

    def __init__(self, emit: Callable[[dict], None]):
        self.emit = emit
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.agent, self.label, self.step, self.max_steps = "", "", 0, 0
        self._started = 0.0
        self._clear("waiting", "")

    def _clear(self, phase: str, path: str) -> None:
        self.phase_name, self.path = phase, path
        self._in_reply = False
        self._reply_start, self._first, self._last = 0.0, 0.0, 0.0
        self._pieces = 0
        self._thinking, self._content = "", ""
        self._tool: Optional[tuple[str, str]] = None
        self._thinking_new = self._code_new = False

    def begin(self) -> None:
        self._started = time.monotonic()
        self._stop.clear()
        with self._lock:
            self._clear("waiting", "")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def end(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def see(self, event: dict) -> None:
        """Follows the session's own events, so the work itself needs no extra calls."""
        kind = event["type"]
        if kind == "agent":
            with self._lock:
                self.agent, self.label = event["agent"], event.get("label", event["agent"])
                self.step, self.max_steps = event.get("step", 0), event.get("max_steps", 0)
                self._clear("waiting", "")
        elif kind == "tool_start":
            with self._lock:
                self.phase_name = "running"
                self.path = str(event.get("args", {}).get("path") or event.get("args", {}).get("command") or "")
        elif kind == "tool_end":
            self.set_phase("waiting")

    def set_phase(self, phase: str, path: str = "") -> None:
        with self._lock:
            self._clear(phase, path)

    def reply_started(self) -> None:
        with self._lock:
            self._clear("waiting", self.path)
            self._in_reply = True
            self._reply_start = time.monotonic()

    def reply_ended(self) -> None:
        with self._lock:
            self._in_reply = False

    def progress(self, kind: str, text: str, name: str = "") -> None:
        """Called by the clients for each piece of a reply. `tool` carries the arguments so far."""
        now = time.monotonic()
        with self._lock:
            self._first = self._first or now
            self._last = now
            self._pieces += 1
            if kind == "thinking":
                self.phase_name = "thinking"
                self._thinking = (self._thinking + text)[-MAX_TAIL:]
                self._thinking_new = True
                return
            self.phase_name = "writing"
            if kind == "tool":
                self._tool = (name, text)
                self._code_new = True
            else:
                self._content += text
                self._tool = tool_text(self._content) or self._tool
                self._code_new = self._tool is not None

    def _code(self) -> Optional[dict]:
        name, arguments = self._tool or ("", "")
        field = WRITING_TOOLS.get(name)
        fields = partial_fields(arguments) if field else {}
        path, text = fields.get("path", ""), fields.get(field or "", "")
        if not path or not text or is_protected(path):
            return None
        self.path = path
        return {"type": "code_delta", "path": path, "text": clean(text[-MAX_TAIL:])}

    def _report(self, now: float) -> dict:
        start = self._reply_start or now
        quiet = now - (self._last or start) if self._in_reply else 0
        if self.phase_name == "thinking" and quiet >= QUIET_SECONDS:
            self.phase_name = "writing"         # it thought, went quiet, and has not answered yet
        spread = now - self._first if self._first else 0
        return {"type": "activity", "agent": self.agent, "label": self.label, "phase": self.phase_name, "path": self.path,
                "seconds": int(now - self._started), "silent": int(quiet), "pieces": self._pieces,
                "rate": round(self._pieces / spread, 1) if spread >= 1 and quiet < QUIET_SECONDS else None,
                "step": self.step, "max_steps": self.max_steps}

    def _run(self) -> None:
        ticks = 0
        while not self._stop.wait(TICK_SECONDS):
            ticks += 1
            out: list[dict] = []
            with self._lock:
                if self._thinking_new:
                    self._thinking_new = False
                    out.append({"type": "thinking_delta", "text": clean(self._thinking)})
                if self._code_new:
                    self._code_new = False
                    code = self._code()
                    out += [code] if code else []
                if ticks % TICKS_PER_REPORT == 0:
                    out.append(self._report(time.monotonic()))
            for event in out:
                self.emit(event)
