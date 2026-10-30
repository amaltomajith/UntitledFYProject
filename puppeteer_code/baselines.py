"""
puppeteer_code/baselines.py
===========================
Non-orchestrated baselines, run in the same sandbox and scored by the same
final test run (+ hidden tests) as Puppeteer-Code:

  single_shot  one QUALITY-model call with every source file and the failing
               test digest; edits applied once.  No feedback loop.
  react        one QUALITY model in a loop with a GROWING transcript: every
               turn re-sends the whole conversation plus the latest test
               output.  No rollback, no integrity checkpoints, no routing.
               This is the "transcript agent" that bounded context (B) is
               measured against: its prompt size grows every turn.
  react_tools  the standard tool-using agent: starts from a file list and must
               READ / GREP before editing; the transcript keeps everything read.
"""

from __future__ import annotations

import json
import re
import time

from core.environment import EnvironmentConfig
from puppeteer_code.agents import EDIT_FORMAT, CoderAgent
from puppeteer_code.environment import CodeEnvironment
from puppeteer_code.integrity import is_test_path
from puppeteer_code.tools import apply_edits, grep, parse_edit_blocks

SOURCE_BUDGET = 14000


def _repo_dump(env: CodeEnvironment, budget: int = SOURCE_BUDGET) -> str:
    parts, used = [], 0
    for rel in env.ws.python_files():
        if is_test_path(rel):
            continue
        block = f"### {rel}\n{env.ws.read(rel)}"
        if used + len(block) > budget:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def _result(method: str, env: CodeEnvironment, final: dict, steps: list[dict], session,
            usage_before: dict, t0: float) -> dict:
    after = session.meter.snapshot()
    tokens = after["total_tokens"] - usage_before["total_tokens"]
    cost = None
    if after["cost_usd"] is not None and usage_before["cost_usd"] is not None:
        cost = after["cost_usd"] - usage_before["cost_usd"]
    return {"policy": method, "status": "completed", "success": final["success"], "steps": steps,
            "final": final, "usage": {"total_tokens": tokens, "cost_usd": cost},
            "wall_s": round(time.monotonic() - t0, 2), "rewards": {}}


def single_shot(session, repo, task: str, holdout=None, test_timeout: int = 120) -> dict:
    t0 = time.monotonic()
    before = session.meter.snapshot()
    env = CodeEnvironment(EnvironmentConfig(task=task, max_steps=1), repo, test_timeout=test_timeout)
    try:
        model = session.model_for("CoderAgent")
        s = session.settings_for("CoderAgent")
        prompt = (f"## TASK\n{task}\n\n## TEST STATUS\n{env.state.baseline.digest()}\n\n"
                  f"## SOURCE FILES\n{_repo_dump(env)}\n\n## YOUR JOB\n{EDIT_FORMAT}")
        resp = session.client.chat(model=model, temperature=s.temperature, max_tokens=s.max_tokens,
                                   messages=[{"role": "system", "content": CoderAgent.ROLE},
                                             {"role": "user", "content": prompt}])
        # Applied as-is: baselines get no checkpoint/rollback protection.
        res = apply_edits(env.ws, parse_edit_blocks(resp["content"] or ""))
        steps = [{"step": 0, "agent": "SingleShot", "tokens": resp["usage"]["total_tokens"],
                  "prompt_chars": len(prompt), "content": "applied" if res.ok else "; ".join(res.errors)}]
        final = env.finalize(holdout)
        return _result("single-shot", env, final, steps, session, before, t0)
    finally:
        env.close()


