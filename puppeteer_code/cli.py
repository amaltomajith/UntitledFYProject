"""
puppeteer_code/cli.py
=====================
Command-line interface.

  pcode models  [--provider nebius]            live catalog + which tier candidates resolve
  pcode fix "TASK" --repo PATH [options]       run one episode in a sandbox; --apply to keep it
  pcode replay runs/<file>.json                step through a saved run
  pcode train / bench / report                 see `pcode <cmd> -h`

Run as `python -m puppeteer_code ...` (or `pcode` once installed).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:   # bars and arrows must not crash a cp1252 Windows console or a pipe
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

try:
    from rich.console import Console
    _console = Console(highlight=False, soft_wrap=True)
except ImportError:  # rich is optional
    _console = None

ROOT = Path(__file__).resolve().parent.parent


def say(msg: str = "", style: str = "") -> None:
    if _console:
        _console.print(msg, style=style or None, markup=False)
    else:
        print(msg)


def _bar(rate: float, width: int = 20) -> str:
    n = int(round(rate * width))
    return "█" * n + "░" * (width - n)


def make_session(args):
    from puppeteer_code.providers import ProviderSession
    responder = None
    if args.provider == "mock":
        from puppeteer_code.mock import scripted_responder
        solution = Path(args.mock_solution).read_text(encoding="utf-8") if getattr(args, "mock_solution", None) else None
        responder = scripted_responder(solution=solution)
    return ProviderSession(args.provider, responder=responder, cache_dir=getattr(args, "cache", None))


# ── models ───────────────────────────────────────────────────────────────────

def cmd_models(args) -> int:
    session = make_session(args)
    rows = session.candidate_report()
    if rows and "error" in rows[0]:
        say(f"Could not list models for {session.provider}: {rows[0]['error']}", "red")
        return 1
    say(f"Provider: {session.provider}   resolved tiers: {session.tiers}")
    for r in rows:
        mark = "✓" if r["served"] else "✗"
        sel = "  ← selected" if r["selected"] else ""
        say(f"  {mark} {r['tier']:<8} {r['model']}{sel}")
    if args.all:
        say("\nFull catalog:")
        for m in session.client.list_models():
            say(f"  {m}")
    return 0


# ── fix ──────────────────────────────────────────────────────────────────────

class Printer:
    """Plain streaming view of an episode (the live dashboard is `--ui`)."""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def __call__(self, e: dict) -> None:
        if self.quiet:
            return
        t = e["type"]
        if t == "start":
            b = e["baseline"]
            say(f"Baseline: {b['passed']}/{b['total']} tests passing  {_bar(b['pass_rate'])}")
        elif t == "step":
            verdict = e.get("verdict") or {}
            tag = f" [{verdict['action']}]" if verdict else ""
            tokens = f"{e['tokens']:>5} tok" if e["tokens"] else "    0 tok"
            first = e["content"].strip().splitlines()[0][:70] if e["content"].strip() else e.get("error", "")
            say(f"{e['step'] + 1:>2}. {e['agent']:<16} {e['model'][:28]:<28} {tokens}  "
                f"{_bar(e['best_pass_rate'], 10)}{tag}  {first}")


def apply_diff(repo: Path, diff: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8", newline="") as f:
        f.write(diff)
        patch = f.name
    try:
        check = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn", patch], cwd=repo,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if check.returncode != 0:
            return False, check.stderr.strip()
        proc = subprocess.run(["git", "apply", "--whitespace=nowarn", patch], cwd=repo,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return proc.returncode == 0, proc.stderr.strip()
    finally:
        Path(patch).unlink(missing_ok=True)


def cmd_fix(args) -> int:
    from puppeteer_code.engine import CodeOrchestrator, RunConfig
    from puppeteer_code.policy import make_policy

    repo = Path(args.repo).resolve()
    session = make_session(args)
    policy = make_policy(args.policy, session=session, weights=args.weights, seed=args.seed)
    config = RunConfig(max_steps=args.max_steps, tdd=args.tdd, budget_usd=args.budget_usd,
                       budget_tokens=args.budget_tokens, explore=not args.no_explore,
                       test_timeout=args.test_timeout)
    say(f"Puppeteer-Code  provider={session.provider}  policy={policy.name}  "
        f"models={session.tiers}")
    say(f"Sandbox copy of {repo} (your files are not touched)")
    result = CodeOrchestrator(session, policy=policy, config=config,
                              on_event=Printer(args.quiet)).run(repo, args.task)

    final = result.final
    ft = final["final_tests"]
    files, added, removed = final["diff_stat"]
    cost = result.usage.get("cost_usd")
    say("")
    say(f"Status: {result.status}   success: {result.success}   tests: {ft['passed']}/{ft['total']}   "
        f"diff: {files} file(s) +{added}/-{removed}   tokens: {result.usage['total_tokens']}   "
        f"cost: {'n/a' if cost is None else f'${cost:.4f}'}   time: {result.wall_s}s",
        "bold green" if result.success else "bold yellow")
    if final["regressions"]:
        say(f"Regressions: {final['regressions']}", "red")
    if final["integrity_violations"]:
        say(f"Integrity violations: {final['integrity_violations']}", "red")

    out_dir = Path(args.out)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = result.save(out_dir / f"fix-{stamp}.json")
    say(f"Run saved to {path}")

    if not result.diff.strip():
        say("No changes were verified.")
        return 0 if result.success else 2
    say("\n" + result.diff)
    if args.apply:
        if not args.yes:
            answer = input(f"Apply this diff to {repo}? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                say("Not applied.")
                return 0
        ok, err = apply_diff(repo, result.diff)
        say("Applied." if ok else f"git apply failed: {err}", "green" if ok else "red")
        return 0 if ok else 1
    say("Re-run with --apply to apply the verified diff to your working tree.")
    return 0 if result.success else 2


# ── replay ───────────────────────────────────────────────────────────────────

def cmd_replay(args) -> int:
    data = json.loads(Path(args.run).read_text(encoding="utf-8"))
    say(f"Task: {data['task']}\nPolicy: {data['policy']}   status: {data['status']}   "
        f"success: {data['success']}")
    pr = Printer()
    for s in data["steps"]:
        pr({"type": "step", **s})
        if args.verbose:
            for line in s["content"].splitlines()[1:12]:
                say(f"      {line}")
    r = data["rewards"]
    say(f"\nterminal reward r={r.get('terminal_reward', 0):.3f}  return R0={r.get('total_return', 0):.3f}  "
        f"tokens={data['usage'].get('total_tokens')}")
    if args.diff:
        say("\n" + data["final"].get("diff", ""))
    return 0


# ── mcp ──────────────────────────────────────────────────────────────────────

def cmd_mcp(args) -> int:
    try:
        from puppeteer_code.mcp_server import main as serve
    except ImportError:
        print("The MCP server needs the optional `mcp` package: pip install mcp", file=sys.stderr)
        return 1
    serve()
    return 0


# ── parser ───────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pcode", description="Puppeteer-Code: RL-orchestrated coding agents")
    sub = p.add_subparsers(dest="cmd", required=True)

    def provider_args(sp):
        sp.add_argument("--provider", default=None, help="nebius (default) | groq | mock")
        sp.add_argument("--cache", default=None, help="directory for the on-disk response cache")

    m = sub.add_parser("models", help="show the provider catalog and resolved tiers")
    provider_args(m)
    m.add_argument("--all", action="store_true", help="also print the full catalog")
    m.set_defaults(func=cmd_models)

    f = sub.add_parser("fix", help="fix a failing test suite / implement a task in a sandbox")
    f.add_argument("task")
    f.add_argument("--repo", default=".")
    provider_args(f)
    f.add_argument("--policy", default="heuristic", choices=["heuristic", "linear", "llm", "fixed", "random"])
    f.add_argument("--weights", default=str(ROOT / "checkpoints" / "pcode_linear.json"))
    f.add_argument("--max-steps", type=int, default=12)
    f.add_argument("--tdd", action="store_true", help="let TestWriterAgent write reproducing tests first")
    f.add_argument("--no-explore", action="store_true", help="disable parallel worktree exploration")
    f.add_argument("--budget-usd", type=float, default=None)
    f.add_argument("--budget-tokens", type=int, default=None)
    f.add_argument("--test-timeout", type=int, default=120)
    f.add_argument("--seed", type=int, default=None)
    f.add_argument("--out", default=str(ROOT / "runs"))
    f.add_argument("--apply", action="store_true", help="apply the verified diff to --repo (asks first)")
    f.add_argument("--yes", action="store_true", help="with --apply: do not ask for confirmation")
    f.add_argument("--quiet", action="store_true")
    f.add_argument("--mock-solution", default=None, help="(mock provider) edit-block file to return")
    f.set_defaults(func=cmd_fix)

    r = sub.add_parser("replay", help="step through a saved run")
    r.add_argument("run")
    r.add_argument("--diff", action="store_true")
    r.add_argument("--verbose", "-v", action="store_true")
    r.set_defaults(func=cmd_replay)

    mc = sub.add_parser("mcp", help="run as an MCP server (stdio) for Claude Code, Cursor, OpenCode")
    mc.set_defaults(func=cmd_mcp)

    try:
        from puppeteer_code import bench as _bench
        _bench.register(sub, provider_args)
    except ImportError:
        pass
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except EnvironmentError as e:
        if "API_KEY" not in str(e):
            raise
        say(str(e), "red")
        return 1


if __name__ == "__main__":
    sys.exit(main())
