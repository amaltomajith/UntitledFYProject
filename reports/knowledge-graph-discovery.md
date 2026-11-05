# Codebase discovery for Knowledge Graph Memory

Prepared 2026-10-31 from the working tree on branch `ide-terminal-redesign` (uncommitted work included). Paths are
relative to the repo root. This describes what the code does today; section 4 and section 5 separate recommendations
from decisions the developer must make.

## Summary

- **The app is a local web IDE, not a VS Code fork.** It has a static Next.js and React frontend, and a Python FastAPI
  server on `127.0.0.1` that runs the agents and talks to Ollama.
- **There is no database, no embedding model and no file watcher.** Conversations are JSON files. Project history is a
  private shadow git repo per project.
- **Chat history does not reach the agents that write code.** In the default flow, Builder, Debugger and TestWriter run
  with `history=False`. A follow-up such as "now make it dark" reaches them only as the current request. This is the
  clearest gap memory could fill.
- **The largest measured token cost is elsewhere.** In a sample of 152 calls, about 83% of tokens were prompts resent in
  the tool loop (README section 14.7, Nebius). That sample covers cloud runs. A graph will not reduce that cost unless it
  also shortens what is resent.
- **The biggest local constraint:** Private's Ollama server sets `OLLAMA_MAX_LOADED_MODELS=1`. An embedding call would
  unload the chat model, unless that setting and the 8 GB of VRAM can accommodate both.

## 1. IDE architecture and tech stack

### Core framework

| Layer | What it is | Where |
|---|---|---|
| Frontend | Next.js 16 static export, React 19, TypeScript, CodeMirror 6, xterm.js | `web/`, IDE in `web/components/ide/` |
| Local server | FastAPI and uvicorn, REST and WebSocket under `/api/ide/` | `puppeteer_code/assistant/ide.py` |
| Terminal client | `rich` and `prompt_toolkit`, reading the same event stream | `puppeteer_code/assistant/terminal.py` |
| Orchestration | One `AssistantSession` per conversation, yielding events | `puppeteer_code/assistant/session.py` |

It is not Electron, Tauri or a VS Code extension. `pcode ide` serves the built site from `web/out` and opens a browser.

### Runtime

Python 3.10+ (developed on 3.13, Windows 11). The Ollama client is hand-written on the standard library's `http.client`
in `puppeteer_code/ollama.py`, on purpose: that library cannot follow a redirect or read a proxy setting, which Private
relies on. Cloud providers use the `openai` SDK through `core/agent.py`.

### Storage

No database is integrated. A repo-wide search found no SQLite, LanceDB, Chroma, LevelDB, IndexedDB or FAISS use. The
only `sqlite` and `chroma` matches are a file-icon list and a colour library.

| Data | Format | Location |
|---|---|---|
| Conversations, undo stack, waiting plan | JSON, secrets scrubbed by `scrub_obj` | `~/.pcode/sessions/<id>.json` |
| Project snapshots | Shadow git repo, separate from the user's `.git` | `~/.pcode/projects/<hash>/git` |
| Per-project settings (only `private` today) | JSON | `~/.pcode/projects/<hash>/settings.json` |
| Sandbox state | JSON | `~/.pcode/private.json` |
| API keys | `.env` format | `~/.pcode/.env` or the repo's `.env` |
| Panel sizes, terminal layout | Browser `localStorage` | Per browser |

`<hash>` is the first 16 hex characters of the SHA-256 of the lower-cased project path
(`puppeteer_code/assistant/snapshots.py:29`).

### Dependencies relevant to this work

- **Python, in use:** `openai`, `fastapi`, `uvicorn`, `websockets`, `numpy`, `pyyaml`, `rich`, `prompt_toolkit`,
  `truststore`, `psutil`, `pywinpty` (Windows) or `ptyprocess`, `python-dotenv`.
- **Optional:** `mcp`, `ruff`.
- **File parsing:** only the standard library's `ast`, for Python. There is no Tree-sitter or other multi-language parser.
- **State management on the web:** React hooks. There is no Redux or Zustand. The IDE's state comes from `useIde` in
  `web/lib/ide.ts`.
- **Not present:** LangChain, LlamaIndex, any vector store, any graph library such as NetworkX.

## 2. Ollama configuration and model constraints

### Target models

Private's list is in `puppeteer_code/models.yaml:49`. The first installed candidate goes into both tiers, so one model
does all the work.

