"""
puppeteer_code/assistant/effort.py
==================================
How hard the assistant thinks. Separate from the permission mode, and from Private.

`normal` on a cloud provider sends exactly what it always did, so benchmarks stay
comparable. A model with native thinking is told per agent through the `think`
option. A model without it gets a reasoning pass at `think` and `deep`: one call
with no tools whose notes go into the task. That pass is experimental, because
nothing here has measured whether it helps a local model.
"""

from __future__ import annotations

from typing import Optional

from puppeteer_code.assistant.agents import RULES

LEVELS = ("fast", "normal", "think", "deep")
MAX_NOTES = 1200
MAX_REASONING_TOKENS = 800
REASONED = ("Planner", "Builder", "Debugger")
REASONING_PROMPT = f"""Think through the task below before anyone acts on it. Reply with short notes: \
the approach, what could go wrong, and what to check. Do not write the final code. {RULES}"""

_THINKING = {
    "normal": ("Planner", "Debugger"),
    "think": ("Planner", "Builder", "Debugger", "Reviewer"),
    "deep": ("Answerer", "Planner", "TestWriter", "Builder", "Debugger", "Reviewer"),
}
_STRENGTH = {False: 0, "low": 1, "medium": 2, True: 2, "high": 3}


def _ordered(values: list) -> list:
    return sorted(values, key=lambda value: _STRENGTH.get(value, 2))


def think_option(level: str, agent: str, values: Optional[list]) -> dict:
    """The `think` option for one call. No values means the model has no thinking to control."""
    if not values:
        return {}
    ordered = _ordered(values)
    if agent not in _THINKING.get(level, ()):
        return {"think": ordered[0]}
    if level == "deep":
        return {"think": ordered[-1]}
    return {"think": True if True in values else ordered[len(ordered) // 2]}


def tier_for(level: str, agent: str) -> Optional[str]:
    """The tier this effort forces on an agent. None keeps the one in models.yaml."""
    if level == "fast":
        return "fast"
    return "quality" if level in ("think", "deep") and agent == "Planner" else None


def reasons(level: str, agent: str, values: Optional[list]) -> bool:
    """Whether this agent gets a reasoning pass before it acts."""
    if agent not in REASONED:
        return False
    return level == "deep" or (level == "think" and not values)


def describe(level: str, values: Optional[list]) -> str:
    """What this effort does on this model, in plain words."""
    if level == "fast":
        return "Fast models only, with no review."
    if level == "normal":
        return "The usual routing."
    native = "The model thinks before it acts." if values else ""
    notes = "A planning pass runs first (experimental)." if reasons(level, "Builder", values) else ""
    extra = " Always plans first." if level == "deep" else ""
    return f"{native} {notes}".strip() + extra


__all__ = ["LEVELS", "MAX_NOTES", "MAX_REASONING_TOKENS", "REASONING_PROMPT", "describe", "reasons",
           "think_option", "tier_for"]
