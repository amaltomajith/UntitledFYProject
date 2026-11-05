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

from puppeteer_code.privacy import PrivacyError

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
                       budget_tokens=args.budget_tokens, explore=not args.no_explore and not session.private,
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

    from puppeteer_code import paths
    out_dir = Path(args.out) if args.out else paths.runs_dir()
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


# ── assistant ────────────────────────────────────────────────────────────────

def cmd_chat(args) -> int:
    from puppeteer_code.assistant.terminal import run_chat
    return run_chat(args)


def cmd_private_setup(args) -> int:
    from puppeteer_code.sandbox import AppContainerRunner, load_state, remove_private, setup_private

    if args.remove:
        remove_private(say=say)
        say("Private's sandbox is removed.")
        return 0
    try:
        state = setup_private(say=say)
    except OSError as e:
        say(str(e), "red")
        return 1
    runner = AppContainerRunner(load_state(), ".")
    if not runner.verify():
        say("Setup finished, but the check that commands have no network did not pass.", "red")
        say("Private will say commands are not sandboxed.", "red")
        return 1
    say(f"Done. Commands in Private now run with no network. Sandbox folder: {state['folder']}", "green")
    return 0


# ── web ──────────────────────────────────────────────────────────────────────

def cmd_serve(args) -> int:
    try:
        from puppeteer_code.server import serve
    except ImportError:
        print("The web console needs FastAPI and uvicorn: pip install fastapi uvicorn", file=sys.stderr)
        return 1
    say(f"Puppeteer-Code console: http://127.0.0.1:{args.port}/console  (Ctrl+C to stop)")
    serve(args.port, mock_solution=args.mock_solution)
    return 0


def cmd_ide(args) -> int:
    try:
        from puppeteer_code.assistant.ide import make_hub
        from puppeteer_code.server import serve
    except ImportError:
        print("The IDE needs FastAPI and uvicorn: pip install fastapi uvicorn", file=sys.stderr)
        return 1
    from puppeteer_code import paths, updates
    if not paths.site_ready():
        if paths.is_source_checkout():
            say("The IDE page is not built yet. Run: cd web && npm ci && npm run build", "red")
        else:
            say("This copy of CompactCode has no IDE page. Reinstall it with the command on "
                "https://compactcode.vercel.app", "red")
        return 1
    port = free_port(args.port)
    if port is None:
        say(f"Ports {args.port} to {args.port + 19} are all in use. Pass --port with a free one.", "red")
        return 1
    if port != args.port:
        say(f"Port {args.port} is in use, so CompactCode uses {port}.")
    hub = make_hub(args.provider, args.mode, args.cache, args.effort, args.private)
    if args.folder:
        hub.open(args.folder)
    url = f"http://127.0.0.1:{port}/ide#token={hub.token}"
    say(f"CompactCode IDE: {url}  (Ctrl+C to stop)")
    line = updates.notice(private=args.provider == "local")
    if line:
        say(line, "dim")
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.0, webbrowser.open, [url]).start()
    try:
        serve(port, ide=hub)
    finally:
        hub.close()
    return 0


def free_port(start: int, tries: int = 20):
    """The first port from `start` that nothing on 127.0.0.1 is listening on, or None."""
    import socket
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return None


def cmd_update(args) -> int:
    from puppeteer_code import __version__, updates
    say(f"CompactCode {__version__}. Checking GitHub for a newer release...")
    s = updates.check(force=True)
    if s["error"]:
        say(s["error"], "red")
        return 1
    if not s["available"]:
        say(f"You have the latest version ({s['current']}).", "green")
        return 0
    say(f"CompactCode {s['latest']} is available.", "green")
    if s["notes"]:
        say(s["notes"].strip()[:1200])
    if args.check:
        say(f"Install it with: {updates.UPDATE_COMMAND}")
        return 0
    if s["kind"] == "source":
        say("This copy runs from a git checkout. Update it with: git pull", "red")
        return 1
    if not args.yes:
        try:
            if input(f"Install {s['latest']} now? [y/N] ").strip().lower() not in ("y", "yes"):
                say("Not installed.")
                return 0
        except EOFError:
            say("Not installed (no answer). Pass --yes to skip the question.")
            return 1
    try:
        info = updates.apply(relaunch=False)
    except updates.UpdateError as e:
        say(str(e), "red")
        return 1
    say(f"Downloaded and verified {info['to']}. It installs as soon as this command exits.", "green")
    say(f"Log: {info['log']}. Then type compactcode.")
    return 0


def cmd_export_web(args) -> int:
    from puppeteer_code.server import export_web
    info = export_web(Path(args.runs), Path(args.out))
    say(f"Wrote {info['benchmarks']} fix benchmark(s), {info.get('build', 0)} build run(s), "
        f"{info.get('reasoning', 0)} reasoning run(s), {info['training']} training run(s) to {args.out}")
    say(f"Demo run: {info['demo_run'] or 'none yet (needs a successful fix on a real provider)'}")
    return 0


