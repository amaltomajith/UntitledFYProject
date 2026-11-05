"""
Run the reasoning benchmark on a real provider.

    python -m benchmarks.reasoning.run --provider nebius --model nvidia/Nemotron-3_5-Lightning \
        --datasets gsm8k,gsm-hard,mmlu-pro,bbh --methods direct,cot,fixed,puppeteer \
        --cache runs/cache/reasoning --out runs/reasoning-nebius.json            # add --resume to continue

    --smoke    use tasks/smoke.jsonl (never in the subsets) to check the pipeline; not a result
    --limit N  first N tasks per dataset

Tasks run one at a time, every method on a task before the next task, so a stopped run is still paired.
The file is saved after every row. Rows with a provider error are kept, flagged, excluded from the
summary, and retried by --resume. A daily-quota stop saves and exits with code 2.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openai import RateLimitError  # noqa: E402

from benchmarks.reasoning import checkers, datasets, methods, stats  # noqa: E402
from benchmarks.reasoning.client import CountingClient, set_thinking  # noqa: E402

REAL = ("nebius", "groq")
REPLY_CHARS = 6000      # the scored reply is kept so rows can be rescored without new calls


class QuotaStop(Exception):
    pass


def make_inner(provider: str, cache: str | None, seed: int, thinking: str):
    from puppeteer_code.providers import CachingClient  # also loads .env
    from core.agent import GroqClient, NebiusClient
    inner = {"nebius": NebiusClient, "groq": GroqClient}[provider]()
    if thinking != "default":
        set_thinking(inner, thinking == "on")
    return CachingClient(inner, Path(cache) / f"thinking-{thinking}" / f"seed{seed}") if cache else inner


def prices_for(provider: str) -> dict:
    try:
        from puppeteer_code.providers import load_config
        return load_config()["providers"][provider].get("prices") or {}
    except Exception:
        return {}


def run_row(client: CountingClient, model: str, method: str, task: dict, seed: int, prices: dict) -> dict:
    client.reset()
    t0 = time.monotonic()
    error = None
    try:
        res = methods.RUNNERS[method](client, model, task)
    except Exception as e:     # one-call methods raise; the orchestrator swallows agent errors itself
        res, error = methods.MethodResult(reply="", status="error"), e
    if client.errors:
        error = client.errors[0]
    if isinstance(error, RateLimitError):
        raise QuotaStop(str(error)[:200])
    seg, tagged = checkers.final_segment(res.reply)
    return {
        "dataset": task["dataset"], "task": task["id"], "method": method, "seed": seed,
        "correct": False if error else checkers.check(task["kind"], res.reply, task["gold"]),
        "gold": task["gold"], "kind": task["kind"], "answer": seg[:200], "tagged": tagged,
        "reply": res.reply[:REPLY_CHARS], "engine_answer": res.engine_answer[:200],
        "status": res.status, "steps": res.steps, "agents": res.agents,
        "policy_raw": res.policy_raw, "policy_fallbacks": res.policy_fallbacks,
        "calls": len(client.calls), "prompt_tokens": client.prompt_tokens,
        "completion_tokens": client.completion_tokens, "tokens": client.prompt_tokens + client.completion_tokens,
        "cached_calls": sum(c.cached for c in client.calls), "think_in_reply": any(c.had_think for c in client.calls),
        "cost_usd": client.cost(prices), "wall_s": round(time.monotonic() - t0, 2),
        "provider_error": f"{type(error).__name__}: {str(error)[:200]}" if error else None,
    }


def save(path: Path, doc: dict) -> None:
    doc["summary"] = stats.summarize(doc["rows"])
    doc["excluded"] = sum(1 for r in doc["rows"] if r.get("provider_error"))
    done = {(r["task"], r["method"]) for r in doc["rows"] if not r.get("provider_error")}
    doc["partial"] = any((t, m) not in done for t in doc["tasks"] for m in doc["methods"])
    doc["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(path)


def rescore(path: Path) -> tuple[int, int]:
    """Recompute `correct` from each row's stored reply (after a checker fix). Returns (rows, changed)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for r in doc["rows"]:
        if r.get("provider_error") or "reply" not in r:
            continue
        seg, tagged = checkers.final_segment(r["reply"])
        ok = checkers.check(r["kind"], r["reply"], r["gold"])
        changed += ok != r["correct"]
        r.update(correct=ok, answer=seg[:200], tagged=tagged)
    save(path, doc)
    return len(doc["rows"]), changed


