"""
puppeteer_code/assistant/verify.py
==================================
Finds out how a project can be checked, runs that, and compares results. This is
the objective signal for the assistant: a model's opinion never counts as a pass.

Any language works if the project has a test or build command. With none, Python
and JavaScript files are at least parsed, and the result says plainly that nothing
checked how the code behaves.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

from puppeteer_code.workspace import IGNORE_DIRS

TESTS, BUILD, SYNTAX, NONE = "tests", "build", "syntax", "none"
TIMEOUT = 120
MAX_SCAN = 3000
_NO_TEST_SCRIPT = "no test specified"
_FAILED_ID = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)
_TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(test|spec)\.[cm]?[jt]sx?)$")


@dataclasses.dataclass
class Check:
    kind: str = NONE
    ok: bool = False
    passed: int = 0
    failed: int = 0
    failures: list[str] = dataclasses.field(default_factory=list)
    summary: str = "Nothing to check yet."
    output: str = ""
    command: str = ""

    @property
    def verified(self) -> bool:
        """True only when something ran the code."""
        return self.kind in (TESTS, BUILD)

    @property
    def pass_rate(self) -> float:
        total = self.passed + self.failed
        return self.passed / total if total else float(self.ok)


def _files(root: Path):
    seen = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            seen += 1
            if seen > MAX_SCAN:
                return
            yield (Path(folder) / name).relative_to(root).as_posix()


def _python(root: Path) -> str:
    for rel in (".venv/Scripts/python.exe", "venv/Scripts/python.exe", ".venv/bin/python", "venv/bin/python"):
        if (root / rel).exists():
            return f'"{root / rel}"'
    return f'"{sys.executable}"'


def detect(root: Path) -> list[tuple[str, str]]:
    """Commands that check this project, as (kind, command)."""
    root = Path(root)
    found: list[tuple[str, str]] = []
    files = list(_files(root))
    has_test_files = any(_TEST_FILE.search(f) for f in files)

    package = root / "package.json"
    scripts: dict = {}
    if package.exists():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
        except ValueError:
            scripts = {}
    if scripts.get("test") and _NO_TEST_SCRIPT not in scripts["test"]:
        found.append((TESTS, "npm test --silent"))
    elif any(re.search(r"\.(test|spec)\.[cm]?js$", f) for f in files):
        found.append((TESTS, "node --test"))
    if scripts.get("build"):
        found.append((BUILD, "npm run build --silent"))

    if any(f.endswith(".py") and _TEST_FILE.search(f) for f in files):
        found.append((TESTS, f"{_python(root)} -m pytest -q --tb=short -p no:cacheprovider"))

    for marker, command in (("go.mod", "go test ./..."), ("Cargo.toml", "cargo test"), ("pom.xml", "mvn -q test"),
                            ("build.gradle", "gradle test"), ("build.gradle.kts", "gradle test")):
        if (root / marker).exists():
            found.append((TESTS, command))
    if any(f.endswith((".csproj", ".sln")) for f in files):
        found.append((TESTS, "dotnet test"))
    if not found and (root / "Makefile").exists() and re.search(
            r"^test:", (root / "Makefile").read_text(encoding="utf-8", errors="replace"), re.M):
        found.append((TESTS, "make test"))
    if has_test_files and not found:
        found.append((TESTS, f"{_python(root)} -m pytest -q --tb=short -p no:cacheprovider"))
    return found


def testable(root: Path, stack: str = "") -> bool:
    """Can the assistant write tests that will run here?"""
    if stack:
        return stack in ("python", "node")
    return any(f.endswith((".py", ".js", ".mjs", ".ts")) for f in _files(Path(root)))


def _counts(output: str) -> tuple[int, int]:
    passed = sum(int(n) for n in re.findall(r"(\d+)\s+(?:passed|passing|tests? passed)", output))
    failed = sum(int(n) for n in re.findall(r"(\d+)\s+(?:failed|failing|errors?)\b", output))
    if not passed and not failed:
        tap = re.findall(r"^\W*(pass|fail)\s+(\d+)\s*$", output, re.M)
        passed = sum(int(n) for word, n in tap if word == "pass")
        failed = sum(int(n) for word, n in tap if word == "fail")
    return passed, failed


def _parse_problems(root: Path) -> tuple[int, list[str]]:
    checked, problems = 0, []
    node = shutil.which("node")
    for rel in _files(root):
        path = root / rel
        if rel.endswith(".py"):
            checked += 1
            try:
                ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError as e:
                problems.append(f"{rel}:{e.lineno}: {e.msg}")
        elif rel.endswith(".json"):
            checked += 1
            try:
                json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except ValueError as e:
                problems.append(f"{rel}: {e}")
        elif rel.endswith((".js", ".mjs", ".cjs")) and node:
            checked += 1
    return checked, problems


def _syntax(root: Path, toolbox) -> Check:
    checked, problems = _parse_problems(root)
    if shutil.which("node"):
        for rel in _files(root):
            if rel.endswith((".js", ".mjs", ".cjs")):
                code, out = toolbox.run(f'node --check "{rel}"', 20)
                if code != 0:
                    problems.append(f"{rel}: {out.strip()[:300]}")
    if not checked:
        return Check(summary="No tests or build here, so I could not check this automatically.")
    if problems:
        return Check(kind=SYNTAX, failed=len(problems), failures=problems, output="\n".join(problems),
                     summary=f"{len(problems)} file(s) have syntax errors.")
    return Check(kind=SYNTAX, ok=True, passed=checked,
                 summary="The code parses, but no tests checked how it behaves.")


def run_checks(root: Path, toolbox) -> Check:
    root = Path(root)
    commands = detect(root)
    toolbox.safe_commands = [command for _, command in commands]
    result: Optional[Check] = None
    for kind, command in commands:
        code, output = toolbox.run(command, TIMEOUT)
        if "no tests ran" in output or "no tests collected" in output:
            continue
        passed, failed = _counts(output)
        check = Check(kind=kind, ok=code == 0, passed=passed, failed=failed, output=output, command=command,
                      failures=_FAILED_ID.findall(output))
        if kind == TESTS and (passed or failed):
            check.summary = f"Tests: {passed} passed, {failed} failed." if failed or code else f"Tests: {passed} passed."
        else:
            check.summary = f"{kind.capitalize()} {'passed' if code == 0 else 'failed'}: {command}"
        if not check.ok:
            return check
        if result is None or kind == TESTS:
            result = check
    return result or _syntax(root, toolbox)


def improved(best: Optional[Check], now: Check) -> bool:
    if best is None or best.kind == NONE:
        return now.ok
    if now.ok and not best.ok:
        return True
    return now.passed > best.passed


def regressed(best: Optional[Check], now: Check) -> bool:
    """Worse than the last good state: fewer tests pass, or a green check went red."""
    if best is None or best.kind == NONE:
        return False
    if best.kind in (TESTS, BUILD) and now.kind not in (TESTS, BUILD):
        return True
    return now.passed < best.passed or (best.ok and not now.ok and now.passed <= best.passed)