# ── parser ───────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    from puppeteer_code import __version__
    p = argparse.ArgumentParser(prog="pcode", description="CompactCode (Puppeteer-Code): RL-orchestrated coding agents")
    p.add_argument("--version", action="version", version=f"CompactCode {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def provider_args(sp):
        sp.add_argument("--provider", default=None, help="nebius (default) | groq | local | mock")
        sp.add_argument("--private", action="store_true", help="run on this computer only, on Ollama")
        sp.add_argument("--cache", default=None, help="directory for the on-disk response cache")

    c = sub.add_parser("chat", help="talk to the assistant in a folder (the default command)")
    provider_args(c)
    c.add_argument("--folder", default=".")
    c.add_argument("--mode", default="ask", choices=["plan", "ask", "auto"],
                   help="plan changes nothing until you approve; ask checks before commands; auto does not")
    c.add_argument("--effort", default="normal", choices=["fast", "normal", "think", "deep"])
    c.add_argument("--resume", default=None, help="id of an earlier conversation")
    c.set_defaults(func=cmd_chat)

    ps = sub.add_parser("private-setup", help="set up the network block for commands in Private (Windows)")
    ps.add_argument("--remove", action="store_true", help="undo it")
    ps.set_defaults(func=cmd_private_setup)

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
    f.add_argument("--out", default=None, help="where to save the run (default: runs/ in a checkout, else ~/.pcode/runs)")
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

    sv = sub.add_parser("serve", help="run the local web console on 127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--mock-solution", default=None, help="(mock provider) edit-block file to return")
    sv.set_defaults(func=cmd_serve)

    id_ = sub.add_parser("ide", help="open the assistant in your browser")
    provider_args(id_)
    id_.add_argument("--folder", default=None, help="open this folder straight away")
    id_.add_argument("--port", type=int, default=8765)
    id_.add_argument("--mode", default="ask", choices=["plan", "ask", "auto"],
                     help="plan changes nothing until you approve; ask checks before commands; auto does not")
    id_.add_argument("--effort", default="normal", choices=["fast", "normal", "think", "deep"])
    id_.add_argument("--no-browser", action="store_true", help="print the link but do not open it")
    id_.set_defaults(func=cmd_ide)

    up = sub.add_parser("update", help="check for a newer CompactCode and install it")
    up.add_argument("--check", action="store_true", help="only say whether one is available")
    up.add_argument("--yes", "-y", action="store_true", help="install without asking")
    up.set_defaults(func=cmd_update)

    ex = sub.add_parser("export-web", help="write real results to web/public/data for the hosted site")
    ex.add_argument("--runs", default=str(ROOT / "runs"))
    ex.add_argument("--out", default=str(ROOT / "web" / "public" / "data"))
    ex.set_defaults(func=cmd_export_web)

    try:
        from puppeteer_code import bench as _bench
        _bench.register(sub, provider_args)
    except ImportError:
        pass
    from puppeteer_code.assistant import buildbench as _buildbench
    _buildbench.register(sub, provider_args)
    return p


def _plain_folder(folder: Path, home: Path | None = None) -> bool:
    """True for a folder worth opening as a project: not the home folder, a drive root or a system folder."""
    import os
    home = (home or Path.home()).resolve()
    folder = folder.resolve()
    if folder == home or folder.parent == folder:
        return False
    system = [Path(os.environ[k]).resolve() for k in ("SYSTEMROOT", "WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)")
              if os.environ.get(k)]
    system += [Path(p) for p in ("/usr", "/bin", "/sbin", "/etc", "/System", "/Library")]
    return not any(folder == s or s in folder.parents for s in system if s.parent != s)


def launch_argv(argv: list[str], cwd: Path, home: Path | None = None) -> list[str]:
    """`compactcode` opens the browser IDE; `compactcode <command> ...` is the same as `pcode <command> ...`."""
    commands = set(build_parser()._subparsers._group_actions[0].choices)          # noqa: SLF001
    if argv and (argv[0] in commands or argv[0] in ("-h", "--help", "--version")):
        return argv
    out = ["ide", *argv]
    if "--folder" not in argv and _plain_folder(cwd, home):
        out += ["--folder", str(cwd)]
    return out


def launch(argv=None) -> int:
    """Entry point of the `compactcode` command."""
    argv = sys.argv[1:] if argv is None else list(argv)
    return main(launch_argv(argv, Path.cwd()))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "chat")
    args = build_parser().parse_args(argv)
    if getattr(args, "private", False):
        if args.provider not in (None, "local"):
            say(f"--private cannot use --provider {args.provider}.", "red")
            return 1
        args.provider = "local"
    try:
        return args.func(args)
    except PrivacyError as e:
        say(str(e), "red")
        return 1
    except EnvironmentError as e:
        if "API_KEY" not in str(e):
            raise
        say(str(e), "red")
        return 1


if __name__ == "__main__":
    sys.exit(main())
