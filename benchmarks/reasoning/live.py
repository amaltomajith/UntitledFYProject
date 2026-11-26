"""
Watch the reasoning benchmark run, task by task, with what each test measures and how it is scored.

    python -m benchmarks.reasoning.live                        # all 224 tasks, replayed: $0, no network
    python -m benchmarks.reasoning.live --limit 3 --steps      # 3 tasks per dataset, every agent step shown
    python -m benchmarks.reasoning.live --task gsm8k-15        # one task in full detail
    python -m benchmarks.reasoning.live --datasets gsm-hard --pace 1
    python -m benchmarks.reasoning.live --explain              # the guide only: datasets, methods, metrics, viva notes
    python -m benchmarks.reasoning.live --fresh --limit 2      # NEW calls to Nebius: costs money, answers can differ

Replay (the default) runs the real pipeline: the same methods.py, core.Orchestrator, checkers.py and stats.py
as the recorded run. Only the model is replaced, by the replies the model actually gave in that run, which the
response cache stored (runs/cache/reasoning/thinking-off/seed0). So every number it shows is the recorded
result being recomputed in front of you, and each row is compared with the run file. Nothing is sent anywhere.

--fresh asks Nebius again. The model samples at temperature 0.7, so its answers, and some scores, can differ
from the recorded run. A fresh run on a few tasks is a demonstration, never the reported result. It is saved to
its own file under runs/, never over the recorded one.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rich import box  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from benchmarks.reasoning import checkers, datasets, methods, stats  # noqa: E402
from benchmarks.reasoning.client import CountingClient  # noqa: E402
from benchmarks.reasoning.run import QuotaStop, make_inner, prices_for, run_row  # noqa: E402

RECORDED = ROOT / "runs" / "reasoning-nebius-lightning.json"
CACHE = ROOT / "runs" / "cache" / "reasoning" / "thinking-off" / "seed0"
MODEL = "nvidia/Nemotron-3_5-Lightning"
DATASETS = ("gsm8k", "gsm-hard", "mmlu-pro", "bbh")

# ─────────────────────────────────────────────────────────────── what each test is, in plain words

DATASET_INFO = {
    "gsm8k": {
        "name": "GSM8K",
        "origin": "Cobbe et al., OpenAI, 2021. 50 of the 1,319 test problems, drawn once with seed 0.",
        "tests": "Grade-school maths word problems that need 2 to 8 steps of arithmetic.",
        "answer": "A single number.",
        "scored": ("The number after the last 'FINAL ANSWER:' must equal the gold answer exactly. "
                   "No partial credit: one slip in any step makes the final number wrong."),
        "chance": "About 0%. An open number cannot be guessed.",
        "means": "Accuracy here is the share of problems whose whole chain of steps ended at the right number.",
        "caveat": "Public since 2021, so some problems may be in the model's training data.",
    },
    "gsm-hard": {
        "name": "GSM-Hard",
        "origin": "Gao et al., 2023 (PAL). 50 of 1,319, seed 0.",
        "tests": ("The same GSM8K problems with the numbers swapped for large ones, so the arithmetic is "
                  "harder and a remembered answer is no help."),
        "answer": "A single number, sometimes a decimal.",
        "scored": ("Integer gold: exact. Decimal gold: within 0.01, because golds are rounded to 2 places. "
                   "Commas, a unicode minus or '$' do not matter."),
        "chance": "About 0%.",
        "means": "Accuracy here is whether the reasoning survives harder arithmetic, not just the plan.",
        "caveat": ("Some GSM-Hard gold answers are known to be wrong (numbers were swapped in without checking, "
                   "for example negative counts). Every method faces the same labels, so the comparison stays fair."),
    },
    "mmlu-pro": {
        "name": "MMLU-Pro",
        "origin": "Wang et al., TIGER-Lab, 2024. 70 questions: 5 from each of 14 subjects, seed 0.",
        "tests": "Knowledge plus reasoning in 14 subjects (law, physics, economics...), multiple choice.",
        "answer": "One option letter, A to J.",
        "scored": "The option letter after 'FINAL ANSWER:' must match the gold letter.",
        "chance": "About 10%: up to 10 options (the original MMLU had 4, so 25%).",
        "means": "Accuracy here is the share of questions where the chosen option was the right one.",
        "caveat": "Built from public exam material, so contamination is possible.",
    },
    "bbh": {
        "name": "BIG-Bench Hard (BBH)",
        "origin": "Suzgun et al., 2022. 54 questions: 2 from each of 27 subtasks, seed 0.",
        "tests": ("Symbolic and logical puzzles: boolean expressions, dates, tracking shuffled objects, "
                  "logical deduction, word sorting, navigation and more."),
        "answer": "Exactly the form the question asks for: True/False, Yes/No, an option like (B), a word list...",
        "scored": ("Normalised exact match: case, quotes, spaces and a trailing full stop are ignored, "
                   "and '(B)' equals 'B'."),
        "chance": "Depends on the subtask: 50% for yes/no ones, lower for options, about 0% for free-form ones.",
        "means": "Accuracy here is careful step-by-step symbol handling, where one slip flips the answer.",
        "caveat": "2 questions per subtask, so a single subtask says nothing alone; only the total counts.",
    },
}

METHOD_INFO = {
    "direct": ("1 call", "The model answers at once, without showing working. The floor: what one call can do."),
    "cot": ("1 call", "Chain of thought: one call that reasons step by step, then answers. The strong one-call baseline."),
    "fixed": ("4 calls", "Planner, then Reasoning, then Critic, then Concluder, always in that order. The same "
                         "agents as the puppeteer, with no policy choosing."),
    "puppeteer": ("up to 8 calls", "core.Orchestrator, the thesis engine: before each step an LLM policy picks "
                                   "the next agent (one policy call plus one agent call per step). The last step "
                                   "is the Concluder, by the engine's own rule. The policy is untrained."),
}

METRICS = [
    ("Accuracy", "correct / tasks. Each task is right or wrong, nothing in between."),
    ("95% CI (Wilson)", "The range the true accuracy probably lies in, given only this many tasks. With 50 tasks it "
                        "is about +/- 13 points. If two methods' ranges overlap a lot, the gap may be luck."),
    ("McNemar p", "A paired test on the same tasks. Only tasks where exactly one of the two methods was right "
                  "count. p is the chance of a split at least this lopsided if both methods were equally good. "
                  "p < 0.05 is the usual line for 'a real difference'."),
    ("Tokens/task", "Prompt plus completion tokens for every call, the policy's calls included. The cost side."),
    ("Tokens per correct", "All tokens divided by correct answers: what one right answer costs."),
]

AGENT_OF = (
    ("You are the central orchestrator", "policy"),
    ("You are an expert task planner", "Planner"),
    ("You are an expert in logical reasoning", "Reasoning"),
    ("You are a critical reviewer", "Critic"),
    ("You are the final synthesis agent", "Concluder"),
    ("You are the episode terminator", "Terminator"),
)

# ─────────────────────────────────────────────────────────────── the model side


class CacheMiss(RuntimeError):
    pass


class StoredReplies:
    """Stands in for the model during a replay. CachingClient answers every call from the stored replies;
    reaching this means the request was never made in the recorded run, so nothing is invented."""

    def chat(self, **_):
        raise CacheMiss("no stored reply for this request")


class TracingClient(CountingClient):
    """CountingClient (which counts every call, the policy's too) plus a note of who spoke and what they said."""

    def __init__(self, inner):
        super().__init__(inner)
        self.trace: list[tuple[str, str]] = []

    def reset(self) -> None:
        super().reset()
        self.trace = []

    def chat(self, model, messages, *args, **kwargs):
        reply = super().chat(model, messages, *args, **kwargs)
        system = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
        who = next((name for prefix, name in AGENT_OF if system.startswith(prefix)), "model")
        self.trace.append((who, reply.get("content") or ""))
        return reply


def shown_answer(kind: str, reply: str) -> str:
    """The value the checker actually compared, as the checker parsed it."""
    seg, tagged = checkers.final_segment(reply)
    if kind == "number":
        value = checkers.parse_number(seg, tagged)
        text = checkers.format_number(value) if value is not None else "no number"
    elif kind == "letter":
        text = checkers.parse_letter(seg, tagged) or "no letter"
    else:
        line = next((ln.strip() for ln in seg.splitlines() if ln.strip()), "")
        text = line[:48] or "empty"
    return text if tagged else f"{text} (no FINAL ANSWER tag)"


def one_line(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."

# ─────────────────────────────────────────────────────────────── drawing


def pct(x) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def number(x) -> str:
    return "-" if x is None else f"{x:,.0f}"


def score_table(summary: dict, scope: str, title: str, ms: list[str]) -> Table:
    t = Table(title=title, box=box.SIMPLE_HEAVY, title_justify="left", pad_edge=False)
    for col, just in (("method", "left"), ("correct", "right"), ("accuracy", "right"), ("95% CI", "right"),
                      ("tokens/task", "right"), ("calls/task", "right"), ("tokens/correct", "right")):
        t.add_column(col, justify=just)
    entry = summary.get(scope, {}).get("methods", {})
    for m in ms:
        s = entry.get(m)
        if not s:
            t.add_row(m, "-", "-", "-", "-", "-", "-")
            continue
        lo, hi = s["ci95"]
        t.add_row(m, f"{s['correct']}/{s['n']}", pct(s["accuracy"]), f"{pct(lo)} to {pct(hi)}",
                  number(s["tokens"]), f"{s['calls']:.1f}", number(s["tokens_per_correct"]))
    return t


def paired_table(summary: dict, scope: str) -> Table | None:
    paired = summary.get(scope, {}).get("paired", {})
    if not paired:
        return None
    t = Table(box=box.SIMPLE, pad_edge=False, title="puppeteer against each, on the same tasks", title_justify="left")
    for col in ("against", "shared", "only puppeteer right", "only other right", "McNemar p", "reading"):
        t.add_column(col, justify="left" if col in ("against", "reading") else "right")
    for m, p in sorted(paired.items()):
        if p["shared"] == 0:
            continue
        verdict = ("significant" if p["p_mcnemar"] < 0.05 else
                   "no evidence of a difference" if p["ours_only"] + p["theirs_only"] else "identical results")
        t.add_row(m, str(p["shared"]), str(p["ours_only"]), str(p["theirs_only"]),
                  "< 0.001" if p["p_mcnemar"] < 0.001 else f"{p['p_mcnemar']:.3f}", verdict)
    return t


def scoreboard(rows: list[dict], ms: list[str], done: int, total: int, mode: str) -> Panel:
    summary = stats.summarize(rows)
    parts = [score_table(summary, "all", f"Running score, {done} of {total} tasks", ms)]
    paired = paired_table(summary, "all")
    if paired is not None:
        parts.append(paired)
    return Panel(Group(*parts), title=f"[b]{mode}[/b]", border_style="magenta")


def explain_dataset(console: Console, name: str) -> None:
    info = DATASET_INFO[name]
    body = Text()
    for label, key in (("What it tests", "tests"), ("Where it is from", "origin"), ("The answer", "answer"),
                       ("How it is scored", "scored"), ("Guessing gives", "chance"),
                       ("So accuracy means", "means"), ("Keep in mind", "caveat")):
        body.append(f"{label}: ", style="bold")
        body.append(info[key] + "\n")
    body.rstrip()
    console.print(Panel(body, title=f"[b]{info['name']}[/b]", border_style="cyan"))


def explain_methods(console: Console) -> None:
    t = Table(box=box.SIMPLE, pad_edge=False, title="The four methods: one model, the same cap per call",
              title_justify="left")
    t.add_column("method")
    t.add_column("calls")
    t.add_column("what it is")
    for m, (calls, what) in METHOD_INFO.items():
        t.add_row(m, calls, what)
    m2 = Table(box=box.SIMPLE, pad_edge=False, title="The numbers", title_justify="left")
    m2.add_column("metric")
    m2.add_column("what it tells you")
    for name, what in METRICS:
        m2.add_row(name, what)
    note = Text("The comparison that tests the thesis is puppeteer against fixed: the same agents and the same "
                "budget per call, so the policy's choices are the only difference. Puppeteer against cot asks "
                "whether several agent steps beat one careful call.", style="italic")
    console.print(Panel(Group(t, m2, note), title="[b]How to read what follows[/b]", border_style="cyan"))


def show_row(console: Console, row: dict, trace, recorded: dict | None, steps: bool, kind: str) -> None:
    ok = row["correct"]
    mark = "[red]ERR[/red]" if row["provider_error"] else ("[green]right[/green]" if ok else "[red]wrong[/red]")
    path = ""
    if row["method"] in ("fixed", "puppeteer"):
        path = " > ".join(a.replace("Agent", "") for a in row["agents"])
    rec = ""
    if recorded is not None:
        same = recorded["correct"] == ok and recorded["tokens"] == row["tokens"]
        rec = "  [dim]= recorded run[/dim]" if same else (
            f"  [yellow]recorded run: {'right' if recorded['correct'] else 'wrong'}, "
            f"{recorded['tokens']:,} tokens[/yellow]")
    answer = "no answer" if row["provider_error"] else shown_answer(kind, row["reply"])
    console.print(f"  {row['method']:<10} {mark:<20} answer [b]{answer}[/b]   "
                  f"{row['tokens']:,} tokens, {row['calls']} call{'s' if row['calls'] != 1 else ''}"
                  f"{('   ' + path) if path else ''}{rec}")
    if row["provider_error"]:
        console.print(f"      [red]{row['provider_error']}[/red]")
    if steps and row["method"] in ("fixed", "puppeteer"):
        for who, text in trace:
            if who == "policy":
                console.print(f"      [magenta]policy picks[/magenta] {one_line(text, 60)}")
            else:
                console.print(f"      [cyan]{who:<10}[/cyan] {one_line(text, 140)}")

# ─────────────────────────────────────────────────────────────── the run


def pick_tasks(args) -> list[dict]:
    if args.task:
        found = [t for t in datasets.load_tasks(list(DATASETS)) if t["id"] == args.task]
        if not found:
            raise SystemExit(f"No task {args.task!r}. Ids look like gsm8k-15, gsm-hard-259, mmlu-pro-8212, bbh-navigate-3.")
        return found
    names = [d.strip() for d in args.datasets.split(",") if d.strip()]
    bad = [d for d in names if d not in DATASETS]
    if bad:
        raise SystemExit(f"Unknown dataset {bad}. Choose from {', '.join(DATASETS)}.")
    tasks = []
    for name in names:
        ts = datasets.load_tasks([name])
        tasks += ts[: args.limit] if args.limit else ts
    return tasks


def recorded_doc() -> dict | None:
    try:
        return json.loads(RECORDED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def confirm_cost(console: Console, doc: dict | None, tasks: list, ms: list[str], yes: bool) -> bool:
    per = None
    if doc:
        entry = doc.get("summary", {}).get("all", {}).get("methods", {})
        known = [entry[m]["cost_usd"] for m in ms if entry.get(m, {}).get("cost_usd") is not None]
        if len(known) == len(ms):
            per = sum(known)
    estimate = f"about ${per * len(tasks):.4f}" if per is not None else "unknown"
    console.print(Panel(
        f"[b]--fresh makes new calls to Nebius ({MODEL}) and spends real credit.[/b]\n"
        f"{len(tasks)} tasks x {len(ms)} methods. Estimated cost from the recorded run's average: {estimate}.\n"
        "Answers are sampled at temperature 0.7, so some scores can differ from the recorded run. "
        "This is a demonstration, not the reported result.", border_style="yellow"))
    if yes:
        return True
    try:
        return input("Continue? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def final_report(console: Console, rows: list[dict], ms: list[str], doc: dict | None, mode: str,
                 complete_set: bool) -> None:
    summary = stats.summarize(rows)
    scopes = [d for d in DATASETS if d in summary] + ["all"]
    for scope in scopes:
        title = "All datasets" if scope == "all" else DATASET_INFO[scope]["name"]
        parts = [score_table(summary, scope, title, ms)]
        paired = paired_table(summary, scope)
        if paired is not None:
            parts.append(paired)
        console.print(Panel(Group(*parts), border_style="magenta"))

    errors = sum(1 for r in rows if r.get("provider_error"))
    lines = [f"{len({r['task'] for r in rows})} tasks, {len(rows)} rows, one seed. "
             f"Rows with a provider error, excluded from the scores: {errors}."]
    if mode == "replay" and doc:
        recorded = {(r["task"], r["method"]): r for r in doc.get("rows", [])}
        same = sum(1 for r in rows if (rec := recorded.get((r["task"], r["method"])))
                   and rec["correct"] == r["correct"] and rec["tokens"] == r["tokens"])
        lines.append(f"Recomputed from the stored replies: {same} of {len(rows)} rows give the same verdict and "
                     "token count as the run file.")
        if not complete_set:
            lines.append("This is a subset. The reported result is the full 224 tasks in docs/BENCHMARKS.md.")
    a = summary.get("all", {})
    pf = a.get("paired", {}).get("fixed")
    if pf and pf["shared"]:
        lines.append(f"Puppeteer against fixed: {pf['ours_only']} tasks only the puppeteer got right, "
                     f"{pf['theirs_only']} only fixed got right, McNemar p "
                     f"{'< 0.001' if pf['p_mcnemar'] < 0.001 else f'{pf['p_mcnemar']:.3f}'}. "
                     + ("A significant difference." if pf["p_mcnemar"] < 0.05 else
                        "No evidence the policy's choices beat the fixed order."))
    mm = a.get("methods", {})
    if "puppeteer" in mm and "fixed" in mm and mm["fixed"]["tokens"]:
        extra = mm["puppeteer"]["tokens"] / mm["fixed"]["tokens"] - 1
        lines.append(f"Tokens: the puppeteer used {extra:+.0%} against fixed, from its policy calls and a context "
                     "that grows with every step.")
    console.print(Panel("\n".join(lines), title="[b]What this shows[/b]", border_style="green"))


def guide(console: Console, doc: dict | None) -> None:
    console.print(Panel(
        "The thesis question: does orchestration buy correctness, and at what token cost, against simpler\n"
        "methods on the same model? Four public benchmarks, four methods, one model (Nebius,\n"
        f"{MODEL}), thinking off for every call, one seed, 224 frozen tasks.",
        title="[b]Reasoning benchmark for the thesis core[/b]", border_style="cyan"))
    explain_methods(console)
    for name in DATASETS:
        explain_dataset(console, name)
    console.print(Panel(
        "- Every method's reply goes through the same checker (checkers.py). No method gets a kinder parser.\n"
        "- datasets.py --check proves each of the 224 tasks' checker accepts its gold answer and rejects a "
        "wrong one.\n"
        "- The scored text is what follows the last 'FINAL ANSWER:'. Without the tag, the same fallback applies "
        "to all: the last number, a bare letter, or an 'answer is X' phrase.\n"
        "- For fixed and puppeteer the scored reply is the step the engine's own get_final_answer() picks.\n"
        "- Every call is counted, the policy's too (core.Orchestrator itself leaves those out).\n"
        "- Provider errors are excluded from scores and counted (0 in the recorded run).\n"
        "- After the last checker fix, rescoring the 896 stored replies changed 0 verdicts.",
        title="[b]Why the scoring is fair[/b]", border_style="cyan"))
    if doc and doc.get("summary"):
        ms = list(doc.get("methods") or methods.METHODS)
        console.print(Panel(Group(score_table(doc["summary"], "all", "Recorded result, all 224 tasks", ms),
                                  paired_table(doc["summary"], "all") or Text("")),
                            border_style="magenta"))
    console.print(Panel(
        "[b]Is the puppeteer better?[/b] Than one call, clearly (p < 0.001 against both). Than the fixed "
        "pipeline, no: 78.1% against 76.8%, 12 tasks against 9, p 0.66. The gain comes from giving the model "
        "several steps, not from the policy's choices.\n"
        "[b]Why is direct so low?[/b] Thinking is off, so a single answer-only call has no room to reason. That "
        "is why puppeteer against fixed is the comparison that matters.\n"
        "[b]Why does the puppeteer cost more?[/b] About 52% more tokens than fixed: one policy call per step, and "
        "each agent is resent all earlier steps (Memory.get_context_for_agent).\n"
        "[b]Is the policy trained?[/b] No. It is the LLM policy with the engine's default prompt. It opened with "
        "Reasoning in 188 of 224 episodes and never chose the Terminator.\n"
        "[b]Why thinking off?[/b] With it on, hidden reasoning used up the engine's output caps and the policy's "
        "reply stopped being an agent name. Off for every method is the fair fix; on is future work.\n"
        "[b]How sure are these numbers?[/b] One seed and 50 to 70 tasks per dataset: intervals are about +/- 6 "
        "points overall and +/- 13 per dataset. Small gaps are noise, which is why McNemar is reported.\n"
        "[b]Contamination?[/b] Possible for GSM8K and MMLU-Pro. It affects all methods alike.\n"
        "[b]Why not GAIA or ToolBench?[/b] Both need tools (web, files, code, RapidAPI) the core agents do not "
        "have, and GAIA is gated. Running them tool-less would measure nothing useful.\n"
        "[b]Can it be reproduced?[/b] Yes: the tasks are frozen in the repo, and this script recomputes every "
        "score from the stored replies with no network.",
        title="[b]Viva notes: likely questions[/b]", border_style="green"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", default=",".join(DATASETS))
    ap.add_argument("--methods", default=",".join(methods.METHODS))
    ap.add_argument("--limit", type=int, default=0, help="first N tasks per dataset (default all)")
    ap.add_argument("--task", default=None, help="one task id, shown in full detail")
    ap.add_argument("--steps", action="store_true", help="show every agent step of fixed and puppeteer")
    ap.add_argument("--pace", type=float, default=0.0, help="seconds to pause after each task, for presenting")
    ap.add_argument("--quiet", action="store_true", help="no explanation panels, rows and score only")
    ap.add_argument("--explain", action="store_true", help="print the guide and the recorded result, run nothing")
    ap.add_argument("--fresh", action="store_true", help="new calls to Nebius (costs money) instead of the replay")
    ap.add_argument("--yes", action="store_true", help="with --fresh: do not ask before spending")
    ap.add_argument("--out", default=None, help="with --fresh: where to save (default runs/reasoning-live-<time>.json)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger().setLevel(logging.ERROR)
    console = Console(highlight=False)
    doc = recorded_doc()

    if args.explain:
        guide(console, doc)
        return 0

    ms = [m.strip() for m in args.methods.split(",") if m.strip()]
    unknown = [m for m in ms if m not in methods.RUNNERS]
    if unknown:
        raise SystemExit(f"Unknown method {unknown}. Choose from {', '.join(methods.METHODS)}.")
    tasks = pick_tasks(args)
    steps = args.steps or bool(args.task)
    mode = "fresh" if args.fresh else "replay"
    methods.MAX_TOKENS = (doc or {}).get("config", {}).get("max_tokens", methods.MAX_TOKENS)

    if mode == "replay":
        if not CACHE.is_dir():
            raise SystemExit(f"The stored replies are not here ({CACHE}). They live only on the machine that ran the "
                             "benchmark. Use --explain, or --fresh to call the model again.")
        from puppeteer_code.providers import CachingClient
        client = TracingClient(CachingClient(StoredReplies(), CACHE))
        title = "Replay: the recorded run's own replies, recomputed. $0, no network."
    else:
        if not confirm_cost(console, doc, tasks, ms, args.yes):
            console.print("Nothing was sent.")
            return 1
        try:
            client = TracingClient(make_inner("nebius", None, 0, "off"))
        except Exception as e:     # no key, most often; the message never contains the key
            raise SystemExit(f"Could not reach Nebius: {type(e).__name__}. Set NEBIUS_API_KEY in .env.") from None
        title = "Fresh: new calls to Nebius. A demonstration, not the reported result."

    recorded = {(r["task"], r["method"]): r for r in (doc or {}).get("rows", [])}
    prices = prices_for("nebius")
    console.print(Panel(f"[b]{title}[/b]\nModel {MODEL}, thinking off, output cap {methods.MAX_TOKENS} per call, "
                        f"{len(tasks)} tasks x {len(ms)} methods.", border_style="magenta"))
    if not args.quiet:
        explain_methods(console)

    rows: list[dict] = []
    out = None
    if mode == "fresh":
        out = Path(args.out) if args.out else ROOT / "runs" / f"reasoning-live-{_dt.datetime.now():%Y%m%d-%H%M%S}.json"
        out.parent.mkdir(parents=True, exist_ok=True)

    seen_dataset = set()
    stopped = None
    try:
        with Live(scoreboard(rows, ms, 0, len(tasks), mode), console=console, refresh_per_second=4,
                  transient=False, vertical_overflow="visible") as live:
            for n, task in enumerate(tasks, 1):
                if task["dataset"] not in seen_dataset:
                    seen_dataset.add(task["dataset"])
                    if not args.quiet:
                        explain_dataset(console, task["dataset"])
                sub = task.get("meta", {}).get("subtask")
                console.rule(f"[b]{DATASET_INFO[task['dataset']]['name']}[/b]  {task['id']}"
                             f"{f'  ({sub})' if sub else ''}  [dim]{n}/{len(tasks)}[/dim]", align="left")
                question = task["question"] if steps else one_line(task["question"], 260)
                console.print(Text(question, style="dim"))
                console.print(f"  gold answer [b]{task['gold']}[/b]")
                for method in ms:
                    row = run_row(client, MODEL, method, task, 0, prices)
                    rows.append(row)
                    show_row(console, row, list(client.trace), recorded.get((task["id"], method)) if mode == "replay"
                             else None, steps, task["kind"])
                    live.update(scoreboard(rows, ms, n, len(tasks), mode))
                if out is not None:
                    out.write_text(json.dumps({"benchmark": "Reasoning", "demo": True, "mode": "fresh",
                                               "provider": "nebius", "model": MODEL, "rows": rows,
                                               "summary": stats.summarize(rows)}, indent=1), encoding="utf-8")
                if args.pace:
                    time.sleep(args.pace)
    except KeyboardInterrupt:
        stopped = "Stopped by you."
    except QuotaStop as e:
        stopped = f"Nebius quota stop: {e}"

    if stopped:
        console.print(f"[yellow]{stopped} The scores below cover the {len({r['task'] for r in rows})} tasks done.[/yellow]")
    if rows:
        complete = len({r["task"] for r in rows}) == 224 and not args.task
        final_report(console, rows, ms, doc, mode, complete)
    if out is not None and rows:
        console.print(f"Saved to {out}. It is a demonstration and is never merged into the recorded result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
