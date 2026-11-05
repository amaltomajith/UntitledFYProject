"""
Fetch and freeze fixed-seed subsets of the standard reasoning datasets.

    python benchmarks/reasoning/datasets.py --build     # fetch from Hugging Face, write tasks/*.jsonl
    python benchmarks/reasoning/datasets.py --check     # verify the frozen tasks and their checkers

Rows come from the Hugging Face datasets-server JSON API with urllib, so no extra dependency is
needed. The frozen files are committed, so a rerun uses exactly the same questions.

| key       | source                          | subset                                   |
|-----------|---------------------------------|------------------------------------------|
| gsm8k     | openai/gsm8k, main, test        | 50                                       |
| gsm-hard  | reasoning-machines/gsm-hard     | 50                                       |
| mmlu-pro  | TIGER-Lab/MMLU-Pro, test        | 70: 5 from each of the 14 categories     |
| bbh       | lukaemon/bbh, 27 subtasks       | 54: 2 from each subtask                  |
| gaia      | gaia-benchmark/GAIA 2023 L1 val | 20 with no attached file; needs HF_TOKEN |

smoke.jsonl holds 2 more tasks per dataset, never in the subsets, for checking the pipeline.
GAIA is written under runs/ (gitignored), because its terms ask that it not be reshared.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.reasoning import checkers  # noqa: E402

HERE = Path(__file__).resolve().parent
TASKS_DIR = HERE / "tasks"
GAIA_PATH = ROOT / "runs" / "reasoning-tasks" / "gaia.jsonl"
API = "https://datasets-server.huggingface.co"

SIZES = {"gsm8k": 50, "gsm-hard": 50, "mmlu-pro": 5, "bbh": 2, "gaia": 20}   # mmlu-pro, bbh: per group
SMOKE = 2
DATASETS = ("gsm8k", "gsm-hard", "mmlu-pro", "bbh", "gaia")

MMLU_CATEGORIES = (
    "biology", "business", "chemistry", "computer science", "economics", "engineering", "health",
    "history", "law", "math", "other", "philosophy", "physics", "psychology",
)
BBH_SUBTASKS = (
    "boolean_expressions", "causal_judgement", "date_understanding", "disambiguation_qa", "dyck_languages",
    "formal_fallacies", "geometric_shapes", "hyperbaton", "logical_deduction_five_objects",
    "logical_deduction_seven_objects", "logical_deduction_three_objects", "movie_recommendation",
    "multistep_arithmetic_two", "navigate", "object_counting", "penguins_in_a_table",
    "reasoning_about_colored_objects", "ruin_names", "salient_translation_error_detection", "snarks",
    "sports_understanding", "temporal_sequences", "tracking_shuffled_objects_five_objects",
    "tracking_shuffled_objects_seven_objects", "tracking_shuffled_objects_three_objects", "web_of_lies",
    "word_sorting",
)
LETTERS = "ABCDEFGHIJ"


_PAGES: dict = {}


def _get(path: str, params: dict, token: str | None = None) -> dict:
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    if url in _PAGES:
        return _PAGES[url]
    headers = {"User-Agent": "puppeteer-plus-benchmark"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(8):
        try:
            time.sleep(0.5)                                  # the API rate-limits bursts
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                _PAGES[url] = json.loads(r.read().decode("utf-8"))
                return _PAGES[url]
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == 7:
                raise
            wait = float(e.headers.get("Retry-After") or 0) or min(60, 5 * 2 ** attempt)
            print(f"  HTTP {e.code}, waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
        except OSError:
            if attempt == 7:
                raise
            time.sleep(5)
    raise RuntimeError("unreachable")


PAGE = 100


def _rows(dataset: str, config: str, split: str, offsets: list[int], where: str | None = None,
          token: str | None = None) -> list[dict]:
    """Rows at these offsets, fetched a page (100 rows) at a time."""
    out = []
    for off in offsets:
        start = off - off % PAGE
        params = {"dataset": dataset, "config": config, "split": split, "offset": start, "length": PAGE}
        if where:
            params["where"] = where
        d = _get("filter" if where else "rows", params, token)
        row = d["rows"][off - start]
        out.append({"row_idx": row["row_idx"], **row["row"]})
    return out


def _total(dataset: str, config: str, split: str, where: str | None = None, token: str | None = None) -> int:
    params = {"dataset": dataset, "config": config, "split": split, "offset": 0, "length": PAGE}
    if where:
        params["where"] = where
    return _get("filter" if where else "rows", params, token)["num_rows_total"]


def _pick(rng: random.Random, total: int, n: int, smoke: int) -> tuple[list[int], list[int]]:
    picked = rng.sample(range(total), n + smoke)
    return sorted(picked[:n]), sorted(picked[n:])


FORMAT = {
    # Only the FINAL ANSWER line is constrained, so this hint never tells a method to skip its working.
    "number": "In the FINAL ANSWER line, write only a single number.",
    "letter": "In the FINAL ANSWER line, write only the letter of the correct option.",
    "exact": ("In the FINAL ANSWER line, write only the answer, in the form the question asks for "
              "(for options, the letter in brackets, e.g. (A))."),
    "gaia": "In the FINAL ANSWER line, write only the answer: a number, a few words, or a comma-separated list.",
}


def _task(dataset: str, tid: str, question: str, gold: str, kind: str, **meta) -> dict:
    return {"id": tid, "dataset": dataset, "question": question, "gold": gold, "kind": kind, "meta": meta}


def build_gsm8k(rng, n, smoke):
    total = _total("openai/gsm8k", "main", "test")
    main, extra = _pick(rng, total, n, smoke)
    def make(rows):
        return [_task("gsm8k", f"gsm8k-{r['row_idx']}", r["question"],
                      r["answer"].split("####")[-1].strip().replace(",", ""), "number") for r in rows]
    return make(_rows("openai/gsm8k", "main", "test", main)), make(_rows("openai/gsm8k", "main", "test", extra))


def build_gsm_hard(rng, n, smoke):
    total = _total("reasoning-machines/gsm-hard", "default", "train")
    main, extra = _pick(rng, total, n, smoke)
    def make(rows):
        return [_task("gsm-hard", f"gsm-hard-{r['row_idx']}", r["input"], checkers.format_number(r["target"]),
                      "number") for r in rows]
    src = ("reasoning-machines/gsm-hard", "default", "train")
    return make(_rows(*src, main)), make(_rows(*src, extra))


def _mmlu_question(r: dict) -> str:
    opts = "\n".join(f"{LETTERS[i]}. {o}" for i, o in enumerate(r["options"]))
    return f"{r['question']}\n\nOptions:\n{opts}"


def build_mmlu_pro(rng, n, smoke):
    # The /filter endpoint was unreliable, so read the whole test split a page at a time (about 121 requests)
    # and stratify locally.
    src = ("TIGER-Lab/MMLU-Pro", "default", "test")
    total = _total(*src)
    rows = []
    for start in range(0, total, PAGE):
        params = {"dataset": src[0], "config": src[1], "split": src[2], "offset": start, "length": PAGE}
        rows += [{"row_idx": r["row_idx"], **r["row"]} for r in _get("rows", params)["rows"]]
    main_tasks, smoke_tasks = [], []
    for i, cat in enumerate(MMLU_CATEGORIES):
        pool = [r for r in rows if r["category"] == cat]
        main, extra = _pick(rng, len(pool), n, smoke if i < 1 else 0)
        for bucket, idxs in ((main_tasks, main), (smoke_tasks, extra)):
            for r in (pool[j] for j in idxs):
                bucket.append(_task("mmlu-pro", f"mmlu-pro-{r['question_id']}", _mmlu_question(r), r["answer"],
                                    "letter", category=cat, options=len(r["options"])))
    return main_tasks, smoke_tasks


def build_bbh(rng, n, smoke):
    main_tasks, smoke_tasks = [], []
    for i, sub in enumerate(BBH_SUBTASKS):
        total = _total("lukaemon/bbh", sub, "test")
        k = 1 if i < smoke else 0
        main, extra = _pick(rng, total, n, k)
        for bucket, offs in ((main_tasks, main), (smoke_tasks, extra)):
            for r in _rows("lukaemon/bbh", sub, "test", offs):
                bucket.append(_task("bbh", f"bbh-{sub}-{r['row_idx']}", r["input"], r["target"], "exact",
                                    subtask=sub))
    return main_tasks, smoke_tasks


def build_gaia(rng, n, smoke, token):
    where = "\"file_name\"=''"
    src = ("gaia-benchmark/GAIA", "2023_level1", "validation")
    total = _total(*src, where, token)
    main, extra = _pick(rng, total, min(n, total - smoke), smoke)
    def make(rows):
        return [_task("gaia", f"gaia-{r['task_id']}", r["Question"], str(r["Final answer"]), "gaia", level=1)
                for r in rows]
    return make(_rows(*src, main, where, token)), make(_rows(*src, extra, where, token))


def _write(path: Path, tasks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_tasks(datasets: list[str], smoke: bool = False) -> list[dict]:
    """The frozen tasks for these datasets, in file order (or the smoke tasks)."""
    if smoke:
        return [t for t in read_jsonl(TASKS_DIR / "smoke.jsonl") if t["dataset"] in datasets]
    out = []
    for name in datasets:
        out += read_jsonl(GAIA_PATH if name == "gaia" else TASKS_DIR / f"{name}.jsonl")
    return out


def build(seed: int = 0) -> dict:
    builders = {"gsm8k": build_gsm8k, "gsm-hard": build_gsm_hard, "mmlu-pro": build_mmlu_pro, "bbh": build_bbh}
    counts, smoke_all = {}, []
    for name, fn in builders.items():
        rng = random.Random(f"{seed}:{name}")
        main, extra = fn(rng, SIZES[name], SMOKE)
        _write(TASKS_DIR / f"{name}.jsonl", main)
        smoke_all += extra
        counts[name] = len(main)
        print(f"{name}: {len(main)} tasks, {len(extra)} smoke", flush=True)
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        try:
            main, _extra = build_gaia(random.Random(f"{seed}:gaia"), SIZES["gaia"], SMOKE, token)
            _write(GAIA_PATH, main)
            counts["gaia"] = len(main)
            print(f"gaia: {len(main)} tasks (written to {GAIA_PATH}, not committed)")
        except Exception as e:  # gated: terms not accepted, or no access
            print(f"gaia: not built ({type(e).__name__}); accept the terms on its Hugging Face page first")
    else:
        print("gaia: not built (no HF_TOKEN)")
    _write(TASKS_DIR / "smoke.jsonl", smoke_all)
    (TASKS_DIR / "MANIFEST.json").write_text(json.dumps({"seed": seed, "counts": counts, "smoke": len(smoke_all),
                                                         "api": API}, indent=1), encoding="utf-8")
    return counts


def check() -> list[str]:
    """Every frozen task: gold present, the checker accepts the gold and rejects a wrong answer, no overlap."""
    problems, seen = [], {}
    files = sorted(TASKS_DIR.glob("*.jsonl")) + ([GAIA_PATH] if GAIA_PATH.exists() else [])
    for path in files:
        for t in read_jsonl(path):
            where = f"{path.name}:{t.get('id')}"
            if not str(t.get("gold", "")).strip() or not t.get("question", "").strip():
                problems.append(f"{where}: empty question or gold")
                continue
            if not checkers.check(t["kind"], f"Working...\nFINAL ANSWER: {t['gold']}", t["gold"]):
                problems.append(f"{where}: checker rejects the gold answer")
            if checkers.check(t["kind"], f"FINAL ANSWER: {checkers.wrong_answer(t)}", t["gold"]):
                problems.append(f"{where}: checker accepts a wrong answer")
            if t["id"] in seen:
                problems.append(f"{where}: duplicate of {seen[t['id']]}")
            seen[t["id"]] = path.name
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="fetch from Hugging Face and overwrite tasks/")
    ap.add_argument("--check", action="store_true", help="verify the frozen tasks")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    if args.build:
        build(args.seed)
    if args.check or not args.build:
        problems = check()
        n = sum(len(read_jsonl(p)) for p in TASKS_DIR.glob("*.jsonl"))
        for p in problems:
            print("PROBLEM", p)
        print(f"{n} tasks checked, {len(problems)} problems")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
