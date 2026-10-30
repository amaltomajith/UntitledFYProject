"""
puppeteer_code/bench.py
=======================
Training and benchmarking on PCodeBench-Mini.

  pcode train  --provider sim|nebius --episodes N      REINFORCE on LinearPolicy (train split)
  pcode bench  --methods heuristic,linear,... --seeds 3 evaluate on the test split (+ hidden tests)
  pcode report --bench <json> --curve <json>            single-file HTML report

Provider "sim" is the MockClient with simulated competence
(puppeteer_code.mock.simulated_responder).  It costs nothing and exercises
the full engine, but numbers produced with it are *mechanism checks*, not
model results; every output file records the provider used.
"""

from __future__ import annotations

import datetime as _dt
import json
import random
import statistics
import time
import zlib
from pathlib import Path
from typing import Callable, Optional

from puppeteer_code.engine import CodeOrchestrator, RunConfig
from puppeteer_code.policy import LinearPolicy, make_policy
from puppeteer_code.providers import ProviderSession
from puppeteer_code.tasks import BenchTask, load_tasks

ROOT = Path(__file__).resolve().parent.parent
ENGINE_METHODS = ["heuristic", "linear", "llm", "fixed", "random"]
BASELINE_METHODS = ["single-shot", "react", "react-tools"]


def make_task_session(provider: str, task: BenchTask, seed: int, cache_dir: Optional[str] = None,
                      models: Optional[dict] = None):
    """models: optional tier -> model id overrides, e.g. {"quality": "openai/gpt-oss-20b"}."""
    if provider == "sim":
        from puppeteer_code.mock import simulated_responder
        # Seed per (seed, task): one seed for every task would make the draws identical
        # across tasks (e.g. every single-shot run fails together).
        task_seed = zlib.crc32(f"{seed}:{task.id}".encode())
        session = ProviderSession("mock", responder=simulated_responder(task.solution, seed=task_seed))
    elif provider == "oracle":
        from puppeteer_code.mock import scripted_responder
        session = ProviderSession("mock", responder=scripted_responder(solution=task.solution))
    else:
        # One cache per seed: replays are free, but seeds still sample independently.
        session = ProviderSession(provider, cache_dir=str(Path(cache_dir) / f"seed{seed}") if cache_dir else None)
    session.tiers.update(models or {})
    return session


QUOTA_MARKERS = ("429", "rate limit", "rate_limit", "quota", "tokens per day", "TPD")


def is_quota_error(text: str) -> bool:
    return any(m.lower() in (text or "").lower() for m in QUOTA_MARKERS)


def _row(method: str, task: BenchTask, seed: int, res: dict) -> dict:
    final = res["final"]
    hold = final.get("holdout_tests")
    steps = res["steps"]
    return {
        "method": method, "task": task.id, "difficulty": task.difficulty, "split": task.split,
        "seed": seed, "success": bool(res["success"]), "status": res["status"],
        "pass_rate": final["final_tests"]["pass_rate"],
        "holdout_pass_rate": hold["pass_rate"] if hold else None,
        "regressions": len(final["regressions"]), "integrity": len(final["integrity_violations"]),
        "tokens": res["usage"].get("total_tokens", 0), "cost_usd": res["usage"].get("cost_usd"),
        "steps": len(steps), "llm_calls": sum(1 for s in steps if s.get("tokens")),
        "max_prompt_chars": max((s.get("prompt_chars", 0) for s in steps), default=0),
        "prompt_chars": [s.get("prompt_chars", 0) for s in steps],
        "agents": [s["agent"] for s in steps], "wall_s": res["wall_s"],
        "diff_lines": sum(final["diff_stat"][1:]),
        "return": res.get("rewards", {}).get("total_return"),
        # An API failure is not a failed fix: such rows are excluded from the summary.
        "provider_error": next((s.get("error") for s in steps if s.get("success") is False and s.get("error")), None),
    }


