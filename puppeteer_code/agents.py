"""
puppeteer_code/agents.py
========================
The Puppeteer-Code agent pool.  Every agent is a core BaseAgent (m, r, t)
so the Puppeteer++ machinery (Memory, AgentRegistry, rewards) applies
unchanged, but agents act on a CodeEnvironment instead of a transcript.

Zero-token tool agents (tier "none")
  RepoScoutAgent   AST ranking of the symbols behind the failures
  TestRunnerAgent  runs pytest in the sandbox; triggers checkpoint/rollback
  LinterAgent      ruff (or a syntax check) on the changed files
  DocsSearchAgent  Tavily search when TAVILY_API_KEY is set, else pydoc

LLM agents (tier from models.yaml)
  PlannerAgent     FAST     short plan, at most once, before the first edit
  CoderAgent       QUALITY  SEARCH/REPLACE edits from the snapshot
  DebuggerAgent    QUALITY  edits driven by the failing traceback + rollbacks
  ReviewerAgent    FAST     reviews the diff for correctness / blast radius
  TestWriterAgent  QUALITY  writes reproducing tests (only agent allowed to
                            touch test files; injected for TDD tasks)
  ConcluderAgent   FAST     summarises the verified change; ends the episode
  TerminatorAgent  FAST     decides to give up when progress has stalled

Each LLM agent sees only `env.snapshot(name).render(budget, order)`: the
bounded Markovian context.  No agent ever sees another agent's messages.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

from core.agent import AgentConfig, AgentOutput, BaseAgent
from puppeteer_code.integrity import is_test_path
from puppeteer_code.tools import apply_edits, parse_edit_blocks

logger = logging.getLogger(__name__)


EDIT_FORMAT = """\
Reply with one or more edit blocks in exactly this format:

FILE: path/to/file.py
<<<<<<< SEARCH
exact lines copied from the CODE section
=======
replacement lines
>>>>>>> REPLACE

