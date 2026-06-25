"""
tests/test_agent.py
===================
Test suite for core/agent.py.

Two modes:
  1. UNIT tests  (no API key needed)  — run with: pytest tests/test_agent.py -v
  2. LIVE test   (needs GROQ_API_KEY) — run with: pytest tests/test_agent.py -v --live

The unit tests use a MockGroqClient that returns a canned response so you can
verify all the parsing, config, and state-management logic without spending API
credits or needing a network connection.

The live test sends a real request to Groq and validates the response shape.

How to run
----------
# Unit tests only (no key needed):
    pytest tests/test_agent.py -v

# Unit + live (needs key in .env or environment):
    pytest tests/test_agent.py -v --live
"""

import sys
import os
import json
import pytest
import dataclasses

# ── Make sure the project root is on the path ─────────────────────────────────
# This allows `python -m pytest` from the puppeteer-plus/ directory OR
# a bare `pytest tests/` call to both find the `core` package correctly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Load .env if present (so GROQ_API_KEY is available for the live test) ─────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass  # python-dotenv is optional; key may be set directly in the shell

from core.agent import (
    AgentOutput,
    AgentConfig,
    GroqClient,
    BaseAgent,
    ReasoningAgent,
)


# ─────────────────────────────────────────────────────────────────────────────
# PYTEST FIXTURE: --live
# Note: pytest_addoption is in conftest.py (pytest requires it there).
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def live(request):
    """Returns True if --live was passed on the command line."""
    return request.config.getoption("--live")


# ─────────────────────────────────────────────────────────────────────────────
# MOCK GROQ CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class MockGroqClient:
    """
    A fake GroqClient that returns a canned response without hitting the API.

    This lets all unit tests run offline.  The response mimics the exact
    structure the real GroqClient returns, so the agents can't tell the
    difference.
    """

    CANNED_RESPONSE = {
        "content": (
            "Let me work through this step by step.\n\n"
            "REASONING RESULT: The train travels at 120 km/h. "
            "Distance = 450 km. Time = Distance / Speed = 450 / 120 = 3.75 hours. "
            "Converting to minutes: 3.75 * 60 = 225 minutes.\n\n"
            "FINAL ANSWER: 225 minutes"
        ),
        "model": "mock-llama-3.1-8b-instant",
        "latency_ms": 42.0,
        "usage": {
            "prompt_tokens": 80,
            "completion_tokens": 60,
            "total_tokens": 140,
        },
    }

    def chat(self, model, messages, temperature=0.7, max_tokens=2048):
        """Return the canned response regardless of input."""
        return self.CANNED_RESPONSE


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_client():
    """A MockGroqClient instance shared across unit tests."""
    return MockGroqClient()


@pytest.fixture
def reasoning_agent(mock_client):
    """A ReasoningAgent wired to the mock client."""
    return ReasoningAgent(groq_client=mock_client)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — AgentConfig tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentConfig:
    """
    Tests for the (m, r, t) triple dataclass.
    Paper reference: §2, paragraph 1.
    """

    def test_config_stores_all_fields(self):
        """All three paper fields (m, r, t) must be stored correctly."""
        cfg = AgentConfig(
            model_name="llama-3.1-8b-instant",
            role_prompt="You are a test agent.",
            tools=["run_python", "search_bing"],
        )
        assert cfg.model_name  == "llama-3.1-8b-instant"   # m
        assert cfg.role_prompt == "You are a test agent."  # r
        assert cfg.tools       == ["run_python", "search_bing"]  # t

    def test_config_defaults(self):
        """temperature and max_tokens must have sensible defaults."""
        cfg = AgentConfig(
            model_name="llama-3.1-8b-instant",
            role_prompt="x",
            tools=[],
        )
        assert cfg.temperature == 0.7
        assert cfg.max_tokens  == 2048

    def test_config_empty_tools_for_pure_reasoning(self):
        """
        Pure reasoning agents have t = [] (no tools).
        Paper §2 Table 2: Reasoning Patterns row has no tools.
        """
        cfg = AgentConfig(model_name="m", role_prompt="r", tools=[])
        assert cfg.tools == []


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — AgentOutput tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentOutput:
    """Tests for the structured return type every agent must produce."""

    def _make_output(self, reasoning="test reasoning", final_answer="test answer"):
        return AgentOutput(
            agent_name       = "ReasoningAgent",
            reasoning_result = reasoning,
            final_answer     = final_answer,
            raw_response     = f"REASONING RESULT: {reasoning}\nFINAL ANSWER: {final_answer}",
            token_usage      = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            latency_ms       = 100.0,
        )

    def test_output_is_dataclass(self):
        """AgentOutput must be a dataclass (for easy serialisation)."""
        out = self._make_output()
        assert dataclasses.is_dataclass(out)

    def test_to_context_string_contains_agent_name(self):
        """
        to_context_string() is how S_t is built up step by step.
        The orchestrator must be able to identify which agent spoke.
        """
        out = self._make_output()
        ctx = out.to_context_string()
        assert "ReasoningAgent" in ctx

    def test_to_context_string_contains_reasoning(self):
        out = self._make_output(reasoning="My reasoning here")
        assert "My reasoning here" in out.to_context_string()

    def test_to_context_string_contains_answer(self):
        out = self._make_output(final_answer="42")
        assert "42" in out.to_context_string()

    def test_metadata_defaults_to_empty_dict(self):
        """metadata field must default to {} not None (mutable default trap)."""
        out = self._make_output()
        assert out.metadata == {}

    def test_two_outputs_dont_share_metadata(self):
        """Mutable default field must NOT be shared between instances."""
        a = self._make_output()
        b = self._make_output()
        a.metadata["key"] = "value"
        assert "key" not in b.metadata


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — BaseAgent._parse_output tests
# ─────────────────────────────────────────────────────────────────────────────

