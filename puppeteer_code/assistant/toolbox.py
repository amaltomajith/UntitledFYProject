"""
puppeteer_code/assistant/toolbox.py
===================================
What the assistant can do in a project. `call()` never raises: a failure comes
back as text the model can read and react to.

Everything a tool returns is data from the project or the machine. Only the user
can approve an action, through the `approve` callback, so text inside a file or a
web page cannot grant anything.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from puppeteer_code.assistant.permissions import ALLOW, REFUSE, classify_command, is_protected
from puppeteer_code.integrity import is_test_path
from puppeteer_code.tools import EditBlock, apply_block
from puppeteer_code.workspace import IGNORE_DIRS, Workspace, scrubbed_env

MAX_READ = 16000
MAX_OUTPUT = 6000
MAX_LIST = 300
MAX_HITS = 40
_JS_OR_GO_TEST = re.compile(r"(\.(test|spec)\.[cm]?[jt]sx?|_test\.\w+)$")
_URL = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\d?\])(?::\d+)?[^\s\"')]*")
_ANY_HOST = re.compile(r"//(0\.0\.0\.0|\[::\d?\])")


def is_test_file(rel: str) -> bool:
    return is_test_path(rel) or bool(_JS_OR_GO_TEST.search(rel))


def _spec(name: str, description: str, required: Optional[dict] = None, optional: Optional[dict] = None) -> dict:
    props = {k: {"type": v[0], "description": v[1]} for k, v in {**(required or {}), **(optional or {})}.items()}
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": list(required or {})},
    }}


PATH = ("string", "Path relative to the project root")

SPECS = {
    "list_files": _spec("list_files", "List files in the project or a folder.", optional={"path": PATH}),
    "read_file": _spec("read_file", "Read a text file.", {"path": PATH}),
    "grep": _spec("grep", "Search text files for a regular expression.",
                  {"pattern": ("string", "Regular expression")}, {"path": PATH}),
    "write_file": _spec("write_file", "Create a file or replace all of it.",
                        {"path": PATH, "content": ("string", "The whole file")}),
    "edit_file": _spec("edit_file", "Replace one exact piece of an existing file. Read the file first.",
                       {"path": PATH, "old": ("string", "Exact text to find"),
                        "new": ("string", "Text to put there")}),
    "delete_file": _spec("delete_file", "Delete a file.", {"path": PATH}),
    "run_command": _spec("run_command", "Run a shell command in the project folder and return its output.",
                         {"command": ("string", "The command")},
                         {"timeout": ("integer", "Seconds to wait, default 120, most 600")}),
    "start_server": _spec("start_server", "Start a long-running app, such as a web server, in the background.",
                          {"command": ("string", "The command")}),
}
READ_ONLY = ["list_files", "read_file", "grep"]
ALL_TOOLS = list(SPECS)


class Toolbox:
    def __init__(self, project: str | Path, on_event: Callable[[dict], None],
                 approve: Callable[[dict], bool], mode: str = "ask"):
        self.root = Path(project).resolve()
        self.on_event = on_event
        self.approve = approve
        self.mode = mode
        self.safe_commands: list[str] = []      # filled in by the verifier
        self.scope = "any"                      # any | code | tests: what the current agent may edit
        self.touched: set[str] = set()
        self.servers: list[subprocess.Popen] = []
        self.commands = 0                   # agent commands that succeeded

    def specs(self, names: list[str]) -> list[dict]:
        return [SPECS[n] for n in names]

    def resolve(self, rel: str) -> Path:
        path = Workspace.resolve(self, rel)     # keeps every path inside the project
        if is_protected(path.relative_to(self.root).as_posix()):
            raise PermissionError(f"{rel} is a protected file (secrets or internals)")
        return path

    def _editable(self, rel: str) -> Path:
        path = self.resolve(rel)
        is_test = is_test_file(path.relative_to(self.root).as_posix())
        if self.scope == "code" and is_test:
            raise PermissionError(f"{rel} is a test file. Tests are locked: change the code instead.")
        if self.scope == "tests" and not is_test:
            raise PermissionError(f"{rel} is not a test file. This step may only edit tests.")
        return path

    def call(self, name: str, arguments: str, allowed: Optional[list[str]] = None) -> str:
        if name not in SPECS:
            return f"ERROR: unknown tool {name}"
        if allowed is not None and name not in allowed:     # a model can name a tool it was not offered
            return f"ERROR: {name} is not available in this step"
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError as e:
            return f"ERROR: bad arguments: {e}"
        known = SPECS[name]["function"]["parameters"]["properties"]
        args = {k: v for k, v in args.items() if k in known}    # models sometimes invent arguments
        self.on_event({"type": "tool_start", "tool": name, "args": _preview(args)})
        try:
            result = getattr(self, f"_{name}")(**args)
            ok = not result.startswith("ERROR")
        except (PermissionError, OSError, TypeError, ValueError) as e:
            result, ok = f"ERROR: {e}", False
        self.on_event({"type": "tool_end", "tool": name, "ok": ok, "summary": result[:160]})
        return result

    # ── reading ──────────────────────────────────────────────────────────────

    def _walk(self, base: Path):
        for folder, dirs, files in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.endswith(".egg-info"))
            for name in sorted(files):
                rel = (Path(folder) / name).relative_to(self.root).as_posix()
                if not is_protected(rel) and not name.endswith((".pyc", ".pyo")):
                    yield rel

    def _list_files(self, path: str = ".") -> str:
        files = []
        for rel in self._walk(self.resolve(path)):
            files.append(rel)
            if len(files) >= MAX_LIST:
                return "\n".join(files) + f"\n(stopped at {MAX_LIST} files)"
        return "\n".join(files) or "(no files yet)"

    def _read_file(self, path: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            return f"ERROR: no such file: {path}"
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ:
            return text[:MAX_READ] + f"\n(cut off: file is {len(text)} characters)"
        return text

    def _grep(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        hits = []
        for rel in self._walk(self.resolve(path)):
            full = self.root / rel
            try:
                if full.stat().st_size > 1_000_000:
                    continue
                text = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                    if len(hits) >= MAX_HITS:
                        return "\n".join(hits) + "\n(more matches not shown)"
        return "\n".join(hits) or "no matches"

    # ── writing ──────────────────────────────────────────────────────────────

    def _changed(self, path: Path, action: str) -> None:
        rel = path.relative_to(self.root).as_posix()
        self.touched.add(rel)
        self.on_event({"type": "file_changed", "path": rel, "action": action})

    def _write_file(self, path: str, content: str) -> str:
        target = self._editable(path)
        action = "edited" if target.exists() else "created"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        self._changed(target, action)
        return f"wrote {path} ({len(content.splitlines())} lines)"

    def _edit_file(self, path: str, old: str, new: str) -> str:
        target = self._editable(path)
        if not target.is_file():
            return f"ERROR: no such file: {path}"
        raw = target.read_bytes().decode("utf-8", errors="replace")
        crlf = "\r\n" in raw
        updated, note = apply_block(raw.replace("\r\n", "\n"), EditBlock(path, old, new))
        if updated is None:
            return f"ERROR: {note}. Read the file and copy the text exactly."
        target.write_text(updated.replace("\n", "\r\n") if crlf else updated, encoding="utf-8", newline="")
        self._changed(target, "edited")
        return f"edited {path} ({note})"

    def _delete_file(self, path: str) -> str:
        target = self._editable(path)
        if not target.is_file():
            return f"ERROR: no such file: {path}"
        target.unlink()
        self._changed(target, "deleted")
        return f"deleted {path}"

    # ── commands ─────────────────────────────────────────────────────────────

    def _permit(self, command: str, kind: str) -> Optional[str]:
        decision, reason = classify_command(command, self.mode, self.safe_commands)
        if decision == REFUSE:
            return f"ERROR: refused, this command {reason}. Do not try to work around this."
        if decision != ALLOW and not self.approve({"kind": kind, "command": command, "reason": reason}):
            return "ERROR: the user declined this command. Do not retry it. Find another way or ask them."
        return None

    def _popen(self, command: str) -> subprocess.Popen:
        env = scrubbed_env()
        env["PYTHONUNBUFFERED"] = "1"
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {
            "start_new_session": True}
        return subprocess.Popen(command, shell=True, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", **flags)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)

    def run(self, command: str, timeout: int = 120) -> tuple[int, str]:
        """Run without a permission check; callers have already decided."""
        proc = self._popen(command)
        lines: list[str] = []

        def pump():
            for line in proc.stdout:
                lines.append(line)
                self.on_event({"type": "command_output", "text": line})

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:
            code = proc.wait(timeout=min(int(timeout), 600))
        except subprocess.TimeoutExpired:
            self._kill(proc)
            code = -1
            lines.append(f"\n(stopped after {timeout} seconds)\n")
        reader.join(2)
        out = "".join(lines)
        if len(out) > MAX_OUTPUT:
            out = f"(first part cut off)\n{out[-MAX_OUTPUT:]}"
        return code, out

    def _run_command(self, command: str, timeout: int = 120) -> str:
        blocked = self._permit(command, "command")
        if blocked:
            return blocked
        code, out = self.run(command, timeout)
        self.commands += code == 0
        return f"exit code {code}\n{out}" if code == 0 else f"ERROR: exit code {code}\n{out}"

    def _start_server(self, command: str) -> str:
        blocked = self._permit(command, "server")
        if blocked:
            return blocked
        self.close()        # one preview at a time, and the new one may want the same port
        self.commands += 1
        proc = self._popen(command)
        self.servers.append(proc)
        found: list[str] = []
        seen: list[str] = []

        def pump():
            for line in proc.stdout:
                seen.append(line)
                self.on_event({"type": "command_output", "text": line})
                match = _URL.search(line)
                if match and not found:
                    found.append(_ANY_HOST.sub("//127.0.0.1", match.group()))

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not found and proc.poll() is None:
            time.sleep(0.1)
        if proc.poll() is not None:
            return f"ERROR: it stopped with exit code {proc.returncode}\n{''.join(seen)[-MAX_OUTPUT:]}"
        if found:
            self.on_event({"type": "preview", "url": found[0]})
            return f"running at {found[0]}"
        return "running, but it did not print an address yet\n" + "".join(seen)[-1000:]

    def close(self) -> None:
        for proc in self.servers:
            self._kill(proc)
        self.servers.clear()


def _preview(args: dict) -> dict:
    return {k: (f"{len(v)} characters" if k in ("content", "old", "new") and isinstance(v, str) else v)
            for k, v in args.items()}