| Priority | Model | Installed size | Notes from this machine |
|---|---|---|---|
| 1 (selected) | `qwen3.5:9b` | 6.6 GB | Tools, thinking, vision. Ran full requests. 5.6 of 6.7 GB on the GPU at `num_ctx` 16384 |
| 2 | `qwen3:8b` | 5.2 GB | Used all 200 tokens thinking in a single test call |
| 3 | `qwen2.5-coder:7b` | 4.7 GB | Returns tool calls as text, which the client handles |
| 4 | `granite4:latest` | 2.1 GB | Native tool calls, 10.6 tok/s in a single test call |

Those figures are single calls or small demos, not benchmark results. Only `qwen3.5:9b` has run a whole request. The
machine has 15.3 GB of RAM and an 8 GB RTX 4060 Laptop. `qwen2.5-coder:14b` and `:32b`, `deepseek-coder-v2` and
`llama3.1` are not installed or configured.

Cloud is the default outside Private: Nebius (`nvidia/Nemotron-3_5-Lightning`, `nvidia/nemotron-3-super-120b-a12b`), with
Groq optional.

### Embeddings

None. `ollama list` shows no embedding model, nothing calls `/api/embed`, and `OllamaClient` has no embedding method. The
only "embedding" text in the repo is in README design notes.

### Context and generation parameters

The endpoint is native `POST /api/chat`, always streaming on a worker thread (`puppeteer_code/ollama.py`). The OpenAI
compatible `/v1` route is not used for local models, because it cannot set `num_ctx` and the default silently cuts
prompts off.

```python
# puppeteer_code/ollama.py:235
body = {"model": model, "messages": native, "stream": True, "keep_alive": self.keep_alive, "truncate": False,
        "options": {"num_ctx": self.num_ctx, "temperature": temperature,
                    "num_predict": self._room(native, tools, max_tokens)}}
```

- `num_ctx` is 16384 and `keep_alive` is 10m, both from `models.yaml`.
- `truncate: false` makes an oversized prompt an error instead of a silent cut. `_room()` also checks size before
  sending, estimating 3 characters per token.
- `temperature` and `max_tokens` are per agent in `models.yaml:88-95`, for example Builder at 0.2 and 6000.
- Limits per request in Private: 150,000 tokens and 1,800 seconds.
- `think` is set per agent by `puppeteer_code/assistant/effort.py`.

### Tool calling

- **Native `tools` API.** Schemas come from `Toolbox.specs()`. Tools are `list_files`, `read_file`, `grep`, `write_file`,
  `edit_file`, `delete_file`, `run_command`, and `start_server` (withheld in Private).
- **Text fallback.** `text_tool_calls()` accepts JSON calls written as plain text, when they are the whole reply or inside
  `<tool_call>` tags, and only for a tool that was offered.
- **Deterministic injection as well.** Before each agent runs, `task_for()` adds a file list and word-matched snippets.
- **Not ReAct.** Text-based ReAct exists only as a Fix-mode comparison baseline in `puppeteer_code/baselines.py`.
- **MCP runs the other way.** `puppeteer_code/mcp_server.py` lets other tools call `puppeteer_fix` and `puppeteer_models`.
  The assistant does not consume MCP servers.

## 3. Current context and chat pipeline

### Chat management

`conversation()` in `puppeteer_code/assistant/context.py:56` keeps the last 6 messages, capped at 600 characters each.
Older messages become one 120-character line each, with that block capped at 1,200 characters. This is truncation, not
summarisation: no model is called.

Which agents receive it:

| Call | History included? | Used by |
|---|---|---|
| `_loop()` with default arguments | Yes | Answerer, plan-mode Planner, `direct` policy |
| `_loop(..., history=False)` in `_edit_step` | **No** | Builder, Debugger, TestWriter in the default flow |
| `_once()` | **No** | Planner, Reviewer, Responder |

Inside a tool loop, `trim()` shrinks the oldest tool output, keeps the last 3 results, and targets 24,000 characters
(`MAX_PROMPT_CHARS`). It runs on every turn.

### File and codebase indexing

- **Assistant:** a file list (up to 60 paths) plus the 2 files sharing the most words with the request, at 1,500
  characters each (`relevant_code()`, `context.py:37`). A filename match counts three times as much as a word match.
- **Cost to note:** `relevant_code()` walks and reads every file under 200 KB each time `task_for()` runs, because nothing
  is cached.
- **Model tools:** `read_file` (16,000 characters), `grep` (Python regex over a directory walk, 40 hits). Ripgrep is not used.
- **Not present:** `@file` mentions, active-tab injection, vector RAG, Tree-sitter.
- **Fix mode only:** `SymbolIndex` in `puppeteer_code/index.py` uses Python's `ast` to record symbols, signatures and call
  relationships, and ranks them against a failure. **The assistant does not use it**: it imports only `tokenize`. It
  covers Python only, while the assistant serves any language.

