"""
core/agent.py
=============
The atomic building block of the Puppeteer++ framework.

Paper reference: Section 2, paragraph 1:
  "A LLM-based agent can be abstracted in its minimal form as a = (m, r, t),
   where m denotes the foundation model, r represents the reasoning pattern or
   prompting strategy, and t is the set of available external tools."

This file defines:
  - AgentOutput      : the structured response every agent MUST return
  - AgentConfig      : the (m, r, t) triple from the paper, as a typed dataclass
  - BaseAgent        : abstract base class all 9 concrete agents will inherit
  - GroqClient       : thin wrapper around the Groq API (OpenAI-compatible)

Design decisions:
  - We do NOT subclass OpenAI's client directly.  We wrap it so we can swap
    backends later (e.g. local vLLM) without touching the 9 agent files.
  - Every agent always returns an AgentOutput, never a raw string.  This lets
    the orchestrator, reward function, and dissent detector work on a single
    consistent interface.
  - Prompt templates are stored ON the agent instance (not hard-coded in the
    call method) so the orchestrator can inspect them for logging / KV caching.
"""

import os
import json
import time
import logging
import dataclasses
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

# ── load .env automatically ───────────────────────────────────────────────────
# python-dotenv reads .env (or .env.local) from the project root and injects
# the values into os.environ *before* any code reads them.  This is a no-op
# when the key is already set in the shell environment (CI / production).
try:
    from dotenv import load_dotenv
    # override=False → shell env vars take priority over the file
    load_dotenv(dotenv_path=None, override=False)
except ImportError:
    pass  # dotenv not installed; rely on shell env vars

# ── third-party ──────────────────────────────────────────────────────────────
from openai import OpenAI          # Groq is OpenAI-compatible; same SDK works
from openai import APIError, RateLimitError, APIConnectionError

# ── logging setup ─────────────────────────────────────────────────────────────
# We use the module-level logger pattern so every file in the project inherits
# the same log level set at the entry point (main block or orchestrator).
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  DATA STRUCTURES
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class AgentOutput:
    """
    The single return type for every agent.invoke() call.

    The orchestrator at each step t receives an AgentOutput and uses it to:
      - Update the global system state S_t  (paper §2.1, eq. for Φ)
      - Provide input to the reward function (paper §2.2)
      - Feed the dissent detector (our Gap-3 extension)

    Fields
    ------
    agent_name : str
        Human-readable name, e.g. "ReasoningAgent".  Used by the orchestrator
        when building the reasoning trajectory τ for logging and reward calc.

    content : str
        The distilled final answer / primary output of the agent.
        Paper prompts end with: "FINAL ANSWER: [YOUR FINAL ANSWER]"
        For tool-use agents this will be the tool's return value.
        Named 'content' (not 'final_answer') to match environment.py's
        interface contract used by Memory and the orchestrator.

    reasoning : str
        The intermediate reasoning text.  Paper prompts always end with:
          "REASONING RESULT: [YOUR REASONING RESULT]"
        We extract this and store it here.
        Named 'reasoning' to match the environment.py / memory.py contract.

    raw_response : str
        The complete, unparsed LLM response.  Kept for debugging and for the
        step critic (Gap-1 extension) which may need the full token stream.

    tokens_used : int
        Total tokens consumed by this call (prompt + completion).
        Replaces the previous token_usage dict to give Memory and the reward
        function a single int C_t directly, matching environment.py's contract.
        Paper §2.2: "we define a step-wise cost C_t based on FLOPs or
        token-level metrics [50]".

    latency_ms : float
        Wall-clock time for the LLM call in milliseconds.
        Useful for efficiency analyses (paper §3.2).

    tool_calls : list[dict]
        Tool invocations made during this step, if any.
        Empty list for pure reasoning agents (no external tools).
        Paper §2, Table 2: tool-use agents populate this.

    metadata : dict
        Arbitrary extra info (model name, error traces, raw usage breakdown).
        The full token breakdown dict {prompt_tokens, completion_tokens,
        total_tokens} is stored here under key 'token_usage'.

    success : bool
        True if the agent completed without error.  False on any exception
        (API timeout, parse failure, etc.).  Environment.transition() uses
        this to trigger the FAILED stopping criterion (§2.1).

    error : str
        Human-readable error message when success=False.  Empty string
        on success.  Logged by Environment and surfaced in to_dict().
    """
    agent_name:  str
    content:     str
    reasoning:   str
    raw_response: str
    tokens_used: int
    latency_ms:  float
    tool_calls:  list = dataclasses.field(default_factory=list)
    metadata:    dict = dataclasses.field(default_factory=dict)
    success:     bool = True
    error:       str  = ""

    def to_context_string(self) -> str:
        """
        Serialise this output into the string that gets appended to the global
        state S_t before the next agent is chosen.

        The paper's global state is:
          S_t = {s_t(a_1), ..., s_t(a_n), aggregated context up to step t}
        We represent each agent's contribution as a labelled block so the
        orchestrator's LLM can parse it easily.
        """
        return (
            f"[{self.agent_name}]\n"
            f"Reasoning: {self.reasoning}\n"
            f"Answer: {self.content}\n"
        )

    def to_dict(self) -> dict:
        """
        Full serialisation used by StepRecord.to_dict(), Environment.to_dict(),
        and the dashboard WebSocket broadcast.
        """
        return {
            "agent_name":   self.agent_name,
            "content":      self.content,
            "reasoning":    self.reasoning,
            "raw_response": self.raw_response,
            "tokens_used":  self.tokens_used,
            "latency_ms":   self.latency_ms,
            "tool_calls":   self.tool_calls,
            "metadata":     self.metadata,
            "success":      self.success,
            "error":        self.error,
        }


