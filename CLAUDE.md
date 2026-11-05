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
   - `--provider sim` (simulated model) and `--provider oracle` runs test the mechanisms only. Both exist only on
     `bench` and `train`. They are **never** reported as results.
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

**Users** install with one line (no Python or Node first) and then type `compactcode` in a project folder:
`curl -fsSL https://compactcode.vercel.app/install.sh | sh` (macOS, Linux) or
`irm https://compactcode.vercel.app/install.ps1 | iex` (PowerShell; cmd wraps it in `powershell -ExecutionPolicy ByPass -c "..."`).
**These only work once a GitHub Release exists** (see §8, 2026-11-05 install block). Developers use the steps below.

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
- Override with `--fast-model` / `--quality-model`, which exist only on `bench`, `train` and `buildbench`.
- **Private mode** is `provider: local` in `models.yaml`: no key, Ollama on `127.0.0.1:11435`, details in §6.
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
benchmarks/reasoning/    the core's fair benchmark: GSM8K, GSM-Hard, MMLU-Pro, BBH (frozen tasks/,
                         run.py, checkers, report.py → docs/BENCHMARKS.md)
benchmarks/token_comparison.py, run_live_proof.py, results/   LEGACY, see §10
tests/           test_agent.py (core) + test_code_*.py (Puppeteer-Code), all offline
checkpoints/     pcode_linear_sim.json = LinearPolicy weights trained on SIM only;
                 policy_ep*.json = core engine prompt-policy snapshots