### Request and response pipeline

From pressing Send to the first Ollama call, in the default `heuristic` policy:

1. `web/components/ide/Chat.tsx`: the prompt form calls `onSend`.
2. `web/components/ide/Ide.tsx:499`: `send({ type: "send", message, mode })`.
3. `web/lib/ide.ts:340`: `useIde` sends it on the WebSocket `/api/ide/ws`.
4. `puppeteer_code/assistant/ide.py:296`, `IdeHub.handle()`: caps the text at 8,000 characters and starts a thread on
   `session.send(text, effort=effort)`.
5. `session.py:138`, `send()`, then `_request()` at line 305: classifies with `is_question`, then:
   - question: `_loop(SPECS["Answerer"], message)`;
   - plan mode: `_draft_plan()`;
   - otherwise: `snapshots.snapshot()`, a `RequestState`, then `_puppeteer()`.
6. `_puppeteer()` at line 372: runs `allowed()` from `shield.py`, then `policy.select()` from `policy.py`, then `_step()`.
7. `_edit_step()` at line 450: `task_for()` in `context.py` builds the prompt, and `_loop(..., history=False)` runs it.
8. `_loop()` at line 626: builds `[system, (history), task]` and applies `trim()`.
9. `_chat()` at line 605: picks the model and options, then calls `provider.client.chat()`.
10. `OllamaClient.chat()`, `ollama.py:229`: `ModelGuard` checks, `to_native()`, `_room()`, `POST /api/chat`.

Then tool calls go through `Toolbox.call()`, `verify.py` runs the project's checks, and `_report()` writes the reply in
Private (`_respond()` calls the Responder on cloud). Events flow back through `_emit()`, `IdeHub.publish()` and the WebSocket.

`ContextSnapshot` in `puppeteer_code/snapshot.py` is Fix mode's bounded view. It is a separate path from the assistant's.
`core/memory.py` is the thesis engine's memory, and the assistant does not import it.

### Project file tree

Benchmark task data, `web/public/` assets, `runs/`, `refs/` and `.venv/` are left out.

```text
puppeteer-plus/
├── CLAUDE.md  README.md  NOTICE  LICENSE  pyproject.toml  requirements.txt  start-pcode.bat
├── core/                  agent.py  environment.py  memory.py  orchestrator.py
├── training/              reinforce.py  reward.py
├── extensions/            agent_registry.py  step_critic.py
├── puppeteer_code/
│   ├── cli.py  models.yaml  providers.py  ollama.py  privacy.py  sandbox.py  appcontainer.py
│   ├── server.py  mcp_server.py  engine.py  environment.py  workspace.py  explore.py
│   ├── agents.py  policy.py  shield.py  reward.py  integrity.py  index.py  snapshot.py
│   ├── tools.py  tasks.py  baselines.py  bench.py  report.py  mock.py
│   └── assistant/
│       ├── session.py  context.py  agents.py  policy.py  shield.py  effort.py
│       ├── toolbox.py  permissions.py  verify.py  snapshots.py  setup.py
│       └── ide.py  terminal.py  terminals.py  buildbench.py
├── benchmarks/            code_tasks/  build_tasks/  token_comparison.py  run_live_proof.py
├── checkpoints/           policy weights
├── tests/                 test_agent.py  test_code_*.py  code_fixtures.py  conftest.py
├── reports/               this report
└── web/
    ├── app/               (marketing)/  (app)/  (ide)/  globals.css  layout.tsx
    ├── components/
    │   ├── ide/           Ide  Chat  CodeEditor  FileTree  BottomPanel  SideViews  Setup  Menu
    │   │                  Slash  Codicon  FileIcon  useCommands.ts  terminal/
    │   ├── app/           Console  Runs  Benchmarks  Charts  ...
    │   └── sections/  scene/  text/
    ├── lib/               ide.ts  content.ts  api.ts  ...
    └── scripts/           file-icons.mjs
```

## 4. Memory and graph scope

### Graph target

**Developer decision.** My recommendation is a unified graph, built in stages, because the two parts solve different
problems and the link between them is the part nothing else here can provide:

1. **Session memory first.** Store decisions and preferences per project, and give them to Builder and Debugger, which
   get no history today. This fixes a visible gap, and needs no parser.