def run(args, inner=None) -> int:
    ds = [d.strip() for d in args.datasets.split(",") if d.strip()]
    ms = [m.strip() for m in args.methods.split(",") if m.strip()]
    methods.MAX_TOKENS = args.max_tokens
    unknown = [m for m in ms if m not in methods.RUNNERS]
    if unknown:
        raise SystemExit(f"unknown methods: {unknown}")
    tasks = datasets.load_tasks(ds, smoke=args.smoke)
    if args.limit:
        kept, per = [], {}
        for t in tasks:
            if per.get(t["dataset"], 0) < args.limit:
                kept.append(t)
                per[t["dataset"]] = per.get(t["dataset"], 0) + 1
        tasks = kept
    missing = [d for d in ds if not any(t["dataset"] == d for t in tasks)]
    if missing:
        print(f"no tasks for {missing} (GAIA needs HF_TOKEN and datasets.py --build)", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "benchmark": "Reasoning", "provider": args.provider, "model": args.model, "smoke": bool(args.smoke),
        "datasets": [d for d in ds if d not in missing], "methods": ms, "seeds": [args.seed],
        "tasks": [t["id"] for t in tasks],
        "config": {"thinking": args.thinking, "max_steps": methods.MAX_STEPS, "max_tokens": methods.MAX_TOKENS,
                   "policy_max_tokens": methods.POLICY_MAX_TOKENS,
                   "baseline_temperature": methods.BASELINE_TEMPERATURE, "fixed_order": list(methods.FIXED_ORDER)},
        "started": _dt.datetime.now().isoformat(timespec="seconds"), "rows": [],
    }
    if args.resume and out.exists():
        old = json.loads(out.read_text(encoding="utf-8"))
        doc["started"] = old.get("started", doc["started"])
        doc["rows"] = [r for r in old.get("rows", []) if not r.get("provider_error")]
    done = {(r["task"], r["method"], r["seed"]) for r in doc["rows"]}
    client = CountingClient(inner if inner is not None else make_inner(args.provider, args.cache, args.seed, args.thinking))
    prices = prices_for(args.provider)
    total = len(tasks) * len(ms)
    i = len([1 for t in tasks for m in ms if (t["id"], m, args.seed) in done])
    try:
        for task in tasks:
            for method in ms:
                if (task["id"], method, args.seed) in done:
                    continue
                row = run_row(client, args.model, method, task, args.seed, prices)
                doc["rows"].append(row)
                save(out, doc)
                i += 1
                mark = "ERR" if row["provider_error"] else ("ok " if row["correct"] else "x  ")
                print(f"[{i}/{total}] {mark} {method:9s} {task['id']:44s} tokens={row['tokens']:6d} "
                      f"calls={row['calls']} {row['wall_s']:.0f}s", flush=True)
    except QuotaStop as e:
        save(out, doc)
        print(f"Quota stop, saved {out}. Rerun with --resume. ({e})", flush=True)
        return 2
    save(out, doc)
    print(f"Done: {out}", flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=REAL, default="nebius")
    ap.add_argument("--model", default="nvidia/Nemotron-3_5-Lightning")
    ap.add_argument("--datasets", default="gsm8k,gsm-hard,mmlu-pro,bbh")
    ap.add_argument("--methods", default=",".join(methods.METHODS))
    ap.add_argument("--seed", type=int, default=0, help="recorded, and separates the response cache")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--thinking", choices=("off", "on", "default"), default="off",
                    help="the model's own reasoning, for every call of every method (default off)")
    ap.add_argument("--max-tokens", type=int, default=methods.MAX_TOKENS, help="output cap per call, all methods")
    ap.add_argument("--rescore", action="store_true", help="recompute scores in --out from stored replies, no calls")
    args = ap.parse_args(argv)
    if args.rescore:
        n, changed = rescore(Path(args.out))
        print(f"rescored {n} rows, {changed} changed")
        return 0
    logging.basicConfig(level=logging.ERROR)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