runs/            benchmark/train outputs and the Groq response cache (gitignored, local only)
```

Install and updates: `puppeteer_code/paths.py` (site and runs folders for a checkout or an installed copy),
`puppeteer_code/updates.py` (check, verify, helper), `cli.launch` (the `compactcode` command), `web/public/install.{sh,ps1}`,
`web/components/InstallTabs.tsx`, `web/components/ide/Updates.tsx`, `scripts/build_release.py`, `.github/workflows/release.yml`.

Private mode: `puppeteer_code/privacy.py` (guard, wording), `ollama.py` (client, spawned server), `sandbox.py`
(runners, probe, setup), `appcontainer.py` (launcher script), `assistant/effort.py`.

Memory: `assistant/graph.py` (the store), `assistant/codemap.py` (parsing and sync), `web/components/ide/MemoryGraph.tsx`,
`web/lib/graph.ts` (canvas drawing), `web/components/ide/SessionPicker.tsx` (the session drawer).

Live view and folders: `assistant/activity.py` (live events), `web/components/ide/LiveView.tsx`, `FolderTabs.tsx`, `Workspaces` and
`LocalGate` in `assistant/ide.py`.

`web/` is the site (Next.js 16 static export, design rules in `web/DESIGN.md`): landing page in
`app/(marketing)`, app pages in `app/(app)`, the IDE in `app/(ide)` and `components/ide/`, copy in
`lib/content.ts`. `web/scripts/file-icons.mjs` regenerates the file logos. `puppeteer_code/server.py` is the
local server behind the IDE and the console. Third-party attribution is in `NOTICE`.

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
  Slash commands are client-side and chat only (`web/components/ide/useCommands.ts`, list in
  `web/lib/content.ts`). `/api/ide/search` uses the toolbox's grep, and `/api/ide/git` is read-only.
- `terminals.py`: real shells for the IDE's bottom panel. `detect_shells()` finds PowerShell, pwsh, cmd, Git Bash and
  WSL distros on Windows, and `/etc/shells` on Unix. Each `Terminal` has a PTY (pywinpty or ptyprocess), a reader
  thread and a 1 MB ring buffer, so a reload reattaches; output pauses above 100 KB unacknowledged. `TerminalManager`
  allows 8. Kill takes the whole process tree (psutil). Routes are `/api/ide/shells`, `/api/ide/terminals` and the
  socket `/api/ide/terminals/{id}/ws`, which needs `LocalOnly` and the token in its first message.
  **A terminal is the user's own shell: full user permissions, outside `permissions.py`, not a sandbox.** Keys are
  dropped from its environment as hygiene, not as a boundary. Models never see its output.
- `buildbench.py`: PCodeBench-Build. It scores with hidden tests the model never sees.

### Modes and Private

Three separate axes: permission (`plan | ask | auto`), effort (`fast | normal | think | deep`), Private.

- **Plan mode.** `Toolbox.call` refuses everything outside `READ_ONLY`, whatever the agent's list says. A non-question
  goes to `_draft_plan`, never into `_puppeteer`, so no checks run. The plan waits in `pending_plan`. Only the user can
  run it: `/approve`, `POST /api/ide/plan/approve`, `session.approve_plan()`. No tool reaches these. A new request
  replaces a waiting plan. Fix mode and Apply are refused in plan mode.
- **Effort** (`effort.py`). `normal` on a cloud provider sends exactly what it did before, so benchmarks stay
  comparable. Models with native thinking get `think` per agent. An experimental reasoning pass runs at `think` on
  models without it and at `deep` on all models, with notes added to the task and never saved. Whether it helps is
  unmeasured.
- **Private** is an explicit choice: `--private`, the IDE toggle or `/private`. It is remembered in
  `~/.pcode/projects/<hash>/settings.json`, outside the repo, so a repo cannot flip it. It never falls back to cloud.
  - `privacy.py` checks run inside `OllamaClient.chat()`, so `/model` cannot get round them. Cloud names are refused
    first, by name, because `/api/show` on one is proxied to ollama.com.
  - `ollama.py` uses `http.client`, which cannot follow a redirect or read a proxy. It starts its own `ollama serve`
    on 11435 with `OLLAMA_NO_CLOUD=1`, and refuses a server that does not return 403 for a cloud name. It never pulls.
  - Off in Private: the response cache, quota fallback, Tavily, `_pydoc` imports, Explorer and `start_server`. So
    Preview is off, and `/preview` and `/private` say so (Phase 7 decision: the pipe relay was not built). Limits come
    from the spec: 150,000 tokens, 1,800 s. The Responder is off too: code writes the reply (`_report`).
  - A private conversation cannot be continued by a cloud session.
- **The OS block** (`sandbox.py`, `appcontainer.py`). A no-capability AppContainer. `python -I appcontainer.py -- <line>`
  stays outside, starts the command inside and exits with its code, so pipes and `taskkill /T` work as usual.
  `pcode private-setup` creates the profile, grants narrow read access to the Python runtime and copies Node; `--remove`
  undoes it (project grants on NTFS stay). Nothing is "contained" until the probe passes, and it runs before each request:
  container token, error 10013 on TCP, nothing arriving at a loopback or LAN canary, with an outside UDP send as control.
  A failure emits a notice and turns Auto off.
  - **Allowed claim:** "Model on this computer. Commands have no network." **Never say "no data leaves".** Outside the
    block: the user's terminals and `!commands`, hooks a command leaves behind, code run later, and the tray app's
    update check. `permissions.py` is still not a sandbox.
  - **It stops sending, not reading.** On exFAT a command can read and delete the whole drive, `.env` and `refs/` included.
    The UI says so.

### Live view and folder tabs

- **Long replies.** `reply_tokens` (10,000, local only) is Builder, Debugger and TestWriter's limit. A reply cut off at the limit is
  marked `cut_off`: it gets a "smaller step" retry, and after `MAX_CUT_OFFS` (2) the request stops with a plain reason.
- **Live events** are on only with `AssistantSession(live=True)`, which the IDE sets, so benchmarks send what they always sent.
  `Activity` ticks every 0.25 s for the whole request and reports each second: `activity` (agent, phase, seconds, silence, rate),
  `thinking_delta`, `code_delta` (latest 4 KB, never for a protected path). Those three are transient: `publish(event, keep=False)`
  does not store or number them. `file_diff` is stored, capped at 8 KB.
- **What streams.** Cloud models stream tool calls as they are written (`_stream_tools` in `core/agent.py`). **Ollama holds a tool
  call back until the reply ends**, so in Private the code appears per reply. Thinking, phase and time stay live, and the tab says so.
- **Wording stays true.** Quiet is "No output for 1m 20s. The model may still be working.", never "stuck". A pending approval says
  "Waiting for your answer in the chat", and the folder tab says "Needs you".
- **Folder tabs.** `Workspaces` holds one `IdeHub` per open folder, at most 6, with one token. `x-pcode-workspace` names the hub, and
  WebSockets name it in their signed first message. A client that names none gets the first hub; a wrong id is 404 or close 4405.
  `LocalGate` lets one Private request run across all folders. A waiting one says what it waits for and can be stopped. Cloud folders
  do not wait. Close is refused during a request.
- **Saved tabs.** `~/.pcode/ide-workspaces.json` holds paths only, outside any repo. After a restart they are dim tabs until clicked,
  so no model starts. Only a missing folder is dropped: a refusal such as no key keeps it.
- **Frontend.** `IdeClient = { token, workspace }` replaces `token`. Each open folder stays mounted and hidden with `visibility: hidden`.
  `terminal/store.ts` keeps one config per folder, and `disposeAll(workspace)` touches only that folder. A hidden folder starts no
  shell until first shown.

### Sessions and memory

- **Sessions** are JSON in `~/.pcode/sessions/<id>.json`, with `title` and per-turn results in `turns`. `_load` takes plain
  ids only, from this project only, and Private and cloud refuse each other. `list_sessions(project, home, private)` is
  filtered by mode. Rename and delete return 409 while busy. Deleting removes the JSON and its episodes; files stay.
- **Memory** is off in the constructor (`AssistantSession(memory=False)`), so benchmarks stay byte-identical. The IDE and
  `pcode` turn it on, as does `buildbench --memory`. A database error turns it off for that request and never fails one.
- **Storage:** `graph-private.db` and `graph-cloud.db` in `~/.pcode/projects/<hash>/`, SQLite with FTS5. Nodes are session,
  episode, file, symbol. Edges are `IN_SESSION`, `MODIFIED`, `DEFINES`, `IMPORTS`, `CALLS`. **No source code is stored.**
- **Parsing:** Python with `ast`, JS and TS with Tree-sitter, nothing else gets symbols. Project code is never executed.
  `CALLS` is by name, written only when the name is unique and not in `COMMON_NAMES`: a missing edge beats a wrong one.
- **Sync** compares `indexed_sha` through `snapshots.changed_since`, so outside edits are caught. The full index uses
  `snapshots.tracked()`, so the project's `.gitignore` applies.
- **Episodes hold code-observed facts only:** the user's words, agents, check result, changed files, SHAs. Never a model's
  summary. Undo removes the episode, or keeps it with only the files that were kept.
- **Prompts:** `_memory_block` adds at most `MEMORY_CHARS` 1,800, labelled as data. `fold` shortens only reads made stale by
  an edit or repeated later.
- **Tokens Saved is a net estimate**, labelled "About". It is never a result, and `export-web` and reports never use it.
- **View:** `d3-force` on `<canvas>`, default 300 nodes, shapes and labels as well as colour, a keyboard list beside the
  canvas. Routes: `GET /api/ide/graph`, `GET /api/ide/graph/node/{id}`. `graph_delta` tells the view to refetch.

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
pcode --folder PATH --mode auto                   # --mode ask (default) | auto; --resume ID, or /resume in chat
pcode ide --folder PATH                           # browser IDE; build the site first (below)
cd web && npm ci && npm run build                 # needed once, and after any web change
pcode --private                                   # Private: Ollama on this computer, no key
pcode private-setup                               # network block for commands (Windows, no admin); --remove undoes it
pcode --mode plan --effort think                  # also on `pcode ide`; in chat: /approve /effort /think /private
python -m puppeteer_code buildbench --memory ...  # memory on for a benchmark; off by default

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

# Reasoning benchmark for the thesis core (core.Orchestrator vs direct, cot, fixed); details in docs/BENCHMARKS.md
python benchmarks/reasoning/datasets.py --check             # verify the 224 frozen tasks
python -m benchmarks.reasoning.run --cache runs/cache/reasoning --out runs/reasoning-nebius-lightning.json --resume

python -m puppeteer_code train --provider sim --episodes 200   # mechanism check only
python -m puppeteer_code report --bench runs/<bench>.json --curve runs/<train>.json
python -m puppeteer_code mcp      # MCP server; register: claude mcp add puppeteer-code -- python -m puppeteer_code mcp
python -m puppeteer_code serve    # local API + built site on http://127.0.0.1:8765; build first: cd web && npm ci && npm run build
python -m puppeteer_code export-web   # real results to web/public/data; refuses sim, oracle, mock

compactcode                       # installed copy: the browser IDE on the current folder (= pcode ide --folder .)
compactcode update [--check]      # or pcode update; refuses in a git checkout (use git pull)
python scripts/build_release.py   # after the site build: dist/release/ = wheel with the site inside, SHA256SUMS, installers
```