class TestParseOutput:
    """
    Tests for the static output parser.

    The paper (Appendix B.2, Figs 15-16) mandates this exact response format:
      REASONING RESULT: [YOUR REASONING RESULT]
      FINAL ANSWER: [YOUR FINAL ANSWER]

    Our parser must be robust to:
      - Correct format   → extract both fields
      - Missing FINAL    → still return reasoning
      - No tags at all   → graceful degradation (whole text = reasoning)
      - Case variants    → must be case-insensitive
    """

    def test_parses_correct_format(self):
        text = "REASONING RESULT: Because 2+2=4.\nFINAL ANSWER: 4"
        r, f = BaseAgent._parse_output(text)
        assert "2+2=4" in r
        assert f == "4"

    def test_parses_multiline_reasoning(self):
        text = (
            "REASONING RESULT: Step 1: blah.\nStep 2: blah blah.\n"
            "FINAL ANSWER: The answer is X."
        )
        r, f = BaseAgent._parse_output(text)
        assert "Step 1" in r
        assert "Step 2" in r
        assert "The answer is X." in f

    def test_graceful_degradation_no_tags(self):
        """If LLM ignores the format, return full text as reasoning, empty answer."""
        text = "The answer is just 42."
        r, f = BaseAgent._parse_output(text)
        assert "42" in r
        assert f == ""

    def test_graceful_degradation_no_final_answer_tag(self):
        """REASONING RESULT present but FINAL ANSWER missing."""
        text = "REASONING RESULT: I thought hard."
        r, f = BaseAgent._parse_output(text)
        assert "I thought hard" in r
        assert f == ""

    def test_case_insensitive(self):
        """Tags should be found regardless of case."""
        text = "reasoning result: something\nfinal answer: 99"
        r, f = BaseAgent._parse_output(text)
        assert "something" in r
        assert "99" in f

    def test_empty_string(self):
        """Empty LLM response must not crash the parser."""
        r, f = BaseAgent._parse_output("")
        assert r == ""
        assert f == ""


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — ReasoningAgent unit tests (uses MockGroqClient)
# ─────────────────────────────────────────────────────────────────────────────

