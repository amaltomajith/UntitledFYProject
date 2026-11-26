"""Offline tests for the cloud providers: OpenAI, Gemini, OpenRouter, Anthropic, and the key and provider routes.
No network and no real key: every SDK is replaced by a fake that records what it was sent."""

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.agent import GeminiClient, GroqClient, NebiusClient, OpenAIClient, OpenRouterClient
from puppeteer_code import providers
from puppeteer_code.anthropic_client import BINDING_BETA, MIN_OUTPUT, AnthropicClient, to_tools
from puppeteer_code.assistant import ide as ide_module
from puppeteer_code.assistant.ide import IdeHub
from puppeteer_code.assistant.session import AssistantSession, _call_message
from puppeteer_code.assistant.setup import KEY_HELP, PROVIDER_KEYS, key_hint, remove_key, save_key
from puppeteer_code.providers import ProviderSession, check_key, load_config
from puppeteer_code.server import create_app

TOKEN = "test-token-123"
LOCAL = {"host": "127.0.0.1:8765"}
AUTH = {**LOCAL, "x-pcode-token": TOKEN}
TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "Read a file",
                                           "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for name in PROVIDER_KEYS.values():
        monkeypatch.delenv(name, raising=False)


# ── OpenAI-compatible clients ────────────────────────────────────────────────

class FakeCompletions:
    def __init__(self, reply):
        self.reply = reply
        self.sent: list[dict] = []

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return self.reply


def completion(content="", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model="m")


def faked(cls, reply):
    client = cls(api_key="test-key-not-real")
    fake = FakeCompletions(reply)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return client, fake


@pytest.mark.parametrize("cls", [NebiusClient, GroqClient])
def test_existing_providers_send_exactly_what_they_did(cls):
    client, fake = faked(cls, completion("hi"))
    client.chat("m", [{"role": "user", "content": "x"}], temperature=0.3, max_tokens=99)
    assert fake.sent == [{"model": "m", "messages": [{"role": "user", "content": "x"}], "temperature": 0.3,
                          "max_tokens": 99}]


def test_openai_sends_completion_tokens_and_no_temperature():
    client, fake = faked(OpenAIClient, completion("hi"))
    client.chat("gpt-6-luna", [{"role": "user", "content": "x"}], temperature=0.3, max_tokens=99)
    assert fake.sent[0]["max_completion_tokens"] == 99
    assert "temperature" not in fake.sent[0] and "max_tokens" not in fake.sent[0]


def test_gemini_sends_no_temperature():
    client, fake = faked(GeminiClient, completion("hi"))
    client.chat("gemini-3.8-flash", [{"role": "user", "content": "x"}], temperature=0.3, max_tokens=99)
    assert fake.sent[0]["max_tokens"] == 99 and "temperature" not in fake.sent[0]


