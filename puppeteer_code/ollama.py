"""
puppeteer_code/ollama.py
========================
A client for a local Ollama server, on its native `/api/chat`, with the same `chat()`
return shape as core.agent.OpenAICompatClient. The `/v1` endpoint cannot set
`num_ctx`, and the default there silently cuts prompts off.

It uses http.client on purpose: that library cannot follow a redirect or read a
proxy setting, so neither can happen by accident. It always streams, which keeps a
long reply from timing out, and it reads on a worker thread so that Stop still works
while a model is loading and no bytes are arriving.
"""

from __future__ import annotations

import atexit
import hashlib
import http.client
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from puppeteer_code import appcontainer
from puppeteer_code.privacy import LOOPBACK, ModelGuard, PrivacyError, check_address, is_cloud_name
from puppeteer_code.workspace import scrubbed_env

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 300
LOOKUP_TIMEOUT = 15
MIN_REPLY_TOKENS = 256
POLL_SECONDS = 0.2
START_SECONDS = 30
TRAY_URL = f"http://{LOOPBACK}:11434"
PROBE_MODEL = "pcode-probe:cloud"       # the answer must be a refusal, which proves cloud is off
SERVER_ENV = {"OLLAMA_NO_CLOUD": "1", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_NOPRUNE": "1"}
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
_TOOL_TAG = re.compile(r"<tool_call>(.*?)</tool_call>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


class Cancelled(Exception):
    """The user pressed Stop."""


def _estimate(text: str) -> int:
    return max(1, len(text) // 3)       # code runs denser than the usual 4 characters per token


def to_native(messages: list[dict]) -> list[dict]:
    """OpenAI-style history to Ollama's: tool results carry a name, tool arguments are objects."""
    names: dict[str, str] = {}
    out = []
    for message in messages:
        if message.get("role") == "tool":
            out.append({"role": "tool", "tool_name": names.get(message.get("tool_call_id"), ""),
                        "content": message.get("content") or ""})
            continue
        native = {"role": message["role"], "content": message.get("content") or ""}
        if message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                names[call["id"]] = call["function"]["name"]
                calls.append({"function": {"name": call["function"]["name"],
                                           "arguments": json.loads(call["function"]["arguments"] or "{}")}})
            native["tool_calls"] = calls
        out.append(native)
    return out


def _call(index: int, name: str, args: dict) -> dict:
    arguments = json.dumps(args, sort_keys=True)    # stable, so the repeat check sees a repeat
    digest = hashlib.sha256(f"{name}{arguments}".encode()).hexdigest()[:10]
    return {"id": f"call_{index}_{digest}", "name": name, "arguments": arguments}


def _as_call(item, offered: set[str], index: int) -> Optional[dict]:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or item["name"] not in offered:
        return None
    args = item.get("arguments", item.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    return _call(index, item["name"], args) if isinstance(args, dict) else None


def _parse(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def text_tool_calls(content: str, tools: list[dict]) -> tuple[list[dict], bool]:
    """
    Some models write a tool call as text. Accepted only as the whole reply or inside
    <tool_call> tags, and only for a tool that was offered, so prose that mentions JSON
    is never run. Returns (calls, malformed).
    """
    offered = {t["function"]["name"] for t in tools}
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    tagged = _TOOL_TAG.findall(text)
    if tagged:
        items = [_parse(body.strip()) for body in tagged]
    elif "<tool_call>" in text:
        return [], True
    else:
        whole = _parse(text)
        looks_like_a_call = isinstance(whole, dict) and "name" in whole and (
            "arguments" in whole or "parameters" in whole)
        if isinstance(whole, list) and whole:
            items = whole
        elif looks_like_a_call:
            items = [whole]
        else:
            return [], False
    calls = [_as_call(item, offered, i) for i, item in enumerate(items)]
    return ([], True) if None in calls else (calls, False)


class OllamaClient:
    def __init__(self, base_url: str, num_ctx: int = 16384, keep_alive: str = "10m"):
        check_address(base_url)
        self.base_url = base_url
        parts = urlsplit(base_url)
        self.host, self.port = parts.hostname, parts.port or 80
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.cancel: Optional[threading.Event] = None       # set by the session, so Stop reaches a running call
        self.guard = ModelGuard(self.installed, self._show)

    # ── lookups ──────────────────────────────────────────────────────────────

    def _json(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=LOOKUP_TIMEOUT)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None,
                         {"Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read().decode("utf-8", errors="replace")
        except OSError as e:
            raise RuntimeError(f"The local model server is not answering: {e}") from e
        finally:
            conn.close()
        if response.status != 200:
            raise RuntimeError(f"The local model server said {response.status}: {data[:200]}")
        return json.loads(data)

    def installed(self) -> list[str]:
        return [m["name"] for m in self._json("GET", "/api/tags").get("models", [])]

    def _show(self, model: str) -> dict:
        return self._json("POST", "/api/show", {"model": model})

    def list_models(self) -> list[str]:
        return [name for name in self.installed() if not is_cloud_name(name)]

    def think_values(self, model: str) -> Optional[list]:
        """The thinking levels a model accepts, or None when it cannot think."""
        info = self.guard.check(model)
        if "thinking" not in (info.get("capabilities") or []):
            return None
        return (info.get("thinking") or {}).get("values") or [False, True]

    def loaded(self) -> list[dict]:
        return self._json("GET", "/api/ps").get("models", [])

    # ── chat ─────────────────────────────────────────────────────────────────

    def _room(self, messages: list[dict], tools: Optional[list[dict]], max_tokens: int) -> int:
        """How many tokens the reply may use. Ollama would refuse a prompt that does not fit."""
        prompt = _estimate(json.dumps(messages)) + (_estimate(json.dumps(tools)) if tools else 0)
        room = self.num_ctx - prompt
        if room < MIN_REPLY_TOKENS:
            raise RuntimeError(f"This request is too big for the local model: about {prompt} tokens of "
                               f"{self.num_ctx}. Ask for something smaller.")
        return min(max_tokens, room)

    def _read(self, conn: http.client.HTTPConnection, body: dict, chunks: queue.Queue) -> None:
        try:
            conn.request("POST", "/api/chat", json.dumps(body), {"Content-Type": "application/json"})
            conn.sock.settimeout(READ_TIMEOUT)
            response = conn.getresponse()
            if response.status != 200:
                text = response.read().decode("utf-8", errors="replace")
                chunks.put(RuntimeError(f"The local model server said {response.status}: {_reason(text)}"))
                return
            for line in response:
                if line.strip():
                    chunks.put(json.loads(line))
            chunks.put(None)
        except Exception as e:      # the reader must always report, or chat() would wait for ever
            chunks.put(e)

    def _stream(self, body: dict):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=CONNECT_TIMEOUT)
        chunks: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, args=(conn, body, chunks), daemon=True).start()
        try:
            while True:
                if self.cancel is not None and self.cancel.is_set():
                    raise Cancelled()
                try:
                    chunk = chunks.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    continue
                if chunk is None:
                    return
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
        finally:
            _hang_up(conn)      # closing the socket is what tells Ollama to stop generating

    def chat(self, model: str, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048,
             tools: Optional[list[dict]] = None, on_delta: Optional[Callable[[str], None]] = None,
             think=None) -> dict:
        check_address(self.base_url)
        self.guard.check(model)
        native = to_native(messages)
        body = {"model": model, "messages": native, "stream": True, "keep_alive": self.keep_alive, "truncate": False,
                "options": {"num_ctx": self.num_ctx, "temperature": temperature,
                            "num_predict": self._room(native, tools, max_tokens)}}
        if tools:
            body["tools"] = tools
        if think is not None:
            body["think"] = think

        started = time.monotonic()
        content, thinking, native_calls = [], [], []
        final: dict = {}
        try:
            for chunk in self._stream(body):
                if chunk.get("error"):
                    raise RuntimeError(f"The local model failed: {chunk['error']}")
                message = chunk.get("message") or {}
                if message.get("content"):
                    content.append(message["content"])
                    if on_delta is not None and not tools:
                        on_delta(message["content"])
                thinking.append(message.get("thinking") or "")
                native_calls += message.get("tool_calls") or []
                if chunk.get("done"):
                    final = chunk
        except (ConnectionError, socket.timeout, http.client.HTTPException) as e:
            raise RuntimeError(f"Lost the local model server: {e}") from e
        if not final:
            raise RuntimeError("The local model stopped before it finished.")

        usage = {"prompt_tokens": int(final.get("prompt_eval_count") or 0),
                 "completion_tokens": int(final.get("eval_count") or 0)}
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        reply = {"content": "".join(content), "model": model, "usage": usage,
                 "latency_ms": (time.monotonic() - started) * 1000}
        if tools:
            self._add_calls(reply, native_calls, tools, final.get("done_reason"), "".join(thinking))
        return reply

    @staticmethod
    def _add_calls(reply: dict, native_calls: list[dict], tools: list[dict], done_reason, thinking: str) -> None:
        """Fills in tool_calls. A reply that was cut off or is only thinking is not a finished agent."""
        offered = {t["function"]["name"] for t in tools}
        calls, malformed = [], False
        for i, item in enumerate(native_calls):
            function = item.get("function") or {}
            call = _as_call({"name": function.get("name"), "arguments": function.get("arguments", {})}, offered, i)
            malformed = malformed or call is None
            calls += [call] if call else []
        if not native_calls:
            calls, malformed = text_tool_calls(reply["content"], tools)
            if calls or malformed:
                reply["content"] = ""
        reply["tool_calls"] = calls
        cut_off = done_reason == "length" and not calls
        only_thought = not calls and not reply["content"].strip() and bool(thinking.strip())
        if malformed or cut_off or only_thought:
            reply["tool_failed"] = True
            reply["tool_calls"] = []


class LocalServer:
    """pcode's own `ollama serve`, with cloud off. It is never the tray app, and it never pulls a model."""

    def __init__(self, url: str, log: Path, binary: Optional[str] = None):
        check_address(url)
        self.url = url
        self.log = log
        self.binary = binary
        self.proc: Optional[subprocess.Popen] = None
        self.job = None
        parts = urlsplit(url)
        self.host, self.port = parts.hostname, parts.port

    def _status(self, method: str, path: str, body: Optional[dict] = None) -> Optional[int]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=CONNECT_TIMEOUT)
        try:
            conn.request(method, path, json.dumps(body) if body else None, {"Content-Type": "application/json"})
            return conn.getresponse().status
        except OSError:
            return None
        finally:
            conn.close()

    def _find_binary(self) -> str:
        found = self.binary or shutil.which("ollama")
        fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if not found and fallback.is_file():
            found = str(fallback)
        if not found:
            raise PrivacyError("Ollama is not installed. Get it from https://ollama.com/download, then try again.")
        return found

    def _spawn(self) -> None:
        env = {k: v for k, v in scrubbed_env().items() if k.upper() not in _PROXY_VARS}
        env.update(SERVER_ENV, OLLAMA_HOST=f"{self.host}:{self.port}")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        with open(self.log, "ab") as output:
            self.proc = subprocess.Popen([self._find_binary(), "serve"], env=env, stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=output, **flags)
        if sys.platform == "win32":     # atexit does not run if pcode is killed, and this must not outlive it
            self.job = appcontainer.tie_to_this_process(self.proc)

    def start(self) -> None:
        """Starts the server, or joins one another pcode left running. If it will not start, nothing runs."""
        if self._status("GET", "/api/version") != 200:
            self._spawn()
            deadline = time.monotonic() + START_SECONDS
            while self._status("GET", "/api/version") != 200:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    self.stop()
                    raise PrivacyError(f"The local model server did not start. See {self.log}.")
                time.sleep(POLL_SECONDS)
        if self._status("POST", "/api/show", {"model": PROBE_MODEL}) != 403:
            self.stop()
            raise PrivacyError(f"The server on port {self.port} did not refuse cloud models, so it was not used.")

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        if sys.platform == "win32":     # the model runners are children of the server
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL, capture_output=True)
        else:
            proc.terminate()

    def warnings(self) -> list[str]:
        """A model loaded in the tray app shares the graphics memory, so the first reply may be slow."""
        try:
            loaded = OllamaClient(TRAY_URL).loaded()
        except (RuntimeError, ValueError):
            return []
        return ["Ollama's own app has a model loaded, so this may run slowly. Quit it to free memory."] if loaded else []


_server: Optional[LocalServer] = None
_server_lock = threading.Lock()


def shared_server(spec: dict) -> LocalServer:
    """One server for chat and Fix mode, stopped when pcode exits."""
    global _server
    from puppeteer_code.assistant.snapshots import pcode_home

    with _server_lock:
        if _server is None:
            server = LocalServer(spec["base_url"], Path(pcode_home()) / "ollama-private.log")
            server.start()
            atexit.register(server.stop)
            _server = server
        return _server


def _reason(text: str) -> str:
    try:
        return str(json.loads(text).get("error", text))[:200]
    except (ValueError, AttributeError):
        return text[:200]


def _hang_up(conn: http.client.HTTPConnection) -> None:
    sock = conn.sock
    try:
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    conn.close()


__all__ = ["Cancelled", "LocalServer", "OllamaClient", "PrivacyError", "shared_server", "text_tool_calls", "to_native"]