class TestReasoningAgentUnit:
    """
    Tests that run without any API key — MockGroqClient is injected.
    """

    def test_agent_name_is_class_name(self, reasoning_agent):
        """Paper §2: agents are identified by name for trajectory logging."""
        assert reasoning_agent.name == "ReasoningAgent"

    def test_agent_has_no_tools(self, reasoning_agent):
        """
        ReasoningAgent is a pure reasoning agent (t = []).
        Paper Table 2: reasoning patterns have no tools.
        """
        assert reasoning_agent.config.tools == []

    def test_invoke_returns_agent_output(self, reasoning_agent):
        """invoke() must always return an AgentOutput, never a raw string."""
        out = reasoning_agent.invoke(task="What is 1+1?", context="")
        assert isinstance(out, AgentOutput)

    def test_invoke_populates_agent_name(self, reasoning_agent):
        out = reasoning_agent.invoke(task="x", context="")
        assert out.agent_name == "ReasoningAgent"

    def test_invoke_populates_token_usage(self, reasoning_agent):
        """Token usage must be populated for the reward function C_t."""
        out = reasoning_agent.invoke(task="x", context="")
        assert "prompt_tokens"     in out.token_usage
        assert "completion_tokens" in out.token_usage
        assert "total_tokens"      in out.token_usage
        # All must be non-negative integers
        for v in out.token_usage.values():
            assert isinstance(v, int) and v >= 0

    def test_invoke_parses_reasoning_result(self, reasoning_agent):
        """The REASONING RESULT tag from the mock response must be extracted."""
        out = reasoning_agent.invoke(task="x", context="")
        assert "225 minutes" in out.reasoning_result or "3.75" in out.reasoning_result

    def test_invoke_parses_final_answer(self, reasoning_agent):
        """The FINAL ANSWER tag from the mock response must be extracted."""
        out = reasoning_agent.invoke(task="x", context="")
        assert "225" in out.final_answer

    def test_invoke_stores_in_episode_history(self, reasoning_agent):
        """
        Paper §2.3 (compaction/cyclicality analysis): the orchestrator tracks
        how many times each agent is called within an episode.
        """
        assert len(reasoning_agent.episode_history) == 0
        reasoning_agent.invoke(task="a", context="")
        reasoning_agent.invoke(task="b", context="")
        assert len(reasoning_agent.episode_history) == 2

    def test_reset_episode_clears_history(self, reasoning_agent):
        """reset_episode() must wipe per-episode state completely."""
        reasoning_agent.invoke(task="x", context="")
        assert len(reasoning_agent.episode_history) == 1
        reasoning_agent.reset_episode()
        assert len(reasoning_agent.episode_history) == 0

    def test_context_is_passed_to_action_prompt(self, mock_client, monkeypatch):
        """
        The previous agents' context string MUST appear in the action prompt
        sent to the LLM.  This is how serialised orchestration works (§2.1).
        """
        captured_messages = []

        original_chat = mock_client.chat
        def capturing_chat(model, messages, **kwargs):
            captured_messages.extend(messages)
            return original_chat(model, messages, **kwargs)

        monkeypatch.setattr(mock_client, "chat", capturing_chat)

        agent = ReasoningAgent(groq_client=mock_client)
        prev_context = "[PlannerAgent]\nReasoning: Decomposed the problem.\nAnswer: Sub-questions: A, B, C\n"
        agent.invoke(task="Solve X", context=prev_context)

        # The user message (action prompt) must contain the prior context
        user_msg = next(m for m in captured_messages if m["role"] == "user")
        assert "PlannerAgent" in user_msg["content"], (
            "Prior agent context was not injected into the action prompt. "
            "Serialised orchestration (§2.1) requires each agent to see all prior outputs."
        )

    def test_role_prompt_used_as_system_message(self, mock_client, monkeypatch):
        """
        The role prompt (r from a=(m,r,t)) MUST be the system message.
        This is the paper's mechanism for giving each agent its persona.
        """
        captured_messages = []

        original_chat = mock_client.chat
        def capturing_chat(model, messages, **kwargs):
            captured_messages.extend(messages)
            return original_chat(model, messages, **kwargs)

        monkeypatch.setattr(mock_client, "chat", capturing_chat)

        agent = ReasoningAgent(groq_client=mock_client)
        agent.invoke(task="x", context="")

        system_msg = next(m for m in captured_messages if m["role"] == "system")
        assert "logical reasoning" in system_msg["content"].lower(), (
            "Role prompt not used as system message. "
            "The r in a=(m,r,t) must be the system-level persona (Appendix B.2)."
        )

    def test_repr_is_informative(self, reasoning_agent):
        """__repr__ must show model and tools for easy debugging."""
        r = repr(reasoning_agent)
        assert "ReasoningAgent" in r
        assert "llama" in r.lower()

    def test_to_context_string_is_labelled(self, reasoning_agent):
        """
        The context string fed back into S_t must be labelled with the agent name
        so the orchestrator's LLM knows who produced each piece of reasoning.
        """
        out = reasoning_agent.invoke(task="x", context="")
        ctx = out.to_context_string()
        assert ctx.startswith("[ReasoningAgent]")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — GroqClient unit tests (mocking at a lower level)