def run_method(method: str, task: BenchTask, provider: str, seed: int, max_steps: int = 12,
               weights: Optional[str] = None, cache_dir: Optional[str] = None,
               holdout: bool = True, models: Optional[dict] = None) -> dict:
    session = make_task_session(provider, task, seed, cache_dir, models)
    hold = task.holdout if holdout else None
    try:
        if method in BASELINE_METHODS:
            from puppeteer_code import baselines
            fn = {"single-shot": baselines.single_shot, "react": baselines.react,
                  "react-tools": baselines.react_tools}[method]
            res = fn(session, task.repo, task.task, holdout=hold)
        else:
            policy = make_policy(method, session=session, weights=weights, seed=seed, greedy=True)
            orch = CodeOrchestrator(session, policy=policy,
                                    config=RunConfig(max_steps=max_steps, holdout_dir=str(hold) if hold else None))
            res = orch.run(task.repo, task.task).to_dict()
    except Exception as e:     # provider failure mid-run (baselines call the client directly)
        return {"method": method, "task": task.id, "difficulty": task.difficulty, "split": task.split,
                "seed": seed, "success": False, "provider_error": f"{type(e).__name__}: {e}"[:500]}
    return _row(method, task, seed, res)


# ═════════════════════════════════════════════════════════════════════════════
# training
# ═════════════════════════════════════════════════════════════════════════════

def train(tasks: list[BenchTask], provider: str, episodes: int, out_weights: Path, curve_path: Path,
          seed: int = 0, lr: float = 0.05, max_steps: int = 12, resume: bool = False,
          on_episode: Optional[Callable[[dict], None]] = None, cache_dir: Optional[str] = None,
          models: Optional[dict] = None) -> list[dict]:
    rng = random.Random(seed)
    if resume and out_weights.exists():
        policy = LinearPolicy.load(out_weights, seed=seed)
        policy.greedy = False
        policy.lr = lr
    else:
        policy = LinearPolicy(lr=lr, seed=seed)
    curve: list[dict] = []
    order: list[BenchTask] = []
    for ep in range(episodes):
        if not order:
            order = list(tasks)
            rng.shuffle(order)
        task = order.pop()
        session = make_task_session(provider, task, seed * 100003 + ep, cache_dir, models)
        orch = CodeOrchestrator(session, policy=policy, config=RunConfig(max_steps=max_steps))
        res = orch.run(task.repo, task.task)
        row = _row("linear-train", task, ep, res.to_dict())
        row.update(episode=ep, terminal_reward=res.rewards.get("terminal_reward"),
                   update=res.rewards.get("policy_update"))
        curve.append(row)
        if on_episode:
            on_episode(row)
        if (ep + 1) % 10 == 0 or ep == episodes - 1:
            policy.save(out_weights)
            _save_json(curve_path, {"provider": provider, "seed": seed, "lr": lr, "episodes": curve,
                                    "weights": str(out_weights), "top_weights": policy.top_weights()})
    return curve


# ═════════════════════════════════════════════════════════════════════════════
# benchmark
# ═════════════════════════════════════════════════════════════════════════════

def _stats(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"mean": None, "std": None, "n": 0}
    return {"mean": statistics.fmean(xs), "std": statistics.pstdev(xs) if len(xs) > 1 else 0.0, "n": len(xs)}


def aggregate(rows: list[dict]) -> dict:
    """mean ± std across seeds of the per-seed task averages, per method."""
    out = {}
    rows = [r for r in rows if not r.get("provider_error")]
    for method in sorted({r["method"] for r in rows}):
        mrows = [r for r in rows if r["method"] == method]
        per_seed: dict[int, list[dict]] = {}
        for r in mrows:
            per_seed.setdefault(r["seed"], []).append(r)

        def seed_metric(fn):
            return _stats([statistics.fmean(fn(r) for r in rs) for rs in per_seed.values()])

        costs = [r["cost_usd"] for r in mrows]
        out[method] = {
            "success": seed_metric(lambda r: float(r["success"])),
            "pass_rate": seed_metric(lambda r: r["pass_rate"]),
            "holdout_pass_rate": seed_metric(lambda r: r["holdout_pass_rate"] or 0.0),
            "tokens": seed_metric(lambda r: r["tokens"]),
            "steps": seed_metric(lambda r: r["steps"]),
            "llm_calls": seed_metric(lambda r: r["llm_calls"]),
            "max_prompt_chars": seed_metric(lambda r: r["max_prompt_chars"]),
            "wall_s": seed_metric(lambda r: r["wall_s"]),
            "regressions": sum(r["regressions"] for r in mrows),
            "integrity_violations": sum(r["integrity"] for r in mrows),
            "cost_usd_total": None if None in costs else sum(costs),
            "tokens_per_success": (sum(r["tokens"] for r in mrows) / max(1, sum(r["success"] for r in mrows))),
            "by_difficulty": {d: statistics.fmean(float(r["success"]) for r in mrows if r["difficulty"] == d)
                              for d in sorted({r["difficulty"] for r in mrows})},
            "runs": len(mrows),
        }
    return out


