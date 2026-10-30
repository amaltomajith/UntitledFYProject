"""
puppeteer_code/mcp_server.py
============================
Model Context Protocol server: exposes Puppeteer-Code as a tool to any MCP
client (Claude Code, Cursor, OpenCode, ...), so a big assistant can hand a
failing test suite to a cheap, sandboxed, test-verified sub-agent.

    python -m puppeteer_code mcp          # stdio transport
    claude mcp add puppeteer-code -- python -m puppeteer_code mcp

Tools
  puppeteer_fix     run one episode in a sandbox copy; returns the verified diff
  puppeteer_models  show which provider models the tiers resolve to

The repository is never modified unless `apply=true` is passed explicitly.
Requires the optional `mcp` package (pip install mcp).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from puppeteer_code.engine import CodeOrchestrator, RunConfig
from puppeteer_code.policy import make_policy
from puppeteer_code.providers import ProviderSession

# stdout carries the protocol; everything else goes to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

server = FastMCP("puppeteer-code")
DEFAULT_WEIGHTS = Path(__file__).resolve().parent.parent / "checkpoints" / "pcode_linear.json"


def _summary(result) -> str:
    final = result.final
    ft = final["final_tests"]
    files, added, removed = final["diff_stat"]
    cost = result.usage.get("cost_usd")
    lines = [
        f"status: {result.status}   success: {result.success}",
        f"tests: {ft['passed']}/{ft['total']} passing (baseline "
        f"{final['baseline_tests']['passed']}/{final['baseline_tests']['total']})",
        f"diff: {files} file(s), +{added}/-{removed}",
        f"tokens: {result.usage['total_tokens']}   cost: {'n/a' if cost is None else f'${cost:.4f}'}",
        "agents: " + " -> ".join(s["agent"].replace("Agent", "") for s in result.steps),
    ]
    if final["regressions"]:
        lines.append(f"regressions: {final['regressions']}")
    if final["integrity_violations"]:
        lines.append(f"integrity violations: {final['integrity_violations']}")
    return "\n".join(lines)


@server.tool()
async def puppeteer_fix(task: str, repo: str, policy: str = "heuristic", max_steps: int = 12,
                        provider: Optional[str] = None, tdd: bool = False, apply: bool = False) -> str:
    """
    Fix a bug or implement a small change in a Python repository, verified by its pytest suite.

    Runs a multi-agent Puppeteer episode (scout, coder, test runner, debugger, ...) in a
    throw-away git sandbox copy of `repo`. Only edits that improve the tests survive;
    regressions are rolled back and test files are protected. Returns a summary and the
    verified unified diff.

    Args:
        task: the bug report or change request, in plain language.
        repo: absolute path to the repository root (must contain the tests).
        policy: agent-selection policy: heuristic | linear | llm | fixed.
        max_steps: maximum agent activations.
        provider: nebius | groq (default from models.yaml).
        tdd: let TestWriterAgent write a reproducing test first.
        apply: if true, apply the verified diff to `repo` with `git apply`. Default false.
    """
    path = Path(repo).expanduser().resolve()
    if not path.is_dir():
        return f"error: repository not found: {path}"
    try:
        session = ProviderSession(provider)
    except EnvironmentError as e:
        return f"error: {e}"
    pol = make_policy(policy, session=session, weights=str(DEFAULT_WEIGHTS))
    orch = CodeOrchestrator(session, policy=pol, config=RunConfig(max_steps=max_steps, tdd=tdd))
    result = await orch.arun(path, task)
    text = _summary(result)
    diff = result.diff
    if apply and diff.strip() and result.success:
        from puppeteer_code.cli import apply_diff
        ok, err = apply_diff(path, diff)
        text += "\napplied to the repository" if ok else f"\ngit apply failed: {err}"
    elif apply:
        text += "\nnot applied (no verified successful change)"
    return text + ("\n\n" + diff if diff.strip() else "\n\nno verified changes")


@server.tool()
def puppeteer_models(provider: Optional[str] = None) -> str:
    """Show the configured provider, the models each tier resolves to, and which candidates are served."""
    try:
        session = ProviderSession(provider)
    except EnvironmentError as e:
        return f"error: {e}"
    rows = session.candidate_report()
    if rows and "error" in rows[0]:
        return f"provider {session.provider}: could not list models ({rows[0]['error']})"
    out = [f"provider: {session.provider}   tiers: {session.tiers}"]
    out += [f"{'served' if r['served'] else 'missing':<8} {r['tier']:<8} {r['model']}"
            f"{'  (selected)' if r['selected'] else ''}" for r in rows]
    return "\n".join(out)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