# ─────────────────────────────────────────────────────────────────────────────

class TestGroqClientUnit:

    def test_raises_without_api_key(self, monkeypatch):
        """GroqClient must fail loudly with a clear message if no key is set."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="GROQ_API_KEY"):
            GroqClient(api_key=None)

    def test_accepts_key_as_argument(self):
        """Should NOT raise if the key is passed directly (useful in tests)."""
        # We're only checking the constructor doesn't raise; not making a real call.
        try:
            client = GroqClient(api_key="gsk_fake_key_for_constructor_test")
            assert client is not None
        except EnvironmentError:
            pytest.fail("GroqClient raised EnvironmentError even with explicit key argument")

    def test_model_aliases_are_strings(self):
        """Model alias constants must be non-empty strings."""
        assert isinstance(GroqClient.FAST_MODEL,    str) and GroqClient.FAST_MODEL
        assert isinstance(GroqClient.QUALITY_MODEL, str) and GroqClient.QUALITY_MODEL

    def test_base_url_is_groq(self):
        """Base URL must point at Groq's endpoint."""
        assert "groq.com" in GroqClient.GROQ_BASE_URL


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — LIVE TEST (skipped unless --live flag is passed)
# ─────────────────────────────────────────────────────────────────────────────

class TestReasoningAgentLive:
    """
    End-to-end test that actually calls Groq.
    Skipped automatically unless you pass --live to pytest.

    Requires: GROQ_API_KEY set in .env or shell environment.
    """

    def test_live_invoke_full_pipeline(self, live):
        """
        Full pipeline test:
          GroqClient → ReasoningAgent.invoke() → AgentOutput with real content.

        This validates the complete chain from user task → Groq API → parsed
        structured output, exactly as it will work inside the orchestrator.
        """
        if not live:
            pytest.skip("Pass --live to run tests that call the Groq API.")

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            pytest.fail(
                "GROQ_API_KEY is not set. "
                "Add it to puppeteer-plus/.env or set it in your shell."
            )

        # -- 1. Build real client + agent --
        client = GroqClient(api_key=api_key)
        agent  = ReasoningAgent(
            groq_client=client,
            model_name=GroqClient.FAST_MODEL,  # llama-3.1-8b-instant (cheapest)
        )

        # -- 2. Simple task similar to GSM-Hard (paper §3) --
        task = (
            "A train travels at 120 km/h and needs to cover 450 km. "
            "How many minutes will the journey take? Show all steps."
        )

        # -- 3. Invoke with empty context (first step of an episode) --
        output = agent.invoke(task=task, context="")

        # -- 4. Structural assertions (shape of output) --
        assert isinstance(output, AgentOutput), "invoke() must return AgentOutput"
        assert output.agent_name == "ReasoningAgent"

        # Token usage must be non-zero (real API always returns usage)
        assert output.token_usage["total_tokens"] > 0, \
            "Real API call must return non-zero token usage"

        # Latency must be positive
        assert output.latency_ms > 0, "Latency must be a positive float"

        # -- 5. Content assertions (answer must contain 225) --
        # 450 km / 120 km/h = 3.75 h = 225 min — the model must get this right
        combined = (output.reasoning_result + " " + output.final_answer).lower()
        assert "225" in combined, (
            f"Expected '225' in the combined output but got:\n"
            f"Reasoning: {output.reasoning_result[:200]}\n"
            f"Answer: {output.final_answer[:200]}"
        )

        # -- 6. Context string for orchestrator --
        ctx = output.to_context_string()
        assert "[ReasoningAgent]" in ctx
        assert len(ctx) > 20, "Context string is suspiciously short"

        # -- 7. Episode history bookkeeping --
        assert len(agent.episode_history) == 1
        agent.reset_episode()
        assert len(agent.episode_history) == 0

        # -- 8. Print a human-readable summary for the log --
        print("\n" + "=" * 60)
        print("  LIVE TEST RESULT")
        print("=" * 60)
        print(f"  Model         : {output.metadata.get('model_used')}")
        print(f"  Total tokens  : {output.token_usage['total_tokens']}")
        print(f"  Latency (ms)  : {output.latency_ms:.0f}")
        print(f"  Reasoning     : {output.reasoning_result[:150]}...")
        print(f"  Final Answer  : {output.final_answer}")
        print("=" * 60)
