"""
core/environment.py
===================
Global task state S_t and the state-transition function Φ (Equation 2).

Paper reference:
  Equation 2:
    o_t = f_{a_t}(s_t(a_t), S_t)
    S_{t+1} = Φ(S_t, o_t)

  Section 2.1:
    "The global state S_t consists of all relevant agent states and
     aggregated contextual information up to step t."

  Section 2.1 (stopping criterion):
    "The process terminates when a stopping criterion is met (e.g., when
     the selected agent is a designated terminator or when the
     task-solving resource is exhausted)."

  Equation 4 (final aggregation):
    o* = F_agg({o_0, o_1, ..., o_T}) = Φ(S_T, o_T)

  Equation 5 (trajectory — consumed by training/reinforce.py):
    τ = {S0, a0, o0, S1, a1, o1, ..., ST, aT, oT}

This file defines:
  - EpisodeStatus    : enum for the five possible episode states
  - EnvironmentConfig: task definition and episode hyper-parameters
  - StepRecord       : one (S_t, a_t, o_t) triple, stored per activation
  - Environment      : the main class; owns _steps and implements Φ

Design decisions:
  - Environment NEVER imports Agent or calls any agent directly.
    That is orchestrator.py's responsibility.  We only consume AgentOutput
    objects handed to us by the orchestrator via transition().
  - All mutable internal state is accessed through read-only @property
    accessors so external code cannot accidentally corrupt the episode.
  - The smoke-test __main__ block uses a fully self-contained
    MockAgentOutput and requires no Groq API key.
"""

import time
import uuid
import logging
import dataclasses
from enum import Enum
from typing import Optional

# ── load .env automatically ───────────────────────────────────────────────────
# Mirrors the pattern in core/agent.py so the module is usable standalone.
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=None, override=False)
except ImportError:
    pass

# ── project import ────────────────────────────────────────────────────────────
# We import ONLY AgentOutput for type annotations.
# Environment never instantiates agents — that is orchestrator.py's job.
#
# sys.path guard: when this file is run directly (`python core/environment.py`)
# Python's working directory is the project root but 'core' is a sub-package,
# so we ensure the root is on sys.path — same pattern as agent.py's __main__.
import sys as _sys
import os as _os
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

from core.agent import AgentOutput

# ── logging setup ─────────────────────────────────────────────────────────────
# Module-level logger; inherits the level set by the entry point / orchestrator.
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  EPISODE STATUS
# ═════════════════════════════════════════════════════════════════════════════

class EpisodeStatus(Enum):
    """
    Represents the five possible states an episode can be in.

    Paper §2.1 (stopping criterion):
      The episode runs while RUNNING; all other values are terminal.

    Values
    ------
    RUNNING    — episode in progress; orchestrator keeps selecting agents.
    COMPLETED  — ConcluderAgent produced the final answer (normal ending).
    TERMINATED — TerminatorAgent explicitly decided the task is done.
    FAILED     — an agent returned success=False; episode cannot continue.
    TRUNCATED  — max_steps reached without COMPLETED or TERMINATED.
    """
    RUNNING    = "running"
    COMPLETED  = "completed"
    TERMINATED = "terminated"
    FAILED     = "failed"
    TRUNCATED  = "truncated"


# ═════════════════════════════════════════════════════════════════════════════
# 2.  ENVIRONMENT CONFIG
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class EnvironmentConfig:
    """
    Defines the task τ and episode hyper-parameters.

    Fields
    ------
    task : str
        The original task specification τ.  Never mutated after __init__.
        Paper §2: "Each episode begins with a task τ."

    task_id : str
        UUID-4 string generated at init if not provided.
        Used for logging, trajectory storage, and dashboard routing.

    max_steps : int
        Maximum number of agent activations per episode.
        Default of 4 matches the paper's episode_length for the base setting
        (Appendix B.1, Table 4).

    max_tokens_per_step : int
        Soft cap on tokens per agent call.  Passed to agents by orchestrator.
        Paper §2.2: "step-wise cost C_t based on FLOPs or token-level metrics."
    """
    task:                str
    task_id:             str  = dataclasses.field(
                                    default_factory=lambda: str(uuid.uuid4())
                                )
    max_steps:           int  = 4     # episode_length from paper defaults
    max_tokens_per_step: int  = 1024


