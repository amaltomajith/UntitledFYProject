"""
puppeteer_code/providers.py
===========================
Model access for Puppeteer-Code.

  ProviderSession  one object per run: the chat client, the resolved model
                   for each tier, per-agent sampling settings, and a usage
                   meter that tracks tokens and (when prices are known) USD.
  MockClient       deterministic offline client for tests and dry runs.
  CachingClient    disk cache keyed on the exact request, so re-running a
                   benchmark during development costs nothing.

All clients expose the same `chat(model, messages, temperature, max_tokens)`
contract as core.agent.OpenAICompatClient, so core BaseAgent works unchanged.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except ImportError:
    pass

from core.agent import NebiusClient, GroqClient, OpenAICompatClient

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).with_name("models.yaml")


def load_config(path: Optional[str | Path] = None) -> dict:
    """Load the model routing table (models.yaml)."""
    with open(path or DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) for offline accounting."""
    return max(1, len(text) // 4)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  OFFLINE + CACHING CLIENTS
# ═════════════════════════════════════════════════════════════════════════════

Responder = Callable[[str, list[dict]], Union[str, dict]]


class MockClient:
    """
    Offline client.  `responder(model, messages)` returns the assistant text, or
    {"content": str, "tool_calls": [...]} to script a tool-using turn.

    The default responder answers every prompt with a no-op so that the
    engine can be exercised structurally; tests pass scripted responders.
    """

    def __init__(self, responder: Optional[Responder] = None):
        self.responder = responder or (lambda model, messages: "REASONING RESULT: n/a\nFINAL ANSWER: n/a")
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def chat(self, model: str, messages: list[dict], temperature: float = 0.7,
             max_tokens: int = 2048, tools: Optional[list[dict]] = None,
             on_delta: Optional[Callable[[str], None]] = None) -> dict:
        reply = self.responder(model, messages)
        text = reply.get("content", "") if isinstance(reply, dict) else reply
        prompt_tokens = sum(approx_tokens(m.get("content") or "") for m in messages)
        completion_tokens = approx_tokens(text)
        with self._lock:
            self.calls.append({"model": model, "messages": messages, "temperature": temperature,
                               "tools": tools})
        if on_delta is not None and text:
            on_delta(text)
        out = {
            "content": text,
            "model": model,
            "latency_ms": 0.0,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        if tools:
            out["tool_calls"] = reply.get("tool_calls", []) if isinstance(reply, dict) else []
        if isinstance(reply, dict) and reply.get("tool_failed"):
            out["tool_failed"] = True
        return out

    def list_models(self) -> list[str]:
        return ["mock-fast", "mock-quality"]


class CachingClient:
    """Wrap any client with an on-disk response cache (development only)."""

    def __init__(self, inner, cache_dir: str | Path):
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0

    def _key(self, model, messages, temperature, max_tokens, tools=None) -> Path:
        request = [model, messages, temperature, max_tokens] + ([tools] if tools else [])
        blob = json.dumps(request, sort_keys=True)
        return self.cache_dir / (hashlib.sha256(blob.encode("utf-8")).hexdigest() + ".json")

    def chat(self, model: str, messages: list[dict], temperature: float = 0.7,
             max_tokens: int = 2048, tools: Optional[list[dict]] = None,
             on_delta: Optional[Callable[[str], None]] = None) -> dict:
        path = self._key(model, messages, temperature, max_tokens, tools)
        if path.exists():
            self.hits += 1
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cached"] = True
            if on_delta is not None and data.get("content"):
                on_delta(data["content"])
            return data
        extra = {k: v for k, v in (("tools", tools), ("on_delta", on_delta)) if v is not None}
        data = self.inner.chat(model=model, messages=messages,
                               temperature=temperature, max_tokens=max_tokens, **extra)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def list_models(self) -> list[str]:
        return self.inner.list_models()


# ═════════════════════════════════════════════════════════════════════════════
# 2.  USAGE METER
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class ModelUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_calls: int = 0


class UsageMeter:
    """Counts tokens per model and converts to USD when prices are configured."""

    def __init__(self, prices: Optional[dict] = None):
        self.prices = prices or {}
        self.by_model: dict[str, ModelUsage] = {}
        self._lock = threading.Lock()

    def record(self, model: str, usage: dict, cached: bool = False) -> None:
        with self._lock:
            u = self.by_model.setdefault(model, ModelUsage())
            u.calls += 1
            u.prompt_tokens += int(usage.get("prompt_tokens", 0))
            u.completion_tokens += int(usage.get("completion_tokens", 0))
            u.cached_calls += int(cached)

    @property
    def total_tokens(self) -> int:
        return sum(u.prompt_tokens + u.completion_tokens for u in self.by_model.values())

    def cost_of(self, model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
        price = self.prices.get(model)
        if not price:
            return None
        return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1e6

    @property
    def cost_usd(self) -> Optional[float]:
        """Total USD, or None if any used model has no configured price."""
        total = 0.0
        for model, u in self.by_model.items():
            c = self.cost_of(model, u.prompt_tokens, u.completion_tokens)
            if c is None:
                return None
            total += c
        return total

    def snapshot(self) -> dict:
        return {
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "by_model": {m: dataclasses.asdict(u) for m, u in self.by_model.items()},
        }


class MeteredClient:
    """Client wrapper that records every call into a UsageMeter."""

    def __init__(self, inner, meter: UsageMeter):
        self.inner = inner
        self.meter = meter

    def chat(self, model: str, messages: list[dict], temperature: float = 0.7,
             max_tokens: int = 2048, **extra) -> dict:
        extra = {k: v for k, v in extra.items() if v is not None}
        data = self.inner.chat(model=model, messages=messages,
                               temperature=temperature, max_tokens=max_tokens, **extra)
        self.meter.record(model, data.get("usage", {}), cached=bool(data.get("cached")))
        return data

    def list_models(self) -> list[str]:
        return self.inner.list_models()


# ═════════════════════════════════════════════════════════════════════════════
# 3.  PROVIDER SESSION
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class AgentSettings:
    tier: str = "fast"            # fast | quality | none
    temperature: float = 0.2
    max_tokens: int = 2048


class ProviderSession:
    """
    Everything model-related for one run.

    Parameters
    ----------
    provider   : "nebius" | "groq" | "mock" (default from models.yaml)
    responder  : MockClient responder (only for provider="mock")
    cache_dir  : enable the on-disk response cache
    config     : pre-loaded models.yaml dict (tests)
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        responder: Optional[Responder] = None,
        cache_dir: Optional[str | Path] = None,
        config: Optional[dict] = None,
        resolve: bool = True,
    ):
        self.config = config or load_config()
        self.provider = provider or self.config.get("default_provider", "nebius")
        spec = self.config["providers"][self.provider]
        self.spec = spec

        raw = self._make_raw_client(spec, responder)
        if cache_dir and self.provider != "mock":
            raw = CachingClient(raw, cache_dir)
        self.meter = UsageMeter(spec.get("prices") or {})
        self.client = MeteredClient(raw, self.meter)

        self.tiers = self._resolve_tiers(spec, raw if resolve else None)
        self.agent_settings = {
            name: AgentSettings(**vals) for name, vals in (self.config.get("agents") or {}).items()
        }

    def _make_raw_client(self, spec: dict, responder: Optional[Responder]):
        if self.provider == "mock":
            return MockClient(responder)
        if self.provider == "nebius":
            return NebiusClient(base_url=spec.get("base_url"), api_key_env=spec.get("api_key_env"))
        if self.provider == "groq":
            return GroqClient(base_url=spec.get("base_url"), api_key_env=spec.get("api_key_env"))
        return OpenAICompatClient(base_url=spec["base_url"], api_key_env=spec["api_key_env"])

    def _resolve_tiers(self, spec: dict, client) -> dict[str, str]:
        """Pick the first served candidate per tier."""
        available: Optional[set[str]] = None
        if client is not None and self.provider != "mock":
            try:
                available = set(client.list_models())
            except Exception as e:  # offline, bad key, endpoint missing
                logger.warning("Could not list models for %s (%s); using first candidates.", self.provider, e)
        resolved = {}
        for tier, candidates in spec.get("tiers", {}).items():
            candidates = candidates if isinstance(candidates, list) else [candidates]
            choice = candidates[0]
            if available:
                served = [c for c in candidates if c in available]
                if served:
                    choice = served[0]
                else:
                    logger.warning("No %s-tier candidate is served by %s; trying %s anyway.",
                                   tier, self.provider, choice)
            resolved[tier] = choice
        return resolved

    # ── per-agent helpers ────────────────────────────────────────────────────

    def settings_for(self, agent_name: str) -> AgentSettings:
        return self.agent_settings.get(agent_name, AgentSettings())

    def model_for(self, agent_name: str, tier_override: Optional[str] = None) -> str:
        tier = tier_override or self.settings_for(agent_name).tier
        if tier == "none":
            return "none"
        return self.tiers.get(tier) or next(iter(self.tiers.values()))

    def candidate_report(self) -> list[dict]:
        """For `pcode models`: which configured candidates are served."""
        try:
            available = set(self.client.list_models())
        except Exception as e:
            return [{"error": str(e)}]
        rows = []
        for tier, candidates in self.spec.get("tiers", {}).items():
            for c in (candidates if isinstance(candidates, list) else [candidates]):
                rows.append({"tier": tier, "model": c, "served": c in available,
                             "selected": self.tiers.get(tier) == c})
        return rows


__all__ = [
    "ProviderSession", "MockClient", "CachingClient", "UsageMeter",
    "MeteredClient", "AgentSettings", "load_config", "approx_tokens",
]