# ═════════════════════════════════════════════════════════════════════════════
# 1b. ENUMS — ModelTier and ReasoningPattern
# ═════════════════════════════════════════════════════════════════════════════

class ModelTier(Enum):
    """
    Which Groq model tier to use for an agent or the policy.

    FAST    → llama-3.1-8b-instant   — cheap, quick; used for Mimas / most agents
    QUALITY → llama-3.3-70b-versatile — better quality; used for Titan / policy

    Keeping this as an enum (not raw strings) means a typo in a model name
    is caught at definition time, not at API call time.
    """
    FAST    = "llama-3.1-8b-instant"
    QUALITY = "llama-3.3-70b-versatile"


class ReasoningPattern(Enum):
    """
    The five reasoning patterns from the paper's agent pool (Appendix B.2).

    Each value maps to a concrete agent class via AgentFactory.create().

    Paper Table 2:
      PLANNER    — decomposes task into sub-steps
      REASONING  — executes logical inference on sub-problems
      CRITIC     — reviews and challenges prior reasoning
      CONCLUDER  — synthesises final answer (terminates episode via COMPLETED)
      TERMINATOR — decides whether to stop without a final answer (TERMINATED)
    """
    PLANNER    = "PlannerAgent"
    REASONING  = "ReasoningAgent"
    CRITIC     = "CriticAgent"
    CONCLUDER  = "ConcluderAgent"
    TERMINATOR = "TerminatorAgent"


@dataclasses.dataclass
class AgentConfig:
    """
    The (m, r, t) triple from the paper's agent abstraction (§2, paragraph 1).

    m → model_name   : which LLM to use for this agent
    r → role_prompt  : the reasoning pattern / persona prompt
        (paper Appendix B.2, Figures 12–16 — we replicate those exact prompts
         in each concrete agent file)
    t → tools        : list of tool names this agent can invoke
        (empty list = pure reasoning agent, no external tool)

    name and description are added for the orchestrator's system prompt:
    the policy LLM needs human-readable labels to make selection decisions.

    temperature is not in the paper's abstraction but is a practical necessity;
    we default to 0.7 which matches the Groq defaults used in the paper's
    Llama-3.1 policy init.
    """
    model_name:  str                    # m  — foundation model
    role_prompt: str                    # r  — reasoning pattern / prompt
    tools:       list[str]              # t  — available external tools
    name:        str        = ""        # human-readable agent name (class name)
    description: str        = ""        # one-line role description for orchestrator
    temperature: float      = 0.7       # practical addition, not in (m,r,t)
    max_tokens:  int        = 2048      # safety cap to avoid runaway costs


# ═════════════════════════════════════════════════════════════════════════════
# 2.  GROQ API CLIENT WRAPPER
# ═════════════════════════════════════════════════════════════════════════════

