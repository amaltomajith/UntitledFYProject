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
- **Puppeteer-Code** (`puppeteer_code/`) is the application and the current focus: a coding
  assistant, in the terminal (`pcode`) and a browser IDE (`pcode ide`). You talk to it, and it
  plans, writes, runs and tests code in your folder, in any language the machine can run.
  - The owner rejected the earlier form ("a debugger"), so the assistant in
    `puppeteer_code/assistant/` is the product. The older bug fixer (`pcode fix`) is now Fix mode.
  - Both route each role to a cheap or a strong model, roll back bad edits, and report honestly
    when something could not be verified.
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
4. **User files.**
   - `pcode fix` never writes to the user's repo except via `--apply` after confirmation (or MCP
     `apply=true` on a successful run). Keep it that way.
   - The assistant is the one exception, by the owner's decision: it edits the user's folder,
     after a snapshot, with Undo. Its tools stay inside the project and refuse `.env*`, key files
     and `.git`. Commands that are not known safe need approval in `ask` mode.
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

On Windows, `start-pcode.bat` does all of this and opens the IDE (see §8: not yet run from scratch).
The IDE and `pcode` also ask for a key on first run and save it to `~/.pcode/.env`.

## 4. API keys and providers

| Key in `.env` | Status | Notes |
|---|---|---|
| `NEBIUS_API_KEY` | **The default, in use since 2026-10-30.** Each person uses their own. Runs spend real credit. | https://tokenfactory.nebius.com. Run `pcode models` first: tier models resolve against the live catalog. |
| `GROQ_API_KEY` | Optional. | Free at https://console.groq.com. Free tier per model: 1000 requests/day, 8000 tokens/min, and a daily token cap of about 200,000 tokens. It cannot finish PCodeBench-Build in a day. |
| `TAVILY_API_KEY` | Optional, not set | Lets DocsSearchAgent search the web; without it, it falls back to `pydoc`. |

- **The default provider is `nebius`.** The config is `puppeteer_code/models.yaml`; a `models.yaml` in
  the repo root is not read. `--provider mock` and `sim` need no key.
- Nebius models, chosen 2026-10-30 from the live catalog (all make tool calls correctly):
  - FAST = `nvidia/Nemotron-3_5-Lightning` ($0.06 in, $0.24 out per 1M tokens)
  - QUALITY = `nvidia/nemotron-3-super-120b-a12b` ($0.30 in, $0.90 out per 1M tokens)
- Groq models: FAST = `openai/gpt-oss-20b`, QUALITY = `openai/gpt-oss-120b`.
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
puppeteer_code/  Fix mode and the server (details in §6)
puppeteer_code/assistant/  the coding assistant (details in §6)
benchmarks/code_tasks/   build_tasks.py → tasks/ (PCodeBench-Mini, 37 tasks)
                         build_repo_tasks.py → tasks_repo/ (PCodeBench-Repo, 44 tasks)
benchmarks/build_tasks/  build.py → tasks/ (PCodeBench-Build, 9 build-from-a-prompt tasks)
benchmarks/token_comparison.py, run_live_proof.py, results/   LEGACY, see §10
tests/           test_agent.py (core) + test_code_*.py (Puppeteer-Code), all offline
checkpoints/     pcode_linear_sim.json = LinearPolicy weights trained on SIM only;
                 policy_ep*.json = core engine prompt-policy snapshots