# ═════════════════════════════════════════════════════════════════════════════
# 3.  STEP RECORD
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class StepRecord:
    """
    One record per agent activation — corresponds to one (S_t, a_t, o_t)
    triple in the trajectory τ (paper Equation 5).

    Fields
    ------
    step         : int         — step index t, zero-based.
    agent_name   : str         — which agent was activated (a_t).
    agent_input  : str         — context passed to the agent (s_t(a_t)).
    agent_output : AgentOutput — the full output o_t.
    timestamp    : float       — time.monotonic() at transition time.

    Properties
    ----------
    tokens → int
        Convenience accessor for the step's token cost C_t.
        Paper §2.2: "we define a step-wise cost C_t based on token-level
        metrics."

    Methods
    -------
    to_dict() → dict
        Full serialisation including agent_output.to_dict(), used by
        get_full_trajectory() to build τ for reinforce.py.
    """
    step:         int
    agent_name:   str
    agent_input:  str
    agent_output: AgentOutput
    timestamp:    float

    @property
    def tokens(self) -> int:
        """Step-wise token cost C_t (paper §2.2)."""
        return self.agent_output.tokens_used

    def to_dict(self) -> dict:
        """
        Full serialisation of the (S_t, a_t, o_t) triple.
        Used by Environment.get_full_trajectory() to assemble τ.
        """
        return {
            "step":         self.step,
            "agent_name":   self.agent_name,
            "agent_input":  self.agent_input,
            "agent_output": self.agent_output.to_dict(),
            "timestamp":    self.timestamp,
        }


# ═════════════════════════════════════════════════════════════════════════════
# 4.  ENVIRONMENT (main class)
# ═════════════════════════════════════════════════════════════════════════════