def react(session, repo, task: str, holdout=None, max_turns: int = 6, test_timeout: int = 120) -> dict:
    t0 = time.monotonic()
    before = session.meter.snapshot()
    env = CodeEnvironment(EnvironmentConfig(task=task, max_steps=max_turns), repo, test_timeout=test_timeout)
    try:
        model = session.model_for("CoderAgent")
        s = session.settings_for("CoderAgent")
        messages = [{"role": "system", "content": CoderAgent.ROLE},
                    {"role": "user", "content": (
                        f"## TASK\n{task}\n\n## TEST STATUS\n{env.state.baseline.digest()}\n\n"
                        f"## SOURCE FILES\n{_repo_dump(env)}\n\n## YOUR JOB\n{EDIT_FORMAT}\n"
                        "After each edit you will see the new test results. Reply DONE when all "
                        "tests pass.")}]
        steps = []
        for turn in range(max_turns):
            prompt_chars = sum(len(m["content"]) for m in messages)
            resp = session.client.chat(model=model, temperature=s.temperature, max_tokens=s.max_tokens,
                                       messages=messages)
            text = resp["content"] or ""
            messages.append({"role": "assistant", "content": text})
            if text.strip().upper().startswith("DONE"):
                steps.append({"step": turn, "agent": "ReAct", "tokens": resp["usage"]["total_tokens"],
                              "prompt_chars": prompt_chars, "content": "DONE"})
                break
            res = apply_edits(env.ws, parse_edit_blocks(text))
            tests = env.ws.run_tests(timeout=test_timeout)
            feedback = ("Edit applied." if res.ok else "Edit NOT applied:\n- " + "\n- ".join(res.errors))
            messages.append({"role": "user", "content": f"{feedback}\n\n## TEST STATUS\n{tests.digest()}"})
            steps.append({"step": turn, "agent": "ReAct", "tokens": resp["usage"]["total_tokens"],
                          "prompt_chars": prompt_chars, "pass_rate": tests.pass_rate,
                          "content": feedback.splitlines()[0]})
            if tests.all_green:
                break
        env.state.last_tests = env.ws.run_tests(timeout=test_timeout)
        final = env.finalize(holdout)
        return _result("react", env, final, steps, session, before, t0)
    finally:
        env.close()


TOOL_PROTOCOL = """\
You can act with exactly one of these per reply:
  READ: path/to/file.py          show a file (the 'NNNN| ' line numbers are not part of it)
  GREP: regex                    search the source files
  (edit blocks)                  change code; the tests run afterwards
  DONE                           finish (only when all tests pass)
"""
_READ_RE = re.compile(r"^\s*READ:\s*`?([^\s`]+)`?\s*$", re.M)
_GREP_RE = re.compile(r"^\s*GREP:\s*(.+?)\s*$", re.M)


def react_tools(session, repo, task: str, holdout=None, max_turns: int = 10, test_timeout: int = 120) -> dict:
    """
    The standard tool-using agent: it starts from the task, the failing tests and a file
    list, and must READ / GREP its way to the code.  Everything it reads stays in its
    transcript, so context grows with every turn (the fair "ReAct" comparison for
    repositories too large to paste whole).
    """
    t0 = time.monotonic()
    before = session.meter.snapshot()
    env = CodeEnvironment(EnvironmentConfig(task=task, max_steps=max_turns), repo, test_timeout=test_timeout)
    try:
        model = session.model_for("CoderAgent")
        s = session.settings_for("CoderAgent")
        listing = "\n".join(f"{rel} ({len(env.ws.read(rel).splitlines())} lines)"
                            for rel in env.ws.python_files() if not is_test_path(rel))
        messages = [{"role": "system", "content": CoderAgent.ROLE},
                    {"role": "user", "content": (
                        f"## TASK\n{task}\n\n## TEST STATUS\n{env.state.baseline.digest()}\n\n"
                        f"## SOURCE FILES\n{listing}\n\n## TOOLS\n{TOOL_PROTOCOL}\n## EDIT FORMAT\n{EDIT_FORMAT}")}]
        steps = []
        for turn in range(max_turns):
            prompt_chars = sum(len(m["content"]) for m in messages)
            resp = session.client.chat(model=model, temperature=s.temperature, max_tokens=s.max_tokens,
                                       messages=messages)
            text = _native_tool_call(resp["content"] or "")
            messages.append({"role": "assistant", "content": text})
            step = {"step": turn, "agent": "ReActTools", "tokens": resp["usage"]["total_tokens"],
                    "prompt_chars": prompt_chars}
            blocks = parse_edit_blocks(text)
            if blocks:
                res = apply_edits(env.ws, blocks)
                tests = env.ws.run_tests(timeout=test_timeout)
                feedback = "Edit applied." if res.ok else "Edit NOT applied:\n- " + "\n- ".join(res.errors)
                observation = f"{feedback}\n\n## TEST STATUS\n{tests.digest()}"
                step.update(pass_rate=tests.pass_rate, content=feedback.splitlines()[0])
                done = tests.all_green
            elif text.strip().upper().startswith("DONE"):
                steps.append({**step, "content": "DONE"})
                break
            elif _READ_RE.search(text) or _GREP_RE.search(text):
                observation, done = _observe(env, text), False
                step["content"] = text.strip().splitlines()[0][:120]
            else:
                observation, done = "No action recognised.\n" + TOOL_PROTOCOL, False
                step["content"] = "no action"
            steps.append(step)
            if done:
                break
            messages.append({"role": "user", "content": observation})
        env.state.last_tests = env.ws.run_tests(timeout=test_timeout)
        final = env.finalize(holdout)
        return _result("react-tools", env, final, steps, session, before, t0)
    finally:
        env.close()


