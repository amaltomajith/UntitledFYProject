"""
benchmarks/code_tasks/build_repo_tasks.py
=========================================
PCodeBench-Repo: the multi-file variant of PCodeBench-Mini.

Every task ships the SAME repository: all seven utility modules plus
`report.py`, a cross-module layer that calls into them, with one test file
per module.  Agents can no longer see "the" source file; they must find it.

  repo_<id>      the 37 PCodeBench-Mini bugs, planted in the shared repo
                 (split and difficulty unchanged)
  repo_x_<name>  cross-module bugs: the visible symptom is a failing
                 test_report.py test, the bug lives in a module report.py
                 calls, and that module's own tests are moved to the hidden
                 holdout.  Fixing them means following the call chain.

Layout per task (same as PCodeBench-Mini):

  tasks_repo/<id>/repo/            the repository (bug planted)
  tasks_repo/<id>/holdout/*.py     hidden tests, used only for scoring
  tasks_repo/<id>/task.json        id, split, difficulty, task text, reference solution

  python benchmarks/code_tasks/build_repo_tasks.py --check
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

from build_tasks import CONFTEST, HERE, MODULES, M, TASKS, _pytest, d, solution_blocks

OUT = HERE / "tasks_repo"

REPORT = d('''
    """Reports that combine the utility modules (the cross-module layer)."""
    from intervals import gaps, total_length
    from rpn import evaluate
    from stats import mean, median, percentile
    from textutils import slugify, title_case, truncate


    def describe_numbers(values):
        """One-line summary of a numeric sample."""
        return (f"n={len(values)} mean={mean(values):.2f} median={median(values):.2f} "
                f"p90={percentile(values, 90)}")


    def busy_time(meetings):
        """Time covered by (start, end) meetings; overlapping time is counted once."""
        return total_length(meetings)


    def free_slots(meetings, day_start, day_end):
        """Free (start, end) slots between day_start and day_end."""
        return gaps(meetings, day_start, day_end)


    def headline(text, width=30):
        """(title, slug) for an article heading; the title is cut to `width` characters."""
        return truncate(title_case(text), width), slugify(text)


    def stock_summary(inventory, threshold=5):
        """Total stock value (rounded to cents) and the items that need reordering."""
        return {"value": round(inventory.total_value(), 2), "reorder": inventory.low_stock(threshold)}


    def budget(formula):
        """Evaluate a budget formula written in RPN, e.g. '1200 3 /' for a monthly share."""
        return round(evaluate(formula), 2)
''')

REPORT_TESTS = d('''
    from inventory import Inventory
    from report import budget, busy_time, describe_numbers, free_slots, headline, stock_summary


    def test_describe_numbers():
        assert describe_numbers([4, 1, 3, 2]) == "n=4 mean=2.50 median=2.50 p90=4"


    def test_busy_time_counts_overlap_once():
        assert busy_time([(9, 12), (10, 11), (13, 14)]) == 4


    def test_free_slots():
        assert free_slots([(10, 11), (9, 10)], 8, 12) == [(8, 9), (11, 12)]


    def test_headline():
        assert headline("the art of war") == ("The Art of War", "the-art-of-war")
        assert headline("Hello World") == ("Hello World", "hello-world")


    def test_stock_summary():
        inv = Inventory()
        inv.add("pen", 10, 1.5)
        inv.add("ink", 2, 4.25)
        assert stock_summary(inv) == {"value": 23.5, "reorder": ["ink"]}


    def test_budget():
        assert budget("1200 3 /") == 400.0
        assert budget("10 4 /") == 2.5
''')

REPORT_HOLDOUT = d('''
    from inventory import Inventory
    from report import budget, busy_time, describe_numbers, free_slots, headline


    def test_describe_odd_sample():
        assert describe_numbers([5, 1, 3]) == "n=3 mean=3.00 median=3.00 p90=5"


    def test_busy_time_touching_and_nested():
        assert busy_time([(1, 10), (2, 3), (4, 5), (10, 12)]) == 11


    def test_free_slots_full_cover():
        assert free_slots([(8, 10), (9, 12)], 8, 12) == []


    def test_headline_truncates():
        title, slug = headline("a guide to the art of very long headings", width=20)
        assert len(title) == 20 and title.endswith("...") and title.startswith("A Guide")
        assert slug == "a-guide-to-the-art-of-very-long-headings"


    def test_stock_after_removal():
        inv = Inventory()
        inv.add("cup", 3, 2.0)
        inv.remove("cup", 3)
        assert inv.quantity("cup") == 0


    def test_budget_compound():
        assert budget("100 20 - 3 /") == 26.67
''')

# (id, mutation keys, difficulty, report)  -- every module listed is hidden from the visible tests
CROSS = [
    ("repo_x_median", ["median_even"], "medium",
     "describe_numbers() in report.py reports the wrong median for samples with an even number of values."),
    ("repo_x_busy", ["iv_nested"], "medium",
     "busy_time() in report.py under-counts when one meeting lies entirely inside another."),
    ("repo_x_free", ["iv_sort"], "medium",
     "free_slots() in report.py returns wrong slots when meetings are listed out of order."),
    ("repo_x_headline", ["title_first"], "medium",
     "headline() in report.py does not capitalise the first word when it is 'the', 'a' or similar."),
    ("repo_x_stock", ["inv_value"], "medium", "stock_summary() in report.py reports the wrong stock value."),
    ("repo_x_budget", ["rpn_div"], "medium",
     "budget() in report.py rounds shares down to whole numbers: budget('10 4 /') should be 2.5."),
    ("repo_x_two", ["median_even", "iv_nested"], "hard",
     "Two figures from report.py are wrong: the median in describe_numbers() for even-sized samples, "
     "and busy_time() when meetings are nested."),
]


def module_of(key: str) -> str:
    orig = M[key][0]
    hits = [name for name, spec in MODULES.items() if orig in spec["source"]]
    assert len(hits) == 1, (key, hits)
    return hits[0]


def all_tasks() -> list[dict]:
    out = []
    for task_id, module, keys, difficulty, report in TASKS:
        out.append(dict(id=f"repo_{task_id}", keys=keys, split=MODULES[module]["split"],
                        difficulty=difficulty, report=report, hidden=[]))
    for task_id, keys, difficulty, report in CROSS:
        hidden = sorted({module_of(k) for k in keys})
        # report.py and the call-chain tracing are never seen in training
        out.append(dict(id=task_id, keys=keys, split="test", difficulty=difficulty, report=report,
                        hidden=hidden))
    return out


def build(out: Path = OUT) -> list[Path]:
    if out.exists():
        shutil.rmtree(out)
    paths = []
    for spec in all_tasks():
        sources = {name: m["source"] for name, m in MODULES.items()}
        by_module: dict[str, list] = {}
        for key in spec["keys"]:
            mod = module_of(key)
            orig, mut = M[key]
            assert sources[mod].count(orig) == 1, (spec["id"], orig)
            sources[mod] = sources[mod].replace(orig, mut, 1)
            assert sources[mod].count(mut) == 1, (spec["id"], mut)
            by_module.setdefault(mod, []).append(M[key])
        tdir = out / spec["id"]
        repo = tdir / "repo"
        (repo / "tests").mkdir(parents=True)
        (tdir / "holdout").mkdir()
        for name, src in sources.items():
            (repo / f"{name}.py").write_bytes(src.encode())
            tests_dir = tdir / "holdout" if name in spec["hidden"] else repo / "tests"
            (tests_dir / f"test_{name}.py").write_bytes(MODULES[name]["tests"].encode())
            (tdir / "holdout" / f"test_holdout_{name}.py").write_bytes(MODULES[name]["holdout"].encode())
        (repo / "report.py").write_bytes(REPORT.encode())
        (repo / "tests" / "test_report.py").write_bytes(REPORT_TESTS.encode())
        (tdir / "holdout" / "test_holdout_report.py").write_bytes(REPORT_HOLDOUT.encode())
        (repo / "conftest.py").write_bytes(CONFTEST.encode())
        meta = {
            "id": spec["id"], "module": "+".join(sorted(by_module)), "split": spec["split"],
            "difficulty": spec["difficulty"], "n_bugs": len(spec["keys"]),
            "cross_module": bool(spec["hidden"]),
            "task": spec["report"] + " Fix the code so the tests pass.",
            "solution": "".join(solution_blocks(mod, pairs) for mod, pairs in sorted(by_module.items())),
        }
        (tdir / "task.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        paths.append(tdir)
    return paths


def check(paths: list[Path]) -> bool:
    """Original passes every test; mutant fails a visible test; solution restores the source."""
    sys.path.insert(0, str(HERE.parent.parent))
    from puppeteer_code.tools import apply_block, parse_edit_blocks
    ok = True
    for tdir in paths:
        meta = json.loads((tdir / "task.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(tdir / "repo", work)
            mutant_fails = _pytest(work, "tests") != 0
            for block in parse_edit_blocks(meta["solution"]):
                path = work / block.file
                content, note = apply_block(path.read_text(encoding="utf-8"), block)
                assert content is not None, (meta["id"], note)
                path.write_bytes(content.encode())
            shutil.copytree(tdir / "holdout", work / "tests" / "_holdout")
            fixed_passes = _pytest(work) == 0
            restored = all((work / f"{n}.py").read_text(encoding="utf-8") == m["source"]
                           for n, m in MODULES.items())
        status = "ok" if (mutant_fails and fixed_passes and restored) else "BAD"
        ok &= status == "ok"
        print(f"{status:>3}  {meta['id']:<24} mutant_fails={mutant_fails} fixed_passes={fixed_passes} "
              f"restored={restored}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    built = build()
    print(f"wrote {len(built)} tasks to {OUT}")
    if args.check:
        sys.exit(0 if check(built) else 1)
