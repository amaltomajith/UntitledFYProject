"""
puppeteer_code/integrity.py
===========================
Test-integrity guard: stop agents from "passing" by weakening the tests.

Three layers:
  1. Write guard   — edits to test files are refused unless the agent is
                     explicitly allowed (TestWriterAgent on TDD tasks).
  2. Count guard   — the number of collected tests must never drop below the
                     baseline (catches deleted or renamed tests).
  3. Diff scanner  — flags added skip/xfail markers, deleted assertions, and
                     source code that sniffs whether it is running under
                     pytest (a classic way to special-case the test run).
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

TEST_DIR_NAMES = {"tests", "test", "testing"}

# Added lines matching these are suspicious anywhere.
_SKIP_PATTERNS = [
    re.compile(r"pytest\.(skip|xfail|importorskip)\s*\("),
    re.compile(r"@pytest\.mark\.(skip|skipif|xfail)"),
    re.compile(r"@unittest\.(skip|skipIf|skipUnless|expectedFailure)"),
]
# Added lines in non-test code that detect the test harness.
_HARNESS_SNIFF = [
    re.compile(r"PYTEST_CURRENT_TEST"),
    re.compile(r"['\"]pytest['\"]\s+in\s+sys\.modules"),
    re.compile(r"sys\.modules\s*\.\s*get\(\s*['\"]pytest['\"]"),
    re.compile(r"_called_from_test"),
]


def is_test_path(rel: str) -> bool:
    """True for files that belong to the test suite."""
    p = PurePosixPath(rel.replace("\\", "/"))
    name = p.name
    if name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py"):
        return True
    return bool(set(p.parts[:-1]) & TEST_DIR_NAMES)


def scan_diff(diff: str) -> list[str]:
    """Return human-readable integrity violations found in a unified diff."""
    violations: list[str] = []
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[4:].removeprefix("b/").strip()
            continue
        if line.startswith("--- ") or line.startswith("@@"):
            continue
        in_test = is_test_path(current) if current else False
        if line.startswith("+"):
            added = line[1:]
            for pat in _SKIP_PATTERNS:
                if pat.search(added):
                    violations.append(f"{current}: adds a skip/xfail marker: {added.strip()[:80]}")
            if not in_test:
                for pat in _HARNESS_SNIFF:
                    if pat.search(added):
                        violations.append(f"{current}: source code detects the test harness: {added.strip()[:80]}")
        elif line.startswith("-") and in_test:
            removed = line[1:].strip()
            if removed.startswith("assert ") or re.match(r"self\.assert\w+\(", removed):
                violations.append(f"{current}: removes an assertion: {removed[:80]}")
    return violations


def check_test_count(baseline_total: int, current_total: int) -> list[str]:
    if current_total < baseline_total:
        return [f"collected tests dropped from {baseline_total} to {current_total}"]
    return []