def _native_tool_call(text: str) -> str:
    """
    gpt-oss may answer with a call to its built-in browser tool, e.g.
    {"name": "repo_browser.open_file", "arguments": {"path": "rpn.py"}}.  Read it as
    the equivalent READ / GREP instead of penalising the baseline for it.
    """
    try:
        call = json.loads(text.strip())
    except ValueError:
        return text
    args = call.get("arguments", call) if isinstance(call, dict) else None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return text
    if not isinstance(args, dict):
        return text
    if isinstance(args.get("patch"), str):
        return _v4a_to_edit_blocks(args["patch"]) or text
    if args.get("path"):
        return f"READ: {args['path']}"
    pattern = args.get("pattern") or args.get("query")
    return f"GREP: {pattern}" if pattern else text


def _v4a_to_edit_blocks(patch: str) -> str:
    """
    Convert an `*** Begin Patch` / `*** Update File:` / `@@` patch (the format of
    gpt-oss's built-in apply_patch tool) into SEARCH/REPLACE blocks, one per hunk.
    """
    out: list[str] = []
    file, minus, plus = None, [], []

    def flush():
        if file and any(ln.strip() for ln in minus):
            out.append(f"FILE: {file}\n<<<<<<< SEARCH\n" + "\n".join(minus) + "\n=======\n"
                       + "\n".join(plus) + "\n>>>>>>> REPLACE\n")

    for line in patch.splitlines():
        if line.startswith("*** Update File:"):
            flush()
            file, minus, plus = line.split(":", 1)[1].strip(), [], []
        elif line.startswith("@@") or line.startswith("***"):
            flush()
            minus, plus = [], []
        elif line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
        elif line.startswith(" ") or not line:
            minus.append(line[1:])
            plus.append(line[1:])
    flush()
    return "\n".join(out)


def _observe(env: CodeEnvironment, text: str) -> str:
    out = []
    for path in _READ_RE.findall(text)[:3]:
        try:
            lines = env.ws.read(path).splitlines()
            out.append(f"### {path}\n" + "\n".join(f"{i:>4}| {ln}" for i, ln in enumerate(lines, 1)))
        except (OSError, PermissionError) as e:
            out.append(f"### {path}\nerror: {e}")
    for pattern in _GREP_RE.findall(text)[:2]:
        out.append(f"### grep {pattern}\n" + ("\n".join(grep(env.ws, pattern)) or "(no matches)"))
    return "\n\n".join(out)
