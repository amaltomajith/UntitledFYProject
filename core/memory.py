"""
core/memory.py
==============
Retrieval and formatting layer that sits on top of Environment's raw
step history.

Responsibility split:
  - Environment  : owns the data — _steps: list[StepRecord]
  - Memory       : owns how that data is *presented* — token-efficient
                   summaries, rolling windows, activation tracking

Paper grounding:

  Section 2.1:
    "The orchestrator observes the updated system state S_{t+1} and
     selects the next agent a_{t+1} using policy π conditioned on
     S_{t+1} and τ."

  The orchestrator needs S_{t+1} in a form it can reason over.
  Memory formats S_t into that form.

  Section 3 (Implementation Details):
    "Dynamic collaboration uses majority voting for output aggregation."

  Memory tracks agent activation counts, which the orchestrator uses
  to implement soft majority voting and detect over-reliance on one
  agent (extensions/dissent.py).

  Equation 1 (policy):
    a_{t+1} = π(S_{t+1}, τ)

  The string returned by get_context_for_orchestrator() IS S_{t+1} as
  seen by π.

This file defines:
  - MemoryEntry      : processed, token-efficient view of one StepRecord
  - MemoryConfig     : truncation and windowing hyper-parameters
  - Memory           : main class; accepts StepRecords, formats contexts

Integration:
  - Input:  StepRecord (from core.environment) — fed by the orchestrator
            after each Environment.transition() call
  - Output: formatted strings consumed by the orchestrator's LLM prompt
            and by agents via get_context_for_agent()
  - Memory does NOT hold an Environment instance.  The orchestrator wires
    Environment → Memory by passing StepRecords to add().
"""

import time
import logging
import dataclasses
from typing import Optional

# ── load .env automatically ───────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=None, override=False)
except ImportError:
    pass

# ── project imports ───────────────────────────────────────────────────────────
# sys.path guard: allows `python core/memory.py` from the project root.
import sys as _sys
import os as _os
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

from core.environment import StepRecord   # the input to Memory.add()

# ── logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  MEMORY CONFIG
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class MemoryConfig:
    """
    Hyper-parameters that control how Memory truncates and windows history.

    Fields
    ------
    max_summary_chars : int
        Maximum character length of the summary stored per MemoryEntry.
        Long outputs are truncated to this limit (+ "... [truncated]" suffix)
        to keep orchestrator prompt tokens predictable.
        Default: 500 chars ≈ ~125 tokens, enough to convey the agent's
        core reasoning without flooding the prompt.

    max_context_steps : int
        Rolling window size for get_context_for_orchestrator().
        Only the last N entries are included in the orchestrator prompt.
        This bounds prompt growth on long episodes.
        Paper §2.1: "The orchestrator observes the updated system state
        S_{t+1}" — the window IS the bounded S_{t+1} view.

    include_reasoning : bool
        If True, the MemoryEntry summary concatenates both the agent's
        reasoning and its content (final answer), giving the orchestrator
        richer context for its next-agent decision.
        If False, only the final content is summarised (cheaper, less
        informative).
    """
    max_summary_chars: int  = 500
    max_context_steps: int  = 10
    include_reasoning: bool = True


# ═════════════════════════════════════════════════════════════════════════════
# 2.  MEMORY ENTRY
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class MemoryEntry:
    """
    A processed, token-efficient view of one StepRecord.

    While StepRecord stores the full raw AgentOutput, MemoryEntry stores
    only the fields the orchestrator and agents actually need, with long
    text truncated to max_summary_chars.

    Fields
    ------
    step        : int   — step index t (zero-based), copied from StepRecord.
    agent_name  : str   — which agent was activated (a_t).
    summary     : str   — truncated combined reasoning+content string.
    tokens_used : int   — C_t step cost (from AgentOutput.tokens_used).
    success     : bool  — whether the agent completed without error.
    timestamp   : float — time.monotonic() value from StepRecord.

    Methods
    -------
    to_dict() → dict
        Serialisation used by Memory.to_dict() and the dashboard.
    """
    step:        int
    agent_name:  str
    summary:     str
    tokens_used: int
    success:     bool
    timestamp:   float

    def to_dict(self) -> dict:
        """Serialise to a plain dict for dashboard / logging."""
        return {
            "step":        self.step,
            "agent_name":  self.agent_name,
            "summary":     self.summary,
            "tokens_used": self.tokens_used,
            "success":     self.success,
            "timestamp":   self.timestamp,
        }


