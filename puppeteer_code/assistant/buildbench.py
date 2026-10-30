"""
puppeteer_code/assistant/buildbench.py
======================================
PCodeBench-Build: can a method build working software from a prompt in an empty
folder? The score comes from acceptance tests the model never sees, so a method
cannot pass by writing weak tests of its own.

Methods: heuristic and linear are the puppeteer; direct is one model in a plain
tool loop. The quota fallback is off, so a row always used the models it says.
Provider errors and quota stops are excluded from aggregates and reported.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from puppeteer_code.assistant.policy import LinearAssistantPolicy, make_policy
from puppeteer_code.assistant.session import AssistantSession
from puppeteer_code.assistant.verify import _counts
from puppeteer_code.workspace import scrubbed_env

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "benchmarks" / "build_tasks" / "tasks"
SCORE_TIMEOUT = 60


@dataclasses.dataclass
class BuildTask:
    id: str
    lang: str
    prompt: str
    folder: Path

    @property
    def hidden(self) -> str:
        return "test_accept.py" if self.lang == "python" else "accept.test.js"


def load_tasks(folder: Path = TASKS_DIR, only: Optional[list[str]] = None) -> list[BuildTask]:
    tasks = []
    for path in sorted(Path(folder).iterdir()):
        if (path / "prompt.txt").exists() and (not only or path.name in only):
            lang = "python" if (path / "test_accept.py").exists() else "node"
            tasks.append(BuildTask(path.name, lang, (path / "prompt.txt").read_text(encoding="utf-8").strip(), path))
    return tasks


def score(task: BuildTask, project: Path) -> dict:
    """Run the hidden test in a copy, so nothing the agent left behind can see or change it."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "scored"
        shutil.copytree(project, work, ignore=shutil.ignore_patterns("node_modules", ".git", "__pycache__"))
        shutil.copy(task.folder / task.hidden, work / task.hidden)
        command = ([sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider", task.hidden]
                   if task.lang == "python" else ["node", "--test", task.hidden])
        try:
            proc = subprocess.run(command, cwd=work, env=scrubbed_env(), stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=SCORE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"passed": 0, "total": 0, "success": False, "timed_out": True}
    passed, failed = _counts(proc.stdout)
    total = passed + failed
    return {"passed": passed, "total": total, "success": proc.returncode == 0 and passed > 0 and failed == 0}


def run_one(task: BuildTask, method: str, seed: int, make_provider: Callable[[], object],
            policy=None, token_budget: int = 60000) -> dict:
    home = Path(tempfile.mkdtemp(prefix="pcode-bench-"))
    project = home / "project"
    provider = make_provider()
    session = AssistantSession(project, provider, mode="auto", home=home, token_budget=token_budget,
                               policy=policy or make_policy(method), fallback=False)
    agents, statuses = [], {}
    started = time.monotonic()
    try:
        for event in session.send(task.prompt):
            if event["type"] == "agent":
                agents.append(event["agent"])
            elif event["type"] == "done":
                statuses = event
        wall = time.monotonic() - started
        result = score(task, project)
    finally:
        session.close()
        shutil.rmtree(home, ignore_errors=True)
    status = statuses.get("status", "error")
    return {"task": task.id, "lang": task.lang, "method": method, "seed": seed, **result,
            "tokens": provider.meter.total_tokens, "cost_usd": provider.meter.cost_usd, "wall_s": round(wall, 1),
            "agents": agents,
            "status": status, "claimed_ok": status == "done",
            "provider_error": status in ("quota", "error"), "models": dict(provider.tiers)}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for method in sorted({r["method"] for r in rows}):
        good = [r for r in rows if r["method"] == method and not r["provider_error"]]
        if not good:
            continue
        out[method] = {
            "runs": len(good),
            "success": sum(r["success"] for r in good) / len(good),
            "tests_passed": sum(r["passed"] for r in good) / max(1, sum(r["total"] for r in good)),
            "tokens": statistics.mean(r["tokens"] for r in good),
            "cost_usd": statistics.mean(r.get("cost_usd") or 0 for r in good),
            "false_claims": sum(r["claimed_ok"] and not r["success"] for r in good),
        }
    return out


def save(path: Path, rows: list[dict], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**meta, "rows": rows, "summary": summarize(rows),
                                "excluded": sum(r["provider_error"] for r in rows)}, indent=1), encoding="utf-8")


def cmd_buildbench(args) -> int:
    from puppeteer_code.assistant.setup import load_keys
    from puppeteer_code.providers import ProviderSession

    load_keys()

    def make_provider():
        provider = ProviderSession(args.provider, cache_dir=args.cache)
        if args.quality_model:
            provider.tiers["quality"] = args.quality_model
        if args.fast_model:
            provider.tiers["fast"] = args.fast_model
        return provider

    out = Path(args.out)
    tasks = load_tasks(only=args.task or None)
    methods = args.methods.split(",")
    meta = {"benchmark": "PCodeBench-Build", "provider": args.provider, "methods": methods,
            "tasks": [t.id for t in tasks], "seeds": args.seeds}
    rows: list[dict] = []
    if args.resume and out.exists():
        rows = [r for r in json.loads(out.read_text(encoding="utf-8"))["rows"] if not r["provider_error"]]
    done = {(r["task"], r["method"], r["seed"]) for r in rows}
    policy = None
    if "linear" in methods:
        weights = Path(args.weights) if args.weights else None
        policy = LinearAssistantPolicy.load(weights, seed=0) if weights and weights.exists() \
            else LinearAssistantPolicy(seed=0)
        policy.greedy = not args.train
    for seed in range(args.seeds):
        for task in tasks:
            for method in methods:
                if (task.id, method, seed) in done:
                    continue
                row = run_one(task, method, seed, make_provider, policy if method == "linear" else None)
                rows.append(row)
                save(out, rows, meta)
                print(f"{task.id:14s} {method:10s} seed {seed}: {row['passed']}/{row['total']} "
                      f"{row['tokens']:>7,} tokens  {row['status']}", flush=True)
                if method == "linear" and args.train and args.weights and not row["provider_error"]:
                    policy.save(args.weights)
                if row["status"] == "quota":
                    print("Stopped at the model's quota. Rerun with --resume.", flush=True)
                    return 0
    print(json.dumps(summarize(rows), indent=1))
    return 0


def register(sub, provider_args) -> None:
    b = sub.add_parser("buildbench", help="PCodeBench-Build: build from a prompt, scored by hidden tests")
    provider_args(b)
    b.add_argument("--methods", default="heuristic,direct")
    b.add_argument("--task", action="append", help="only this task id (repeatable)")
    b.add_argument("--seeds", type=int, default=1)
    b.add_argument("--quality-model", default=None)
    b.add_argument("--fast-model", default=None)
    b.add_argument("--weights", default=None, help="LinearAssistantPolicy weights file")
    b.add_argument("--train", action="store_true", help="update and save the linear policy after each run")
    b.add_argument("--resume", action="store_true")
    b.add_argument("--out", default=str(ROOT / "runs" / "buildbench.json"))
    b.set_defaults(func=cmd_buildbench)


__all__ = ["BuildTask", "load_tasks", "score", "run_one", "summarize", "register"]