class GroqClient:
    """
    Thin wrapper around the OpenAI-compatible Groq API.

    Why a wrapper instead of calling OpenAI() directly in BaseAgent?
    - We want ONE place to handle retries, rate-limit back-off, and logging.
    - The orchestrator's policy is also a Groq call — centralising the client
      means we track ALL token usage in one place for the reward function.

    Usage:
        client = GroqClient()           # reads GROQ_API_KEY from env
        response = client.chat(
            model="llama-3.1-8b-instant",
            messages=[...],
            temperature=0.7,
            max_tokens=512,
        )
    """

    # Groq's base URL for the OpenAI-compatible REST interface
    GROQ_BASE_URL = "https://api.groq.com/openai/v1"

    # Model aliases so callers don't hardcode strings everywhere
    FAST_MODEL    = "llama-3.1-8b-instant"      # cheap, quick — used in Mimas
    QUALITY_MODEL = "llama-3.3-70b-versatile"   # better, slower — used in Titan

    def __init__(self, api_key: Optional[str] = None, max_retries: int = 3):
        """
        Parameters
        ----------
        api_key : str, optional
            Groq API key.  Falls back to the GROQ_API_KEY environment variable.
            Set this in a .env file or export it in your shell; NEVER hard-code.
        max_retries : int
            How many times to retry a failed API call before raising.
            Groq's free tier has rate limits; exponential back-off is applied.
        """
        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise EnvironmentError(
                "GROQ_API_KEY not found.  "
                "Set it with: export GROQ_API_KEY='gsk_...'"
            )

        # The OpenAI SDK works with any OpenAI-compatible endpoint via base_url.
        # Groq documents this pattern explicitly.
        self._client = OpenAI(
            api_key=key,
            base_url=self.GROQ_BASE_URL,
        )
        self.max_retries = max_retries

    def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> dict:
        """
        Call the Groq chat completion endpoint with automatic retry/back-off.

        Returns a plain dict (not the Pydantic object from the SDK) so that
        callers have a stable interface regardless of SDK version upgrades.

        Return shape:
        {
            "content": str,          # the assistant message text
            "model": str,            # model that actually ran
            "usage": {
                "prompt_tokens": int,
                "completion_tokens": int,
                "total_tokens": int,
            },
        }
        """
        last_error: Exception = None

        for attempt in range(1, self.max_retries + 1):
            try:
                t0 = time.monotonic()
                completion = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                latency_ms = (time.monotonic() - t0) * 1000

                # Extract the useful fields into a plain dict
                choice = completion.choices[0]
                usage  = completion.usage

                return {
                    "content": choice.message.content or "",
                    "model":   completion.model,
                    "latency_ms": latency_ms,
                    "usage": {
                        "prompt_tokens":      usage.prompt_tokens,
                        "completion_tokens":  usage.completion_tokens,
                        "total_tokens":       usage.total_tokens,
                    },
                }

            except RateLimitError as e:
                # Groq's free tier: 30 req/min for 8B, 14 req/min for 70B.
                # Exponential back-off: wait 2^attempt seconds before retry.
                wait = 2 ** attempt
                logger.warning(
                    f"[GroqClient] Rate limit hit (attempt {attempt}/{self.max_retries}). "
                    f"Waiting {wait}s... Error: {e}"
                )
                time.sleep(wait)
                last_error = e

            except APIConnectionError as e:
                logger.warning(
                    f"[GroqClient] Connection error (attempt {attempt}/{self.max_retries}): {e}"
                )
                time.sleep(2)
                last_error = e

            except APIError as e:
                # Non-retriable API error (bad request, invalid model, etc.)
                logger.error(f"[GroqClient] Non-retriable API error: {e}")
                raise

        raise last_error  # all retries exhausted


# ═════════════════════════════════════════════════════════════════════════════
# 3.  BASE AGENT (ABSTRACT)
# ═════════════════════════════════════════════════════════════════════════════