# ═════════════════════════════════════════════════════════════════════════════
# 3.  MEMORY (main class)
# ═════════════════════════════════════════════════════════════════════════════

class Memory:
    """
    Retrieval and formatting layer for the orchestrator's decision loop.

    The orchestrator calls:
      1. memory.add(step_record)           — after every transition()
      2. memory.get_context_for_orchestrator(task) — to build π's prompt
      3. memory.get_context_for_agent(name, task)  — before each agent call

    Responsibilities:
      - Summarise and truncate raw StepRecords into MemoryEntries
      - Maintain a rolling window (max_context_steps) for the prompt
      - Track per-agent activation counts for soft majority voting
        (paper §3: "Dynamic collaboration uses majority voting")
      - Provide token history for reward function (training/reward.py)

    This class does NOT own an Environment.  The orchestrator wires them:
      record = env.transition(...)
      memory.add(record)
    """

    def __init__(self, config: Optional[MemoryConfig] = None) -> None:
        """
        Parameters
        ----------
        config : MemoryConfig, optional
            Truncation and windowing settings.
            Defaults to MemoryConfig() if not provided.
        """
        self.config = config if config is not None else MemoryConfig()
        self._entries: list[MemoryEntry] = []
        # activation counts — used for soft majority voting (paper §3)
        # and over-reliance detection (extensions/dissent.py)
        self._agent_activation_counts: dict[str, int] = {}

        logger.debug("[Memory] Initialised with config=%r", self.config)

    # ── main add method ───────────────────────────────────────────────────────

    def add(self, step_record: StepRecord) -> MemoryEntry:
        """
        Process a StepRecord into a MemoryEntry and store it.

        Called by the orchestrator immediately after Environment.transition()
        returns.  The orchestrator then passes the result of
        get_context_for_orchestrator() to its policy LLM.

        Processing steps:
          1. Build the summary string (reasoning + content, or content only).
          2. Truncate to max_summary_chars with "... [truncated]" suffix.
          3. Create and store the MemoryEntry.
          4. Increment the activation count for the agent.

        Parameters
        ----------
        step_record : StepRecord
            The record returned by Environment.transition().

        Returns
        -------
        MemoryEntry
            The newly created, stored entry.
        """
        ao = step_record.agent_output

        # ── build summary ─────────────────────────────────────────────────────
        if self.config.include_reasoning and ao.reasoning.strip():
            raw_summary = f"[Reasoning]: {ao.reasoning}\n[Output]: {ao.content}"
        else:
            raw_summary = ao.content

        # ── truncate if needed ────────────────────────────────────────────────
        limit = self.config.max_summary_chars
        if len(raw_summary) > limit:
            summary = raw_summary[:limit] + "... [truncated]"
        else:
            summary = raw_summary

        # ── create entry ──────────────────────────────────────────────────────
        entry = MemoryEntry(
            step        = step_record.step,
            agent_name  = step_record.agent_name,
            summary     = summary,
            tokens_used = step_record.tokens,   # property on StepRecord
            success     = ao.success,
            timestamp   = step_record.timestamp,
        )
        self._entries.append(entry)

        # ── update activation counts (paper §3 majority voting support) ───────
        name = step_record.agent_name
        self._agent_activation_counts[name] = (
            self._agent_activation_counts.get(name, 0) + 1
        )

        logger.debug(
            "[Memory] Added step %d | agent=%s | tokens=%d | success=%s",
            entry.step, entry.agent_name, entry.tokens_used, entry.success,
        )
        return entry

    # ── orchestrator context ──────────────────────────────────────────────────

    def get_context_for_orchestrator(self, task: str) -> str:
        """
        Return the formatted prompt context the orchestrator uses to decide
        which agent to activate next (π(S_{t+1}, τ) from Equation 1).

        Paper §2.1:
          "The orchestrator observes the updated system state S_{t+1} and
           selects the next agent a_{t+1} using policy π conditioned on
           S_{t+1} and τ."

        Format
        ------
        TASK: {task}

        REASONING HISTORY:
        [Step 0 — PlannerAgent] (tokens: 45)
        {summary}
        ---
        [Step 1 — ReasoningAgent] (tokens: 78)
        {summary}
        ---

        AGENT ACTIVATION COUNTS:
        PlannerAgent: 1
        ReasoningAgent: 1

        Notes
        -----
        - Only the last max_context_steps entries are shown (rolling window).
        - Activation counts are over ALL entries, not just the window, so the
          orchestrator can detect if an agent has been over-used even if its
          steps fell out of the window.
        - If no entries exist, returns the "No reasoning history yet" stub.

        Parameters
        ----------
        task : str
            The original task τ — always included at the top.

        Returns
        -------
        str
            The complete orchestrator prompt context.
        """
        if not self._entries:
            return f"TASK: {task}\n\nNo reasoning history yet."

        # Apply rolling window (last N entries)
        window = self._entries[-self.config.max_context_steps:]

        lines: list[str] = [
            f"TASK: {task}",
            "",
            "REASONING HISTORY:",
        ]
        for entry in window:
            lines.append(
                f"[Step {entry.step} — {entry.agent_name}] "
                f"(tokens: {entry.tokens_used})"
            )
            lines.append(entry.summary)
            lines.append("---")

        # Activation counts for all activations (not just window)
        lines.append("")
        lines.append("AGENT ACTIVATION COUNTS:")
        for agent_name, count in sorted(self._agent_activation_counts.items()):
            lines.append(f"{agent_name}: {count}")

        return "\n".join(lines)

    # ── agent context ─────────────────────────────────────────────────────────

    def get_context_for_agent(self, agent_name: str, task: str) -> str:
        """
        Return formatted context for the agent about to be activated.

        Simpler than orchestrator context — just prior summarised outputs,
        no activation counts.  Uses Memory's truncated summaries instead of
        Environment's raw content, reducing token cost on long episodes.

        The calling agent is identified by agent_name but we do NOT filter
        to only show that agent's prior steps — it receives the full prior
        history so it can reason over everything that has happened.

        Format per prior step:
          [Step {n} — {AgentName}]
          {summary}
          ---

        If no prior entries exist (first activation), returns "" so the
        agent knows it is the first to act.

        Parameters
        ----------
        agent_name : str
            The agent about to be activated.
        task : str
            The original task τ.

        Returns
        -------
        str
            Summarised prior context, or "" if no steps have occurred.
        """
        if not self._entries:
            return ""

        lines: list[str] = [f"TASK: {task}", ""]
        for entry in self._entries:
            lines.append(f"[Step {entry.step} — {entry.agent_name}]")
            lines.append(entry.summary)
            lines.append("---")

        return "\n".join(lines)

    # ── utility methods ───────────────────────────────────────────────────────

    def get_activation_count(self, agent_name: str) -> int:
        """
        Return how many times agent_name has been activated this episode.

        Used by the orchestrator to implement soft majority voting
        (paper §3) and by extensions/dissent.py to detect over-reliance.

        Returns 0 if the agent has never been activated.
        """
        return self._agent_activation_counts.get(agent_name, 0)

    def get_most_active_agent(self) -> Optional[str]:
        """
        Return the agent_name with the highest activation count this episode.

        Returns None if no agents have been activated yet.

        Used by extensions/dissent.py to detect if one agent is dominating
        the episode (a form of echo-chamber / over-reliance bias).

        Tie-breaking: Python's max() returns the first maximum encountered
        in dict iteration order (insertion order in Python 3.7+), i.e. the
        agent that was first activated among those tied.
        """
        if not self._agent_activation_counts:
            return None
        return max(
            self._agent_activation_counts,
            key=lambda name: self._agent_activation_counts[name],
        )

    def get_token_history(self) -> list[int]:
        """
        Return the per-step token costs [C_0, C_1, ..., C_T].

        Paper §2.2:
          "We define a step-wise cost C_t based on FLOPs or token-level
           metrics [50]."

        Used by training/reward.py to compute the efficiency penalty
        component of R(τ).

        Returns
        -------
        list[int]
            One integer per stored MemoryEntry, in chronological order.
        """
        return [entry.tokens_used for entry in self._entries]

    def reset(self) -> None:
        """
        Clear all entries and activation counts for a new episode.

        Called by the orchestrator between episodes to ensure no state
        leaks from one task to the next.  Mirrors Environment.reset().
        """
        self._entries.clear()
        self._agent_activation_counts.clear()
        logger.info("[Memory] Reset — all entries and activation counts cleared.")

    def to_dict(self) -> dict:
        """
        Full serialisation for dashboard WebSocket messages and checkpointing.

        Returns
        -------
        dict
            {
              "entries":           list[dict],   # one per MemoryEntry
              "activation_counts": dict[str,int],
              "total_tokens":      int,
              "entry_count":       int
            }
        """
        return {
            "entries":           [e.to_dict() for e in self._entries],
            "activation_counts": dict(self._agent_activation_counts),
            "total_tokens":      sum(self.get_token_history()),
            "entry_count":       len(self._entries),
        }

    def __repr__(self) -> str:
        return (
            f"Memory(entries={len(self._entries)}, "
            f"agents={list(self._agent_activation_counts.keys())})"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 4.  __main__ — SMOKE TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Self-contained smoke test — no Groq API key required.

    Manually constructs 3 mock StepRecords (same episode as the
    environment smoke test), feeds them into Memory, and verifies all
    methods produce the correct output.

    Run from the project root:
        python core/memory.py          (Windows)
        python3 core/memory.py         (Linux / macOS)

    Expected final line:
        [PASS] Smoke test passed. core/memory.py is working correctly.
    """
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # We also need AgentOutput and EnvironmentConfig for building mocks
    from core.agent import AgentOutput
    from core.environment import EnvironmentConfig, EpisodeStatus

    print("\n" + "=" * 60)
    print("  Puppeteer++ -- core/memory.py smoke test")
    print("=" * 60 + "\n")

    TASK = "What is 15% of 200, and what is 30% of 150?"

    # ── helpers to build mock objects ─────────────────────────────────────────

    def make_agent_output(
        agent_name:  str,
        content:     str,
        reasoning:   str = "",
        tokens_used: int = 0,
        success:     bool = True,
        error:       str = "",
    ) -> AgentOutput:
        """Build a real AgentOutput (now compatible) with stub fields."""
        return AgentOutput(
            agent_name   = agent_name,
            content      = content,
            reasoning    = reasoning,
            raw_response = reasoning + "\n" + content,
            tokens_used  = tokens_used,
            latency_ms   = 0.0,
            tool_calls   = [],
            metadata     = {},
            success      = success,
            error        = error,
        )

    def make_step_record(
        step:        int,
        agent_name:  str,
        agent_input: str,
        output:      AgentOutput,
    ) -> StepRecord:
        """Build a real StepRecord manually (bypassing Environment)."""
        return StepRecord(
            step         = step,
            agent_name   = agent_name,
            agent_input  = agent_input,
            agent_output = output,
            timestamp    = time.monotonic(),
        )

    # ── 1. Build 3 mock StepRecords ───────────────────────────────────────────

    out0 = make_agent_output(
        agent_name  = "PlannerAgent",
        content     = "I will break this into two calculations: (1) 15% of 200, (2) 30% of 150",
        reasoning   = "The task has two independent percentage calculations.",
        tokens_used = 45,
    )
    rec0 = make_step_record(0, "PlannerAgent", "", out0)

    out1 = make_agent_output(
        agent_name  = "ReasoningAgent",
        content     = "15% of 200 = 0.15 x 200 = 30. 30% of 150 = 0.30 x 150 = 45.",
        reasoning   = "Applying percentage formula p/100 * n for each case.",
        tokens_used = 78,
    )
    rec1 = make_step_record(1, "ReasoningAgent", "...", out1)

    out2 = make_agent_output(
        agent_name  = "ConcluderAgent",
        content     = "The answers are: 15% of 200 is 30, and 30% of 150 is 45.",
        reasoning   = "Combining the two results from ReasoningAgent.",
        tokens_used = 52,
    )
    rec2 = make_step_record(2, "ConcluderAgent", "...", out2)

    # ── 2. Create Memory and add all 3 records ────────────────────────────────

    mem = Memory()

    print("Adding 3 StepRecords to Memory...")
    e0 = mem.add(rec0)
    e1 = mem.add(rec1)
    e2 = mem.add(rec2)
    print(f"[OK] Memory now has {len(mem._entries)} entries.\n")

    # ── 3. get_context_for_orchestrator ───────────────────────────────────────

    orch_ctx = mem.get_context_for_orchestrator(TASK)
    print("-" * 60)
    print("get_context_for_orchestrator():")
    print("-" * 60)
    print(orch_ctx)

    assert "TASK:"                   in orch_ctx, "Missing TASK header"
    assert "REASONING HISTORY:"      in orch_ctx, "Missing REASONING HISTORY"
    assert "PlannerAgent"            in orch_ctx, "Missing PlannerAgent"
    assert "ReasoningAgent"          in orch_ctx, "Missing ReasoningAgent"
    assert "ConcluderAgent"          in orch_ctx, "Missing ConcluderAgent"
    assert "AGENT ACTIVATION COUNTS" in orch_ctx, "Missing activation counts"
    print("\n[OK] Orchestrator context contains all expected sections.\n")

    # ── 4. get_context_for_agent (before ConcluderAgent runs) ─────────────────
    # Simulates the state just BEFORE ConcluderAgent's activation:
    # create a fresh Memory with only steps 0 and 1.

    mem_pre = Memory()
    mem_pre.add(rec0)
    mem_pre.add(rec1)
    agent_ctx = mem_pre.get_context_for_agent("ConcluderAgent", TASK)

    print("-" * 60)
    print("get_context_for_agent('ConcluderAgent') — 2 prior steps only:")
    print("-" * 60)
    print(agent_ctx)

    assert "PlannerAgent"   in agent_ctx, "Missing PlannerAgent"
    assert "ReasoningAgent" in agent_ctx, "Missing ReasoningAgent"
    assert "ConcluderAgent" not in agent_ctx or agent_ctx.count("ConcluderAgent") == 0, \
        "ConcluderAgent should not appear before its own step"
    print("\n[OK] Agent context shows prior 2 steps only.\n")

    # ── 5. Activation count checks ────────────────────────────────────────────

    print("-" * 60)
    print("Activation counts:")
    count_p = mem.get_activation_count("PlannerAgent")
    count_r = mem.get_activation_count("ReasoningAgent")
    count_c = mem.get_activation_count("ConcluderAgent")
    count_x = mem.get_activation_count("NonExistentAgent")
    print(f"  PlannerAgent    : {count_p}")
    print(f"  ReasoningAgent  : {count_r}")
    print(f"  ConcluderAgent  : {count_c}")
    print(f"  NonExistentAgent: {count_x}")
    assert count_p == 1, f"Expected 1, got {count_p}"
    assert count_r == 1, f"Expected 1, got {count_r}"
    assert count_c == 1, f"Expected 1, got {count_c}"
    assert count_x == 0, f"Expected 0, got {count_x}"
    print("[OK] All activation counts correct.\n")

    # ── 6. get_most_active_agent ──────────────────────────────────────────────

    most_active = mem.get_most_active_agent()
    assert most_active is not None,                     "Should not be None"
    assert most_active in mem._agent_activation_counts, "Should be a known agent"
    print(f"[OK] get_most_active_agent() = {most_active!r} (all tied at 1).\n")

    # ── 7. Token history ──────────────────────────────────────────────────────

    token_hist = mem.get_token_history()
    print(f"[OK] get_token_history() = {token_hist}")
    assert token_hist == [45, 78, 52], f"Expected [45, 78, 52], got {token_hist}"
    print()

    # ── 8. Truncation test ────────────────────────────────────────────────────

    print("-" * 60)
    print("Testing truncation (max_summary_chars=50) ...")
    tiny_cfg = MemoryConfig(max_summary_chars=50, include_reasoning=False)
    mem_tiny  = Memory(config=tiny_cfg)
    long_out  = make_agent_output(
        agent_name  = "ReasoningAgent",
        content     = "A" * 200,   # 200 chars >> 50 char limit
        tokens_used = 10,
    )
    long_rec  = make_step_record(0, "ReasoningAgent", "", long_out)
    long_entry = mem_tiny.add(long_rec)
    assert long_entry.summary.endswith("... [truncated]"), \
        f"Expected truncation suffix, got: {long_entry.summary[-30:]!r}"
    assert len(long_entry.summary) == 50 + len("... [truncated]"), \
        f"Wrong truncated length: {len(long_entry.summary)}"
    print(f"[OK] Truncated summary ends with '... [truncated]'.")
    print(f"     Summary length: {len(long_entry.summary)} chars "
          f"(limit=50 + 15 suffix = 65).\n")

    # ── 9. to_dict ────────────────────────────────────────────────────────────

    d = mem.to_dict()
    assert d["entry_count"]   == 3,   f"Expected 3, got {d['entry_count']}"
    assert d["total_tokens"]  == 175, f"Expected 175, got {d['total_tokens']}"
    assert len(d["entries"])  == 3,   "entries list wrong length"
    print(f"[OK] to_dict() entry_count=3, total_tokens=175.\n")

    # ── 10. Reset ─────────────────────────────────────────────────────────────

    print("-" * 60)
    print("Testing reset() ...")
    mem.reset()
    assert len(mem._entries)                == 0, "entries not cleared"
    assert len(mem._agent_activation_counts) == 0, "activation counts not cleared"
    assert mem.get_most_active_agent()      is None, "should be None after reset"
    assert mem.get_token_history()          == [],   "token history should be empty"
    print("[OK] All entries and counts cleared after reset().\n")

    print("=" * 60)
    print("[PASS] Smoke test passed. core/memory.py is working correctly.")
    print("=" * 60 + "\n")
