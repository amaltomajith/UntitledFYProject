# CLAUDE.md: project context and handoff

This file is the single source of truth for anyone (human or coding agent) continuing this
project. Read it fully before changing anything. `README.md` holds the theory and results for
the thesis; this file holds state, plan, setup and the rules of work.

**Keep it current.** At the end of every work session, update §8 (Status) and §9 (Next steps),
dated. Several people take turns on this repo, and the next person knows only what is written
here.

---

## 1. What this project is

- **Puppeteer++** is a B.Tech (IT) final-year thesis at Christ University, Bengaluru, by Amal Tom
  Ajith (GitHub `amaltomajith`). Roshan Joseph Thomas owns a separate application layer
  ("NeuralForge"), which is not in this repo.
- The project extends the NeurIPS 2025 paper *Multi-Agent Collaboration via Evolving
  Orchestration*: a central "puppeteer" policy picks which agent acts next, and is trained with
  REINFORCE to be both correct and cheap.
- **Puppeteer-Code** (`puppeteer_code/`) is the application and the current focus: a
  terminal coding assistant that fixes failing tests.
  - It works in a git sandbox, routes each role to a cheap or a strong model, rolls back bad
    edits, and returns a verified diff.
  - It is being built for the **Nebius AI Builder Program** (https://dev.nebius.com/builders).
    The program gives Token Factory credits plus Tavily, LangSmith and Toloka perks.
- Faculty review this work in viva-style project reviews (Review 1 was about Sep 2026).
  **Every claim must survive questioning.** Do not overstate results.

## 2. Rules of work

1. **Git.** Commit or push only when the person you are working with asks. Branch first if you
   are on `main`. Remote: `github.com/amaltomajith/UntitledFYProject`.
2. **Keys.** Never print, log or commit API keys. Keys live only in `.env`, which is gitignored.
   `refs/` (the owner's private review material) is gitignored too, because it contains a key
   file. Never un-ignore it.
3. **Results honesty.**
   - `--provider sim` (simulated model) and `--provider oracle` runs test the mechanisms only.
     They are **never** reported as results.
   - Real numbers come from Groq or Nebius runs. Say how many tasks and seeds they cover.
   - Provider errors are excluded from aggregates and must be reported as such.
4. **User files.** `pcode fix` never writes to the user's repo except via `--apply` after
   confirmation (or MCP `apply=true` on a successful run). Keep it that way.
5. **Tests.** Run the offline tests after changes; they need no key. Do not weaken a test to
   make it pass.
6. **Machine.** The owner's Windows machine runs low on memory. Run one benchmark at a time,
   not in parallel.

## 3. Setup

Python 3.10+ (developed on 3.13, Windows 11; bash and PowerShell both work).

```bash
python -m venv .venv
.venv\Scripts\activate            # bash: source .venv/Scripts/activate (Win) or .venv/bin/activate
pip install -r requirements.txt
pip install -e .                  # optional: gives the `pcode` command (else: python -m puppeteer_code)
pip install mcp ruff              # optional: MCP server; ruff enables the lint signal
copy .env.example .env            # then fill in keys (see §4)
```

## 4. API keys and providers

| Key in `.env` | Status | Notes |
|---|---|---|
| `GROQ_API_KEY` | **Required for real runs today.** Each person uses their own. | Free at https://console.groq.com. Free tier per model: 1000 requests/day, 8000 tokens/min, and a daily token cap (hit after about 7 tasks × 4 methods on gpt-oss-120b). |
| `NEBIUS_API_KEY` | **Missing.** Nebius Token Factory sign-up needs a payment card, and the owner has none yet. | https://tokenfactory.nebius.com. Joining the Builder Program gives $25 credit. When a key exists, run `pcode models --provider nebius` first: tier models resolve against the live catalog. |
| `TAVILY_API_KEY` | Optional, not set | Lets DocsSearchAgent search the web; without it, it falls back to `pydoc`. |

- **The default provider in `models.yaml` is `nebius`**, so until a Nebius key exists, pass
  `--provider groq` to every real command. `--provider mock` and `sim` need no key.
- Groq models (as `models.yaml` resolves them):
  - FAST = `openai/gpt-oss-20b`
  - QUALITY = `openai/gpt-oss-120b`
  - Override with `--fast-model` / `--quality-model`.
- TLS: certificates are always verified (via `truststore`). On a proxy network, set
  `PUPPETEER_CA_BUNDLE=/path/ca.pem`.

## 5. Repository map

```
core/            Puppeteer++ engine (thesis core): agent.py (OpenAI-compatible clients: Groq,
                 Nebius; agents; ModelTier), environment.py (state transition Φ, stopping rules),
                 memory.py, orchestrator.py (LLM policy loop)
training/        reward.py (R = γR' − λC + βs; optional token_budget), reinforce.py (prompt-space
                 policy improvement for the core engine, NOT gradient REINFORCE)
extensions/      step_critic.py (Ext. 1, done), agent_registry.py (Ext. 2: dynamic agent injection;
                 wired into Puppeteer-Code only). Ext. 3 "dissent detector" is planned, not started.
puppeteer_code/  the coding assistant (details in §6)
benchmarks/code_tasks/   build_tasks.py → tasks/ (PCodeBench-Mini, 37 tasks)
                         build_repo_tasks.py → tasks_repo/ (PCodeBench-Repo, 44 tasks)
benchmarks/token_comparison.py, run_live_proof.py, results/   LEGACY, see §10
tests/           test_agent.py (core) + test_code_*.py (Puppeteer-Code), all offline
checkpoints/     pcode_linear_sim.json = LinearPolicy weights trained on SIM only;
                 policy_ep*.json = core engine prompt-policy snapshots
runs/            benchmark/train outputs and the Groq response cache (gitignored, local only)
```

The landing website is on branch `worktree-pcode-landing`, commit `5acbe9a`: `web/`, Next.js 16
static export, design rules in `web/DESIGN.md`. It is not merged and not pushed.

## 6. How Puppeteer-Code works

`pcode fix "<bug report>" --repo PATH` copies the repo into a temporary git sandbox. Each step
then runs this loop:

```
registry.update()      inject TestWriter / DocsSearch / Explorer when the state calls for them
allowed = shield(S)    hard rules remove pointless/unsafe actions (shield.py)
a = policy(S, allowed) heuristic | linear (numpy REINFORCE) | llm | fixed | random  (policy.py)
o = a.run(env)         agent sees a BOUNDED snapshot (snapshot.py), never a growing transcript
S' = Φ(S, o)           tests decide: checkpoint an improvement, otherwise git reset to best
```

**Agents** (`agents.py`). Tier is set in `models.yaml`.
- Zero-token tools: RepoScout (AST symbol ranking), TestRunner, Linter, DocsSearch.
- FAST tier: Planner, Reviewer, Concluder, Terminator.
- QUALITY tier: Coder, Debugger, TestWriter (the only agent allowed to edit tests).
- Explorer (`explore.py`) runs 3 variants in parallel git worktrees and promotes the best.

**Edits** are SEARCH/REPLACE blocks (`tools.py`), applied atomically with whitespace-tolerant
matching. An agent gets one repair turn if a block does not match.

**Sandbox rules** (`environment.py`, `workspace.py`, `integrity.py`):
- Any of these roll back to the best checkpoint: a syntax error, a regression, a lower pass
  rate, or no improvement.
- The integrity guard blocks:
  - test-file edits by non-TestWriter agents,
  - a falling test count,
  - added skip/xfail markers,
  - removed asserts.
- Tests run with secrets scrubbed from the environment.
- The baseline test run is retried once and the episode aborts if it still fails, so a flaky
  run cannot fake a "no failing tests" baseline.

**Reward** (`reward.py`):
- Terminal: r = pass rate + 0.5·success − 0.5·regression − 1·integrity + 0.1·lint-clean.
- Step: signed pass-rate change, credited to the agent that wrote the code.
- Cost: tokens / token_budget (4000).
- `LinearPolicy` learns by true REINFORCE with a baseline.

**Other entry points**
- `bench.py`: runs methods × tasks × seeds, scores hidden holdout tests, stops on quota and
  saves partial results (`--resume` continues). Baselines are in `baselines.py`:
  - `single-shot`: one call with all source files.
  - `react`: all source files plus a growing transcript.
  - `react-tools`: file list, READ/GREP tools, growing transcript.
- `report.py`: a self-contained HTML report.
- `mcp_server.py`: an MCP stdio server exposing `puppeteer_fix` and `puppeteer_models`.
- `mock.py`:
  - `scripted_responder` gives fixed replies, for tests.
  - `simulated_responder` is the "sim" provider. It can only fix code that appears in the
    prompt, and succeeds with fixed probabilities.

## 7. Commands

```bash
pytest tests -q                                   # all offline, ~10 min (MCP tests skip without `mcp`)
pytest tests/test_code_bench.py -q                # faster subset

python -m puppeteer_code fix "mean() is wrong" --repo PATH --provider groq
python -m puppeteer_code fix "..." --repo PATH --provider groq --apply   # asks before applying
python -m puppeteer_code replay runs/fix-<time>.json --diff
python -m puppeteer_code models --provider groq

python benchmarks/code_tasks/build_tasks.py --check        # rebuild + verify Mini (37 tasks)
python benchmarks/code_tasks/build_repo_tasks.py --check   # rebuild + verify Repo (44 tasks, slow)

# real benchmark (Groq); add --resume to continue after a quota stop
python -m puppeteer_code bench --provider groq --cache runs/cache/groq \
  --tasks benchmarks/code_tasks/tasks_repo --split test \
  --methods heuristic,react-tools,single-shot,react --seeds 1 --out runs/bench-groq-repo.json

python -m puppeteer_code train --provider sim --episodes 200   # mechanism check only
python -m puppeteer_code report --bench runs/<bench>.json --curve runs/<train>.json
python -m puppeteer_code mcp      # MCP server; register: claude mcp add puppeteer-code -- python -m puppeteer_code mcp
```

- `--policy linear` defaults to `checkpoints/pcode_linear.json`, which does not exist yet. Pass
  `--weights checkpoints/pcode_linear_sim.json` until a real-model training run produces one.
- `--seeds N` is a count, not a list.

## 8. Status (2026-10-30)

**Done**
- Engine fixes:
  - The SSL bypass was removed.
  - Reward cost can be normalised.
  - The stopping rules are overridable.
  - Groq Retry-After is honoured.
  - A Groq `tool_use_failed` error (gpt-oss calling its built-in tools) is returned as text,
    not raised.
- Puppeteer-Code: requirements A–F of the plan, all done:
  - A: tiered routing
  - B: bounded context
  - C: AST scouting with call-chain ranking
  - D: sandbox, rollback and integrity guard
  - E: parallel exploration
  - F: objective rewards and REINFORCE
- CLI commands: `fix`, `replay`, `models`, `train`, `bench`, `report`, `mcp`.
- Benchmarks:
  - PCodeBench-Mini: 37 tasks, 7 modules, train/test split by module, hidden tests.
  - PCodeBench-Repo: 44 tasks in one shared repo, including 7 cross-module `repo_x_*` bugs
    whose only visible failure is in `test_report.py`.
- Baselines: single-shot, react, react-tools.
- Offline test suite green (last full run: 113 passed, 1 skipped; about 8 tests were added since
  and pass).

**Real results so far** (Groq gpt-oss, seed 0). Details are in README §14.4 and `runs/*.json`.
- Mini test split (10 tasks): every method succeeds on every task. Tokens per task:

  | method | tokens/task |
  |---|---|
  | single-shot | 1,024 |
  | react | 1,837 |
  | heuristic | 1,886 |
  | fixed pipeline | 9,879 |

- Repo test split: only the first 7 of 17 tasks are done, because the run stopped at the daily
  token cap. All methods succeed. Heuristic used 2,391 tokens/task with a 3.3k-char max prompt,
  against about 3.1k tokens and 10.3k chars for single-shot and react. react-tools used about
  9.2k tokens, from 2 runs.
- **Honest takeaway so far:**
  - These bugs are easy for a 120B model, so success rate does not separate the methods yet.
  - Puppeteer-Code is cheaper per task on the multi-file repo, with much smaller prompts.
  - The harder settings (the cross-module tasks, and a 20B model in the QUALITY tier) are
    not measured yet.

**Interrupted (partial results saved; resume, don't restart)**
- `runs/bench-groq-repo.json`: gpt-oss-120b, 7 of 17 tasks.
- `runs/bench-groq-repo-20b.json`: 20B small-model setting, 1 task.
- `runs/bench-sim-repo.json`: sim run. Note that sim numbers made before the per-task seeding
  fix (2026-10-30) are invalid.

These folders are local to the owner's machine (`runs/` is gitignored). Someone starting fresh
must rerun the benchmarks.

## 9. Next steps (in order)

1. **Finish the real Groq benchmarks**, one at a time, with `--resume`:
   - the 120b run on the repo test split (command in §7);
   - the same run with `--quality-model openai/gpt-oss-20b --out runs/bench-groq-repo-20b.json`;
   - then the Mini test split with the same 4 methods.
   - Add seeds 1–2 if the quota allows.
2. **Write up the results** in README §14.4: replace the "preliminary" block. Generate
   `pcode report`.
3. **Train LinearPolicy on real models**, train split, small episode count, response cache on.
   Compare it against the heuristic policy on the test split.
4. **Nebius**, once a key exists:
   - `pcode models --provider nebius`, then fix prices in `models.yaml`.
   - Rerun the benchmark on Nebius, with `$ per fix`.
5. **Remaining "cherry" features**, in priority order:
   1. Role×Model cost matrix (`bench --matrix`, auto-writes `models.yaml`).
   2. Live terminal UI plus replay polish.
   3. LangSmith tracing (Builder perk).
   4. Budget mode in the policy features (`--budget-usd` already exists as a hard cap).
   5. GitHub Action: on red CI, run `pcode fix` and post a suggestion.
   6. Stretch goals: LoRA fine-tune on successful trajectories; Toloka human review of patches.
6. **Submission polish**:
   - a 30-second terminal GIF,
   - a results table (pass@1 · tokens · $ per fix · steps),
   - a cookbook-style write-up,
   - a licence. Apache-2.0 was proposed, but the **owner must decide** because of possible
     university IP rules.
7. **Thesis core, still open**:
   - Extension 3 (dissent detector).
   - A fair benchmark for the general reasoning orchestrator in `core/` (see §10).
8. **Website**: review and merge branch `worktree-pcode-landing`. The next frontend step is a
   live run dashboard.

## 10. Known limitations and legacy

- `benchmarks/token_comparison.py`, `run_live_proof.py` and `results/` are legacy. They do
  **not** use `core.Orchestrator` (they run a hard-coded agent sequence), and they do not check
  answers. Do not quote their numbers. The old token report and dashboard were removed for
  this reason; they are in git history at `bda62f3`.
- Core engine (`core/`, `training/`) issues, documented in README §12:
  - Token cost swamps the 0/1 terminal reward unless `token_budget` is set.
  - The terminal reward is "finished", not "correct".
  - The core REINFORCE is in-context prompting.
  - Agent context is unbounded.
  - Puppeteer-Code fixes these for coding tasks only.
- `AgentRegistry` is wired into Puppeteer-Code, not into `core.Orchestrator`.
- The scout's top-4 snippets contain the buggy lines in 80 of 81 benchmark tasks. The miss
  (`repo_x_two`) needs a second scout after the first fix, which the policy does.

## 11. Gotchas (learned the hard way)

- **Windows subprocesses.** Every `subprocess.run` passes `stdin=subprocess.DEVNULL`. Without
  it, git and pytest inherit the MCP server's stdin pipe and hang. Keep this in new code.
- **Console encoding.** The CLI reconfigures stdout to UTF-8 because rich crashed on cp1252
  consoles.
- **gpt-oss on Groq** sometimes emits built-in tool calls (`repo_browser.open_file`,
  `apply_patch`) even when no tools are offered.
  - `core/agent.py` returns these as text.
  - `baselines._native_tool_call` maps them to READ, GREP or SEARCH/REPLACE.
  - Tokens for such calls are unknown and counted as 0.
- **The sim responder must be seeded per (seed, task)** (`bench.make_task_session`). A shared
  seed made every task draw the same random numbers.
- **Test IDs** from the JUnit parser look like `tests.test_report::test_name`, not
  `tests/test_report.py::test_name`.
- **Groq quota stop.** A `Retry-After` over 90 s means a daily cap: `bench` stops and saves.
  Rerun with `--resume`. Rows with a provider error are retried; rows without one are kept.
- **The response cache** (`--cache runs/cache/groq`) is per seed, so reruns are free. Delete
  the cache dir to force fresh samples.
- **Bash heredocs** can mangle `\n` inside Python string literals written to files. Use the Edit
  tool or a script file.