class BaseAgent(ABC):
    """
    Abstract base class that all 9 concrete agents inherit from.

    Relationship to the paper:
      Each concrete subclass IS one instantiation of the agent abstraction
      a = (m, r, t) defined in §2.  The BaseAgent holds the shared machinery:
        - The Groq API call
        - The output parser  (extracts REASONING RESULT / FINAL ANSWER)
        - The state_string property (what the orchestrator sees)
        - The invoke() orchestration entry point

    The concrete agents (ReasoningAgent, CriticAgent, ...) only need to:
      1. Pass their (m, r, t) config to super().__init__()
      2. Implement _build_action_prompt() — the action-specific part that
         gets appended AFTER the role prompt (paper Appendix B.2, Figs 14–16).

    Why abstractmethod?
      The paper clearly distinguishes "role prompt" (who am I?) from "action
      prompt" (what exactly should I do right now?).  The action prompt is
      task-context-dependent, so it MUST be overridden per agent type.
    """

    def __init__(self, config: AgentConfig, groq_client: GroqClient):
        """
        Parameters
        ----------
        config : AgentConfig
            The (m, r, t) triple for this agent.
        groq_client : GroqClient
            A shared GroqClient instance.  Shared so rate-limit tracking and
            retry logic is centralised (injected by the orchestrator).
        """
        self.config      = config
        self.groq_client = groq_client

        # Human-readable name inferred from the class name.
        # e.g.  ReasoningAgent  →  "ReasoningAgent"
        self.name = self.__class__.__name__

        # The agent's "memory" within one episode:
        # a list of AgentOutput objects from every time THIS agent was invoked.
        # The orchestrator can read this to detect cycles / repeated activation.
        self.episode_history: list[AgentOutput] = []

        logger.debug(f"[{self.name}] Initialised with model={config.model_name}, tools={config.tools}")

    # ── abstract method every concrete agent must implement ──────────────────

    @abstractmethod
    def _build_action_prompt(self, task: str, context: str) -> str:
        """
        Build the action-specific part of the prompt.

        Paper Appendix B.2: role prompts are combined with action prompts.
        The action prompt tells the agent WHAT to do given the current
        task and the aggregated context from previous agents.

        Parameters
        ----------
        task : str
            The original task specification τ (unchanged throughout episode).
        context : str
            The aggregated context up to this step: all previous AgentOutputs
            serialised via AgentOutput.to_context_string().

        Returns
        -------
        str
            The complete action prompt string to append after the role prompt.
        """
        ...

    # ── public orchestration entry point ─────────────────────────────────────

    def invoke(self, task: str, context: str) -> AgentOutput:
        """
        The orchestrator calls this at each step t to activate this agent.

        Paper §2.1:
          "Upon activation, agent a_t receives its state s_t(a_t) (extracted
           from S_t) and generates its output by a generative reasoning
           mapping f_{a_t}, after which the system state is updated (Φ)."

        Here:
          - task + context  → s_t(a_t)     (the agent's slice of S_t)
          - LLM call        → f_{a_t}       (the generative mapping)
          - returned output → the orchestrator uses it to compute Φ(S_t)

        Parameters
        ----------
        task : str
            The original task τ.
        context : str
            Accumulated context from all previous steps (concatenated
            AgentOutput.to_context_string() calls).

        Returns
        -------
        AgentOutput
            Structured result.  The orchestrator appends output.to_context_string()
            to the global state before selecting the next agent.
        """
        # 1. Build the full message list for the chat API
        messages = self._build_messages(task, context)

        # 2. Call Groq
        logger.info(f"[{self.name}] Invoking model={self.config.model_name} ...")
        t0 = time.monotonic()
        response = self.groq_client.chat(
            model=self.config.model_name,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        wall_ms = (time.monotonic() - t0) * 1000

        raw_text = response["content"]
        usage    = response["usage"]

        # 3. Parse the structured output
        reasoning, content = self._parse_output(raw_text)

        output = AgentOutput(
            agent_name   = self.name,
            content      = content,
            reasoning    = reasoning,
            raw_response = raw_text,
            tokens_used  = usage["total_tokens"],
            latency_ms   = wall_ms,
            tool_calls   = [],                         # populated by tool-use agents
            metadata     = {
                "model_used":  response["model"],
                "token_usage": usage,                  # full breakdown preserved here
            },
            success      = True,
            error        = "",
        )

        # 4. Store in episode history (used by orchestrator & dissent detector)
        self.episode_history.append(output)

        logger.info(
            f"[{self.name}] Done. tokens={usage['total_tokens']}, "
            f"latency={wall_ms:.0f}ms"
        )
        return output

    def reset_episode(self) -> None:
        """
        Clear per-episode state between tasks.

        The orchestrator calls this on every agent at the start of each new
        reasoning episode so that history from task N doesn't leak into task N+1.
        """
        self.episode_history.clear()
        logger.debug(f"[{self.name}] Episode history cleared.")

    # ── internal helpers ─────────────────────────────────────────────────────

    def _build_messages(self, task: str, context: str) -> list[dict]:
        """
        Construct the OpenAI-format messages list.

        Structure:
          [
            {"role": "system",    "content": role_prompt},      ← r from (m,r,t)
            {"role": "user",      "content": action_prompt},    ← task + context
          ]

        We keep it to two turns (system + user) because Groq's Llama-3 models
        are instruction-tuned and respond well to this format.  Multi-turn
        history is passed via the 'context' string rather than as extra message
        objects, keeping token counts predictable.
        """
        action_prompt = self._build_action_prompt(task, context)
        return [
            {"role": "system", "content": self.config.role_prompt},
            {"role": "user",   "content": action_prompt},
        ]

    @staticmethod
    def _parse_output(raw_text: str) -> tuple[str, str]:
        """
        Extract REASONING RESULT and FINAL ANSWER from the LLM's response.

        The paper (Appendix B.2, Figures 15–16) specifies that all reasoning
        agents must end their response with:
          REASONING RESULT: [YOUR REASONING RESULT]
          FINAL ANSWER: [YOUR FINAL ANSWER]

        This parser is lenient:
          - If the tags are present → extracts the text after them.
          - If missing → returns the full text as reasoning_result and an
            empty string as final_answer (graceful degradation).

        Why a static method?
          All 9 agents share the exact same parsing logic; no subclass needs
          to override it.
        """
        reasoning   = ""
        final_answer = ""

        # ── Try to extract REASONING RESULT ──
        reasoning_tag = "REASONING RESULT:"
        final_tag     = "FINAL ANSWER:"

        r_idx = raw_text.upper().find(reasoning_tag.upper())
        f_idx = raw_text.upper().find(final_tag.upper())

        if r_idx != -1:
            # Text starts after the tag; ends either at FINAL ANSWER or EOF
            r_start = r_idx + len(reasoning_tag)
            r_end   = f_idx if f_idx != -1 else len(raw_text)
            reasoning = raw_text[r_start:r_end].strip()
        else:
            # No structured output found; treat the whole response as reasoning
            reasoning = raw_text.strip()

        # ── Try to extract FINAL ANSWER ──
        if f_idx != -1:
            f_start      = f_idx + len(final_tag)
            final_answer = raw_text[f_start:].strip()

        return reasoning, final_answer

    def execute(self, task: str, context: str = "") -> AgentOutput:
        """
        Public alias for invoke(), used by the orchestrator.

        The orchestrator calls execute() (not invoke()) so that future
        tool-use agents can override execute() to add pre/post-processing
        around the LLM call without touching the base invoke() logic.

        Parameters
        ----------
        task    : str  — the original task τ
        context : str  — formatted prior context from Memory

        Returns
        -------
        AgentOutput
        """
        return self.invoke(task=task, context=context)

    def close(self) -> None:
        """
        Release resources held by this agent (HTTP connection pool, etc.).

        The orchestrator calls close() on every agent in the pool after
        each episode via Orchestrator.cleanup().  For the current
        synchronous GroqClient (which reuses the OpenAI SDK's connection
        pool) this is a no-op, but the hook exists so async HTTP clients
        (httpx.AsyncClient) used in future tool-use agents can be closed
        cleanly without leaking file descriptors.
        """
        # No-op for now; overridden by tool-use agents in agents/*.py
        logger.debug("[%s] close() called (no-op on sync client).", self.name)

    def __repr__(self) -> str:
        return (
            f"{self.name}("
            f"model={self.config.model_name!r}, "
            f"tools={self.config.tools!r})"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 4.  CONCRETE AGENT — ReasoningAgent (used in __main__ test)
# ═════════════════════════════════════════════════════════════════════════════
# NOTE: The full suite of 9 agents lives in agents/*.py.
# We define ONE here purely so the __main__ block can run without importing
# any other file — a self-contained smoke test.

class ReasoningAgent(BaseAgent):
    """
    The most fundamental agent in the Puppeteer pool.

    Paper Appendix B.2, Figure 13:
      Role: "You are an expert in logical reasoning. Responsible for
             synthesizing solutions to sub-problems (reasoning)."

    Paper Appendix B.2, Figure 15 (reasoning action prompt template):
      "Now, you need to continue the reasoning to get closer to the correct
       answer. You should finish your reasoning with the following template:
       REASONING RESULT: [YOUR REASONING RESULT].
       Finish your answer with:
       FINAL ANSWER: [YOUR FINAL ANSWER].
       *Your previous reasoning was: {}.* ..."

    The {} placeholder is filled with the context from previous agents.
    This is how the paper's serialised orchestration works: each agent
    sees the accumulated context and continues from where others left off.
    """

    # The exact role prompt from the paper (Appendix B.2, Figure 13)
    ROLE_PROMPT = (
        "You are an expert in logical reasoning. "
        "Responsible for synthesizing solutions to sub-problems (reasoning)."
    )

    def __init__(
        self,
        groq_client: GroqClient,
        model_name: str = GroqClient.FAST_MODEL,   # default: fast/cheap model
    ):
        config = AgentConfig(
            model_name  = model_name,
            role_prompt = self.ROLE_PROMPT,
            tools       = [],              # pure reasoning — no external tools
            name        = "ReasoningAgent",
            description = "Executes logical inference on sub-problems to advance the solution.",
            temperature = 0.7,
            max_tokens  = 1024,
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        """
        Action prompt from paper Appendix B.2, Figure 15 (reasoning pattern).

        The template literally says:
          "*Your previous reasoning was: {}.* You need to follow the direction
           of the reasoning path and go forward:"
        We fill {} with the accumulated context string.
        """
        previous_context = context if context.strip() else "None (this is the first step)."

        return (
            f"Task: {task}\n\n"
            f"Now, you need to continue the reasoning to get closer to the correct answer.\n"
            f"You should finish your reasoning with the following template:\n"
            f"REASONING RESULT: [YOUR REASONING RESULT].\n"
            f"Finish your answer with:\n"
            f"FINAL ANSWER: [YOUR FINAL ANSWER].\n\n"
            f"*Your previous reasoning was: {previous_context}.*\n"
            f"You need to follow the direction of the reasoning path and go forward:"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 5.  ADDITIONAL CONCRETE AGENTS (Planner, Critic, Concluder, Terminator)
# ═════════════════════════════════════════════════════════════════════════════
# The full 9-agent suite lives in agents/*.py (Step 7).
# These 4 + ReasoningAgent cover the 5 patterns the orchestrator needs
# to run a complete episode end-to-end from Week 2 onward.

class PlannerAgent(BaseAgent):
    """
    Paper Appendix B.2, Figure 12: Decomposition / planning agent.
    Breaks the task into sub-steps before any reasoning occurs.
    """
    ROLE_PROMPT = (
        "You are an expert task planner. "
        "Your job is to decompose the given problem into clear, "
        "ordered sub-steps that other agents can solve sequentially. "
        "Be concise and precise. "
        "Finish with:\n"
        "REASONING RESULT: [your decomposition plan]\n"
        "FINAL ANSWER: [ordered list of sub-steps]"
    )
    DESCRIPTION = "Decomposes the task into ordered sub-steps for other agents."

    def __init__(self, groq_client: GroqClient, model_name: str = GroqClient.FAST_MODEL):
        config = AgentConfig(
            model_name  = model_name,
            role_prompt = self.ROLE_PROMPT,
            tools       = [],
            name        = "PlannerAgent",
            description = self.DESCRIPTION,
            temperature = 0.3,   # low temp: planning should be deterministic
            max_tokens  = 512,
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        ctx = context if context.strip() else "No prior reasoning yet."
        return (
            f"Task: {task}\n\n"
            f"Prior context:\n{ctx}\n\n"
            f"Break this task into sub-steps. "
            f"REASONING RESULT: [plan]\nFINAL ANSWER: [steps]"
        )


class CriticAgent(BaseAgent):
    """
    Paper Appendix B.2, Figure 14: Critic / reviewer agent.
    Reviews prior reasoning for errors and suggests corrections.
    """
    ROLE_PROMPT = (
        "You are a critical reviewer of reasoning. "
        "Your job is to identify flaws, incorrect assumptions, or gaps "
        "in the reasoning provided so far, and suggest corrections. "
        "Be specific about what is wrong and why. "
        "Finish with:\n"
        "REASONING RESULT: [critique and corrections]\n"
        "FINAL ANSWER: [corrected answer if applicable, else 'continue reasoning']"
    )
    DESCRIPTION = "Reviews prior reasoning for errors and suggests corrections."

    def __init__(self, groq_client: GroqClient, model_name: str = GroqClient.FAST_MODEL):
        config = AgentConfig(
            model_name  = model_name,
            role_prompt = self.ROLE_PROMPT,
            tools       = [],
            name        = "CriticAgent",
            description = self.DESCRIPTION,
            temperature = 0.5,
            max_tokens  = 768,
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        ctx = context if context.strip() else "No prior reasoning to critique."
        return (
            f"Task: {task}\n\n"
            f"Reasoning so far:\n{ctx}\n\n"
            f"Identify any errors or gaps. "
            f"REASONING RESULT: [critique]\nFINAL ANSWER: [verdict]"
        )


class ConcluderAgent(BaseAgent):
    """
    Paper Appendix B.2: Final synthesis agent.
    Produces the definitive final answer. Triggering this agent causes
    Environment.transition() to set status=COMPLETED.
    """
    ROLE_PROMPT = (
        "You are the final synthesis agent. "
        "Your job is to combine all prior reasoning into a single, "
        "clear, correct final answer. Do not add new reasoning — "
        "only synthesise what has already been established. "
        "Finish with:\n"
        "REASONING RESULT: [brief synthesis summary]\n"
        "FINAL ANSWER: [the definitive answer]"
    )
    DESCRIPTION = "Synthesises all prior reasoning into the definitive final answer."

    def __init__(self, groq_client: GroqClient, model_name: str = GroqClient.FAST_MODEL):
        config = AgentConfig(
            model_name  = model_name,
            role_prompt = self.ROLE_PROMPT,
            tools       = [],
            name        = "ConcluderAgent",
            description = self.DESCRIPTION,
            temperature = 0.3,   # low temp: synthesis should be stable
            max_tokens  = 512,
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        ctx = context if context.strip() else "No prior reasoning provided."
        return (
            f"Task: {task}\n\n"
            f"All reasoning so far:\n{ctx}\n\n"
            f"Synthesise into the final answer. "
            f"REASONING RESULT: [synthesis]\nFINAL ANSWER: [answer]"
        )


class TerminatorAgent(BaseAgent):
    """
    Paper §2.1 (stopping criterion): Explicit termination agent.
    Decides whether the task is done without a conclusive answer,
    e.g. when the task is unanswerable or the episode is going in circles.
    If it determines the episode should stop, it includes "TERMINATE"
    in its content, which triggers Environment status=TERMINATED.
    """
    ROLE_PROMPT = (
        "You are the episode terminator. "
        "Your job is to decide whether the current episode should end. "
        "If the task has been answered sufficiently, respond with TERMINATE "
        "and a brief reason. "
        "If more reasoning is needed, respond with CONTINUE and explain why. "
        "Finish with:\n"
        "REASONING RESULT: [your assessment]\n"
        "FINAL ANSWER: [TERMINATE or CONTINUE]"
    )
    DESCRIPTION = "Decides whether the episode should terminate or continue."

    def __init__(self, groq_client: GroqClient, model_name: str = GroqClient.FAST_MODEL):
        config = AgentConfig(
            model_name  = model_name,
            role_prompt = self.ROLE_PROMPT,
            tools       = [],
            name        = "TerminatorAgent",
            description = self.DESCRIPTION,
            temperature = 0.2,   # very deterministic: stop/go decision
            max_tokens  = 256,
        )
        super().__init__(config, groq_client)

    def _build_action_prompt(self, task: str, context: str) -> str:
        ctx = context if context.strip() else "No prior reasoning."
        return (
            f"Task: {task}\n\n"
            f"Episode state so far:\n{ctx}\n\n"
            f"Should this episode terminate? "
            f"REASONING RESULT: [assessment]\nFINAL ANSWER: [TERMINATE or CONTINUE]"
        )


# Convenience alias: orchestrator imports 'Agent' as the base type
Agent = BaseAgent


# ═════════════════════════════════════════════════════════════════════════════
# 6.  AGENT FACTORY
# ═════════════════════════════════════════════════════════════════════════════

class AgentFactory:
    """
    Centralised factory for creating Agent instances.

    The orchestrator uses this to build its pool and its own policy agent.
    Having a factory (rather than direct instantiation) means:
      1. One shared GroqClient per factory call — all agents in a pool
         share the same connection pool and rate-limit tracking.
      2. ModelTier enum prevents typos in model name strings.
      3. ReasoningPattern enum guarantees only valid patterns are used.

    Usage:
        pool = AgentFactory.create_pool(
            patterns=[ReasoningPattern.PLANNER, ReasoningPattern.REASONING,
                      ReasoningPattern.CRITIC, ReasoningPattern.CONCLUDER,
                      ReasoningPattern.TERMINATOR],
            tier=ModelTier.FAST,
        )
    """

    # Maps ReasoningPattern → concrete class
    _CLASS_MAP: dict = {}  # filled after class definitions below

    @classmethod
    def _get_class_map(cls) -> dict:
        """
        Lazy-build the pattern → class map.
        Defined as a method (not class-level constant) to avoid forward-ref
        issues since the agent classes are defined above this factory.
        """
        if not cls._CLASS_MAP:
            cls._CLASS_MAP = {
                ReasoningPattern.PLANNER:    PlannerAgent,
                ReasoningPattern.REASONING:  ReasoningAgent,
                ReasoningPattern.CRITIC:     CriticAgent,
                ReasoningPattern.CONCLUDER:  ConcluderAgent,
                ReasoningPattern.TERMINATOR: TerminatorAgent,
            }
        return cls._CLASS_MAP

    @classmethod
    def create(
        cls,
        pattern:    ReasoningPattern,
        tier:       ModelTier = ModelTier.FAST,
        groq_client: Optional[GroqClient] = None,
    ) -> Agent:
        """
        Create a single agent instance.

        Parameters
        ----------
        pattern     : ReasoningPattern  — which agent type to create
        tier        : ModelTier         — FAST or QUALITY model
        groq_client : GroqClient, optional
            Pass an existing client to share the connection pool.
            If None, a new GroqClient is created.

        Returns
        -------
        Agent (BaseAgent subclass)
        """
        if groq_client is None:
            groq_client = GroqClient()
        agent_cls = cls._get_class_map()[pattern]
        return agent_cls(groq_client=groq_client, model_name=tier.value)

    @classmethod
    def create_pool(
        cls,
        patterns:    list[ReasoningPattern],
        tier:        ModelTier = ModelTier.FAST,
        groq_client: Optional[GroqClient] = None,
    ) -> list[Agent]:
        """
        Create a list of agents sharing ONE GroqClient instance.

        Sharing the client means all agents in the pool go through the
        same rate-limit back-off logic, preventing thundering-herd
        retries when the 30-req/min free-tier limit is hit.

        Parameters
        ----------
        patterns    : list[ReasoningPattern]  — agents to include in the pool
        tier        : ModelTier               — FAST or QUALITY model
        groq_client : GroqClient, optional    — shared client (created if None)

        Returns
        -------
        list[Agent]
            One instance per pattern, in the same order as patterns.
        """
        if groq_client is None:
            groq_client = GroqClient()
        return [
            cls.create(pattern=p, tier=tier, groq_client=groq_client)
            for p in patterns
        ]


# ═════════════════════════════════════════════════════════════════════════════
# 7.  __main__ — SMOKE TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Smoke test: instantiate a ReasoningAgent and call Groq with a simple task.

    Before running:
        export GROQ_API_KEY="gsk_..."     (Linux / macOS)
        $env:GROQ_API_KEY = "gsk_..."    (PowerShell)

    Then:
        python core/agent.py

    Expected output structure:
        ─── AgentOutput ────────────────────────────────
        Agent Name      : ReasoningAgent
        Model Used      : llama-3.1-8b-instant
        Reasoning Result: <text between REASONING RESULT: and FINAL ANSWER:>
        Final Answer    : <text after FINAL ANSWER:>
        Token Usage     : {'prompt_tokens': N, 'completion_tokens': M, ...}
        Latency (ms)    : X.X
        ─────────────────────────────────────────────────
    """
    import sys
    # Force UTF-8 on Windows so box-drawing / tick chars print safely.
    # (Python 3.7+ supports reconfigure(); no-op on platforms already UTF-8.)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Configure logging so we can see what's happening
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("  Puppeteer++ -- core/agent.py smoke test")
    print("=" * 60 + "\n")

    # ── 1. Create the shared Groq client ──────────────────────────────────
    try:
        client = GroqClient()
        print(f"[OK] GroqClient created (base_url={GroqClient.GROQ_BASE_URL})\n")
    except EnvironmentError as e:
        print(f"[FAIL] {e}")
        raise SystemExit(1)

    # ── 2. Instantiate the ReasoningAgent ─────────────────────────────────
    agent = ReasoningAgent(
        groq_client=client,
        model_name=GroqClient.FAST_MODEL,   # llama-3.1-8b-instant
    )
    print(f"[OK] Agent created: {agent!r}\n")

    # ── 3. Define a simple test task ──────────────────────────────────────
    # Using a simple arithmetic task similar to GSM-Hard from the paper's
    # evaluation suite (§3, Datasets and Metrics).
    task = (
        "If a train travels at 120 km/h and needs to cover 450 km, "
        "how many minutes will the journey take?"
    )
    context = ""   # empty context → this is the first step (no prior agents)

    print(f"Task: {task}")
    print(f"Context: (empty — first step in episode)\n")
    print("Calling Groq API...")
    print("-" * 60)

    # ── 4. Invoke the agent ───────────────────────────────────────────────
    try:
        output: AgentOutput = agent.invoke(task=task, context=context)
    except Exception as e:
        print(f"\n✗ Agent invocation failed: {e}")
        raise SystemExit(1)

    # ── 5. Print the structured output ────────────────────────────────────
    print("\n" + "-" * 60)
    print("  AgentOutput")
    print("-" * 60)
    print(f"  Agent Name       : {output.agent_name}")
    print(f"  Model Used       : {output.metadata.get('model_used', 'N/A')}")
    print(f"  Latency (ms)     : {output.latency_ms:.1f}")
    print(f"  Tokens Used      : {output.tokens_used}")
    print(f"  Success          : {output.success}")
    print()
    print(f"  Reasoning :")
    for line in output.reasoning.splitlines():
        print(f"    {line}")
    print()
    print(f"  Content (Final Answer) :")
    for line in output.content.splitlines():
        print(f"    {line}")
    print("-" * 60)

    # ── 6. Test to_dict() and to_context_string() ─────────────────────────
    d = output.to_dict()
    assert "content"     in d, "to_dict() missing 'content'"
    assert "tokens_used" in d, "to_dict() missing 'tokens_used'"
    assert "success"     in d, "to_dict() missing 'success'"
    print("\n[OK] to_dict() contains all required keys.")

    print("\n  Context string passed to next agent in the episode:")
    print("  " + "-" * 40)
    for line in output.to_context_string().splitlines():
        print(f"  {line}")
    print("  " + "-" * 40)

    # ── 7. Test reset_episode ─────────────────────────────────────────────
    print(f"\n  Episode history length (before reset): {len(agent.episode_history)}")
    agent.reset_episode()
    print(f"  Episode history length (after reset) : {len(agent.episode_history)}")

    print("\n[PASS] Smoke test passed. core/agent.py is working correctly.\n")