runs/            benchmark/train outputs and the Groq response cache (gitignored, local only)
```

`web/` is the site (Next.js 16 static export, design rules in `web/DESIGN.md`): landing page in
`app/(marketing)`, app pages in `app/(app)`, the IDE in `app/(ide)`, copy in `lib/content.ts`.
`puppeteer_code/server.py` is the local server behind the IDE and the console.

## 6. How Puppeteer-Code works

### The assistant (`puppeteer_code/assistant/`)

`AssistantSession` holds one conversation on one project. `send(message)` yields events, and the
terminal (`terminal.py`) and the IDE (`ide.py`) both read that one stream.

One request:
1. **Classify** with cheap rules (`is_question`). A question gets read-only tools and no snapshot.
2. **Snapshot** the folder into a shadow git in `~/.pcode/projects/<hash>/`, so the user's own
   `.git` is never touched.
3. **Plan.** The Planner writes a checklist for anything beyond a small edit.
4. **Each step:** `shield.py` filters the agents, `policy.py` picks one, and it works in a short
   tool loop. Agents: Answerer, Planner, TestWriter, Builder, Debugger, Reviewer, Responder.
5. **Verify** (`verify.py`): find and run the project's tests or build (pytest, npm, node, go, cargo,
   maven, gradle, dotnet, make, or a syntax check). A pass checkpoints. A first regression gets one
   repair attempt; a second rolls back and retries from the good state once.
6. **Respond** in plain words. Code, not the model, adds the honesty note when something failed
   or could not be verified. If it gives up, the status is `failed` and the message says so.

Limits per request: `MAX_STEPS` 12, `TOKEN_BUDGET` 60,000, and a stall stop. If the QUALITY model is
out of quota, the assistant falls back to FAST with a `notice`; benchmarks turn this off
(`fallback=False`).

Other parts:
- `toolbox.py`: `list_files`, `read_file`, `grep`, `write_file`, `edit_file`, `delete_file`,
  `run_command`, `start_server`. `call(..., allowed)` enforces each agent's tool list, so a read-only
  question cannot write even if the model names a tool it was not offered.
- `permissions.py`: allow, ask or refuse each command. This is a rule list, not a sandbox: commands
  run with the user's permissions, and the docs must keep saying so.
- `snapshots.py`: Undo restores byte for byte and keeps files the user changed afterwards. Files
  excluded by the project's own `.gitignore` are not covered.
- `context.py`: bounded prompts (`MAX_PROMPT_CHARS` 24,000).
- `policy.py`: `heuristic`, `direct` (one Builder loop, the baseline) and `linear` (REINFORCE, no real
  weights yet). The reward comes from verification, never a model's opinion.
- `ide.py`: REST and WebSocket under `/api/ide/`. A per-launch token in the URL fragment protects it
  because it can run commands; `LocalOnly` also covers WebSockets. Fix mode runs through `RunManager`.
- `buildbench.py`: PCodeBench-Build. It scores with hidden tests the model never sees.

### Fix mode (`pcode fix`)

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
pcode                                             # chat in the current folder (python -m puppeteer_code)
pcode --folder PATH --mode auto                   # --mode ask (default) | auto; --resume continues
pcode ide --folder PATH                           # browser IDE; build the site first (below)
cd web && npm ci && npm run build                 # needed once, and after any web change

pytest tests -q                                   # all offline (MCP tests skip without `mcp`)
pytest tests/test_code_bench.py -q                # faster subset
pytest tests/test_code_assistant.py -q            # the assistant

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

# PCodeBench-Build: build from a prompt, scored by hidden tests (add --resume after a quota stop)
python benchmarks/build_tasks/build.py --check             # rebuild + prove the tasks are fair
python -m puppeteer_code buildbench --provider groq --cache runs/cache/build --methods heuristic,direct \
  --out runs/buildbench-groq.json

python -m puppeteer_code train --provider sim --episodes 200   # mechanism check only
python -m puppeteer_code report --bench runs/<bench>.json --curve runs/<train>.json
python -m puppeteer_code mcp      # MCP server; register: claude mcp add puppeteer-code -- python -m puppeteer_code mcp
python -m puppeteer_code serve    # local API + built site on http://127.0.0.1:8765; build first: cd web && npm ci && npm run build
python -m puppeteer_code export-web   # real results to web/public/data; refuses sim, oracle, mock
```

- `--policy linear` defaults to `checkpoints/pcode_linear.json`, which does not exist yet. Pass
  `--weights checkpoints/pcode_linear_sim.json` until a real-model training run produces one.
- `--seeds N` is a count, not a list.

## 8. Status (2026-10-30)

**The assistant, built 2026-10-30 (uncommitted).** The product is now a general coding assistant:
terminal chat (`pcode`) and browser IDE (`pcode ide`), with Fix mode inside the IDE.
- Verified on real Groq models. These are working demos, never benchmark results:
  - Terminal: a tip calculator and a tested text-stats library from empty folders; a question that
    changed no files; Undo.
  - IDE: a to-do web app from an empty folder, running in Preview; a dark-mode follow-up; Undo
    restoring byte for byte; a declined command that did not run; save from the editor; phone-width
    notice. Fix mode: 3 of 3 tests in the sandbox, repo unchanged until Apply.