Rules:
- SEARCH must match the current file exactly (copy it from CODE) and occur once.
- Make the smallest change that fixes the problem; keep the callers working.
- Never edit, delete or skip tests, and never special-case test inputs.
- To create a new file, leave the SEARCH section empty.
- Do not repeat an approach listed under ROLLED-BACK ATTEMPTS."""


def _output(agent: str, content: str, *, tokens: int = 0, model: str = "none",
            reasoning: str = "", raw: str = "", latency_ms: float = 0.0,
            success: bool = True, error: str = "", **meta) -> AgentOutput:
    return AgentOutput(
        agent_name=agent, content=content, reasoning=reasoning, raw_response=raw or content,
        tokens_used=tokens, latency_ms=latency_ms, metadata={"model_used": model, **meta},
        success=success, error=error,
    )


class CodeAgent(BaseAgent):
    """Base for all Puppeteer-Code agents: built from a ProviderSession."""

    ROLE = ""
    DESCRIPTION = ""
    TOOLS: list[str] = []

    def __init__(self, session):
        name = self.__class__.__name__
        s = session.settings_for(name)
        config = AgentConfig(
            model_name=session.model_for(name), role_prompt=self.ROLE, tools=list(self.TOOLS),
            name=name, description=self.DESCRIPTION, temperature=s.temperature,
            max_tokens=s.max_tokens,
        )
        super().__init__(config, session.client)
        self.session = session

    @property
    def zero_token(self) -> bool:
        return self.config.model_name == "none"

    def _build_action_prompt(self, task: str, context: str) -> str:
        return context

    def run(self, env) -> AgentOutput:
        raise NotImplementedError

    def execute(self, task: str, context: str = "") -> AgentOutput:
        raise TypeError(f"{self.name} acts on a CodeEnvironment; call run(env)")


# ═════════════════════════════════════════════════════════════════════════════
# zero-token tool agents
# ═════════════════════════════════════════════════════════════════════════════

class RepoScoutAgent(CodeAgent):
    DESCRIPTION = "Zero-token AST scout: finds the functions behind the failing tests and their callers."
    TOOLS = ["ast_index"]
    K = 4

    def run(self, env) -> AgentOutput:
        st = env.state
        env.refresh_index()
        ranked = env.index.rank(st.task, st.last_tests.frames(), st.last_tests.failing, k=self.K)
        st.scout_keys = [s.key for s, _, _ in ranked]
        test_keys = []
        for nodeid in sorted(st.last_tests.failing)[:2]:
            name = nodeid.split("::")[-1].split("[")[0]
            for sym in env.index.by_name.get(name, []):
                if sym.is_test and sym.key not in test_keys:
                    test_keys.append(sym.key)
        st.test_keys = test_keys[:2]
        st.scouted += 1
        st.scouted_at_tests = st.tests_run
        lines = [f"{s.header()}  score={score:.1f}  ({'; '.join(why[:2])})" for s, score, why in ranked]
        content = "\n".join(lines) if lines else "no relevant symbols found"
        return _output(self.name, content, symbols=st.scout_keys)


class TestRunnerAgent(CodeAgent):
    __test__ = False
    DESCRIPTION = "Zero-token: runs the test suite; improvements are checkpointed, regressions rolled back."
    TOOLS = ["pytest"]

    def run(self, env) -> AgentOutput:
        st = env.state
        result = env.ws.run_tests(timeout=env.test_timeout)
        verdict = env.record_test_result(result, env.current_step)
        content = (f"{verdict['action'].upper()}: {len(result.passed)}/{result.total} passing"
                   + (f" ({verdict['reason']})" if verdict["reason"] else ""))
        return _output(self.name, content, raw=result.digest(), verdict=verdict,
                       pass_rate=result.pass_rate, best_pass_rate=st.best_pass_rate)


class LinterAgent(CodeAgent):
    DESCRIPTION = "Zero-token: lints the changed files (ruff if installed)."
    TOOLS = ["ruff"]

    def run(self, env) -> AgentOutput:
        st = env.state
        lint = env.ws.run_lint(env.ws.changed_files())
        st.lint = lint
        st.linted_since_edit = True
        if lint.clean:
            content = "lint clean" if lint.available else "syntax ok (ruff not installed)"
        else:
            content = f"{len(lint.issues)} lint issue(s):\n" + "\n".join(lint.issues[:10])
        return _output(self.name, content, lint_clean=lint.clean)


_UNKNOWN_API_RE = re.compile(
    r"(?:No module named '([\w.]+)'|cannot import name '(\w+)' from '([\w.]+)'"
    r"|module '([\w.]+)' has no attribute '(\w+)'|'(\w+)' object has no attribute '(\w+)')")


class DocsSearchAgent(CodeAgent):
    """
    Looks up documentation for the API the failure points at.

    With TAVILY_API_KEY set it calls the Tavily search REST API (only the
    failure message and task text are sent — never source code); otherwise
    it falls back to local `pydoc` for installed modules.
    """

    DESCRIPTION = "Zero-token: looks up documentation for unknown APIs / import errors (Tavily or pydoc)."
    TOOLS = ["tavily", "pydoc"]
    TAVILY_URL = "https://api.tavily.com/search"

    def run(self, env) -> AgentOutput:
        st = env.state
        st.docs_at_tests = st.tests_run
        messages = [st.last_tests.cases[n].message for n in sorted(st.last_tests.failing)[:3]]
        query_bits = []
        for msg in messages:
            m = _UNKNOWN_API_RE.search(msg)
            if m:
                query_bits.append(" ".join(g for g in m.groups() if g))
        query = ("python " + " ".join(query_bits) if query_bits else st.task)[:300]

        notes: list[str] = []
        source = "pydoc"
        if os.environ.get("TAVILY_API_KEY"):
            try:
                notes = self._tavily(query)
                source = "tavily"
            except Exception as e:
                logger.warning("Tavily search failed: %s", e)
        if not notes:
            notes = self._pydoc(query_bits)
        if notes:
            st.docs_notes.extend(notes)
            st.docs_notes = st.docs_notes[-3:]
        content = f"[{source}] {query}\n" + ("\n".join(notes) if notes else "no documentation found")
        return _output(self.name, content, source=source, query=query)

    def _tavily(self, query: str) -> list[str]:
        import httpx

        from core.agent import _http_client_from_env
        client = _http_client_from_env() or httpx.Client(timeout=30.0)
        with client:
            r = client.post(self.TAVILY_URL, json={"query": query, "max_results": 3,
                                                   "search_depth": "basic", "include_answer": True},
                            headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"})
            r.raise_for_status()
            data = r.json()
        notes = []
        if data.get("answer"):
            notes.append(f"Answer: {data['answer'][:400]}")
        for item in data.get("results", [])[:3]:
            notes.append(f"{item.get('title', '')} <{item.get('url', '')}>: {item.get('content', '')[:300]}")
        return notes

    @staticmethod
    def _pydoc(bits: list[str]) -> list[str]:
        import importlib
        import inspect
        notes = []
        for bit in bits:
            for name in bit.split():
                parts = name.split(".")
                try:
                    obj = importlib.import_module(parts[0])
                    for p in parts[1:]:
                        obj = getattr(obj, p)
                except Exception:
                    continue
                public = [a for a in dir(obj) if not a.startswith("_")][:40]
                doc = (inspect.getdoc(obj) or "").split("\n\n")[0][:300]
                notes.append(f"{name}: {doc}\n  members: {', '.join(public)}")
        return notes[:3]


# ═════════════════════════════════════════════════════════════════════════════
# LLM agents
# ═════════════════════════════════════════════════════════════════════════════

class LLMCodeAgent(CodeAgent):
    BUDGET = 8000
    ORDER: Optional[list[str]] = None
    INSTRUCTIONS = ""

    def prompt(self, env) -> str:
        snap = env.snapshot(self.name)
        return snap.render(self.BUDGET, self.ORDER) + "\n\n## YOUR JOB\n" + self.INSTRUCTIONS

    def _call(self, env, prompt: str) -> tuple[str, int, float, str]:
        t0 = time.monotonic()
        resp = self.groq_client.chat(model=self.config.model_name,
                                     messages=[{"role": "system", "content": self.config.role_prompt},
                                               {"role": "user", "content": prompt}],
                                     temperature=self.config.temperature,
                                     max_tokens=self.config.max_tokens)
        return (resp["content"] or "", resp["usage"]["total_tokens"],
                (time.monotonic() - t0) * 1000, resp.get("model", self.config.model_name))

    def run(self, env) -> AgentOutput:
        prompt = self.prompt(env)
        try:
            text, tokens, ms, model = self._call(env, prompt)
        except Exception as e:
            logger.error("[%s] LLM call failed: %s", self.name, e)
            return _output(self.name, "", success=False, error=str(e)[:300],
                           model=self.config.model_name, prompt_chars=len(prompt))
        out = self.handle(env, text)
        return _output(self.name, out, tokens=tokens, model=model, raw=text, latency_ms=ms,
                       prompt_chars=len(prompt))

    def handle(self, env, text: str) -> str:
        return text.strip()


class PlannerAgent(LLMCodeAgent):
    ROLE = ("You are PlannerAgent, a senior engineer. You read failing tests and the relevant code "
            "and write a short, concrete fix plan. You never write code.")
    DESCRIPTION = "FAST model: writes a 3-5 line fix plan before the first edit."
    BUDGET = 6000
    ORDER = ["task", "status", "code", "callers"]
    INSTRUCTIONS = ("Write a plan of at most 5 numbered lines: the root cause, the file and function "
                    "to change, and what must stay working. No code.")

    def handle(self, env, text: str) -> str:
        env.state.plan = text.strip()[:1500]
        env.state.planned = True
        return env.state.plan


class EditingAgent(LLMCodeAgent):
    may_edit_tests = False
    only_tests = False
    REPAIRS = 1

    def run(self, env) -> AgentOutput:
        st = env.state
        step = env.current_step
        prompt = self.prompt(env)
        tokens = 0
        ms = 0.0
        texts = []
        model = self.config.model_name
        messages = [{"role": "system", "content": self.config.role_prompt},
                    {"role": "user", "content": prompt}]
        result = None
        for attempt in range(1 + self.REPAIRS):
            t0 = time.monotonic()
            try:
                resp = self.groq_client.chat(model=self.config.model_name, messages=messages,
                                             temperature=self.config.temperature,
                                             max_tokens=self.config.max_tokens)
            except Exception as e:
                logger.error("[%s] LLM call failed: %s", self.name, e)
                return _output(self.name, "", success=False, error=str(e)[:300], tokens=tokens,
                               model=model, prompt_chars=len(prompt))
            ms += (time.monotonic() - t0) * 1000
            tokens += resp["usage"]["total_tokens"]
            model = resp.get("model", model)
            text = resp["content"] or ""
            texts.append(text)
            blocks = parse_edit_blocks(text)
            if self.only_tests:
                bad = [b.file for b in blocks if not is_test_path(b.file)]
                if bad:
                    result = None
                    errors = [f"{f}: {self.name} may only edit test files" for f in bad]
                else:
                    result = apply_edits(env.ws, blocks, may_edit_tests=True)
                    errors = result.errors
            else:
                result = apply_edits(env.ws, blocks, may_edit_tests=self.may_edit_tests)
                errors = result.errors
            if result is not None and result.ok:
                break
            # One bounded repair turn: the error replaces nothing in the snapshot,
            # it is a single follow-up message for this activation only.
            messages = messages[:2] + [
                {"role": "assistant", "content": text[-3000:]},
                {"role": "user", "content": "Your edit could not be applied:\n- " + "\n- ".join(errors)
                 + "\n\nReturn corrected edit blocks only."},
            ]

        if result is not None and result.ok:
            err = env.after_edit(self.name, step, result.files)
            content = ("applied: " + ", ".join(result.applied)) if err is None else \
                      f"rolled back: syntax error {err}"
        else:
            env.edit_rejected(self.name, step, errors)
            content = "edit rejected: " + "; ".join(errors)[:400]
        return _output(self.name, content, tokens=tokens, model=model, raw="\n\n".join(texts),
                       latency_ms=ms, prompt_chars=len(prompt), attempts=len(texts))


class CoderAgent(EditingAgent):
    ROLE = ("You are CoderAgent, an expert Python engineer. You fix bugs and implement features with "
            "minimal, correct SEARCH/REPLACE edits to the source code.")
    DESCRIPTION = "QUALITY model: writes the code change as SEARCH/REPLACE edits."
    BUDGET = 9000
    ORDER = ["task", "status", "code", "rollbacks", "callers", "plan", "docs", "diff"]
    INSTRUCTIONS = "Fix the task so the failing tests pass.\n\n" + EDIT_FORMAT


class DebuggerAgent(EditingAgent):
    ROLE = ("You are DebuggerAgent, an expert at reading tracebacks. You find the root cause of the "
            "remaining failures and fix it with minimal SEARCH/REPLACE edits.")
    DESCRIPTION = "QUALITY model: fixes remaining failures from the traceback and rollback history."
    BUDGET = 9000
    ORDER = ["task", "status", "rollbacks", "code", "callers", "docs", "diff", "review"]
    INSTRUCTIONS = ("Read the traceback, identify the root cause in the source code, and fix it. "
                    "Previous attempts are listed under ROLLED-BACK ATTEMPTS; try a different "
                    "approach.\n\n" + EDIT_FORMAT)


class TestWriterAgent(EditingAgent):
    __test__ = False
    ROLE = ("You are TestWriterAgent, a test engineer. You write small pytest tests that reproduce "
            "the reported bug or specify the requested feature. You never change source code.")
    DESCRIPTION = "QUALITY model: writes failing tests that reproduce the task (TDD); only it may edit tests."
    BUDGET = 7000
    ORDER = ["task", "status", "code", "callers", "diff"]
    may_edit_tests = True
    only_tests = True
    INSTRUCTIONS = ("Add 1-3 focused pytest tests under tests/ that FAIL on the current code and will "
                    "pass once the task is done. Put them in a new file tests/test_task_repro.py "
                    "(empty SEARCH) or append to an existing test file.\n\n" + EDIT_FORMAT)


class ReviewerAgent(LLMCodeAgent):
    ROLE = ("You are ReviewerAgent, a careful code reviewer. You check a diff for correctness, edge "
            "cases, and breakage in callers. You are terse.")
    DESCRIPTION = "FAST model: reviews the current diff; notes go to the next editor."
    BUDGET = 5000
    ORDER = ["task", "diff", "status", "callers", "lint"]
    INSTRUCTIONS = ("Review the CURRENT DIFF. Reply 'APPROVE' if it is correct and minimal, otherwise "
                    "list at most 3 concrete problems, one per line.")

    def handle(self, env, text: str) -> str:
        env.state.review = text.strip()[:1200]
        env.state.reviewed_since_edit = True
        return env.state.review


class ConcluderAgent(LLMCodeAgent):
    ROLE = ("You are ConcluderAgent. You write the final summary of a verified code change for the "
            "developer who will review it.")
    DESCRIPTION = "FAST model: summarises the verified diff and ends the episode."
    BUDGET = 4000
    ORDER = ["task", "status", "diff", "review"]
    INSTRUCTIONS = ("Summarise in at most 4 lines: what was wrong, what changed, and the test result. "
                    "End with 'FINAL ANSWER: <one-line summary>'.")

    def run(self, env) -> AgentOutput:
        out = super().run(env)
        if not out.success:
            # The change is already verified; a failed summary must not fail the episode.
            st = env.state
            files, added, removed = env.ws.diff_stat()
            out = _output(self.name, f"{len(st.best_tests.passed)}/{st.best_tests.total} tests passing; "
                          f"{files} file(s), +{added}/-{removed}", error_note=out.error)
        return out

    def handle(self, env, text: str) -> str:
        _, final = self._parse_output(text)
        return final or text.strip()


class TerminatorAgent(LLMCodeAgent):
    ROLE = ("You are TerminatorAgent. You decide whether further attempts are worthwhile. Reply with "
            "exactly one word: TERMINATE or CONTINUE.")
    DESCRIPTION = "FAST model: stops the episode when progress has stalled."
    BUDGET = 3000
    ORDER = ["task", "status", "rollbacks", "diff"]
    INSTRUCTIONS = ("Several attempts have failed. Reply TERMINATE if further attempts are unlikely to "
                    "help, otherwise CONTINUE. One word.")

    def handle(self, env, text: str) -> str:
        word = text.strip().upper()
        return "TERMINATE" if "TERMINATE" in word and "CONTINUE" not in word else "CONTINUE"


# ═════════════════════════════════════════════════════════════════════════════
# pool construction
# ═════════════════════════════════════════════════════════════════════════════

BASE_POOL = ["RepoScoutAgent", "PlannerAgent", "CoderAgent", "TestRunnerAgent", "DebuggerAgent",
             "LinterAgent", "ReviewerAgent", "ConcluderAgent", "TerminatorAgent"]
INJECTABLE = ["TestWriterAgent", "DocsSearchAgent", "ExplorerAgent"]
ALL_AGENTS = ["RepoScoutAgent", "PlannerAgent", "CoderAgent", "TestRunnerAgent", "DebuggerAgent",
              "LinterAgent", "ReviewerAgent", "TestWriterAgent", "DocsSearchAgent", "ExplorerAgent",
              "ConcluderAgent", "TerminatorAgent"]

_CLASSES = {c.__name__: c for c in (
    RepoScoutAgent, TestRunnerAgent, LinterAgent, DocsSearchAgent, PlannerAgent, CoderAgent,
    DebuggerAgent, TestWriterAgent, ReviewerAgent, ConcluderAgent, TerminatorAgent)}


def make_agent(name: str, session) -> CodeAgent:
    if name == "ExplorerAgent":
        from puppeteer_code.explore import ExplorerAgent
        return ExplorerAgent(session)
    if name not in _CLASSES:
        raise ValueError(f"unknown agent {name}")
    return _CLASSES[name](session)


def make_pool(session, names: Optional[list[str]] = None) -> list[CodeAgent]:
    return [make_agent(n, session) for n in (names or BASE_POOL)]


__all__ = ["CodeAgent", "LLMCodeAgent", "EditingAgent", "make_agent", "make_pool",
           "BASE_POOL", "INJECTABLE", "ALL_AGENTS", "EDIT_FORMAT", *list(_CLASSES)]
