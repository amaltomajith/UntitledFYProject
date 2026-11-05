"""
Markdown tables for docs/BENCHMARKS.md, generated from the run files so no number is typed by hand.

    python -m benchmarks.reasoning.report --reasoning runs/reasoning-nebius-lightning.json \
        --build runs/buildbench-nebius.json runs/buildbench-nebius-150k.json \
        --fix runs/bench-groq-test.json runs/bench-groq-repo.json runs/bench-groq-repo-20b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NAMES = {"all": "All four", "gsm8k": "GSM8K", "gsm-hard": "GSM-Hard", "mmlu-pro": "MMLU-Pro", "bbh": "BBH"}
METHODS = {"direct": "direct (1 call, answer only)", "cot": "cot (1 call, step by step)",
           "fixed": "fixed (Planner, Reasoning, Critic, Concluder)", "puppeteer": "puppeteer (core.Orchestrator)"}


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _n(x, d=0):
    return "n/a" if x is None else f"{x:,.{d}f}"


def _usd(x):
    return "n/a" if x is None else f"${x:.6f}"


def reasoning(path: Path) -> str:
    d = json.loads(path.read_text(encoding="utf-8"))
    cfg = d.get("config", {})
    rows = [r for r in d["rows"] if not r.get("provider_error")]
    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    total_tokens = sum(r["tokens"] for r in rows)
    out = [f"Source: `{path.as_posix()}` · provider `{d['provider']}` · model `{d['model']}` · thinking "
           f"`{cfg.get('thinking')}` · max_steps {cfg.get('max_steps')} · output cap {cfg.get('max_tokens')} per call "
           f"(policy {cfg.get('policy_max_tokens')}) · seed {d['seeds']} · {len(d['tasks'])} tasks · "
           f"{len(rows)} scored rows · {d.get('excluded', 0)} provider-error rows excluded · "
           f"{'PARTIAL' if d.get('partial') else 'complete'} · total {total_tokens:,} tokens, ${total_cost:.4f}", ""]
    order = ["all"] + [k for k in d["datasets"]]
    for ds in order:
        s = d["summary"].get(ds)
        if not s:
            continue
        out += [f"**{NAMES.get(ds, ds)}**", "",
                "| Method | Correct | Accuracy | 95% CI (Wilson) | Tokens/task | Prompt/task | Completion/task "
                "| Calls/task | $/task | Tokens per correct |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for m in d["methods"]:
            v = s["methods"].get(m)
            if not v:
                continue
            out.append(f"| {METHODS.get(m, m)} | {v['correct']}/{v['n']} | {_pct(v['accuracy'])} | "
                       f"{_pct(v['ci95'][0])} to {_pct(v['ci95'][1])} | {_n(v['tokens'])} | {_n(v['prompt_tokens'])} | "
                       f"{_n(v['completion_tokens'])} | {_n(v['calls'], 1)} | {_usd(v['cost_usd'])} | "
                       f"{_n(v['tokens_per_correct'])} |")
        if s.get("paired"):
            out += ["", "| Puppeteer vs | Shared tasks | Only puppeteer right | Only other right | Exact McNemar p |",
                    "|---|---|---|---|---|"]
            for m, p in s["paired"].items():
                pv = "< 0.001" if p["p_mcnemar"] < 0.001 else f"{p['p_mcnemar']:.3f}"
                out.append(f"| {m} | {p['shared']} | {p['ours_only']} | {p['theirs_only']} | {pv} |")
        out.append("")
    pup = [r for r in rows if r["method"] == "puppeteer"]
    if pup:
        from collections import Counter
        first = Counter(r["agents"][0] for r in pup if r["agents"])
        used = Counter(a for r in pup for a in r["agents"])
        status = Counter(r["status"] for r in pup)
        fallbacks = sum(r.get("policy_fallbacks", 0) for r in pup)
        steps = sum(r["steps"] for r in pup) / len(pup)
        out += ["**What the puppeteer's policy did**", "",
                f"- Episodes: {len(pup)}; mean agent steps {steps:.2f}; statuses {dict(status)}.",
                f"- First agent chosen: {dict(first.most_common())}.",
                f"- Agent activations overall: {dict(used.most_common())}.",
                f"- Policy replies that named no known agent (engine fell back to the first agent): {fallbacks}.", ""]
    return "\n".join(out)


def build(paths: list[Path]) -> str:
    out = ["| Token limit | Method | Tasks passed | Hidden tests | Tokens/task | $/task | False claims | Source |",
           "|---|---|---|---|---|---|---|---|"]
    for p in paths:
        d = json.loads(p.read_text(encoding="utf-8"))
        limit = d.get("token_budget") or 60000
        for m in d["methods"]:
            s = d["summary"].get(m)
            if s:
                out.append(f"| {limit:,} | {m} | {round(s['success'] * s['runs'])}/{s['runs']} | {_pct(s['tests_passed'])} "
                           f"| {_n(s['tokens'])} | {_usd(s.get('cost_usd'))} | {s['false_claims']} | `{p.as_posix()}` |")
    return "\n".join(out)


def fix(paths: list[Path]) -> str:
    out = ["| Run | Dataset | Model | Tasks measured | Method | Success | Tokens/task | Largest prompt (chars) | Provider errors excluded |",
           "|---|---|---|---|---|---|---|---|---|"]
    for p in paths:
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = d.get("rows") or []
        errors = sum(1 for r in rows if r.get("provider_error"))
        good = [r for r in rows if not r.get("provider_error")]
        from puppeteer_code.server import BENCH_LABELS
        dataset, split, model = BENCH_LABELS.get(p.name, ("?", None, "see file"))
        for m in d.get("methods") or []:
            mr = [r for r in good if r["method"] == m]
            if not mr:
                continue
            ok = sum(1 for r in mr if r.get("success"))
            tok = sum(r["tokens"] for r in mr) / len(mr)
            pr = sum(r.get("max_prompt_chars") or 0 for r in mr) / len(mr)
            out.append(f"| `{p.stem}` | {dataset} {split or ''} | {model} | {len({r['task'] for r in mr})} of {len(d.get('tasks') or [])} | {m} | "
                       f"{ok}/{len(mr)} | {tok:,.0f} | {pr:,.0f} | {errors} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reasoning", nargs="*", default=[])
    ap.add_argument("--build", nargs="*", default=[])
    ap.add_argument("--fix", nargs="*", default=[])
    ap.add_argument("--write", help="replace the generated blocks in this markdown file instead of printing")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.write:
        blocks = {"reasoning": "\n\n".join(reasoning(Path(p)) for p in a.reasoning),
                  "build": build([Path(p) for p in a.build]) if a.build else None,
                  "fix": fix([Path(p) for p in a.fix]) if a.fix else None}
        doc = Path(a.write).read_text(encoding="utf-8")
        for name, text in blocks.items():
            start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
            if text is None or start not in doc:
                continue
            head, rest = doc.split(start, 1)
            doc = f"{head}{start}\n{text}\n{end}{rest.split(end, 1)[1]}"
        Path(a.write).write_text(doc, encoding="utf-8")
        print(f"updated {a.write}")
        return 0
    for p in a.reasoning:
        print(reasoning(Path(p)))
    if a.build:
        print(build([Path(p) for p in a.build]), "\n")
    if a.fix:
        print(fix([Path(p) for p in a.fix]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