- Decisions to know about:
  - `/console` was left in place but removed from the nav. It still works with `pcode serve`.
  - The landing hero still sells MCP only. New copy should be proposed to the owner, not changed
    silently.
  - The `web_search` tool in the plan was not built.
- `start-pcode.bat` was written but has never been run from scratch.
- `web/out` must be rebuilt after any web change.

**PCodeBench-Build, built 2026-10-30, no results yet.** 9 tasks (6 Python, 3 Node), hidden acceptance
tests, each proved fair by `build.py --check`. Status of the runs:
- `runs/buildbench-groq-20b.json` (20b in both tiers) and `runs/buildbench-groq.json` (120b) each hold
  one quota-excluded row and nothing else. **There is no valid comparison.**
- An early 20b run, on code since fixed and now deleted, had the puppeteer fail `js_calc` where the
  plain loop passed at 47,795 tokens. Replaying the cache showed why: a malformed tool call was
  treated as a finished Builder, and `built` was set with no files written. Both are fixed, and a
  rerun shows the Builder retrying. Whether the puppeteer beats the plain loop is **unknown**.
- The same tasks were used to debug the engine, so they are a development set. Say so when reporting.
- `gpt-oss-20b` sometimes drops the leading `/` of a JS regex in its own output (seen in the raw
  cached reply). This is the model, not our parsing.

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
- Offline test suite green (last full run, 2026-10-30: 365 passed, 1 skipped, ~9 min).

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

**Real training, started 2026-10-30:** LinearPolicy on Groq, PCodeBench-Repo train split, 40
episodes, warm-started from the sim weights (say so when reporting it). Weights:
`checkpoints/pcode_linear_groq.json`; curve: `runs/train-groq.json`. It saves after every episode,
and a quota stop discards the failed episode. Stopped at 11 of 40 episodes on the 120b daily cap
(11 of 11 succeeded, about 3.5k tokens/episode; too few to show learning). To continue:
`python -m puppeteer_code train --provider groq --cache runs/cache/groq --tasks
benchmarks/code_tasks/tasks_repo --split train --episodes 29 --lr 0.05 --resume --weights
checkpoints/pcode_linear_groq.json --curve runs/train-groq.json`
Latest count when this was written: 36 of 40 episodes; read `runs/train-groq.json` for the current
count. Still too few to claim learning. This trains Fix mode's `LinearPolicy`. The assistant's
`LinearAssistantPolicy` has no real weights yet.

**Frontend, built and verified (2026-10-30).** The whole site works.
- Pages: landing, `/console`, `/runs`, `/runs/view?id=`, `/benchmarks`, `/docs`, `/research`, 404.
- Console: live mode through `pcode serve` (streamed steps, agent rail, prompt size per step, cancel,
  diff, apply dialog). With no server it shows run instructions and a recorded run. A page reload
  reattaches to the active run.
- Server: `puppeteer_code/server.py` (one active run, SSE with `Last-Event-ID`, cancel, apply needing
  success and `confirm: true`, Host and Origin checks, secret scrubbing, static site).
  `pcode export-web` refuses sim, oracle, mock and any run with no recorded provider.
- Engine additions: a `cancel` event (status `cancelled`), `rollback_reason` in `step` events,
  `provider` in `RunResult`.
- Verified in a real browser with a scripted model on a throwaway repo (never a result): live run with
  a real rollback, apply wrote the fix to disk, replay and slider, form errors, 409, cancel, reattach,
  offline mode, static site alone, phone width with no horizontal scroll, skip link, reduced motion.
- The shared-task table recomputes 2,503 vs 3,077 tokens from raw rows; the landing page says 19% from
  the same data.
- Recorded demo: `runs/fix-20261030-184034.json`, a real Groq fix (gpt-oss, 1,352 tokens, 3 of 3 tests)
  on the small test-fixture repo. It shows the product working and is not a benchmark result. Replace it
  with a fix on a real project when one is available: fix, `pcode export-web`, `npm run build`.
- `web/public/data/` holds the exported real results; rerun `pcode export-web` after new runs.
- Hosted static sites return 404 for Next's dotted prefetch files. Navigation still works; `pcode serve`
  maps them. No deployment exists yet.