class Environment:
    """
    The single source of truth for episode state S_t.

    Implements the state-transition function Φ from Equation 2:
      S_{t+1} = Φ(S_t, o_t)

    The orchestrator drives the episode loop:
      1. Calls get_state_for_agent(name) to build s_t(a_t).
      2. Passes s_t(a_t) to the chosen agent → receives AgentOutput o_t.
      3. Calls transition(agent_name, agent_input, o_t) → S_{t+1}.
      4. Checks is_done; if True, calls get_final_answer().

    Why a separate Environment class?
      The paper's global state S_t is more than a list of outputs — it
      carries the task τ, step indices, token budget, and status.
      Centralising this in one object lets the orchestrator, reward
      function, and dashboard all read from a single consistent view.
    """

    def __init__(self, config: EnvironmentConfig) -> None:
        """
        Parameters
        ----------
        config : EnvironmentConfig
            Task definition and episode hyper-parameters.
        """
        self.config       = config
        self._steps:      list[StepRecord] = []
        self._status:     EpisodeStatus    = EpisodeStatus.RUNNING
        self._start_time: float            = time.monotonic()

        logger.info(
            "Episode started | task_id=%s | task=%.60r...",
            config.task_id,
            config.task,
        )

    # ── read-only properties ─────────────────────────────────────────────────

    @property
    def current_step(self) -> int:
        """Current step index t = number of completed transitions."""
        return len(self._steps)

    @property
    def is_done(self) -> bool:
        """True when the episode has reached any terminal status."""
        return self._status != EpisodeStatus.RUNNING

    @property
    def total_tokens(self) -> int:
        """
        Cumulative token cost Σ C_t across all steps.
        Paper §2.2: used by the reward function R(τ).
        """
        return sum(r.tokens for r in self._steps)

    @property
    def status(self) -> EpisodeStatus:
        """Current episode status."""
        return self._status

    @property
    def step_history(self) -> list[StepRecord]:
        """
        Shallow copy of the step list — callers cannot corrupt internal state.
        """
        return list(self._steps)

    @property
    def elapsed_ms(self) -> float:
        """Wall-clock time since episode start in milliseconds."""
        return (time.monotonic() - self._start_time) * 1000

    # ── state transition (Φ) ─────────────────────────────────────────────────

    def transition(
        self,
        agent_name:   str,
        agent_input:  str,
        agent_output: AgentOutput,
    ) -> StepRecord:
        """
        Apply Φ: advance global state from S_t to S_{t+1}.

        This IS the Φ function from Equation 2:
          S_{t+1} = Φ(S_t, o_t)

        The orchestrator calls this after every agent activation.

        Stopping criteria are evaluated IN ORDER so a failed ConcluderAgent
        is marked FAILED rather than COMPLETED:
          (a) success=False               → FAILED
          (b) TerminatorAgent + TERMINATE → TERMINATED
          (c) ConcluderAgent              → COMPLETED
          (d) current_step >= max_steps   → TRUNCATED

        Parameters
        ----------
        agent_name   : str         — which agent produced the output (a_t).
        agent_input  : str         — context that was passed to the agent.
        agent_output : AgentOutput — the output o_t from f_{a_t}.

        Returns
        -------
        StepRecord
            The newly created record for step t.

        Raises
        ------
        RuntimeError
            If called on an already-terminal episode.
        """
        if self.is_done:
            raise RuntimeError(
                f"Cannot transition: episode is already '{self._status.value}'. "
                "Call reset() to start a new episode."
            )

        # Create and store the step record BEFORE evaluating stopping criteria
        # so current_step reflects the completed step in the log messages.
        record = StepRecord(
            step         = self.current_step,   # len before append = index t
            agent_name   = agent_name,
            agent_input  = agent_input,
            agent_output = agent_output,
            timestamp    = time.monotonic(),
        )
        self._steps.append(record)

        # ── (a) failure check — MUST come first ──────────────────────────────
        if not agent_output.success:
            self._status = EpisodeStatus.FAILED
            logger.error(
                "Agent %s failed at step %d: %s",
                agent_name, record.step, agent_output.error,
            )

        # ── (b) explicit termination ──────────────────────────────────────────
        elif (
            agent_name == "TerminatorAgent"
            and "TERMINATE" in agent_output.content.upper()
        ):
            self._status = EpisodeStatus.TERMINATED
            logger.info(
                "Episode TERMINATED by TerminatorAgent at step %d", record.step
            )

        # ── (c) normal completion via ConcluderAgent ──────────────────────────
        elif agent_name == "ConcluderAgent":
            self._status = EpisodeStatus.COMPLETED
            logger.info(
                "Episode COMPLETED by ConcluderAgent at step %d", record.step
            )

        # ── (d) budget exhausted ──────────────────────────────────────────────
        elif self.current_step >= self.config.max_steps:
            self._status = EpisodeStatus.TRUNCATED
            logger.warning(
                "Episode TRUNCATED at max_steps=%d", self.config.max_steps
            )

        return record

    # ── state accessors ───────────────────────────────────────────────────────

    def get_state_for_agent(self, agent_name: str) -> str:
        """
        Return s_t(a_t) — the agent-local view of the global state S_t.

        Paper §2.1:
          "Each agent a receives its local state s_t(a_t) extracted from
           the global state S_t."

        The returned string is passed as 'context' to agent.execute().
        It contains every prior step formatted as a labelled block so
        the receiving agent's LLM can parse it easily.

        Format per step:
          [Step {n} — {AgentName}]
          {output content}
          ---

        Parameters
        ----------
        agent_name : str
            The agent about to be activated.  Included in the signature
            for future use (e.g. agent-specific filtering); currently all
            prior steps are returned regardless of agent type.

        Returns
        -------
        str
            Serialised prior context, or "" if no steps have occurred yet.
        """
        if not self._steps:
            return ""

        blocks: list[str] = []
        for record in self._steps:
            blocks.append(f"[Step {record.step} — {record.agent_name}]")
            blocks.append(record.agent_output.content)
            blocks.append("---")
        return "\n".join(blocks)

    def get_full_trajectory(self) -> list[dict]:
        """
        Return τ = {S0,a0,o0, S1,a1,o1, ..., ST,aT,oT}.

        Paper Equation 5:
          τ = {S0, a0, o0, S1, a1, o1, ..., ST, aT, oT}

        Used by training/reinforce.py to compute R(τ) (Equation 3).

        Returns
        -------
        list[dict]
            One serialised StepRecord per activation, in chronological order.
        """
        return [record.to_dict() for record in self._steps]

    def get_final_answer(self) -> str:
        """
        Aggregate the final answer using F_agg (Equation 4).

        Paper Equation 4:
          o* = F_agg({o_0, o_1, ..., o_T}) = Φ(S_T, o_T)

        Priority order (first match wins):
          1. Last ConcluderAgent output  — canonical final answer.
          2. Last TerminatorAgent output — agent may have stated something
             before issuing TERMINATE.
          3. Last *successful* agent output of any type — graceful fallback.
          4. Empty string if no steps exist.

        Returns
        -------
        str
            The best available final answer content string.
        """
        if not self._steps:
            return ""

        # 1. Last ConcluderAgent
        for record in reversed(self._steps):
            if record.agent_name == "ConcluderAgent":
                return record.agent_output.content

        # 2. Last TerminatorAgent
        for record in reversed(self._steps):
            if record.agent_name == "TerminatorAgent":
                return record.agent_output.content

        # 3. Last successful output of any agent
        for record in reversed(self._steps):
            if record.agent_output.success:
                return record.agent_output.content

        # 4. Nothing usable
        return ""

    def reset(self, new_task: str = "") -> None:
        """
        Reset the environment for a new episode, keeping the same config.

        The orchestrator calls this between episodes on a benchmark run so
        the same Environment object can be reused without reconstruction.

        Parameters
        ----------
        new_task : str, optional
            If provided, replaces config.task and generates a fresh task_id.
            Leave blank to re-run the same task (e.g. for repeated trials).
        """
        self._steps.clear()
        self._status     = EpisodeStatus.RUNNING
        self._start_time = time.monotonic()

        if new_task:
            self.config.task    = new_task
            self.config.task_id = str(uuid.uuid4())

        logger.info("Environment reset | new task_id=%s", self.config.task_id)

    def to_dict(self) -> dict:
        """
        Full serialisation for dashboard WebSocket messages and checkpointing.

        The dashboard (Step 10) and API server (Step 9) call this after every
        transition to broadcast the updated state to the React frontend.

        Returns
        -------
        dict
            {
              "task":         str,
              "task_id":      str,
              "status":       str,       # EpisodeStatus.value
              "current_step": int,
              "max_steps":    int,
              "total_tokens": int,
              "elapsed_ms":   float,
              "step_history": list[dict] # full trajectory τ
            }
        """
        return {
            "task":         self.config.task,
            "task_id":      self.config.task_id,
            "status":       self.status.value,
            "current_step": self.current_step,
            "max_steps":    self.config.max_steps,
            "total_tokens": self.total_tokens,
            "elapsed_ms":   self.elapsed_ms,
            "step_history": self.get_full_trajectory(),
        }

    def __repr__(self) -> str:
        return (
            f"Environment("
            f"task_id={self.config.task_id!r}, "
            f"step={self.current_step}/{self.config.max_steps}, "
            f"status={self._status.value!r})"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 5.  __main__ — SMOKE TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Self-contained smoke test — no Groq API key required.

    Uses MockAgentOutput (defined inline below) to simulate a 3-step episode,
    then tests reset() and the FAILED → RuntimeError path.

    Run from the project root:
        python core/environment.py          (Windows)
        python3 core/environment.py         (Linux / macOS)

    Expected final line:
        [PASS] Smoke test passed. core/environment.py is working correctly.
    """
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── MockAgentOutput ───────────────────────────────────────────────────────
    # Implements the spec's AgentOutput interface so the smoke test runs
    # without modifying core/agent.py.  The real AgentOutput (produced by
    # concrete agents in agents/*.py) will expose the same surface.

    @dataclasses.dataclass
    class MockAgentOutput:
        """Mock that matches the AgentOutput interface used by Environment."""
        agent_name:  str
        content:     str
        reasoning:   str   = ""
        tokens_used: int   = 0
        latency_ms:  float = 0.0
        tool_calls:  list  = dataclasses.field(default_factory=list)
        metadata:    dict  = dataclasses.field(default_factory=dict)
        success:     bool  = True
        error:       str   = ""

        def to_dict(self) -> dict:
            return dataclasses.asdict(self)

    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Puppeteer++ -- core/environment.py smoke test")
    print("=" * 60 + "\n")

    # ── EPISODE 1: normal 3-step run ending with ConcluderAgent ──────────────
    TASK = "What is 15% of 200, and what is 30% of 150?"

    config = EnvironmentConfig(task=TASK, max_steps=4)
    env    = Environment(config)

    print(f"Task      : {TASK}")
    print(f"task_id   : {config.task_id}")
    print(f"max_steps : {config.max_steps}\n")
    print("-" * 60)

    # Step 0 — PlannerAgent
    out0 = MockAgentOutput(
        agent_name  = "PlannerAgent",
        content     = "I will break this into two calculations: (1) 15% of 200, (2) 30% of 150",
        tokens_used = 45,
        success     = True,
    )
    rec0 = env.transition("PlannerAgent", "", out0)
    print(f"Step {rec0.step} | Status: {env.status.value:10s} | "
          f"Total tokens: {env.total_tokens:4d} | is_done: {env.is_done}")

    # Step 1 — ReasoningAgent
    state1 = env.get_state_for_agent("ReasoningAgent")
    out1   = MockAgentOutput(
        agent_name  = "ReasoningAgent",
        content     = "15% of 200 = 0.15 × 200 = 30. 30% of 150 = 0.30 × 150 = 45.",
        tokens_used = 78,
        success     = True,
    )
    rec1 = env.transition("ReasoningAgent", state1, out1)
    print(f"Step {rec1.step} | Status: {env.status.value:10s} | "
          f"Total tokens: {env.total_tokens:4d} | is_done: {env.is_done}")

    # Step 2 — ConcluderAgent (triggers COMPLETED)
    state2 = env.get_state_for_agent("ConcluderAgent")
    out2   = MockAgentOutput(
        agent_name  = "ConcluderAgent",
        content     = "The answers are: 15% of 200 is 30, and 30% of 150 is 45.",
        tokens_used = 52,
        success     = True,
    )
    rec2 = env.transition("ConcluderAgent", state2, out2)
    print(f"Step {rec2.step} | Status: {env.status.value:10s} | "
          f"Total tokens: {env.total_tokens:4d} | is_done: {env.is_done}")

    print("-" * 60)
    print(f"\nFinal answer      : {env.get_final_answer()}")
    print(f"Trajectory length : {len(env.get_full_trajectory())}")
    print(f"Elapsed           : {env.elapsed_ms:.1f}ms")

    # Spot-check to_dict keys
    d = env.to_dict()
    assert d["status"]       == "completed",  f"Expected 'completed', got {d['status']!r}"
    assert d["current_step"] == 3,            f"Expected 3 steps, got {d['current_step']}"
    assert d["total_tokens"] == 175,          f"Expected 175 tokens, got {d['total_tokens']}"
    print("\n[OK] to_dict() keys and values verified.\n")

    # ── RESET test ────────────────────────────────────────────────────────────
    print("-" * 60)
    print("Testing reset() ...")
    old_task_id = config.task_id
    env.reset(new_task="New task after reset")
    assert env.current_step == 0,                            "current_step should be 0"
    assert env.status       == EpisodeStatus.RUNNING,        "status should be RUNNING"
    assert env.total_tokens == 0,                            "total_tokens should be 0"
    assert config.task_id   != old_task_id,                  "task_id should be regenerated"
    print(f"[OK] current_step=0, status=RUNNING, total_tokens=0, new task_id generated.")
    print(f"     New task: {config.task!r}\n")

    # ── FAILED path test ──────────────────────────────────────────────────────
    print("-" * 60)
    print("Testing FAILED path + RuntimeError guard ...")

    env2    = Environment(EnvironmentConfig(task="fail test", max_steps=4))
    bad_out = MockAgentOutput(
        agent_name  = "ReasoningAgent",
        content     = "",
        tokens_used = 10,
        success     = False,
        error       = "Groq API timeout after 3 retries",
    )
    rec_fail = env2.transition("ReasoningAgent", "", bad_out)
    assert env2.status == EpisodeStatus.FAILED, f"Expected FAILED, got {env2.status}"
    print(f"[OK] Status correctly set to FAILED after success=False output.")

    # Subsequent transition must raise RuntimeError
    try:
        env2.transition("ReasoningAgent", "", bad_out)
        assert False, "RuntimeError was not raised — this is a bug."
    except RuntimeError as exc:
        print(f"[OK] RuntimeError raised as expected: {exc}\n")

    print("=" * 60)
    print("[PASS] Smoke test passed. core/environment.py is working correctly.")
    print("=" * 60 + "\n")
