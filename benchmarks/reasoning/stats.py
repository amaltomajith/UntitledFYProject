"""Aggregates for the reasoning benchmark. Rows with a provider error are excluded and counted, never scored."""

from __future__ import annotations

import math
from collections import defaultdict

Z95 = 1.959963984540054


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: b and c are the two kinds of discordant pair."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def method_summary(rows: list[dict]) -> dict:
    n = len(rows)
    k = sum(1 for r in rows if r["correct"])
    lo, hi = wilson(k, n)
    tokens = sum(r["tokens"] for r in rows)
    costs = [r.get("cost_usd") for r in rows]
    return {
        "n": n, "correct": k, "accuracy": k / n if n else None, "ci95": [lo, hi],
        "tokens": _mean([r["tokens"] for r in rows]),
        "prompt_tokens": _mean([r["prompt_tokens"] for r in rows]),
        "completion_tokens": _mean([r["completion_tokens"] for r in rows]),
        "calls": _mean([r["calls"] for r in rows]),
        "cost_usd": _mean(costs) if all(c is not None for c in costs) else None,
        "cost_usd_total": sum(costs) if costs and all(c is not None for c in costs) else None,
        "tokens_per_correct": tokens / k if k else None,
        "no_answer_tag": sum(1 for r in rows if not r.get("tagged")),
        "wall_s": _mean([r.get("wall_s") for r in rows]),
        "policy_fallbacks": sum(r.get("policy_fallbacks", 0) for r in rows),
        "statuses": dict(sorted(_count(r.get("status") for r in rows).items())),
    }


def _count(xs):
    out = defaultdict(int)
    for x in xs:
        out[x] += 1
    return out


def summarize(rows: list[dict], ours: str = "puppeteer") -> dict:
    scored = [r for r in rows if not r.get("provider_error")]
    by = defaultdict(lambda: defaultdict(list))
    for r in scored:
        by[r["dataset"]][r["method"]].append(r)
        by["all"][r["method"]].append(r)
    out = {}
    for dataset, methods in by.items():
        entry = {"methods": {m: method_summary(rs) for m, rs in sorted(methods.items())}, "paired": {}}
        if ours in methods:
            mine = {(r["task"], r["seed"]): r["correct"] for r in methods[ours]}
            for m, rs in methods.items():
                if m == ours:
                    continue
                theirs = {(r["task"], r["seed"]): r["correct"] for r in rs}
                shared = mine.keys() & theirs.keys()
                b = sum(1 for key in shared if mine[key] and not theirs[key])
                c = sum(1 for key in shared if theirs[key] and not mine[key])
                entry["paired"][m] = {"shared": len(shared), "ours_only": b, "theirs_only": c,
                                      "p_mcnemar": mcnemar_exact(b, c)}
        out[dataset] = entry
    return out