2. **Code structure second.** Python can reuse the `SymbolIndex` logic. Other languages need a parser such as Tree-sitter,
   which would be a new dependency with compiled parts.
3. **Links between them last.** `_finish()` already knows which files each request changed, so a decision can point at
   files from day one, and at symbols once they exist.

### Persistence scope

Project state lives in the global app-data folder, `~/.pcode/projects/<hash>/`. There is no `.ide/` folder in the
workspace, on purpose: `CLAUDE.md` and `setup.py` keep settings outside the repo, so a repo cannot change them.

Recommended: one `graph.db` in that folder, using Python's built-in `sqlite3`, which adds no dependency. Its FTS5 search
should be checked on the target Python builds. Writes happen in the pcode process, outside the command sandbox. Sandboxed
commands should not be able to reach `~/.pcode` on NTFS, because the container has no grant there, but that specific path
has not been tested.

### File watching and hooks

No file watcher exists: no `chokidar`, `fs.watch`, `watchdog` or `watchfiles`. The file tree reloads through
`view.fileTick`, on agent events, or from the refresh button. Edits made outside the assistant go unnoticed until then.

There is no general hook system, but these are clean places to add calls:

| Point | Where | Fires | Useful for |
|---|---|---|---|
| Agent changed a file | `Toolbox._changed()`, `toolbox.py:180` | Each write, edit or delete | Marking nodes stale |
| Request finished | `AssistantSession._finish()`, `session.py:359` | Once per request, with the changed-file list | Saving decisions, linking files |
| Episode learned | `_learn()`, `session.py:567` | After `_finish` when work ran | Recording outcomes |
| Undo | `session.py:208` area | Each undo | Reversing what a request added |
| Change detection | `snapshots.changed_since(sha)` | On demand | Catching outside edits, with no watcher |

`changed_since` runs git against the shadow repo, so it can replace a watcher: comparing the last indexed snapshot at
the start of each request would also catch edits made outside the assistant.

## 5. Constraints a design must respect

- **VRAM and model loading.** `SERVER_ENV` sets `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1`
  (`ollama.py:45`). An embedding call would evict the chat model and force a reload. Lexical search plus graph links
  avoids this. If embeddings are wanted, measure the reload cost first.
- **Prompt budget.** Prompts target 24,000 characters. Memory must have its own cap, and must not be protected from
  `trim()` by accident.
- **Private's guarantees.** Any new model call must go through `OllamaClient.chat()` or an equally guarded method, so
  `ModelGuard` still applies. Memory from a Private conversation must not reach a cloud session: `PRIVATE_ONLY` enforces
  this for conversations today, and a shared project graph would bypass it unless it is separated.
- **Secrets.** Conversations pass through `scrub_obj` before saving. A graph needs the same, and must skip protected files
  such as `.env*`.
- **Prompt injection.** `RULES` treats file and tool text as data. Stored memory comes from that same text, so it can
  carry instructions into later requests. It should be labelled as data when injected.
- **Undo.** A memory created by a change that was undone should not survive as fact.
- **Trustworthy memory.** A real local model denied finished work even with the true file list in its prompt, so memory
  should come from code-observed facts where possible, such as changed files and check results, not from a model's
  own summary alone.
- **Benchmarks.** Cloud `normal` effort currently sends byte-identical requests. Adding memory changes prompts, so old
  and new numbers need separate labels, and memory should be a switch that can be turned off.
- **Thesis claims.** A token saving needs measuring on real models, stating tasks and seeds, with PCodeBench-Build as the
  likely test. The existing 9 tasks are single requests, so they would not exercise memory across turns: follow-up tasks
  would have to be added. Numbers from local models also need the owner's wording, since `CLAUDE.md` section 2 names only
  Groq and Nebius.

## 6. Decisions for the developer

1. **Scope:** session memory, code structure, or unified? Recommended: unified, in the stages above.
2. **Retrieval:** lexical search only, or embeddings too? Recommended: start lexical, because of the one-model limit.
3. **Order:** should memory come before the tool-loop prompt cost, which `CLAUDE.md` lists as the first next step and
   which is the largest measured cost? Recommended: run them together, because a graph that replaces repeated file reads
   may help both, but measure them separately.
4. **Languages:** Python only at first, or Tree-sitter for all languages from the start?
5. **Provider scope:** does memory apply to cloud sessions too, and do Private and cloud keep separate graphs? Recommended:
   separate graphs.
6. **Novelty plan:** `CLAUDE.md` reserves the model picker for a novelty plan. Is this graph part of that plan, or
   separate? It changes what the thesis can claim as its contribution.