def bench(tasks: list[BenchTask], methods: list[str], seeds: list[int], provider: str, out: Path,
          weights: Optional[str] = None, max_steps: int = 12, cache_dir: Optional[str] = None,
          on_row: Optional[Callable[[dict], None]] = None, resume: bool = False,
          models: Optional[dict] = None) -> dict:
    """
    Run every (seed, method, task).  With resume=True, rows already in `out`
    (without a provider error) are kept and skipped.  A quota / rate-limit
    failure stops the run with partial results saved, ready to --resume.
    """
    rows: list[dict] = []
    if resume and out.exists():
        rows = [r for r in json.loads(out.read_text(encoding="utf-8")).get("rows", [])
                if not r.get("provider_error")]
    done = {(r["seed"], r["method"], r["task"]) for r in rows}
    started = time.monotonic()
    stopped = None
    # Task-major order: if a quota stops the run, every method has covered the same tasks.
    for seed in seeds:
        for task in tasks:
            for method in methods:
                if (seed, method, task.id) in done or stopped:
                    continue
                row = run_method(method, task, provider, seed, max_steps=max_steps, weights=weights,
                                 cache_dir=cache_dir, models=models)
                rows.append(row)
                if on_row:
                    on_row(row)
                if row.get("provider_error") and is_quota_error(row["provider_error"]):
                    stopped = row["provider_error"]
                _save_json(out, {"provider": provider, "methods": methods, "seeds": seeds,
                                 "tasks": [t.id for t in tasks], "rows": rows,
                                 "summary": aggregate(rows), "partial": True, "stopped": stopped})
    data = {"provider": provider, "methods": methods, "seeds": seeds, "tasks": [t.id for t in tasks],
            "rows": rows, "summary": aggregate(rows), "partial": bool(stopped), "stopped": stopped,
            "wall_s": round(time.monotonic() - started, 1),
            "created": _dt.datetime.now().isoformat(timespec="seconds")}
    _save_json(out, data)
    return data


def summary_table(summary: dict) -> str:
    head = ("| method | success | pass rate | hidden-test pass | tokens | LLM calls | steps | "
            "max prompt chars | regressions |\n|---|---|---|---|---|---|---|---|---|")
    lines = [head]

    def f(m, pct=False, digits=0):
        if m["mean"] is None:
            return "n/a"
        if pct:
            return f"{100 * m['mean']:.0f}% ± {100 * m['std']:.0f}"
        return f"{m['mean']:.{digits}f} ± {m['std']:.{digits}f}"

    for method, s in summary.items():
        lines.append(f"| {method} | {f(s['success'], True)} | {f(s['pass_rate'], True)} | "
                     f"{f(s['holdout_pass_rate'], True)} | {f(s['tokens'])} | {f(s['llm_calls'], digits=1)} | "
                     f"{f(s['steps'], digits=1)} | {f(s['max_prompt_chars'])} | {s['regressions']} |")
    return "\n".join(lines)


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def cmd_train(args) -> int:
    from puppeteer_code.cli import say
    tasks = load_tasks(args.tasks, split=args.split, limit=args.limit)
    say(f"Training LinearPolicy on {len(tasks)} {args.split} tasks, provider={args.provider}, "
        f"episodes={args.episodes}")

    def show(r):
        say(f"ep {r['episode']:>4}  {r['task']:<20} {'OK ' if r['success'] else 'x  '} "
            f"steps={r['steps']:>2} tokens={r['tokens']:>6} R0={r['return']:+.3f}  "
            f"{' '.join(a.replace('Agent', '')[:6] for a in r['agents'])}")

    train(tasks, args.provider, args.episodes, Path(args.weights), Path(args.curve), seed=args.seed,
          lr=args.lr, max_steps=args.max_steps, resume=args.resume, on_episode=show, cache_dir=args.cache,
          models=tier_overrides(args))
    say(f"weights -> {args.weights}\ncurve   -> {args.curve}")
    return 0


