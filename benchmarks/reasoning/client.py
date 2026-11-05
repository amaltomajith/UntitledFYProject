"""
CountingClient: one place that sees every model call a method makes.

core.Orchestrator adds only the pool agents' tokens to EpisodeResult.total_tokens; the policy agent's own
calls (one per step) are never counted there. Every method in this benchmark talks to the model through
this wrapper, so the policy's tokens are counted like any other call.
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Optional

from benchmarks.reasoning.checkers import strip_think


@dataclasses.dataclass
class Call:
    model: str
    requested: str
    prompt_tokens: int
    completion_tokens: int
    cached: bool
    had_think: bool


class CountingClient:
    """Wrap a chat client (NebiusClient, CachingClient, or a scripted fake) and count every call."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[Call] = []
        self.errors: list[BaseException] = []

    def reset(self) -> None:
        self.calls, self.errors = [], []

    def chat(self, model: str, messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048,
             tools: Optional[list[dict]] = None, **kwargs) -> dict:
        try:
            reply = self.inner.chat(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens)
        except BaseException as e:   # the orchestrator swallows agent errors; record them first
            self.errors.append(e)
            raise
        usage = reply.get("usage") or {}
        raw = reply.get("content") or ""
        clean = strip_think(raw)
        self.calls.append(Call(model=reply.get("model") or model, requested=model,
                               prompt_tokens=int(usage.get("prompt_tokens") or 0),
                               completion_tokens=int(usage.get("completion_tokens") or 0),
                               cached=bool(reply.get("cached")), had_think=clean != raw))
        return {**reply, "content": clean}

    # totals for the current row
    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)

    def cost(self, prices: Optional[dict]) -> Optional[float]:
        """USD from models.yaml prices ([input, output] per 1M tokens), or None when a price is unknown."""
        total = 0.0
        for c in self.calls:
            p = (prices or {}).get(c.requested) or (prices or {}).get(c.model)
            if not p:
                return None
            total += c.prompt_tokens * p[0] / 1e6 + c.completion_tokens * p[1] / 1e6
        return total

    def list_models(self):
        return self.inner.list_models()


def set_thinking(client, on: bool):
    """Turn a reasoning model's thinking on or off for every call through an OpenAI-compatible client.

    Uses chat_template_kwargs.enable_thinking, which Nebius honours for the Nemotron models. Applied to the
    client, so every method and every agent gets the same setting.
    """
    completions = client._client.chat.completions
    completions.create = functools.partial(completions.create,
                                           extra_body={"chat_template_kwargs": {"enable_thinking": on}})
    return client