- `runs/fix-20261030-183525.json` and `-183746.json` are real failed runs (Groq 429). They are not results.

## 9. Next steps (in order, updated 2026-10-30)

0. **Show the owner the assistant, and propose new landing hero copy.** The hero sells MCP only,
   while the product is now an assistant.
1. **Run PCodeBench-Build** when quota is back. It does not fit one day's free quota: expect several
   days, or use Nebius. Run one setting at a time and rerun with `--resume`:
   `python -m puppeteer_code buildbench --provider groq --cache runs/cache/build --methods heuristic,direct --resume --out runs/buildbench-groq.json`
   Add `--quality-model openai/gpt-oss-20b --out runs/buildbench-groq-20b.json` for the small model.
   Then write the result in README §14.7. Retrain `LinearAssistantPolicy` with `--methods linear
   --train --weights checkpoints/assistant_linear_groq.json`, on tasks kept apart from the final test.
2. **Check the hackathon deadline.** Third-party pages say the Nebius x NVIDIA Global AI Hackathon
   closes 2026-10-30 10:00 am PT (https://nebiusglobalaihackathon.devpost.com/; not confirmed on
   Devpost, which blocks automated access). Reported requirements: runs on Token Factory or AI
   Cloud, uses an NVIDIA open model, public repo with an open-source licence, demo URL, video
   under 3 minutes. `models.yaml` lists Nemotron candidates, but those IDs are unverified.
3. **Finish the real Groq Fix-mode benchmarks**, one at a time, with `--resume`:
   - the 120b run on the repo test split (command in §7);
   - the same run with `--quality-model openai/gpt-oss-20b --out runs/bench-groq-repo-20b.json`;
   - then the Mini test split with the same 4 methods.
   - Add seeds 1–2 if the quota allows.
4. **Write up the results** in README §14.4: replace the "preliminary" block. Generate
   `pcode report`.
5. **Train LinearPolicy on real models**, train split, small episode count, response cache on.
   Compare it against the heuristic policy on the test split.
6. **Nebius**, once a key exists:
   - `pcode models --provider nebius`, then fix prices in `models.yaml`.
   - Rerun the benchmark on Nebius, with `$ per fix`.
7. **Remaining "cherry" features**, in priority order:
   1. Role×Model cost matrix (`bench --matrix`, auto-writes `models.yaml`).
   2. Live terminal UI plus replay polish.
   3. LangSmith tracing (Builder perk).
   4. Budget mode in the policy features (`--budget-usd` already exists as a hard cap).
   5. GitHub Action: on red CI, run `pcode fix` and post a suggestion.
   6. Stretch goals: LoRA fine-tune on successful trajectories; Toloka human review of patches.
8. **Submission polish**:
   - a 30-second terminal GIF,
   - a results table (pass@1 · tokens · $ per fix · steps),
   - a cookbook-style write-up,
   - a licence. Apache-2.0 was added on 2026-10-30 by the owner's decision, ahead of the guide's
     answer. If the university's IP rules object, the owner must revisit it.
9. **Thesis core, still open**:
   - Extension 3 (dissent detector).
   - A fair benchmark for the general reasoning orchestrator in `core/` (see §10).
10. **Website**: deploy `web/out` to a host. This needs the owner's account, so ask first. The hackathon
   reportedly requires a working demo URL.

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
- **CSS class names.** `web/app/globals.css` is global. The landing page's `.motion-ok .line` moved a
  chart line out of its plot, so search the file before adding a generic class name.
- **Background kills.** A job the harness stops on low memory can leave its shell running. Check the
  process list before saying nothing is running.
- **Bash heredocs** can mangle `\n` inside Python string literals written to files. Use the Edit
  tool or a script file.
- **gpt-oss tool calls.** Hidden reasoning eats `max_tokens`, and the model invents arguments such as
  `line_end`. `Toolbox.call` drops unknown arguments and never raises.
- **`.gitattributes` in a user's project** could normalize CRLF and break exact Undo, so the shadow
  git sets `* -text -filter -ident` in `info/attributes`.
- **`lstrip("./")`** turns `.env` into `env`. `is_protected` uses a regex for this reason.
- **`web/out` is gitignored**, so a fresh clone needs Node to build the site.