def test_gemini_keeps_a_thought_signature_and_the_session_sends_it_back():
    extra = {"google": {"thought_signature": "sig-abc"}}
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="read_file", arguments='{"path": "a.py"}'),
                           model_extra={"extra_content": extra})
    client, _ = faked(GeminiClient, completion("", [call]))
    reply = client.chat("gemini-3.8-flash", [{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply["tool_calls"] == [{"id": "c1", "name": "read_file", "arguments": '{"path": "a.py"}',
                                    "extra_content": extra}]
    assert _call_message(reply["tool_calls"][0])["extra_content"] == extra


def test_a_call_without_extras_is_the_same_history_entry_as_before():
    entry = _call_message({"id": "c1", "name": "read_file", "arguments": "{}"})
    assert entry == {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}


def test_gemini_model_ids_lose_their_prefix():
    client, _ = faked(GeminiClient, completion())
    client._client.models = SimpleNamespace(list=lambda: [SimpleNamespace(id="models/gemini-3.8-flash"),
                                                          SimpleNamespace(id="gemini-3.5-flash-lite")])
    assert client.list_models() == ["gemini-3.5-flash-lite", "gemini-3.8-flash"]


def test_openrouter_labels_the_app_and_reads_prices_per_million():
    assert OpenRouterClient.HEADERS["X-Title"] == "CompactCode"
    client, _ = faked(OpenRouterClient, completion())
    client._client.models = SimpleNamespace(list=lambda: [
        SimpleNamespace(id="a/x", model_extra={"pricing": {"prompt": "0.000001", "completion": "0.000004"}}),
        SimpleNamespace(id="b/y", model_extra={"pricing": {"prompt": "n/a"}}),
        SimpleNamespace(id="c/z", model_extra={})])
    prices = client.catalog_prices()
    assert prices.keys() == {"a/x"} and prices["a/x"] == pytest.approx([1.0, 4.0])


def test_catalog_prices_reach_the_meter(monkeypatch):
    class Catalog:
        def __init__(self, **_):
            pass

        def list_models(self):
            return ["openai/gpt-oss-120b"]

        def catalog_prices(self):
            return {"openai/gpt-oss-120b": [0.15, 0.6]}

    monkeypatch.setattr(providers, "OpenRouterClient", Catalog)
    session = ProviderSession("openrouter")
    assert session.tiers["quality"] == "openai/gpt-oss-120b"
    assert session.meter.cost_of("openai/gpt-oss-120b", 1_000_000, 0) == pytest.approx(0.15)
    assert session.catalog() == [{"id": "google/gemini-3.5-flash-lite", "price": None},     # unknown, not guessed
                                 {"id": "openai/gpt-oss-120b", "price": [0.15, 0.6]}]


def test_every_keyed_provider_has_a_config_block_and_a_key_page():
    config = load_config()
    for name in PROVIDER_KEYS:
        assert name in config["providers"] and name in KEY_HELP
        assert config["providers"][name]["api_key_env"] == PROVIDER_KEYS[name]


@pytest.mark.parametrize("name,cls", [("openai", OpenAIClient), ("gemini", GeminiClient),
                                      ("openrouter", OpenRouterClient)])
def test_each_provider_gets_its_own_client(monkeypatch, name, cls):
    monkeypatch.setenv(PROVIDER_KEYS[name], "test-key-not-real")
    session = ProviderSession(name, resolve=False)
    assert type(session.client.inner) is cls
    assert session.append_only is False


# ── Anthropic ────────────────────────────────────────────────────────────────

class Block(dict):
    def model_dump(self, exclude_none=True):
        return dict(self)


class FakeStream:
    def __init__(self, events, final, error=None):
        self.events, self.final, self.error = events, final, error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        yield from self.events
        if self.error:
            raise self.error

    def get_final_message(self):
        return self.final


class FakeAnthropic:
    def __init__(self, *streams):
        self.streams = list(streams)
        self.sent: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.models = SimpleNamespace(list=lambda: [SimpleNamespace(id="claude-opus-5-5")])

    def _stream(self, **request):
        self.sent.append(request)
        return self.streams.pop(0)


def final(blocks, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=100, cache_read_input_tokens=20, cache_creation_input_tokens=0,
                            output_tokens=30)
    return SimpleNamespace(content=[Block(b) for b in blocks], usage=usage, stop_reason=stop, model="claude-opus-5-5")


THINK = {"type": "thinking", "thinking": "look at a.py", "signature": "s1"}
USE = {"type": "tool_use", "id": "tu1", "name": "read_file", "input": {"path": "a.py"}}


def tool_turn():
    events = [SimpleNamespace(type="thinking", thinking="look"),
              SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="tool_use", name="read_file")),
              SimpleNamespace(type="input_json", partial_json='{"path": '),
              SimpleNamespace(type="input_json", partial_json='"a.py"}')]
    return FakeStream(events, final([THINK, USE], "tool_use"))


def test_anthropic_request_shape_and_streamed_tool_call():
    sdk = FakeAnthropic(tool_turn())
    client = AnthropicClient(api_key="x", sdk=sdk)
    seen = []
    reply = client.chat("claude-opus-5-5", [{"role": "system", "content": "Be brief."},
                                            {"role": "user", "content": "read a.py"}],
                        temperature=0.2, max_tokens=1024, tools=TOOLS, on_progress=lambda *a: seen.append(a),
                        think="high")
    request = sdk.sent[0]
    assert "temperature" not in request and request["max_tokens"] == MIN_OUTPUT
    assert request["system"] == "Be brief." and request["betas"] == [BINDING_BETA]
    assert request["thinking"]["type"] == "adaptive"
    assert request["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert request["output_config"] == {"effort": "high"}
    assert request["tools"][0]["eager_input_streaming"] is True
    assert request["messages"] == [{"role": "user", "content": [{"type": "text", "text": "read a.py"}]}]
    assert ("tool", '{"path": "a.py"}', "read_file") in seen and ("thinking", "look") in seen
    assert reply["tool_calls"] == [{"id": "tu1", "name": "read_file", "arguments": '{"path": "a.py"}'}]
    assert reply["usage"] == {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}


def test_anthropic_replays_its_own_blocks_and_groups_results():
    sdk = FakeAnthropic(tool_turn(), FakeStream([], final([{"type": "text", "text": "done"}])))
    client = AnthropicClient(api_key="x", sdk=sdk)
    reply = client.chat("claude-opus-5-5", [{"role": "user", "content": "go"}], tools=TOOLS)
    history = [{"role": "user", "content": "go"},
               {"role": "assistant", "content": "", "tool_calls": [_call_message(c) for c in reply["tool_calls"]]},
               {"role": "tool", "tool_call_id": "tu1", "content": "print(1)"}]
    assert client.chat("claude-opus-5-5", history, tools=TOOLS)["content"] == "done"
    messages = sdk.sent[1]["messages"]
    assert messages[1] == {"role": "assistant", "content": [THINK, USE]}     # thinking kept, unchanged
    assert messages[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "print(1)"}]}


