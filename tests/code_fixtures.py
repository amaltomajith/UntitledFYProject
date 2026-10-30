"""Shared fixture repos for the Puppeteer-Code test suites."""

import textwrap
from pathlib import Path

CALC_BUGGY = textwrap.dedent('''\
    """Tiny calculator used by the Puppeteer-Code tests."""


    def add(a, b):
        """Add two numbers."""
        return a + b


    def mean(values):
        """Arithmetic mean of a non-empty list."""
        return sum(values) / (len(values) - 1)


    def describe(values):
        """Human-readable summary built on mean()."""
        return f"n={len(values)} mean={mean(values):.2f}"
''')

CALC_TESTS = textwrap.dedent('''\
    from calc import add, mean, describe


    def test_add():
        assert add(2, 3) == 5


    def test_mean():
        assert mean([2, 4, 6]) == 4


    def test_describe():
        assert describe([1, 2, 3]) == "n=3 mean=2.00"
''')

# The fix a mock Coder returns for the bug above.
CALC_FIX_BLOCK = textwrap.dedent('''\
    FILE: calc.py
    <<<<<<< SEARCH
        return sum(values) / (len(values) - 1)
    =======
        return sum(values) / len(values)
    >>>>>>> REPLACE
''')

CONFTEST = "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n"


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))   # always LF, even on Windows


def make_calc_repo(root: Path) -> Path:
    """calc.py with an off-by-one in mean(); 1 of 3 tests passes."""
    repo = root / "calc_repo"
    (repo / "tests").mkdir(parents=True)
    _write(repo / "calc.py", CALC_BUGGY)
    _write(repo / "tests" / "test_calc.py", CALC_TESTS)
    # rootdir conftest so `import calc` works from tests/
    _write(repo / "conftest.py", CONFTEST)
    # noise that must NOT be copied into the sandbox
    _write(repo / ".env", "NEBIUS_API_KEY=secret\n")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "calc.cpython-313.pyc").write_bytes(bytes(1))
    return repo
