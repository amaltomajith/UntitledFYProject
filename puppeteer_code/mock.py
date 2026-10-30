"""
puppeteer_code/mock.py
======================
Role-aware offline responder for the MockClient.

Each Puppeteer-Code role prompt begins "You are <AgentName>", so the mock
can answer per role.  `replies` maps an agent name to a reply or a list of
replies consumed in order (the last one repeats).  The orchestrator prompt
("You are the Puppeteer orchestrator") is answered with the first allowed
agent listed, unless "PolicyLLM" replies are given.
"""

from __future__ import annotations

import dataclasses
import re
import threading
from typing import Optional, Union

Reply = Union[str, list[str]]

DEFAULTS = {
    "PlannerAgent": "1. Find the faulty function from the traceback.\n2. Make the minimal fix.\n"
                    "3. Keep the callers working.",
    "ReviewerAgent": "APPROVE",
    "ConcluderAgent": "REASONING RESULT: The failing tests now pass.\n"
                      "FINAL ANSWER: Fixed the bug with a minimal, test-verified change.",
    "TerminatorAgent": "CONTINUE",
    "CoderAgent": "I could not determine a fix.",
    "DebuggerAgent": "I could not determine a fix.",
    "TestWriterAgent": "No tests to add.",
    "ExplorerAgent": "I could not determine a fix.",
}

_ROLE_RE = re.compile(r"You are (\w+)")
_ALLOWED_RE = re.compile(r"^- (\w+Agent):", re.M)


def scripted_responder(replies: Optional[dict[str, Reply]] = None, solution: Optional[str] = None):
    """
    Build a responder(model, messages) -> str.

    solution: shorthand for the edit text returned by Coder, Debugger and Explorer.
    """
    table: dict[str, Reply] = dict(DEFAULTS)
    if solution is not None:
        for role in ("CoderAgent", "DebuggerAgent", "ExplorerAgent"):
            table[role] = solution
    table.update(replies or {})
    counters: dict[str, int] = {}
    lock = threading.Lock()
    log: list[str] = []

    def responder(model: str, messages: list[dict]) -> str:
        system = messages[0].get("content", "") if messages else ""
        m = _ROLE_RE.match(system)
        role = m.group(1) if m else ""
        if role == "the":                              # orchestrator / PolicyLLM
            if "PolicyLLM" in table:
                role = "PolicyLLM"
            else:
                allowed = _ALLOWED_RE.findall(messages[-1]["content"])
                return allowed[0] if allowed else "ConcluderAgent"
        reply = table.get(role, "REASONING RESULT: n/a\nFINAL ANSWER: n/a")
        with lock:
            log.append(role)
            if isinstance(reply, list):
                i = counters.get(role, 0)
                counters[role] = i + 1
                return reply[min(i, len(reply) - 1)]
        return reply

    responder.log = log
    return responder


# ═════════════════════════════════════════════════════════════════════════════
# simulated competence (for zero-cost training / CI runs, never for results)
# ═════════════════════════════════════════════════════════════════════════════

SIM_SKILL = {
    "CoderAgent": 0.6,        # P(correct fix | the buggy code is visible in the prompt)
    "CoderAgent+plan": 0.8,   # ... when a PLAN section is present
    "DebuggerAgent": 0.75,
    "ExplorerAgent": 0.5,
    "repair": 0.6,            # after an "edit could not be applied" follow-up
    "fast_penalty": 0.7,      # multiplier for FAST-tier models
}


def simulated_responder(solution: str, seed: Optional[int] = None, skill: Optional[dict] = None):
    """
    A stochastic stand-in for real models on PCodeBench-Mini tasks.

    It can only fix a bug whose buggy lines appear in the prompt (exactly
    like a real model, it cannot edit what it cannot see).  With probability
    p[role] it returns the reference fix for every visible bug; otherwise it
    returns a partial fix (when several bugs are visible) or a plausible but
    useless edit, which the sandbox rolls back.

    Used to exercise the RL machinery without spending tokens.  Learning
    curves produced with it are labelled "simulated" and are not results.
    """
    import random

    from puppeteer_code.tools import parse_edit_blocks

    p = dict(SIM_SKILL, **(skill or {}))
    blocks = parse_edit_blocks(solution)
    rng = random.Random(seed)
    lock = threading.Lock()
    base = scripted_responder()

    def render(bs) -> str:
        return "\n".join(f"FILE: {b.file}\n<<<<<<< SEARCH\n{b.search}\n=======\n{b.replace}\n"
                         f">>>>>>> REPLACE\n" for b in bs)

    def responder(model: str, messages: list[dict]) -> str:
        system = messages[0].get("content", "") if messages else ""
        m = _ROLE_RE.match(system)
        role = m.group(1) if m else ""
        if role not in ("CoderAgent", "DebuggerAgent", "ExplorerAgent"):
            return base(model, messages)
        prompt = "\n".join(m["content"] for m in messages[1:] if m["role"] == "user")
        prompt = re.sub(r"(?m)^\s*\d+\| ", "", prompt)      # READ output carries line numbers
        visible = [b for b in blocks if b.search in prompt]
        if not visible:
            if "READ:" in messages[1]["content"]:          # tool-using baseline: look first
                return f"READ: {blocks[0].file}"
            return "The relevant code is not shown, so here is my best guess:\n" + render(
                [dataclasses.replace(blocks[0], search="# code that is not in this file")])
        prob = p["CoderAgent+plan"] if role == "CoderAgent" and "## PLAN" in prompt else p[role]
        if len(messages) > 2 and "could not be applied" in messages[-1]["content"]:
            prob = p["repair"]
        if "fast" in model:
            prob *= p["fast_penalty"]
        with lock:
            r1, r2 = rng.random(), rng.random()
        if r1 < prob:
            return render(visible)
        if len(visible) > 1 and r2 < 0.5:
            return render(visible[:1])
        b = visible[0]
        useless = dataclasses.replace(b, replace=b.search + "  # adjusted")
        return render([useless])

    responder.log = base.log
    return responder