- `--policy linear` defaults to `checkpoints/pcode_linear.json`, which does not exist yet. Pass
  `--weights checkpoints/pcode_linear_sim.json` until a real-model training run produces one.
- `--seeds N` is a count, not a list.

## 8. Status (2026-11-05)

**One-line install and updates, 2026-11-05 (branch `ide-terminal-redesign`, uncommitted, nothing published).** Owner's choices:
`compactcode` opens the browser IDE; the installer uses uv; updates notify and install on one click; IDE banner, status bar,
terminal line and desktop notification.
- **Package:** distribution `compactcode` 0.2.0, scripts `compactcode` (`cli.launch`) and `pcode`. The wheel carries the built
  site as `puppeteer_code/site/` (gitignored, copied in only by `scripts/build_release.py`); 1.8 MB. An installed copy keeps
  runs in `~/.pcode/runs`. `websockets` was added to the `web` extra: without it the installed IDE could not connect.
- **Installers** (`web/public/install.sh`, `install.ps1`): uv from Astral if missing, latest release from the GitHub API, wheel
  checked against `SHA256SUMS`, `uv tool install --force --python 3.12`, `uv tool update-shell`. A cut-off earlier install
  is uninstalled and retried once. Options `COMPACTCODE_VERSION`, `COMPACTCODE_RELEASE_URL` (tests), `COMPACTCODE_NO_MODIFY_PATH`.
- **Updates** (`updates.py`): GitHub releases API, at most once a day, cached in `~/.pcode/update.json`; pre-releases ignored;
  never automatic in Private or with `COMPACTCODE_NO_UPDATE_CHECK=1`. Apply refuses while any folder is busy and in a git
  checkout, downloads and verifies the wheel, starts a helper, and the IDE stops (watchdog: `os._exit` after 15 s). The helper
  waits for the whole launcher chain, installs, logs to `~/.pcode/logs/update.log`, and starts `compactcode` again.
- **IDE:** routes `GET /api/ide/update`, `POST .../check`, `POST .../apply`, `PUT .../settings`; event `update_available`;
  status bar item, banner ("Later" per version), panel, `/update`, desktop notification toggle (browser Notification API).
