"""
puppeteer_code/workspace.py
===========================
Transactional sandbox for agent edits.

The user's repository is never modified.  `Workspace.create(repo)` copies the
source files (respecting .gitignore when the repo uses git) into a temporary
directory and initialises a private git repository there.  Git then gives us,
for free:

  checkpoint()  — commit the current state (a high-water mark)
  rollback()    — reset --hard to a checkpoint and clean untracked files
  diff()        — the cumulative change against the original code
  fork()        — a linked `git worktree` for parallel exploration; forks
                  share the object store, so a fork's commit can be promoted
                  into the main workspace with a plain reset

Tests run in a subprocess with a scrubbed environment (no API keys), a
timeout, and JUnit XML output for structured per-test results.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional

IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", "dist", "build",
    ".idea", ".vscode", ".pcode_cache", ".pcode_runs", ".eggs",
}
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
GIT_IDENTITY = ["-c", "user.name=puppeteer-code", "-c", "user.email=pcode@localhost",
                "-c", "commit.gpgsign=false"]
_KEY_VAR = re.compile(r"(API_KEY|TOKEN|SECRET)$")


class WorkspaceError(RuntimeError):
    pass


def scrub(text: str) -> str:
    """Remove the values of secret environment variables from outgoing text."""
    for name, value in os.environ.items():
        if value and len(value) >= 8 and _KEY_VAR.search(name):
            text = text.replace(value, "[hidden]")
    return text


def scrub_obj(obj):
    return json.loads(scrub(json.dumps(obj, default=str)))


def scrubbed_env() -> dict[str, str]:
    """Environment for running untrusted code: drop anything that looks secret."""
    env = {k: v for k, v in os.environ.items()
           if not any(marker in k.upper() for marker in SECRET_MARKERS)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env.pop("PYTEST_ADDOPTS", None)
    return env


def _rmtree(path: Path) -> None:
    """rmtree that copes with read-only git object files on Windows."""
    def _onexc(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_onexc)
    else:
        shutil.rmtree(path, onerror=lambda f, p, e: _onexc(f, p, e))


# ═════════════════════════════════════════════════════════════════════════════
# 1.  TEST RESULTS
# ═════════════════════════════════════════════════════════════════════════════

FRAME_RE = re.compile(r"^(?P<file>[^\s:][^:\n]*?\.py):(?P<line>\d+)", re.M)


@dataclasses.dataclass
class TestCase:
    __test__ = False        # not a pytest class

    nodeid: str
    outcome: str            # passed | failed | error | skipped
    message: str = ""
    text: str = ""          # traceback text from JUnit


@dataclasses.dataclass
class TestResult:
    __test__ = False

    cases: dict[str, TestCase]
    returncode: int
    duration_s: float
    error: str = ""         # timeout / no tests / crash description
    raw_tail: str = ""

    @property
    def passed(self) -> set[str]:
        return {k for k, c in self.cases.items() if c.outcome == "passed"}

    @property
    def failing(self) -> set[str]:
        return {k for k, c in self.cases.items() if c.outcome in ("failed", "error")}

    @property
    def total(self) -> int:
        """Tests that count towards the pass rate (skipped tests excluded)."""
        return sum(1 for c in self.cases.values() if c.outcome != "skipped")

    @property
    def pass_rate(self) -> float:
        return len(self.passed) / self.total if self.total else 0.0

    @property
    def all_green(self) -> bool:
        return self.total > 0 and not self.failing and not self.error

    def frames(self) -> list[tuple[str, int]]:
        """(file, line) locations mentioned in failure tracebacks, in order."""
        out: list[tuple[str, int]] = []
        for c in self.cases.values():
            if c.outcome in ("failed", "error"):
                for m in FRAME_RE.finditer(c.text):
                    out.append((m.group("file").replace("\\", "/"), int(m.group("line"))))
        return out

    def digest(self, max_chars: int = 2500, per_test_lines: int = 18) -> str:
        """Compact failure summary for agent prompts."""
        head = f"{len(self.passed)}/{self.total} tests passing"
        if self.error:
            head += f"  [{self.error}]"
        lines = [head]
        for nodeid in sorted(self.failing):
            c = self.cases[nodeid]
            lines.append(f"FAILED {nodeid}: {c.message.strip()[:200]}")
            tb = [ln for ln in c.text.strip().splitlines() if ln.strip()]
            lines.extend("    " + ln for ln in tb[-per_test_lines:])
        out = "\n".join(lines)
        if len(out) > max_chars:
            out = out[:max_chars] + "\n... [digest truncated]"
        return out

    def to_dict(self) -> dict:
        return {
            "passed": len(self.passed), "total": self.total, "pass_rate": self.pass_rate,
            "failing": sorted(self.failing), "error": self.error,
            "returncode": self.returncode, "duration_s": self.duration_s,
        }


def parse_junit(xml_path: Path) -> dict[str, TestCase]:
    cases: dict[str, TestCase] = {}
    if not xml_path.exists():
        return cases
    root = ET.parse(xml_path).getroot()
    for tc in root.iter("testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        nodeid = f"{classname}::{name}" if classname else name
        outcome, message, text = "passed", "", ""
        for tag in ("failure", "error", "skipped"):
            el = tc.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = el.get("message", "") or ""
                text = (el.text or "")
                break
        cases[nodeid] = TestCase(nodeid, outcome, message, text)
    return cases


@dataclasses.dataclass
class LintResult:
    issues: list[str]
    available: bool

    @property
    def clean(self) -> bool:
        return not self.issues


# ═════════════════════════════════════════════════════════════════════════════
# 2.  WORKSPACE
# ═════════════════════════════════════════════════════════════════════════════

def _list_source_files(src: Path) -> list[Path]:
    """Files to copy into the sandbox (relative paths)."""
    rels: list[Path] = []
    if (src / ".git").exists() and shutil.which("git"):
        proc = subprocess.run(
            ["git", "-C", str(src), "ls-files", "-co", "--exclude-standard", "-z"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )
        if proc.returncode == 0:
            for rel in filter(None, proc.stdout.split("\0")):
                p = Path(rel)
                if (src / p).is_file():
                    rels.append(p)
    if not rels:
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.endswith(".egg-info")]
            for fn in filenames:
                rels.append((Path(dirpath) / fn).relative_to(src))
    return [r for r in rels
            if not (set(r.parts[:-1]) & IGNORE_DIRS)
            and not r.name.startswith(".env")
            and r.suffix not in (".pyc", ".pyo")]


class Workspace:
    """An isolated, git-backed copy of the user's repository."""

    def __init__(self, root: Path, base_sha: str, scratch: Path, owner: Optional["Workspace"] = None):
        self.root = root
        self.base_sha = base_sha
        self.scratch = scratch          # outside root: junit files, forks
        self.owner = owner
        self._fork_count = 0

    # ── construction / teardown ──────────────────────────────────────────────

    @classmethod
    def create(cls, repo: str | Path, tmp_parent: Optional[str] = None) -> "Workspace":
        src = Path(repo).resolve()
        if not src.is_dir():
            raise WorkspaceError(f"repository not found: {src}")
        if not shutil.which("git"):
            raise WorkspaceError("git is required for the sandbox")

        scratch = Path(tempfile.mkdtemp(prefix="pcode_", dir=tmp_parent))
        root = scratch / "ws"
        root.mkdir()
        for rel in _list_source_files(src):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, dest)

        ws = cls(root, base_sha="", scratch=scratch)
        ws.git("init", "-q")
        ws.git("config", "core.autocrlf", "false")
        ws.git("config", "core.safecrlf", "false")
        ws.base_sha = ws.checkpoint("base")
        return ws

    def cleanup(self) -> None:
        if self.owner is not None:
            self.owner.git("worktree", "remove", "--force", str(self.root), check=False)
            _rmtree(self.root)
        else:
            _rmtree(self.scratch)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()

    # ── git plumbing ─────────────────────────────────────────────────────────

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(["git", *GIT_IDENTITY, *args], cwd=self.root,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if check and proc.returncode != 0:
            raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def checkpoint(self, label: str) -> str:
        """Commit everything; return the commit sha."""
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "--no-verify", "-m", label)
        return self.head()

    def rollback(self, sha: str) -> None:
        """Restore a checkpoint exactly (tracked + untracked files)."""
        self.git("reset", "-q", "--hard", sha)
        self.git("clean", "-fdq")

    def diff(self, against: Optional[str] = None) -> str:
        """Unified diff of the working tree vs. `against` (default: original code)."""
        self.git("add", "-A")
        return self.git("diff", "--cached", "--no-color", "--no-ext-diff", against or self.base_sha)

    def diff_stat(self, against: Optional[str] = None) -> tuple[int, int, int]:
        """(files changed, lines added, lines removed)."""
        self.git("add", "-A")
        out = self.git("diff", "--cached", "--numstat", against or self.base_sha)
        files = added = removed = 0
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                files += 1
                added += int(parts[0]) if parts[0].isdigit() else 0
                removed += int(parts[1]) if parts[1].isdigit() else 0
        return files, added, removed

    def changed_files(self, against: Optional[str] = None) -> list[str]:
        self.git("add", "-A")
        out = self.git("diff", "--cached", "--name-only", against or self.base_sha)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def fork(self, sha: Optional[str] = None) -> "Workspace":
        """A linked worktree at `sha` (default HEAD) for parallel exploration."""
        self._fork_count += 1
        path = self.scratch / f"fork_{self._fork_count}"
        self.git("worktree", "add", "-q", "--detach", str(path), sha or self.head())
        fork_scratch = self.scratch / f"fork_{self._fork_count}_meta"
        fork_scratch.mkdir(exist_ok=True)
        return Workspace(path, self.base_sha, scratch=fork_scratch, owner=self)

    # ── file access (path-guarded) ───────────────────────────────────────────

    def resolve(self, rel: str) -> Path:
        rel = rel.strip().replace("\\", "/")
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise PermissionError(f"absolute paths are not allowed: {rel}")
        p = (self.root / rel).resolve()
        root = self.root.resolve()
        if p != root and root not in p.parents:
            raise PermissionError(f"path escapes the workspace: {rel}")
        if ".git" in p.relative_to(root).parts:
            raise PermissionError(f"refusing to touch git internals: {rel}")
        return p

    def exists(self, rel: str) -> bool:
        try:
            return self.resolve(rel).is_file()
        except PermissionError:
            return False

    def read(self, rel: str) -> str:
        with open(self.resolve(rel), encoding="utf-8", errors="replace", newline="") as f:
            return f.read()

    def write(self, rel: str, text: str) -> None:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def python_files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    out.append((Path(dirpath) / fn).relative_to(self.root).as_posix())
        return sorted(out)

    # ── checks ───────────────────────────────────────────────────────────────

    def syntax_errors(self, files: Iterable[str]) -> list[str]:
        errors = []
        for rel in files:
            if not rel.endswith(".py") or not self.exists(rel):
                continue
            try:
                ast.parse(self.read(rel), filename=rel)
            except SyntaxError as e:
                errors.append(f"{rel}:{e.lineno}: SyntaxError: {e.msg}")
        return errors

    def run_tests(self, paths: Optional[list[str]] = None, timeout: int = 120) -> TestResult:
        junit = self.scratch / f"junit_{time.time_ns()}.xml"
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               f"--junitxml={junit}", "-o", "junit_family=xunit2", *(paths or [])]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=self.root, env=scrubbed_env(), stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return TestResult({}, returncode=-1, duration_s=float(timeout), error=f"timeout after {timeout}s")
        duration = time.monotonic() - t0
        cases = parse_junit(junit)
        junit.unlink(missing_ok=True)
        error = ""
        if proc.returncode == 5:
            error = "no tests collected"
        elif proc.returncode in (2, 3, 4) and not cases:
            error = "pytest could not run (collection/usage error)"
        tail = (proc.stdout + proc.stderr)[-3000:]
        return TestResult(cases, proc.returncode, duration, error=error, raw_tail=tail)

    def run_lint(self, files: Iterable[str]) -> LintResult:
        files = [f for f in files if f.endswith(".py") and self.exists(f)]
        if not files:
            return LintResult([], available=True)
        if shutil.which("ruff"):
            cmd = ["ruff"]
        elif importlib.util.find_spec("ruff") is not None:
            cmd = [sys.executable, "-m", "ruff"]
        else:
            return LintResult(self.syntax_errors(files), available=False)
        proc = subprocess.run([*cmd, "check", "--output-format=concise", "--no-cache", *files],
                              cwd=self.root, env=scrubbed_env(), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        issues = [ln for ln in proc.stdout.splitlines()
                  if re.match(r"^\S+\.py:\d+:\d+:", ln)]
        return LintResult(issues, available=True)
