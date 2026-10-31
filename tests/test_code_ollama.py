"""The local model client and its privacy guard, against a fake Ollama on loopback."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from puppeteer_code.ollama import Cancelled, OllamaClient, text_tool_calls, to_native
from puppeteer_code.privacy import ModelGuard, PrivacyError, check_address, is_cloud_name

TOOLS = [{"type": "function", "function": {"name": n, "description": "", "parameters": {}}}
         for n in ("read_file", "write_file")]


class Quiet(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass                # a client hanging up on purpose is what the Stop tests are for


class Fake:
    """Scripted replies. `chunks` is a list of NDJSON objects, `seen` the request bodies."""

    def __init__(self):
        self.chunks = []
        self.status = 200
        self.error = ""
        self.redirect = False
        self.delay = 0.0
        self.hold = 0.0
        self.models = ["local:latest"]
        self.info = {"capabilities": ["completion", "tools"]}
        self.seen = []
        self.shown = []
        self.gone = threading.Event()


def handler(fake):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _json(self, data, status=200):
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._json({"models": [{"name": n} for n in fake.models]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/show":
                fake.shown.append(body["model"])
                return self._json(fake.info)
            fake.seen.append(body)
            if fake.redirect:
                self.send_response(302)
                self.send_header("Location", "http://example.invalid/api/chat")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if fake.status != 200:
                return self._json({"error": fake.error}, fake.status)
            time.sleep(fake.hold)
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for chunk in fake.chunks:
                    line = json.dumps(chunk).encode() + b"\n"
                    self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                    self.wfile.flush()
                    time.sleep(fake.delay)
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                fake.gone.set()
    return Handler


@pytest.fixture
def fake():
    state = Fake()
    server = Quiet(("127.0.0.1", 0), handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def no_way_out(monkeypatch):
    """Any attempt to reach something other than loopback fails the test."""
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def connect(self, address):
        if self.family == socket.AF_INET and address[0] != "127.0.0.1":
            pytest.fail(f"tried to connect to {address}")
        return real_connect(self, address)

    def lookup(host, *args, **kwargs):
        if host not in ("127.0.0.1", "localhost"):
            pytest.fail(f"tried to look up {host}")
        return real_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)


def done(text="", reason="stop", calls=None, thinking=""):
    message = {"role": "assistant", "content": text, "thinking": thinking}
    if calls:
        message["tool_calls"] = calls
    return {"message": message, "done": True, "done_reason": reason, "prompt_eval_count": 40, "eval_count": 10}


def part(text):
    return {"message": {"role": "assistant", "content": text}, "done": False}


def ask(fake, **options):
    return OllamaClient(fake.url).chat("local", [{"role": "user", "content": "hi"}], **options)


# ── the reply ────────────────────────────────────────────────────────────────

def test_a_plain_reply_has_the_usual_shape_and_streams(fake):
    fake.chunks = [part("Hel"), part("lo"), done()]
    pieces = []
    reply = ask(fake, on_delta=pieces.append)
    assert reply["content"] == "Hello" and pieces == ["Hel", "lo"]
    assert reply["usage"] == {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50}
    assert "tool_calls" not in reply


def test_the_request_sets_context_and_refuses_silent_truncation(fake):
    fake.chunks = [done("ok")]
    ask(fake, temperature=0.1, max_tokens=99, think=True)
    body = fake.seen[0]
    assert body["options"] == {"num_ctx": 16384, "temperature": 0.1, "num_predict": 99}
    assert body["truncate"] is False and body["stream"] is True and body["think"] is True


def test_think_is_left_out_unless_set(fake):
    fake.chunks = [done("ok")]
    ask(fake)
    assert "think" not in fake.seen[0]


def test_native_tool_calls(fake):
    fake.chunks = [done(calls=[{"function": {"name": "read_file", "arguments": {"path": "a", "b": 1}}}])]
    reply = ask(fake, tools=TOOLS)
    call = reply["tool_calls"][0]
    assert call["name"] == "read_file" and call["arguments"] == '{"b": 1, "path": "a"}'


def test_the_same_call_gets_the_same_id(fake):
    fake.chunks = [done(calls=[{"function": {"name": "read_file", "arguments": {"path": "a"}}}])]
    assert ask(fake, tools=TOOLS)["tool_calls"][0]["id"] == ask(fake, tools=TOOLS)["tool_calls"][0]["id"]


def test_a_tool_call_written_as_text_runs(fake):
    fake.chunks = [done('{"name": "read_file", "arguments": {"path": "a.py"}}')]
    reply = ask(fake, tools=TOOLS)
    assert reply["tool_calls"][0]["name"] == "read_file" and reply["content"] == ""


def test_a_tool_that_was_not_offered_does_not_run(fake):
    fake.chunks = [done(calls=[{"function": {"name": "delete_everything", "arguments": {}}}])]
    reply = ask(fake, tools=TOOLS)
    assert reply["tool_calls"] == [] and reply["tool_failed"]


def test_a_reply_cut_off_with_tools_is_not_a_finished_agent(fake):
    fake.chunks = [done("I will now", reason="length")]
    assert ask(fake, tools=TOOLS)["tool_failed"]


def test_a_reply_that_is_only_thinking_is_not_a_finished_agent(fake):
    fake.chunks = [done("", thinking="Let me consider this at length")]
    assert ask(fake, tools=TOOLS)["tool_failed"]


def test_a_cut_off_tool_tag_is_malformed(fake):
    fake.chunks = [done('<tool_call>{"name": "read_file"', reason="stop")]
    assert ask(fake, tools=TOOLS)["tool_failed"]


def test_an_ordinary_answer_with_tools_is_just_an_answer(fake):
    fake.chunks = [done("All finished.")]
    reply = ask(fake, tools=TOOLS)
    assert reply["content"] == "All finished." and reply["tool_calls"] == [] and "tool_failed" not in reply


@pytest.mark.parametrize("text,count", [
    ('<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>', 1),
    ('<tool_call>{"name": "read_file", "arguments": {}}</tool_call>\n<tool_call>{"name": "write_file", "arguments": {}}</tool_call>', 2),
    ('```json\n{"name": "read_file", "arguments": {"path": "a"}}\n```', 1),
    ('{"name": "read_file", "parameters": {"path": "a"}}', 1),
    ('{"name": "read_file", "arguments": "{\\"path\\": \\"a\\"}"}', 1),
    ('[{"name": "read_file", "arguments": {}}]', 1),
])
def test_text_tool_call_forms(text, count):
    calls, malformed = text_tool_calls(text, TOOLS)
    assert len(calls) == count and not malformed


@pytest.mark.parametrize("text", [
    'I would call {"name": "read_file", "arguments": {}} next.',
    'Here is some JSON: {"a": 1}',
    '{"status": "fine"}',
    "plain words",
])
def test_prose_is_never_run(text):
    assert text_tool_calls(text, TOOLS) == ([], False)


def test_history_converts_to_the_native_form():
    native = to_native([
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "text"}])
    assert native[0]["tool_calls"] == [{"function": {"name": "read_file", "arguments": {"path": "a"}}}]
    assert native[1] == {"role": "tool", "tool_name": "read_file", "content": "text"}


# ── limits and failures ──────────────────────────────────────────────────────

def test_the_reply_is_limited_to_the_room_left(fake):
    fake.chunks = [done("ok")]
    OllamaClient(fake.url, num_ctx=1000).chat("local", [{"role": "user", "content": "x" * 1500}], max_tokens=6000)
    assert fake.seen[0]["options"]["num_predict"] < 600


def test_a_prompt_that_cannot_fit_is_refused_before_sending(fake):
    with pytest.raises(RuntimeError, match="too big"):
        OllamaClient(fake.url, num_ctx=1000).chat("local", [{"role": "user", "content": "x" * 4000}])
    assert fake.seen == []


def test_the_server_refusing_a_long_prompt_is_reported(fake):
    fake.status, fake.error = 400, "request (9000 tokens) exceeds the available context size"
    with pytest.raises(RuntimeError, match="exceeds the available context"):
        ask(fake)


def test_an_error_in_the_stream_is_reported(fake):
    fake.chunks = [part("x"), {"error": "model runner has unexpectedly stopped"}]
    with pytest.raises(RuntimeError, match="unexpectedly stopped"):
        ask(fake)


def test_a_stream_that_ends_early_is_reported(fake):
    fake.chunks = [part("x")]
    with pytest.raises(RuntimeError, match="before it finished"):
        ask(fake)


def test_a_redirect_is_not_followed(fake):
    fake.redirect = True
    with pytest.raises(RuntimeError, match="302"):
        ask(fake)


def test_a_server_that_is_not_there_is_reported():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(RuntimeError, match="not answering"):
        OllamaClient(f"http://127.0.0.1:{port}").chat("local", [])


def test_stop_ends_a_reply_in_progress_and_hangs_up(fake):
    fake.chunks = [part("x")] * 500 + [done()]
    fake.delay = 0.02
    client = OllamaClient(fake.url)
    client.cancel = threading.Event()
    threading.Timer(0.2, client.cancel.set).start()
    with pytest.raises(Cancelled):
        client.chat("local", [{"role": "user", "content": "hi"}])
    assert fake.gone.wait(5)


def test_stop_works_while_the_model_loads_and_nothing_is_arriving(fake):
    fake.hold = 5
    client = OllamaClient(fake.url)
    client.cancel = threading.Event()
    threading.Timer(0.2, client.cancel.set).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        client.chat("local", [{"role": "user", "content": "hi"}])
    assert time.monotonic() - started < 2


# ── the guard ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", ["gpt-oss:20b-cloud", "qwen3:cloud", "minimax-cloud", "x:y-CLOUD", " a:cloud "])
def test_cloud_names_are_recognised(model):
    assert is_cloud_name(model)


@pytest.mark.parametrize("model", ["cloudy:latest", "qwen3:8b", "granite4", "mycloud:7b"])
def test_local_names_are_not_cloud_names(model):
    assert not is_cloud_name(model)


@pytest.mark.parametrize("model", ["gpt-oss:20b-cloud", "qwen3:cloud"])
def test_a_cloud_model_is_refused_before_any_request(fake, model):
    client = OllamaClient(fake.url)
    with pytest.raises(PrivacyError):
        client.chat(model, [])
    with pytest.raises(PrivacyError):
        client.think_values(model)
    assert fake.seen == [] and fake.shown == []


def test_a_model_that_is_not_installed_is_refused(fake):
    with pytest.raises(PrivacyError, match="not installed"):
        OllamaClient(fake.url).chat("other:7b", [])
    assert fake.seen == []


@pytest.mark.parametrize("field", ["remote_host", "remote_model"])
def test_a_model_that_runs_elsewhere_is_refused(fake, field):
    fake.info = {field: "https://ollama.com:443"}
    with pytest.raises(PrivacyError, match="remote"):
        ask(fake)
    assert fake.seen == []


def test_the_guard_looks_a_model_up_once(fake):
    fake.chunks = [done("ok")]
    client = OllamaClient(fake.url)
    for _ in range(3):
        client.chat("local", [{"role": "user", "content": "hi"}])
    assert fake.shown == ["local:latest"]


def test_a_cached_pass_does_not_cover_a_cloud_name():
    guard = ModelGuard(lambda: ["a:cloud"], lambda m: {})
    guard.known["a:cloud"] = {}
    with pytest.raises(PrivacyError):
        guard.check("a:cloud")


@pytest.mark.parametrize("url", ["http://localhost:11435", "http://192.168.1.5:11434", "https://127.0.0.1:1",
                                 "http://127.0.0.1.evil.example:80", "http://ollama.com"])
def test_only_loopback_by_number_is_accepted(url):
    with pytest.raises(PrivacyError):
        check_address(url)
    with pytest.raises(PrivacyError):
        OllamaClient(url)


def test_cloud_names_are_left_out_of_the_list(fake):
    fake.models = ["local:latest", "gpt-oss:20b-cloud"]
    assert OllamaClient(fake.url).list_models() == ["local:latest"]


# ── thinking ─────────────────────────────────────────────────────────────────

def test_thinking_values(fake):
    assert OllamaClient(fake.url).think_values("local") is None
    fake.info = {"capabilities": ["thinking"], "thinking": {"values": ["low", "high"]}}
    assert OllamaClient(fake.url).think_values("local") == ["low", "high"]
    fake.info = {"capabilities": ["thinking"]}
    assert OllamaClient(fake.url).think_values("local") == [False, True]