def cmd_bench(args) -> int:
    from puppeteer_code.cli import say
    tasks = load_tasks(args.tasks, split=None if args.ids else args.split, limit=args.limit,
                       ids=args.ids.split(",") if args.ids else None)
    methods = args.methods.split(",")
    seeds = list(range(args.seeds))
    say(f"Benchmark: {len(tasks)} tasks x {len(methods)} methods x {len(seeds)} seeds, provider={args.provider}")

    def show(r):
        if r.get("provider_error"):
            say(f"{r['method']:<12} seed={r['seed']} {r['task']:<20} PROVIDER ERROR {r['provider_error'][:120]}")
            return
        hold = "-" if r["holdout_pass_rate"] is None else f"{r['holdout_pass_rate']:.2f}"
        say(f"{r['method']:<12} seed={r['seed']} {r['task']:<20} {'OK ' if r['success'] else 'x  '} "
            f"pass={r['pass_rate']:.2f} hidden={hold} tokens={r['tokens']:>6} steps={r['steps']}")

    data = bench(tasks, methods, seeds, args.provider, Path(args.out), weights=args.weights,
                 max_steps=args.max_steps, cache_dir=args.cache, on_row=show, resume=args.resume,
                 models=tier_overrides(args))
    if data.get("stopped"):
        say(f"\nStopped on a provider quota error; partial results saved. Re-run the same command "
            f"with --resume later.\n  {data['stopped'][:200]}")
    table = summary_table(data["summary"])
    Path(args.out).with_suffix(".md").write_text(
        f"Provider: {args.provider}\n\n{table}\n", encoding="utf-8")
    say("\n" + table)
    say(f"\nresults -> {args.out}")
    return 0


def cmd_report(args) -> int:
    from puppeteer_code.cli import say
    from puppeteer_code.report import write_report
    path = write_report(Path(args.out), bench_path=args.bench, curve_path=args.curve)
    say(f"report -> {path}")
    return 0


def model_args(parser) -> None:
    parser.add_argument("--fast-model", default=None, help="override the FAST tier model id")
    parser.add_argument("--quality-model", default=None, help="override the QUALITY tier model id")


def tier_overrides(args) -> dict:
    out = {}
    if getattr(args, "fast_model", None):
        out["fast"] = args.fast_model
    if getattr(args, "quality_model", None):
        out["quality"] = args.quality_model
    return out


def register(sub, provider_args) -> None:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    default_weights = str(ROOT / "checkpoints" / "pcode_linear.json")

    t = sub.add_parser("train", help="train the LinearPolicy with REINFORCE on PCodeBench-Mini")
    provider_args(t)
    t.add_argument("--tasks", default=None)
    t.add_argument("--split", default="train")
    t.add_argument("--limit", type=int, default=None)
    t.add_argument("--episodes", type=int, default=60)
    t.add_argument("--lr", type=float, default=0.05)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--max-steps", type=int, default=12)
    t.add_argument("--weights", default=default_weights)
    t.add_argument("--curve", default=str(ROOT / "runs" / f"train-{stamp}.json"))
    t.add_argument("--resume", action="store_true")
    model_args(t)
    t.set_defaults(func=cmd_train)

    b = sub.add_parser("bench", help="benchmark methods on PCodeBench-Mini")
    provider_args(b)
    b.add_argument("--tasks", default=None)
    b.add_argument("--split", default="test")
    b.add_argument("--ids", default=None, help="comma-separated task ids")
    b.add_argument("--limit", type=int, default=None)
    b.add_argument("--methods", default="heuristic,linear,fixed,single-shot,react")
    b.add_argument("--seeds", type=int, default=3)
    b.add_argument("--max-steps", type=int, default=12)
    b.add_argument("--weights", default=default_weights)
    b.add_argument("--out", default=str(ROOT / "runs" / f"bench-{stamp}.json"))
    b.add_argument("--resume", action="store_true", help="skip runs already saved in --out")
    model_args(b)
    b.set_defaults(func=cmd_bench)

    r = sub.add_parser("report", help="write a single-file HTML report")
    r.add_argument("--bench", default=None)
    r.add_argument("--curve", default=None)
    r.add_argument("--out", default=str(ROOT / "runs" / f"report-{stamp}.html"))
    r.set_defaults(func=cmd_report)