- **Site:** `InstallTabs` (cmd, PowerShell, macOS/Linux, picked from the visitor's OS) on the landing page, Docs and the IDE's
  no-token page; a Docs "Update" section; "From source" folded away. Release workflow on tag `v*`.
- **Tests:** 33 new in `tests/test_code_updates.py`. `pytest tests -q`: 863 passed, 2 skipped, 5:55. `npx tsc --noEmit` clean.
- **End to end on this machine, nothing published** (fake release server on 127.0.0.1, throwaway uv tool folder and home):
  the cmd one-liner installed 0.2.0; `compactcode` in a folder opened the IDE on it; `update --check`, the terminal line, the
  banner, the status bar item, `/update`, Esc and "Later" all worked; "Update and restart" from the banner installed 0.2.1
  and relaunched it. `install.sh` installed uv and 0.2.1 from scratch in WSL Ubuntu with a throwaway HOME, and the IDE served.
- **Bugs that end-to-end run found, now fixed:** PowerShell 5.1 made uv's stderr fatal; a half-made environment blocked
  reinstalls; no WebSocket library; the old IDE never exited; the helper waited for only 2 of 3 launcher processes;
  `DETACHED_PROCESS` made the helper PowerShell exit at once doing nothing; `*>>` wrote a UTF-16 log; the panel sat outside
  the IDE's colour tokens.
- **Not tested:** macOS; the desktop notification itself (Chrome's permission prompt blocks the automation); a real GitHub
  release and the real Vercel-served `install.ps1` (`irm` content type); the workflow itself (it has never run).
- **Not published:** no tag, no release, and the live site still shows the old install text (the deploy on 2026-11-05 was
  built from `main`, which does not have this work). Publishing the release and the site together is the owner's call.

**Reasoning benchmark for the thesis core, 2026-11-05.** `benchmarks/reasoning/`, write-up in `docs/BENCHMARKS.md`.
- Runs `core.Orchestrator` unchanged against `direct`, `cot` and `fixed` (Planner, Reasoning, Critic, Concluder) on 224
  frozen tasks: GSM8K 50, GSM-Hard 50, MMLU-Pro 70, BBH 54. Nebius, `nvidia/Nemotron-3_5-Lightning`, one seed.
- **Thinking is off for every call** (`enable_thinking=false`). With it on, hidden reasoning used up the caps and the
  policy's reply became a paragraph, so the engine fell back to its first agent. Recorded in the run's `config`.
- `CountingClient` counts the policy's calls, which `EpisodeResult.total_tokens` leaves out. Every reply is stored, so
  `--rescore` recomputes scores after a checker fix without new calls.
- Smoke checks caught, before the result run: a CoT instruction in the system turn only was ignored (3-token
  replies), so one-call methods repeat it in the user turn; Unicode minus; bare-letter replies; GSM tolerance.
- GAIA (gated, needs tools) and ToolBench (RapidAPI, GPT-4 judge, tools) were **not run**, and the page says why.
- **Final result, complete (`runs/reasoning-nebius-lightning.json`, 896 rows, 0 provider errors, $0.25 in all):**
  puppeteer 78.1%, fixed 76.8%, CoT 60.3%, direct 29.5%. Puppeteer beats both single calls (p < 0.001) but **not**
  fixed (12 against 9 discordant tasks, McNemar p 0.66), and costs about 52% more tokens (5,026 against 3,301 per
  task). The policy is untrained: it opened with Reasoning in 188 of 224 episodes and never chose the Terminator.
  `--rescore` with the final checkers changed 0 rows.
- **Do not quote the partial numbers** written here earlier (about 91% and 87%): they covered GSM8K and 27 GSM-Hard
  tasks only.
- Site: the Benchmarks page now has Reasoning, Build (the Nebius runs, missing before) and Fix groups; the old lead
  ("every method fixed every bug") and "cost pending" were wrong for Build and are gone. `export-web` writes
  `build.json` and `reasoning.json` and skips smoke runs. 52 new offline tests.


**Stuck local run, Live tab, folder tabs, 2026-11-01 (branch `ide-terminal-redesign`, uncommitted).** All plan phases are written.
- **Tests:** `pytest tests -q`: 777 passed, 2 skipped, 5:48. `npx tsc --noEmit` clean, site built. The last Python edit came before
  that run; only the frontend changed after it.
- **Scripted requests with live off** were compared byte for byte with `HEAD` (11 requests): identical.
- **The owner's 26-minute run:** `~/.pcode/ollama-private.log` shows 8 calls of exactly 3m40s, then a 500 at Stop, and `index.html`
  untouched. Builder's 6,000-token limit at about 27 tok/s is 3m40s, and a reply cut off there was retried as "malformed". This is
  the likely cause, consistent with the evidence, and not separately proved.
- **The same request on a copy, `qwen3.5:9b`, one run each, demos, never results:** with the cut-off fix, 278 s, 74,353 tokens, code
  written, then stopped by the repeat guard, because re-reading a file it had just changed counted as repeating. With that fixed
  too, 263 s, 36,106 tokens, status done, no cut-offs, scientific code present. There are no tests in that folder, so it reports unverified.
- **Repeat guard change affects cloud too:** a successful write now clears it. This can change rare cloud runs. `reply_tokens` does not.
- **Gate result:** Ollama sent 106 thinking chunks, 46.5 s of silence, then the whole 4,557-character call. The model ignored a
  "plain file block" instruction, and `<tool_call>` text with no `tools` gave `{"error":"EOF"}`, so text tool calls were not adopted.
  `qwen2.5-coder:7b` writes calls as text and might stream; untested. Thinking did not need limiting.
- **Deviations from the plan:** the ticker runs for the whole request, not per call. A missing workspace header means the first hub,
  not a refusal, so existing clients work. Close is disabled during a request instead of asking inline.
- **Real Nebius check, one request, a smoke test, never a result:** 2,658 tokens, 3 s, `code_delta` arrived, usage reported, file
  written. So `_stream_tools` works on Nebius. **Groq was not run through it.**
- **Real local model in a headless browser, two small requests, demos:** both edited the file. Phases, thinking and rate updated
  live. That run found four flaws, now fixed: the tab opened only at the first writing agent; "Code appears..." showed during
  Planning; the rate kept decaying during a command; and a pending approval read as "working on".
- **Verified in a headless browser with a scripted model (never a result):** Live through a request, reload mid-run, Stop, a failed
  check, reduced motion, an approval waiting, two folders with a run in the hidden one, tab clears when it ends, Esc in another folder
  leaves the run alone, intact terminals on return, same-name tabs, arrow keys, unsaved prompt, a folder held by another tab refused,
  Private queue and stopping a wait, restart with saved tabs and no provider made, closing the last folder, phone width.
- **Bugs that browser run found, now fixed:** repeated panel group ids threw "No layout data found" once a second folder mounted;
  repeated `ide-input` ids pointed a folder's label at another folder's box; hidden folders started shells.
- **Offline only:** a saved folder surviving a refusal, shutdown reaching every folder, Private and cloud sessions kept apart.
- **Not run:** a cloud folder beside a Private one in a browser, six folders at once, the Memory Graph's pause in a hidden tab, many
  terminals past the browser's WebGL limit (xterm falls back to its DOM renderer, by design, untested here), and speed with a real GPU.
- The terminal chat (`pcode`) has no live view. That was out of scope.
- **The owner's running IDE has the old code.** Restart it to get any of this.

**Sessions and memory graph, 2026-10-31 (branch `ide-terminal-redesign`, uncommitted).** All plan phases are written.
- **Tests:** `pytest tests -q`: 722 passed, 2 skipped, 5:38. This ran after every code change below.
- **Scripted requests with memory off** were compared byte for byte with `HEAD` (11 requests): identical.
- **Changed from the plan:** indexing uses the shadow git's tracked files, not `Toolbox._walk`. Generated folders and
  minified bundles have no symbols. Folded reads list symbols parsed from the folded text, so their line numbers are right.
  `graph_delta` tells the view to refetch rather than carrying nodes.
- **First index of this repo:** 11.8 s, 1,727 files, 8,233 symbols, 11 MB. No change: 0.1 s.
- **Verified in a headless browser, a demo, never a result:** nodes arrive, reload keeps the session, scope, search and type
  filters, a symbol opens with its range selected, jump to turn, Undo removes nodes and code, new session, rename, switch, Esc
  closes the drawer, delete of the active session keeps code, reduced motion, keyboard selection, empty state, phone width with
  no overflow.
- **Verified on the API:** toggling Private off gives Nebius, an empty graph and no sessions; on returns the private graph
  intact; nothing reached `graph-cloud.db`.
- **Bugs that browser run found, now fixed:** Esc with focus on the toggle; a 503 on a new session's graph with type filters;
  `--private` left the provider `local` after toggling off.
- **Memory on against off, one two-turn task on `qwen3.5:9b`, one run each. A demo, never a result:** both finished both turns
  with tests passing. Tokens on, 3,809 then 5,642 (9,451); off, 5,263 then 4,197 (9,460). This shows **no saving**, and the
  follow-up was easy enough without memory. "About 0 tokens saved" in both. The off run's first turn includes a command the
  script declined. The script used a throwaway home, so the block was not set up: `auto` became `ask` and checks ran uncontained.
- **Folding is modest by design:** it has shown 0 in every demo so far.
- **Final graph layout checked in a browser on 8 nodes:** an unlinked session stays near the rest, labels readable, no errors.
- **Built but not rechecked in a browser:** clearing the inspector when Undo removes the selected node.
- A project shows an empty graph until its first request, because indexing runs at request start.
- **Not run:** a real request on a big repo, so speed at 300 live nodes is unmeasured.
- Left on disk, delete by hand: `F:\tmp\pcode-graph`, `F:\tmp\pcode-live`, `F:\tmp\pcode-onoff-on`, `F:\tmp\pcode-onoff-off`.

**Private mode and modes, 2026-10-31 (branch `ide-terminal-redesign`, uncommitted).** Phases 1 to 7 are built and work on
a real local model. One small task, repeated: a working demo, never a benchmark result.
- **Real model, `qwen3.5:9b`:** in the terminal and in a headless browser on a throwaway folder: plan, approve, command
  approval, tests passing inside the container, Undo (also after `/resume`), the toggle both ways, `/private`, `/preview`.
  About 90 s and 13k to 19k tokens each run; 5.6 of 6.7 GB on the GPU at `num_ctx` 16384.
- **The model's own reply was false, so Private no longer uses it.** It twice said `stats_lite.py` was not created when
  it was, the second time with the changed-file list in its prompt. `_report` now writes Private's reply from the snapshot
  and the checks, so it is plain. Cloud replies are unchanged.
- **Fixed: a killed `pcode` left Private's server running**, and the next start joined it. It is now tied to pcode by a
  kill-on-close job. Proved by force-killing the IDE: port 11435 closed. Unix still relies on `atexit`.
- **Verified on this machine:** `pcode private-setup` (exit 0, rerun after the probe fix); the egress matrix through the
  real runner (below); pytest with `tmp_path` and `node --test` inside the container on `F:`; kill of the tree. Against a
  fake Ollama: client, guard, plan, effort.
- **Egress matrix, real runner:** blocked TCP, UDP (a public DNS query got a reply outside and none inside), ICMP, DNS,
  LAN HTTP, loopback, 11434, SMB, WebDAV, BITS, certutil, schtasks, WMI, wsl.exe. `explorer` launched and the listener saw
  no hit. `nslookup` exits 0 with no server. 8765 was tested afterwards: connected from outside, timed out inside.
- **Not proved:** a `pktmon` capture at the NIC (needs admin), issue-2207 binaries, Docker (pipe absent), IPv6 (none here,
  so it fails in both), and `ollama serve` making no outbound connection. A watcher sampled that server every 3 s through
  one real run and saw no non-loopback connection; that is a sample, not proof.
- **Not run:** Go, cargo and dotnet in the container; `think` and `deep` effort on the real model; Fix mode in Private.
- `qwen3.5:9b` is the default because it is first in `models.yaml` and worked; no other model was run through a whole
  request. Single calls at `num_ctx` 8192, not results: granite4 native tool calls, 10.6 tok/s; qwen2.5-coder:7b tool calls
  as text (handled), 51 tok/s; qwen3:8b used all 200 tokens thinking.
- `qwen3.5:9b` retries a declined command once despite being told not to, and writes `cd /f/tmp/...` (Git Bash style) for a
  Windows shell. A human still decides each one in `ask` mode.
- **Limits:** Node tools that spawn children with pipes hang in a container (`node --test` is adapted; jest and vitest are
  not). **On NTFS, pytest cannot start**, because the container cannot stat parent folders; the UI warns. The fix is a
  read-attributes grant on the parents, such as `C:\Users\<name>`, which changes the home folder's ACL, so it waits for
  the owner. AppContainer was kept despite the plan's Node gate, because the fallback needs admin and likely leaks DNS (untested).
- Numbers on local models need the owner's wording first, since §2.3 names only Groq and Nebius.
- Mistake to know about: `tests/test_code_sandbox.py` was overwritten and restored from `HEAD`. It had no uncommitted
  work. The runner tests moved to `tests/test_code_runner.py`.
- Left on disk: `F:\tmp\pcode-spike` (delete by hand), and `F:\.pcode-tmp` (Private's temp folder; it holds empty
  pytest folders from the live tests and is safe to delete).

**IDE redesign, 2026-10-31 (branch `ide-terminal-redesign`, uncommitted until asked).** Two passes, the second
modelled on Antigravity at the owner's request. Design rules are in `web/DESIGN.md`.
- Near-black with slight violet from the landing page (the first pass's amber is gone), Geist for the workbench.
- Activity bar (Explorer, Search, Source Control read-only, Testing), status bar, file logos, and sashes on every panel.
- Real terminals: `+` and a shell menu, split, rename, a tab rail, drag to reorder, split or dock in the editor.
  Slash commands left the terminal for this reason.
- Agent panel: rounded bubbles and prompt card. The model name there is plain text. **The picker and the AI part
  wait for the owner's novelty plan, by their decision.**
- Left out on purpose: Run and Extensions icons, Problems, Debug and Ports tabs, `+` in the prompt, because nothing
  would feed them. Dragging is what the owner chose: not free docking of every panel.
- Verified in a real browser with a scripted model on a throwaway folder (never a result): PowerShell, cmd, Git Bash and
  WSL Ubuntu start in a path with spaces; colours; resize; vim in WSL; a `yes` flood then Ctrl+C; reload keeps shells and
  scrollback; every drag; killing a terminal leaves no process, in Windows or in WSL.
- **Not tested:** pwsh (not on this machine), macOS, a native Linux machine. Linux detection is covered by fakes.
- The `yes` flood recovered after a couple of seconds in headless software rendering. Real-GPU speed is unmeasured.
- 22 slash commands in the chat, listed by `/help`. 14 run locally and spend no
  tokens; 8 are premade requests to the assistant (`/explain`, `/review`, `/design`, `/tests`, `/bugs`,
  `/refactor`, `/docs`, `/run`). `!command` in the chat runs a shell command, shown in the Output tab.
- New routes: `POST /api/ide/clear` and `/api/ide/model`. `/api/ide/resume` now publishes `resumed`.
- `AssistantSession.clear()` gives a new id, so `/clear` no longer overwrites the old conversation (this
  also changes `pcode`'s `/clear`). The terminal chat (`pcode`) did not get the premade requests.
- Fixed: CRLF files showed "(unsaved)" on open, and saving kept LF only.
- Verified in a real browser with a scripted model on a throwaway folder (never a result). `/review` and
  `/explain` are read-only only because their wording passes `is_question`, so keep that if you edit them.

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
  - The landing hero now sells the assistant, with the owner's approval. Lower sections still sell MCP.
  - The `web_search` tool in the plan was not built.
- `start-pcode.bat` was written but has never been run from scratch.
- `web/out` must be rebuilt after any web change.

**PCodeBench-Build, built 2026-10-30.** 9 tasks (6 Python, 3 Node), hidden acceptance tests, each
proved fair by `build.py --check`. Real Nebius results are in README §14.7 (`runs/buildbench-nebius.json`,
primary limit 60k; `runs/buildbench-nebius-150k.json`, sensitivity run at 150k):
- **The puppeteer did not win.** At 60k: direct 7 of 9, 25.6k tokens; heuristic 6 of 9, 51.6k. At 150k:
  both 7 of 9, 27.6k against 81.8k. False success claims: direct 2, heuristic 0.
- Do not oversell the zero: every heuristic failure ended at the token limit.
- One seed, and the tasks were used for debugging, so they are a development set.
- About 83% of tokens (152 sampled calls) are prompts resent in the tool loop. Fix that first.
- A malformed tool call used to end the agent, and `built` was set with no files. Both are fixed.
- `gpt-oss-20b` on Groq sometimes dropped the leading `/` of a JS regex in its own output.

**Submission state, 2026-10-30.** Public repo pushed to `main`, Apache-2.0 added by the owner's decision,
site at https://puppeteer-code.vercel.app (`web/out`, static, no live IDE). Demo video:
`runs/puppeteer-code-demo.mp4`, 66 s, silent, 800x450. It is gitignored, so it must be uploaded by hand.

**Rename, 2026-10-31.** The product is **CompactCode** in the frontend (`web/`) by the owner's decision.
The site is at https://compactcode.vercel.app, a domain on the Vercel project, so production deploys update
it. https://puppeteer-code.vercel.app still works, and the Vercel project is still named `puppeteer-code`.
Deploy with `vercel deploy --prod` from `web/out`; a build deletes the link in `web/out/.vercel`, so run
`vercel link` there first if it is gone. Deliberately unchanged:
- real identifiers the docs must match: `pcode`, `python -m puppeteer_code`, `puppeteer_fix`, `x-pcode-token`;
- the thesis name Puppeteer++, the paper's term "puppeteer", and the benchmark names (PCodeBench-*);
- the Python side still says Puppeteer-Code: terminal UI, `report.py`, MCP server, README and this file's
  older sections.

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
- Offline test suite green (last full run, 2026-11-01: 777 passed, 2 skipped, ~6 min).

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

## 9. Next steps (in order, updated 2026-11-05)

First, ship the one-line install, with the owner's go-ahead, in this order: commit the install and update work, merge to
`main`, push; tag `v0.2.0` with release notes as the tag message and push the tag; check the workflow passed and the release
has the wheel, `SHA256SUMS`, `install.sh` and `install.ps1`; then build the site from `main` and deploy it (the install
commands go live only now); then run the real one-liner once in cmd, PowerShell and WSL. If `irm` gets a binary content type
from Vercel, add a `vercel.json` header for `.ps1`. Later releases: bump `__version__`, tag, push.

Reasoning benchmark, done 2026-11-05 (final numbers in §8 and `docs/BENCHMARKS.md`). Next for it: 3 seeds, thinking on, `nemotron-3-super-120b-a12b`.


First, restart `pcode ide` so it runs the new code, and rerun the scientific calculator request on the real folder.

First, sessions and memory: nothing yet shows memory saves tokens. A real claim needs a paired `buildbench --memory` run on new
multi-turn tasks, which spends credit. The 9 existing tasks are single requests, so those tasks have to be written first.

First, finish Private, with memory free (close Chrome), one at a time:
1. Decide on the NTFS parent-folder grant: until then pytest cannot run in a Private project on `C:`.
2. Capture one real request with `pktmon` as admin, to prove what the watcher only sampled.
3. Try `think` and `deep` on the real model, and run another model through a whole request.

Then, by the owner's request: the model picker and AI part of the agent panel (Groq, Nebius and others). The owner
has their own plan to make it novel, so ask them for it before building anything.

0. **Reduce the tool-loop prompt cost**, the biggest measured cost (README §14.7).
1. **Rerun PCodeBench-Build on fresh tasks with more seeds**, so the result is a real test set:
   `python -m puppeteer_code buildbench --cache runs/cache/nebius --methods heuristic,direct --seeds 3 --out runs/buildbench-nebius-3.json`
   Then retrain `LinearAssistantPolicy` with `--methods linear --train --weights
   checkpoints/assistant_linear_nebius.json`, on tasks kept apart from the final test.
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

- **Every panel `Group` id must be unique on the page, and that includes hidden folders.** A repeat throws "No layout data found for
  index 0" from a `ResizeObserver`. The same goes for `htmlFor` ids, so use `useId`. Panel ids inside a group may repeat.
- **Never `display: none` a folder.** The panels read size 0 as collapsed, and xterm cannot measure. Use `visibility: hidden`.
- **Anything on `window` needs the `active` or `shown` prop.** Every folder stays mounted, so a hidden one would answer Esc or Ctrl+backtick.
- **Do not read a tool call's text from Ollama while it generates.** It is not sent. Silence during a reply is normal there.
- **A live run stuck on "working" may be an unanswered approval.** It waits forever by design, so a script must answer it.
- **A demo that deletes its folders needs its old server stopped first**, or that server reports "not a git repository".

- **`_approve` has no timeout, by design.** A script that drives `session.send` must answer `approval_request`, or it hangs
  forever with no CPU and no output. One did for 45 minutes. A scripted `home` also has no `private.json`, so `auto` becomes `ask`.
- **`scrub` only hides values in this process's environment.** A pasted key is not one, so `graph.clean` also matches key shapes.
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
- **Ctrl+C in a Windows terminal.** A process started in the background passes "ignore Ctrl+C" to its children, so
  `ping -t` could not be stopped under ConPTY. `WindowsPty` clears the flag before each spawn. Linux shells in WSL hide
  this bug, because their tty handles Ctrl+C itself, so always test it in cmd or PowerShell.
- **`os.environ` keys are upper case on Windows**: `ProgramFiles` is `PROGRAMFILES`. `windows_shells` normalises them.
- **A dragged element that a drop removes never fires `dragend`.** `useTerminals` listens on `window` for that reason.
- **Restart the IDE after changing `terminals.py`.** `web/out` covers only the frontend.
- **Look before writing a "new" test file.** Check `git ls-files` first.
- **A UDP `sendto` succeeds in an AppContainer**, and the packet goes nowhere. Only arrival counts, so the probe uses a
  listener plus a control send from outside.
- **Python 3.13 temp folders on NTFS** are owner-only, and a container cannot open them. On exFAT `TMPDIR` points at
  `<drive>\.pcode-tmp`. An AppContainer overrides `TEMP` and `TMP`, but not `TMPDIR`.
- **Node from `Program Files` cannot run in a container**, which is why setup copies it. `NODE_OPTIONS` carries
  `--preserve-symlinks`, because Node stats `C:\`.
- **`node --test` hangs in a container** (libuv's named pipes are refused), so `adapt()` adds `--experimental-test-isolation=none`.
- **Bash heredocs here** failed on text with apostrophes. Use the Write tool for scripts.
- **Git Bash rewrites a leading `/`** in arguments: `/private` reached the chat as `C:/Program Files/Git/private`, and the
  agent treated it as a real request. Set `MSYS_NO_PATHCONV=1` before scripting slash commands.
- **A model can reject a true fact in its prompt.** If a claim matters, code writes it.
- **`pcode ide` buffers its first line when redirected.** Set `PYTHONUNBUFFERED=1`, or an empty log looks like a dead IDE.
- **On Windows a venv's `python.exe` is a launcher.** Killing it can leave the real process, so kill the port's owner.
- **A uv tool on Windows is three processes:** `compactcode.exe`, the venv's `python.exe` launcher, the real Python. All three lock
  files an update replaces, so the helper waits for all three (`updates.processes_to_wait_for`).
- **Windows PowerShell 5.1 started with `DETACHED_PROCESS` exits at once with code 0 and runs nothing.** Use `CREATE_NO_WINDOW`.
- **In PowerShell 5.1 a native program's stderr is an error record:** with `$ErrorActionPreference = 'Stop'` uv's progress lines
  kill the script. `install.ps1` runs native programs through `Invoke-Native` for this reason. `*>>` writes UTF-16 there.
- **`irm ... | iex` runs in the user's own session**, so `exit` would close their window: `install.ps1` returns instead.
- **The Claude in Chrome extension's navigation is `sec-fetch-site: cross-site`**, which `LocalOnly` refuses. In a test,
  open any page on the IDE's port first, then go to `/ide?x#token=...` with `location.replace` (a hash-only change does not load).
- **F: is exFAT:** no junctions, and a Next build in a worktree there failed on `readlink`. Build a copy on C: instead.