def test_anthropic_builds_blocks_for_a_turn_it_did_not_write():
    client = AnthropicClient(api_key="x", sdk=FakeAnthropic())
    system, messages = client.to_messages([
        {"role": "assistant", "content": "I will read it.", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}},
            {"id": "t2", "type": "function", "function": {"name": "read_file", "arguments": "not json"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "A"},
        {"role": "tool", "tool_call_id": "t2", "content": "B"}])
    assert system == "" and messages[0]["role"] == "user"       # a conversation must open with the user
    assert [b["type"] for b in messages[1]["content"]] == ["text", "tool_use", "tool_use"]
    assert messages[1]["content"][2]["input"] == {}
    assert [b["tool_use_id"] for b in messages[2]["content"]] == ["t1", "t2"]


def test_anthropic_refusal_and_cut_off_never_run_tools():
    refused = FakeStream([], final([{"type": "text", "text": ""}, USE], "refusal"))
    cut = FakeStream([], final([USE], "max_tokens"))
    client = AnthropicClient(api_key="x", sdk=FakeAnthropic(refused, cut))
    reply = client.chat("claude-opus-5-5", [{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply["tool_calls"] == [] and reply["content"] == "The model declined this request."
    reply = client.chat("claude-opus-5-5", [{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply["tool_calls"] == [] and reply["tool_failed"] and reply["cut_off"]


def test_anthropic_unparseable_tool_input_is_a_failed_call():
    client = AnthropicClient(api_key="x", sdk=FakeAnthropic(FakeStream([], None, error=ValueError("bad json"))))
    reply = client.chat("claude-opus-5-5", [{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply["tool_failed"] is True and reply["tool_calls"] == []


def test_anthropic_plain_reply_streams_text():
    events = [SimpleNamespace(type="text", text="Hel"), SimpleNamespace(type="text", text="lo")]
    client = AnthropicClient(api_key="x", sdk=FakeAnthropic(FakeStream(events, final([{"type": "text", "text": "Hello"}]))))
    parts = []
    reply = client.chat("claude-haiku-5-5", [{"role": "user", "content": "hi"}], on_delta=parts.append, think=False)
    assert parts == ["Hel", "lo"] and reply["content"] == "Hello" and "tool_calls" not in reply


def test_anthropic_session_is_append_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    session = ProviderSession("anthropic", resolve=False)
    assert type(session.client.inner) is AnthropicClient and session.append_only is True
    assert session.tiers == {"fast": "claude-haiku-5-5", "quality": "claude-opus-5-5"}


def test_to_tools_keeps_the_schema():
    assert to_tools(TOOLS)[0]["input_schema"]["properties"] == {"path": {"type": "string"}}


def test_memory_does_not_fold_an_append_only_history(tmp_path, monkeypatch):
    folded = []
    monkeypatch.setattr("puppeteer_code.assistant.session.fold", lambda m: folded.append(1) or [])
    provider = ProviderSession("mock", responder=lambda m, msgs: "Answer.")
    provider.append_only = True
    session = AssistantSession(tmp_path / "p", provider, home=tmp_path / "home", memory=True, policy="direct")
    list(session.send("what does this do?"))
    assert folded == []


# ── keys ─────────────────────────────────────────────────────────────────────

def test_a_key_hint_shows_four_characters_only(tmp_path):
    save_key("openai", "sk-test-abcdefgh1234", tmp_path)
    assert key_hint("openai") == "1234"
    os.environ["OPENAI_API_KEY"] = "short"
    assert key_hint("openai") is None


def test_removing_a_key_leaves_others_and_a_shell_key(tmp_path, monkeypatch):
    save_key("groq", "gsk_keepthisone", tmp_path)
    save_key("gemini", "gem_removethisone", tmp_path)
    remove_key("gemini", tmp_path)
    text = (tmp_path / ".env").read_text()
    assert "GEMINI_API_KEY" not in text and "GROQ_API_KEY=gsk_keepthisone\n" in text
    assert "GEMINI_API_KEY" not in os.environ
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-shell")     # not saved by pcode
    remove_key("openai", tmp_path)
    assert os.environ["OPENAI_API_KEY"] == "sk-from-the-shell"


def test_check_key_never_returns_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-123")

    def refuse(self):
        raise RuntimeError("401 Incorrect API key provided: sk-secret-value-123")

    monkeypatch.setattr(OpenAIClient, "list_models", refuse)
    with pytest.raises(RuntimeError) as caught:
        check_key("openai")
    assert "sk-secret-value-123" not in str(caught.value) and "401" in str(caught.value)
    monkeypatch.setattr(OpenAIClient, "list_models", lambda self: ["a", "b"])
    assert check_key("openai") == 2


# ── routes ───────────────────────────────────────────────────────────────────

def ide_client(tmp_path):
    made = []

    def make(name):
        made.append(name)
        return ProviderSession("mock", responder=lambda m, msgs: "done")

    hub = IdeHub(TOKEN, make, home=tmp_path / "home", provider="mock")
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub)
    return TestClient(app, base_url="http://127.0.0.1:8765"), hub, made


@pytest.mark.parametrize("name", sorted(PROVIDER_KEYS))
def test_every_provider_key_can_be_saved_and_is_never_returned(tmp_path, name):
    client, _, _ = ide_client(tmp_path)
    secret = f"{name}-key-verysecret-wxyz"
    reply = client.post("/api/ide/keys", json={"provider": name, "key": secret}, headers=AUTH)
    assert reply.status_code == 200
    body = reply.json()
    assert body["keys"][name] is True and body["key_hints"][name] == "wxyz"
    assert secret not in reply.text and secret not in client.get("/api/ide/state", headers=AUTH).text


def test_unknown_provider_is_refused(tmp_path):
    client, _, _ = ide_client(tmp_path)
    assert client.post("/api/ide/keys", json={"provider": "evil", "key": "x"}, headers=AUTH).status_code == 422
    assert client.post("/api/ide/provider", json={"provider": "evil"}, headers=AUTH).status_code == 422
    assert client.delete("/api/ide/keys/evil", headers=AUTH).status_code == 422


def test_key_test_route(tmp_path, monkeypatch):
    client, _, _ = ide_client(tmp_path)
    assert client.post("/api/ide/keys/test", json={"provider": "groq"}, headers=AUTH).status_code == 409
    client.post("/api/ide/keys", json={"provider": "groq", "key": "gsk_abcdefghijkl"}, headers=AUTH)
    monkeypatch.setattr(ide_module, "check_key", lambda name: 12)
    assert client.post("/api/ide/keys/test", json={"provider": "groq"}, headers=AUTH).json() == {"ok": True, "models": 12}

    def bad(name):
        raise RuntimeError("401 invalid key")

    monkeypatch.setattr(ide_module, "check_key", bad)
    assert client.post("/api/ide/keys/test", json={"provider": "groq"}, headers=AUTH).json() == {
        "ok": False, "message": "401 invalid key"}


def test_key_routes_need_the_token(tmp_path):
    client, _, _ = ide_client(tmp_path)
    assert client.post("/api/ide/keys/test", json={"provider": "groq"}, headers=LOCAL).status_code == 401
    assert client.delete("/api/ide/keys/groq", headers=LOCAL).status_code == 401
    assert client.get("/api/ide/models", headers=LOCAL).status_code == 401


def test_forgetting_the_active_key(tmp_path):
    client, hub, _ = ide_client(tmp_path)
    client.post("/api/ide/keys", json={"provider": "groq", "key": "gsk_abcdefghijkl"}, headers=AUTH)
    hub.provider_name = "groq"
    body = client.delete("/api/ide/keys/groq", headers=AUTH).json()
    assert body["keys"]["groq"] is False and hub.provider_name is None
    assert "GROQ_API_KEY" not in (tmp_path / "home" / ".env").read_text()


def test_switching_provider_keeps_the_conversation(tmp_path):
    client, hub, made = ide_client(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    assert client.post("/api/ide/open", json={"path": str(project)}, headers=AUTH).status_code == 200
    session = hub.session
    session.history.append({"role": "user", "content": "earlier"})
    assert client.post("/api/ide/provider", json={"provider": "openai"}, headers=AUTH).status_code == 409   # no key
    client.post("/api/ide/keys", json={"provider": "openai", "key": "sk-abcdefghijkl"}, headers=AUTH)
    reply = client.post("/api/ide/provider", json={"provider": "openai"}, headers=AUTH)
    assert reply.status_code == 200 and reply.json()["provider"] == "openai"
    assert hub.session is session and session.history[-1]["content"] == "earlier"
    assert made[-1] == "openai"


def test_switching_provider_is_refused_in_private(tmp_path):
    client, hub, _ = ide_client(tmp_path)
    client.post("/api/ide/keys", json={"provider": "openai", "key": "sk-abcdefghijkl"}, headers=AUTH)
    hub.private = True
    reply = client.post("/api/ide/provider", json={"provider": "openai"}, headers=AUTH)
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "private_on"


def test_models_route_lists_the_catalog_once(tmp_path, monkeypatch):
    client, hub, made = ide_client(tmp_path)
    client.post("/api/ide/keys", json={"provider": "groq", "key": "gsk_abcdefghijkl"}, headers=AUTH)
    first = client.get("/api/ide/models", params={"provider": "groq"}, headers=AUTH).json()
    assert first["provider"] == "groq"
    assert {m["id"] for m in first["models"]} >= {"mock-fast", "mock-quality"}
    assert [m for m in first["models"] if m["id"] == "mock-quality"][0]["price"] == [1.0, 3.0]
    count = len(made)
    client.get("/api/ide/models", params={"provider": "groq"}, headers=AUTH)
    assert len(made) == count          # held for ten minutes
    assert client.get("/api/ide/models", params={"provider": "gemini"}, headers=AUTH).status_code == 409


def test_state_reports_cost_only_when_known(tmp_path):
    client, hub, _ = ide_client(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    body = client.post("/api/ide/open", json={"path": str(project)}, headers=AUTH).json()
    assert body["cost_usd"] == 0.0 and body["prices"]["quality"] == [1.0, 3.0]
    assert body["provider_names"]["openrouter"] == "OpenRouter"
