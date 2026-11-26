"""
puppeteer_code/anthropic_client.py
==================================
Claude through Anthropic's own Messages API and SDK, behind the same
`chat(model, messages, ...)` contract as core.agent.OpenAICompatClient.

The rest of pcode speaks OpenAI-shaped messages (a "system" message, assistant
turns with "tool_calls", and "tool" results). This client converts them:

- system messages become the top-level `system`;
- an assistant turn with tool calls becomes `tool_use` blocks, and every result
  of that turn goes back in one user message as `tool_result` blocks;
- tool specs become `input_schema` tools with eager input streaming.

Thinking is adaptive and steered by effort (low, medium, high), which pcode's own
effort setting chooses per agent through the `think` option. The current models
keep each reply's thinking blocks bound to the conversation that produced them, so
the client remembers each assistant turn's raw blocks and sends them back
unchanged. If the history was edited anyway (pcode trims long prompts), requests
ask the API to drop the stale blocks instead of failing.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Optional

EFFORTS = ["low", "medium", "high"]
BINDING_BETA = "thinking-binding-controls-2026-08-01"
# Thinking shares the output budget, so a small cap set for a non-thinking model would cut replies off.
MIN_OUTPUT = 32000
MAX_REMEMBERED = 512


def _effort(think) -> str:
    if think in EFFORTS:
        return think
    return "low" if think is False else "medium"


def _parse_args(text: str) -> dict:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def to_tools(tools: Optional[list[dict]]) -> list[dict]:
    """OpenAI function specs to Anthropic tools. Inputs stream as they are written."""
    out = []
    for spec in tools or []:
        fn = spec.get("function", spec)
        out.append({"name": fn["name"], "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                    "eager_input_streaming": True})
    return out


class AnthropicClient:
    PROVIDER = "anthropic"
    API_KEY_ENV = "ANTHROPIC_API_KEY"

    def __init__(self, api_key: Optional[str] = None, api_key_env: Optional[str] = None,
                 base_url: Optional[str] = None, max_retries: int = 4, sdk=None):
        name = api_key_env or self.API_KEY_ENV
        key = api_key or os.environ.get(name)
        if not key:
            raise EnvironmentError(f"{name} not found. Add an Anthropic key first.")
        if sdk is None:
            import anthropic
            sdk = anthropic.Anthropic(api_key=key, base_url=base_url or None, max_retries=max_retries)
        self._client = sdk
        self._turns: dict[str, list[dict]] = {}     # first tool_use id of a turn -> that turn's raw blocks

    # ── messages ─────────────────────────────────────────────────────────────

    def to_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        system: list[str] = []
        out: list[dict] = []

        def add(role: str, blocks: list[dict]) -> None:
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": list(blocks)})

        for message in messages:
            role, content = message["role"], message.get("content") or ""
            if role == "system":
                system.append(content)
            elif role == "user":
                if content:
                    add("user", [{"type": "text", "text": content}])
            elif role == "tool":
                add("user", [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": content}])
            elif role == "assistant":
                calls = message.get("tool_calls") or []
                remembered = self._turns.get(calls[0]["id"]) if calls else None
                if remembered is not None:
                    add("assistant", remembered)
                    continue
                blocks = [{"type": "text", "text": content}] if content else []
                for call in calls:
                    fn = call.get("function", call)
                    blocks.append({"type": "tool_use", "id": call["id"], "name": fn["name"],
                                   "input": _parse_args(fn.get("arguments", ""))})
                if blocks:
                    add("assistant", blocks)
        if out and out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": [{"type": "text", "text": "Continue."}]})
        return "\n\n".join(s for s in system if s), out

    def _remember(self, blocks: list[dict]) -> None:
        first = next((b["id"] for b in blocks if b.get("type") == "tool_use"), None)
        if first is None:
            return
        if len(self._turns) >= MAX_REMEMBERED:
            self._turns.pop(next(iter(self._turns)))
        self._turns[first] = blocks

    # ── calls ────────────────────────────────────────────────────────────────

    def chat(self, model: str, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048,
             tools: Optional[list[dict]] = None, on_delta: Optional[Callable[[str], None]] = None,
             on_progress: Optional[Callable[..., None]] = None, think=None) -> dict:
        """temperature is accepted for the shared contract and not sent: current Claude models reject it."""
        system, converted = self.to_messages(messages)
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max(max_tokens, MIN_OUTPUT),
            "messages": converted,
            "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            "output_config": {"effort": _effort(think)},
            "betas": [BINDING_BETA],
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = to_tools(tools)
        t0 = time.monotonic()
        name, arguments = "", ""      # the tool call being written now: one at a time in a stream
        try:
            with self._client.beta.messages.stream(**request) as stream:
                for event in stream:
                    kind = getattr(event, "type", "")
                    if kind == "content_block_start":
                        block = event.content_block
                        if getattr(block, "type", "") == "tool_use":
                            name, arguments = block.name, ""
                    elif kind == "text":
                        if on_delta is not None and not tools:
                            on_delta(event.text)
                        if on_progress is not None:
                            on_progress("content", event.text)
                    elif kind == "thinking" and on_progress is not None:
                        on_progress("thinking", event.thinking)
                    elif kind == "input_json":
                        arguments += event.partial_json
                        if on_progress is not None:
                            on_progress("tool", arguments, name)
                final = stream.get_final_message()
        except ValueError:
            # Tool input the SDK could not parse at all: the agent gets one more try.
            return {"content": "", "model": model, "latency_ms": (time.monotonic() - t0) * 1000,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "tool_calls": [], "tool_failed": True}
        return self._reply(final, model, t0, tools)

    def _reply(self, final, model: str, t0: float, tools) -> dict:
        blocks = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b) for b in final.content]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [{"id": b["id"], "name": b["name"], "arguments": json.dumps(b.get("input") or {})}
                 for b in blocks if b.get("type") == "tool_use"]
        usage = final.usage
        prompt = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        completion = getattr(usage, "output_tokens", 0) or 0
        reply = {"content": text, "model": getattr(final, "model", model) or model,
                 "latency_ms": (time.monotonic() - t0) * 1000,
                 "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                           "total_tokens": prompt + completion}}
        reason = getattr(final, "stop_reason", None)
        if reason == "refusal":
            # A declined request: nothing to run, and the reply says so in plain words.
            reply["content"] = text or "The model declined this request."
            reply["tool_calls"] = [] if tools else None
            reply = {k: v for k, v in reply.items() if v is not None}
            return reply
        if tools:
            if reason == "max_tokens" and calls:
                # A tool input cut off at the limit parses as a partial object. Never run it.
                reply.update(tool_calls=[], tool_failed=True, cut_off=True)
                return reply
            reply["tool_calls"] = calls
            self._remember(blocks)
        return reply

    def list_models(self) -> list[str]:
        return sorted(m.id for m in self._client.models.list())

    def think_values(self, model: str) -> list:
        return list(EFFORTS)
