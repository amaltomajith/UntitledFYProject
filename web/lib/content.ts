// All visible copy lives here. House rules: plain speech, no em or en dashes, and every number
// must trace to a real-provider run (see README 14.4 and runs/*.json). Sim numbers never appear.

export const GITHUB_URL = "https://github.com/amaltomajith/UntitledFYProject";
export const CTA_LABEL = "View on GitHub";

export const NAV = [
  { href: "#about", label: "Why us" },
  { href: "#mcp", label: "MCP" },
  { href: "#how", label: "How it works" },
  { href: "#capabilities", label: "What you get" },
  { href: "#start", label: "Get started" },
];

export const CHAPTERS = ["Welcome", "Why us", "MCP", "How it works", "What you get", "Get started"];

export const HERO = {
  eyebrow: "A coding assistant that checks its own work",
  lines: ["Say what", "you want."],
  sub: "It builds and tests it. Describe it in plain words, and it plans, writes, runs and tests the code in your folder. Every change can be undone.",
  secondary: "See the MCP tool",
};

export const ABOUT = {
  chip: "Why us",
  line1: "Fewer tokens.",
  line2: "More use.",
  sub: "5× fewer tokens than running every agent on every bug, on real models across 10 tasks.",
  m1: "Keep the assistant you like.",
  m2: ["Add one", "MCP", "tool"],
  m3: "and send it your bugs.",
};

export const MCP = {
  chip: "MCP",
  title: "One tool. Twelve agents behind it.",
  clients: ["Claude Code", "Cursor", "OpenCode", "Any MCP client"],
  call: {
    label: "Your assistant calls",
    fn: "puppeteer_fix",
    args: ['task="mean() is wrong for even lists"', 'repo="/path/to/repo"'],
  },
  result: {
    label: "You get back",
    // Same line format as _summary() in puppeteer_code/mcp_server.py.
    lines: ["tests: 12/12 passing (baseline 10/12)", "diff: 1 file(s), +3/-1", "agents: RepoScout -> Coder -> TestRunner"],
    note: "Example output",
  },
  foot: "It runs on its own cheap models in a throwaway copy, so your assistant's context stays clean. Nothing touches your repo until you say so.",
};

export const HOW = {
  chip: "How it works",
  title: "Inside that one tool.",
  steps: [
    { verb: "Call in help", body: "Extra agents join only when this bug needs them." },
    { verb: "Rule out bad moves", body: "Hard rules skip the pointless steps." },
    { verb: "Pick one agent", body: "A heuristic picks who acts next. A trained policy is on the way." },
    { verb: "Try a fix", body: "Edits happen in a throwaway git copy." },
    { verb: "Run the tests", body: "Better tests, the change stays. Worse tests, it's rolled back." },
  ],
  episodeLabel: "Illustrative run",
  passLabel: "Tests passing",
};

export const CAPABILITIES = {
  title: "What you get.",
  cells: {
    routing: {
      title: "Cheap models for the easy parts",
      body: "Strong models write the patches. Search, tests and linting use no LLM at all.",
    },
    context: {
      title: "Prompts with a hard limit",
      body: "Step 10 is no bigger than step 2.",
    },
    scouting: {
      title: "Finds the right code first",
      body: "The buggy lines made the top four snippets on 80 of 81 tasks.",
    },
    sandbox: {
      title: "Your repo stays untouched",
      body: "It works on a throwaway copy. Bad edits get reset with the reason logged.",
    },
    explore: {
      title: "Three tries when it gets stuck",
      body: "Three worktrees go at the bug. The best one wins.",
    },
    reward: {
      title: "No faking a green test",
      body: "Editing tests, adding skips or deleting asserts gets blocked or flagged.",
    },
  },
};

export const START = {
  title: "Add it in two commands.",
  note: "Put NEBIUS_API_KEY or GROQ_API_KEY in .env first. Today the fixer works on repos with a pytest suite.",
  commands: [
    { label: "Install", cmd: 'pip install -e ".[mcp]"' },
    { label: "Add to Claude Code", cmd: "claude mcp add compactcode -- python -m puppeteer_code mcp" },
    { label: "Any other MCP client", cmd: "pcode mcp" },
    { label: "Or from your terminal", cmd: 'pcode fix "mean() is wrong for even lists" --repo path/to/repo' },
  ],
  results: {
    title: "Early numbers",
    body: "Real gpt-oss runs on Groq's free tier, one seed. Every method fixed every bug, so these numbers compare cost only.",
    // Sources: runs/bench-groq-test.md (1,886 vs 9,879 tokens per task) and
    // runs/bench-groq-repo.json, on the 6 tasks both methods finished (2,502 vs 3,077 tokens,
    // 3,349 vs 10,313 max prompt chars).
    stats: [
      { value: "5×", label: "fewer tokens than running every agent on every bug (10 tasks)" },
      { value: "19%", label: "fewer tokens than a one-shot prompt on a multi-file repo (6 tasks)" },
      { value: "3×", label: "smaller largest prompt on that same repo" },
      { value: "Pending", label: "cost per fix, until the Nebius runs are done" },
    ],
    caveat: "On small single-file bugs a one-shot prompt is still cheaper: about 1,000 tokens against 1,900.",
  },
  closing: "Keep your tokens.",
};

export const FOOTER = {
  line: "Built on Puppeteer++, a B.Tech final-year thesis at Christ University.",
};

// ------------------------------------------------------------------ app pages

export const APP_LINK = { href: "/ide", label: "IDE" };

export const APP_NAV = [
  { href: "/ide", label: "IDE" },
  { href: "/runs", label: "Runs" },
  { href: "/benchmarks", label: "Benchmarks" },
  { href: "/docs", label: "Docs" },
  { href: "/research", label: "Research" },
];

export const SERVER_STATUS = { checking: "Checking", live: "Local server", offline: "No local server" };

export const LOCAL_RUN = {
  title: "Run it on your own repo",
  body: "The assistant runs on your machine, so your code stays there.",
  commands: [
    { label: "Install", cmd: 'pip install -e ".[web]"' },
    { label: "Build the site", cmd: "cd web && npm ci && npm run build" },
    { label: "Start the IDE", cmd: "pcode ide" },
  ],
  after: "It opens in your browser. On Windows, you can double-click start-pcode.bat instead.",
};

export const CONSOLE = {
  title: "Console",
  lead: "Describe the bug and point at a repo with a pytest suite. Your files stay untouched until you apply the diff.",
  recorded: "Recorded run",
  recordedNote: "This is a saved run on a real model, replayed in your browser. Nothing is running now.",
  demoPending: "Recorded demo pending",
  demoPendingBody: "A demo appears here after the first successful fix on a real model.",
  form: {
    task: "What is broken?",
    taskHint: "Paste the bug report or the failing behaviour.",
    taskPlaceholder: "mean() is wrong for even lists",
    repo: "Repository folder",
    repoHint: "Full path on this machine.",
    repoPlaceholder: "C:\\code\\my-project",
    provider: "Provider",
    policy: "Policy",
    maxSteps: "Max steps",
    tdd: "Write a reproducing test first",
    submit: "Start fix",
    starting: "Starting",
  },
  noKey: (name: string) => `${name} is not set. Add it to .env and restart pcode serve.`,
  mockNote: "Mock replies are scripted. They test the app and are never a result.",
  busy: "Another run is active.",
  busyAction: "Watch it",
  cancel: "Stop after this step",
  cancelling: "Stopping after this step",
  newRun: "New fix",
  quota: "The provider's quota ran out during this run. Try again later or switch provider.",
  failedTitle: "The run could not finish",
  lost: "Lost the connection to the local server. The run may still be going.",
};

export const RUN_STATUS: Record<string, string> = {
  completed: "Finished",
  terminated: "Stopped by the agent",
  truncated: "Hit the step limit",
  failed: "Failed",
  cancelled: "Stopped by you",
};

export const RUN_VIEW = {
  agents: "Agents",
  timeline: "Steps",
  waiting: "Waiting for the first step.",
  acting: "is working",
  tests: "Tests passing",
  tokens: "Tokens so far",
  tokensNote: "Tool agents use none.",
  prompt: "Prompt size per step",
  promptNote: "Characters sent to the model. Tool steps send none.",
  promptEmpty: "No model calls yet.",
  rolledBack: "Rolled back",
  output: "Output",
  zero: "No tokens",
  tiers: { "zero-token": "No LLM", fast: "Fast model", quality: "Strong model", injected: "Join when needed" },
  verdicts: { checkpoint: "Kept", rollback: "Rolled back", accepted: "Accepted", baseline: "Baseline" } as Record<string, string>,
  diff: "Verified diff",
  diffEmpty: "This run changed nothing.",
  regressions: "Regressions",
  integrity: "Integrity violations",
  outcomeOk: "Tests pass",
  outcomeBad: "Not fixed",
  outcomeStopped: "Stopped",
  apply: "Apply to repo",
  applied: "Applied. The change is in your working tree, uncommitted.",
  applyTitle: "Apply this diff?",
  applyBody: "This writes to your working tree:",
  applyConfirm: "Apply diff",
  applyCancel: "Keep my repo as it is",
  applying: "Applying",
  applyFailed: "The diff could not be applied",
  play: "Play",
  pause: "Pause",
  stepSlider: "Replay position",
  stepOf: (now: number, total: number) => `Step ${now} of ${total}`,
};

export const RUNS = {
  title: "Runs",
  lead: "Every fix you start here or with pcode fix, saved on this machine.",
  offlineTitle: "History lives on your machine",
  offlineBody: "Saved runs are read through the local server. The recorded demo works without it.",
  demoLink: "Open the recorded demo",
  empty: "No runs yet",
  emptyBody: "Run a fix in the IDE and it shows up here.",
  emptyAction: "Open the IDE",
  error: "Could not read the run history.",
  retry: "Try again",
  columns: { task: "Task", provider: "Provider", result: "Result", tests: "Tests", tokens: "Tokens", steps: "Steps", when: "When" },
  notReal: "Mock, not a result",
  unknown: "Provider not recorded",
  notFound: "Run not found",
  notFoundBody: "It may have been saved on another machine.",
  back: "All runs",
  loadError: "Could not load this run.",
  noRun: "Pick a run from the list.",
};

export const BENCH = {
  title: "Benchmarks",
  lead: "Real model runs only. Simulated numbers never reach this page. Each section says what it covers: tasks, seeds, model, and any provider errors left out.",
  groups: {
    reasoning: {
      title: "Reasoning: the thesis core",
      body: "The Puppeteer++ engine (core.Orchestrator) on fixed samples of four standard datasets, scored by an answer checker. Every method uses the same model and the same output cap, and every call is counted, the policy's included.",
    },
    build: {
      title: "Build from a prompt: PCodeBench-Build",
      body: "Build working software in an empty folder, scored by hidden acceptance tests the model never sees. The puppeteer did not beat the plain tool loop here: it used two to three times the tokens and at best matched its pass rate. Its one lead is zero false success claims, but every puppeteer failure ended at the token limit, so that lead is not evidence that its checks caught them. 9 tasks, one seed, and the tasks were used while debugging, so this is a development set.",
    },
    fix: {
      title: "Fix a bug: PCodeBench-Mini and -Repo",
      body: "Every method fixed every bug measured so far, so these numbers compare cost, not skill.",
    },
  },
  reasoning: {
    datasets: {
      all: "All four datasets",
      gsm8k: "GSM8K",
      "gsm-hard": "GSM-Hard",
      "mmlu-pro": "MMLU-Pro",
      bbh: "BIG-Bench Hard",
      gaia: "GAIA",
    } as Record<string, string>,
    methods: {
      direct: "One call, answer only",
      cot: "One call, step by step",
      fixed: "Fixed four-agent pipeline",
      puppeteer: "Puppeteer (LLM policy)",
    } as Record<string, string>,
    columns: {
      method: "Method",
      correct: "Correct",
      accuracy: "Accuracy, 95% interval",
      tokens: "Tokens per task",
      calls: "Calls per task",
      cost: "$ per task",
      perCorrect: "Tokens per correct answer",
    },
    paired: "Puppeteer against each method, same tasks",
    pairedColumns: { against: "Against", shared: "Shared tasks", ours: "Only puppeteer right", theirs: "Only it right", p: "Exact McNemar p" },
    tokensChart: "Mean tokens per task",
    facts: (thinking: string, steps: number) =>
      `thinking ${thinking} · up to ${steps} agent steps · untrained policy`,
    notRunTitle: "Not run, and why",
    notRun: [
      "GAIA: gated on Hugging Face, so it needs a token and accepted terms, and its questions need web search and file tools that the core agents do not have. Not run, rather than run without tools and called a result.",
      "ToolBench: needs RapidAPI keys, its own tool server and a GPT-4 judge (ToolEval). The core has no tool-calling agents. Not run.",
    ],
    caveats: "One seed. Subsets of 50 to 70 questions, so the intervals are wide. GSM8K and MMLU questions may be in the model's training data, and some GSM-Hard answers are known to be wrong.",
  },
  build: {
    columns: {
      limit: "Token limit",
      method: "Method",
      passed: "Tasks passed",
      tests: "Hidden tests passed",
      tokens: "Tokens per task",
      cost: "$ per task",
      claims: "False success claims",
    },
    role: { primary: "primary", sensitivity: "sensitivity" } as Record<string, string>,
  },
  loadError: "Could not load the results.",
  retry: "Try again",
  empty: "No real results exported yet",
  emptyBody: "Run a benchmark on a real provider, then pcode export-web.",
  partial: "Partial",
  complete: "Complete",
  tasks: (measured: number, planned: number) => `${measured} of ${planned} tasks`,
  seeds: (n: number) => (n === 1 ? "1 seed" : `${n} seeds`),
  runs: (n: number) => (n === 1 ? "1 run" : `${n} runs`),
  excluded: (n: number) => `${n} provider ${n === 1 ? "error" : "errors"} excluded`,
  tokens: "Mean tokens per task",
  prompt: "Mean largest prompt, characters",
  taskCount: (n: number) => (n === 1 ? "1 task" : `${n} tasks`),
  uneven: "Methods stopped at different points, so these bars cover different tasks. Use the shared-task table for comparisons.",
  sharedTitle: "On the tasks both methods finished",
  sharedColumns: {
    method: "Against",
    tasks: "Shared tasks",
    ours: "Our tokens",
    theirs: "Their tokens",
    oursPrompt: "Our largest prompt",
    theirsPrompt: "Their largest prompt",
  },
  perTask: "Every task, tokens used",
  perTaskColumns: { task: "Task" },
  methods: {
    heuristic: "CompactCode (puppeteer)",
    direct: "Plain tool loop",
    fixed: "Every agent, every bug",
    "single-shot": "One-shot prompt",
    react: "ReAct, all files",
    "react-tools": "ReAct with read tools",
    "linear-train": "Trained policy",
  } as Record<string, string>,
  costNote: "Dollar costs use Nebius Token Factory prices from models.yaml. The Fix-mode runs used Groq's free tier and recorded no prices, so they show none.",
  trainTitle: "Policy training",
  trainLead: (n: number, ok: number) => `${n} episodes on the train split, ${ok} succeeded. Far too few to show learning, so no such claim is made here.`,
  trainTokens: "Tokens per episode",
  trainReturn: "Return per episode",
  episode: "Episode",
  trainTable: "Every episode",
};

export const DOCS = {
  title: "Docs",
  lead: "From a fresh clone to a verified diff.",
  sections: [
    {
      id: "install",
      title: "Install",
      body: "Python 3.10 or newer, and Node.js to build the IDE page. The assistant works in any folder, in any language your machine can run.",
      commands: [
        { label: "Clone", cmd: "git clone https://github.com/amaltomajith/UntitledFYProject" },
        { label: "Install", cmd: 'pip install -e ".[mcp,web]"' },
      ],
    },
    {
      id: "keys",
      title: "Keys",
      body: "Copy .env.example to .env and add NEBIUS_API_KEY from Nebius Token Factory. By default the assistant runs on NVIDIA Nemotron models there. Keys stay in .env and are removed from the environment your tests run in.",
      commands: [{ label: "Check your models", cmd: "pcode models" }],
      note: "GROQ_API_KEY also works, with a free tier. Add --provider groq to terminal commands if that is the key you have.",
    },
    {
      id: "mcp",
      title: "Use it from your assistant",
      body: "Add one MCP server. Your assistant gets puppeteer_fix, which returns a verified diff, and puppeteer_models. Nothing is written to your repo unless the call sets apply=true on a successful run.",
      commands: [
        { label: "Claude Code", cmd: "claude mcp add compactcode -- python -m puppeteer_code mcp" },
        { label: "Any other MCP client", cmd: "pcode mcp" },
      ],
    },
    {
      id: "assistant",
      title: "Use the assistant",
      body: "Talk to it in plain words and it plans, writes, runs and checks code in your folder. Every request can be undone. It asks before running commands it does not recognise as safe, but an approved command runs with your own permissions, so read what it asks to run.",
      commands: [
        { label: "In the browser", cmd: "pcode ide" },
        { label: "In the terminal", cmd: "pcode" },
      ],
      note: "Open the IDE from the link pcode ide prints. The link is the key to your local session, so do not share it. Files your .gitignore excludes are not covered by Undo.",
    },
    {
      id: "cli",
      title: "Use the terminal",
      body: "Every run happens in a throwaway copy and is saved to runs/.",
      commands: [
        { label: "Fix", cmd: 'pcode fix "mean() is wrong" --repo path/to/repo' },
        { label: "Fix and apply", cmd: 'pcode fix "mean() is wrong" --repo path/to/repo --apply' },
        { label: "Replay", cmd: "pcode replay runs/fix-<time>.json --diff" },
      ],
      note: "--apply asks before it writes.",
    },
  ],
  limits: {
    title: "Limits today",
    items: [
      "It needs a pytest suite. Other test runners are not supported yet.",
      "It fixes what tests can show. A passing suite is the only proof it has.",
      "Only one fix runs at a time in the console.",
    ],
  },
  trouble: {
    title: "Troubleshooting",
    items: [
      { q: "The console says no local server", a: "Run pcode serve, then open the site from http://127.0.0.1:8765, not from another address." },
      { q: "A key is reported as not set", a: "Add it to .env in the project folder and restart pcode serve." },
      { q: "A run stops with a quota error", a: "Free tiers have daily limits. Wait for the reset or switch provider." },
      { q: "Apply fails", a: "The file changed since the run started. Start a new fix from the current code." },
      { q: "Requests fail on a company network", a: "Point PUPPETEER_CA_BUNDLE at your proxy's certificate file." },
    ],
  },
};

export const RESEARCH = {
  title: "Research",
  lead: "CompactCode is the applied half of Puppeteer++, a B.Tech final-year thesis at Christ University, Bengaluru.",
  sections: [
    {
      title: "The idea",
      body: [
        "It builds on the NeurIPS 2025 paper Multi-Agent Collaboration via Evolving Orchestration. A central policy, the puppeteer, picks which agent acts next, one step at a time, and learns to be both correct and cheap.",
        "Most multi-agent systems fix their pipeline in advance. Here the pipeline is chosen during the run, so an easy bug can stop after four steps and a hard one can call in more help.",
      ],
    },
    {
      title: "How a coding run works",
      body: [
        "Each step sees a bounded snapshot of the state, never a growing transcript. Hard rules remove pointless actions, the policy picks one agent, and the tests decide what happens next. An improvement becomes a checkpoint. Anything else is reset to the best checkpoint.",
      ],
    },
    {
      title: "The reward",
      body: [
        "The reward comes from test results, not from a model's opinion. A step earns the signed change in pass rate, credited to the agent that wrote the code. Tokens count as a cost, measured against a budget of 4,000.",
      ],
      formula: "r = pass rate + 0.5 success - 0.5 regression - 1 integrity + 0.1 lint clean",
    },
  ],
  limits: {
    title: "What is not proven yet",
    items: [
      "The bugs so far are easy for a 120B model. Every method succeeds, so success rate does not separate them.",
      "Real results cover one seed, and the multi-file run is incomplete.",
      "On small single-file bugs a one-shot prompt is cheaper: about 1,000 tokens against 1,900.",
      "Harder settings, the cross-module tasks and a smaller model doing the coding, are not measured yet.",
      "The trained policy started from simulator weights and has too few real episodes to show learning. It has not been compared with the heuristic policy.",
      "The general reasoning engine in the thesis core has no fair benchmark yet, so no numbers are quoted for it.",
    ],
  },
  link: "See the measured results",
};

export const NOT_FOUND = {
  title: "Nothing here.",
  body: "This page does not exist. The IDE and the docs do.",
  primary: "Open the IDE",
  secondary: "Back to the home page",
};

export const IDE = {
  home: "CompactCode home",
  connecting: "Connecting to pcode ide",
  lost: "Lost the connection. Reconnecting.",
  small: "The IDE needs a wider screen. On this device, use the chat in a terminal.",
  smallCommand: "Terminal chat",
  tokens: (n: number) => `${n.toLocaleString("en-US")} tokens`,
  tokensHint: "Tokens used since pcode ide started. Free plans have a daily limit.",
  tokensHintLocal: "Tokens used since pcode ide started, on the model on this computer.",
  mode: {
    label: "When to ask",
    plan: "Plan",
    ask: "Ask first",
    auto: "Auto",
    hints: {
      plan: "Reads and writes a plan. Changes nothing until you approve it.",
      ask: "Asks before running commands it does not recognise as safe.",
      auto: "Runs commands without asking. Dangerous ones are still refused.",
    },
  },
  private: {
    label: "Private",
    hintOn: "Using the model on this computer. Turn off to use a cloud model.",
    hintOff: "Use a model on this computer instead of the cloud. Starts a new conversation.",
    useLocal: "Use a model on this computer",
    useLocalHint: "Needs Ollama and a model you have pulled. No key, and your code stays off the cloud.",
    outside: (rows: string[]) => `Not covered: ${rows.join(", ")}.`,
    off: "Private is off. This conversation uses a cloud model.",
    noPreview: "Preview is off in Private. The assistant gives you the command to start your app, and you open it yourself.",
    setup: "To block the network for commands, run: pcode private-setup",
  },
  noToken: {
    title: "Open this from your computer",
    body: "The IDE runs on your machine, because it reads your files and runs your code. Start it, and it opens here by itself.",
    home: "Back to the site",
    commands: [
      { label: "Install", cmd: "pip install -e \".[web]\"" },
      { label: "Start", cmd: "pcode ide" },
    ],
  },
  setup: {
    keyTitle: "Connect a model",
    keyBody: "The assistant needs an API key for an AI model. Nebius Token Factory runs the NVIDIA models it uses by default. Groq has a free tier.",
    provider: "Provider",
    groq: "Groq (free tier)",
    nebius: "Nebius Token Factory",
    key: "API key",
    saveKey: "Save key",
    getKey: "Get a key",
    keyHint: "Saved only on this computer, in your .pcode folder. It is never shown again.",
    folderTitle: "Choose a folder",
    folderBody: "Open a project you already have, or make a new one. Every change can be undone.",
    places: "Places",
    up: "Up one level",
    noFolders: "No folders here.",
    openHere: (name: string) => `Open ${name}`,
    newLabel: "Or start a new project in this folder",
    newPlaceholder: "my-app",
    create: "Create",
    cancel: "Cancel",
    failed: "That did not work. Try again.",
  },
  activity: { label: "Views", files: "Explorer", search: "Search", git: "Source Control", tests: "Testing" },
  search: {
    label: "Search in files",
    placeholder: "Search",
    empty: "Search every file in this folder.",
    searching: "Searching",
    none: "No results.",
    truncated: "Only the first results are shown. Search for something longer to narrow them.",
    count: (hits: number, files: number) =>
      `${hits} result${hits === 1 ? "" : "s"} in ${files} file${files === 1 ? "" : "s"}`,
  },
  git: {
    refresh: "Refresh",
    notRepo: "This folder is not a git repository.",
    clean: "No changes.",
    status: { M: "Modified", A: "Added", D: "Deleted", R: "Renamed", C: "Copied", U: "Untracked" } as Record<string, string>,
  },
  testing: { none: "Test and build results appear here." },
  status: {
    label: "Status",
    connected: "Connected",
    terminal: "Terminal",
    terminalHint: "Show or hide the panel (Ctrl+`)",
  },
  tree: {
    label: "Project files",
    title: "Explorer",
    refresh: "Refresh files",
    empty: "No files yet. Ask for something and they appear here.",
    marks: { created: "new", edited: "changed", deleted: "deleted" } as Record<string, string>,
  },
  editor: {
    tabs: "Open files",
    empty: "Pick a file on the left, or ask the assistant to build something.",
    loading: "Loading editor",
    locked: "Read-only while the assistant works, so nothing you type is lost.",
    unsaved: "unsaved",
    close: (name: string) => `Close ${name}`,
    changes: "Changes",
    preview: "Preview",
    changedFiles: "Changed files",
    noChanges: "No changes to show.",
    noPreview: "Nothing is running yet.",
    binary: "This file is not text, so it cannot be shown here.",
    tooLarge: "This file is too large to open here.",
    terminal: "Terminal",
    graph: "Memory Graph",
    live: "Live",
    toPanel: (name: string) => `Move ${name} back to the panel`,
    dropEditor: "Move to editor",
  },
  folders: {
    label: "Open folders",
    bar: "Folders",
    add: "Open another folder",
    none: "New folder",
    working: "Working",
    waiting: "Waiting",
    needs: "Needs you",
    lock: "Private",
    close: (name: string) => `Close ${name}`,
    busy: "Stop the request in this folder first.",
    unsaved: (count: number) => `${count} unsaved file${count === 1 ? "" : "s"}. Close anyway?`,
    discard: "Close",
    keep: "Keep",
    saved: "Folders from last time",
    reopen: (path: string) => `Open ${path}`,
    forget: (name: string) => `Forget ${name}`,
  },
  live: {
    empty: "Ask for a change, and you can watch the work here.",
    timeline: "Work so far",
    working: "Working",
    finished: "Finished",
    thinking: "Thinking",
    files: "Files changed in this request",
    code: "Code changes",
    arriving: "Arriving",
    openTab: "Open the Live tab",
    more: "More changed here than this view shows. Open Changes to see it all.",
    noEdits: "No code has changed yet.",
    asking: "Waiting for your answer in the chat",
    noStream: "Code appears when the model finishes this reply.",
    counts: (added: number, removed: number) => `+${added} -${removed}`,
    open: (line: number) => `Open at line ${line}`,
    step: (at: number, of: number) => `step ${at} of ${of}`,
    rate: (rate: number) => `about ${Math.round(rate)} tokens a second`,
    quiet: (time: string) => `No output for ${time}. The model may still be working.`,
    phase: (label: string, phase: string, path: string) => {
      if (phase === "thinking") return `${label}: thinking`;
      if (phase === "writing") return path ? `${label}: writing ${path}` : `${label}: writing a reply`;
      if (phase === "running") return path ? `${label}: working on ${path}` : `${label}: working`;
      if (phase === "checking") return "Checking the work";
      if (phase === "queued") return `Waiting for ${path} to finish`;
      return label ? `${label}: waiting for the model` : "Getting started";
    },
    result: (status: string) => {
      if (status === "done") return "Finished";
      if (status === "cancelled") return "Stopped by you";
      if (status === "failed") return "This did not work";
      return "Stopped";
    },
  },
  bottom: {
    label: "Panel",
    terminal: "Terminal",
    output: "Output",
    outputLabel: "Command output",
    noOutput: "Commands the assistant runs, and !commands from the chat, appear here.\n",
    maximise: "Maximise panel",
    restore: "Restore panel size",
    close: "Close panel",
  },
  terminal: {
    list: "Terminals",
    create: "New terminal",
    choose: "Choose a terminal",
    split: "Split terminal",
    splitNamed: (name: string) => `Split ${name}`,
    splitWith: "Split terminal with...",
    chooseDefault: "Select default terminal...",
    isDefault: "Default",
    kill: "Kill terminal",
    killNamed: (name: string) => `Kill ${name}`,
    rename: "Rename",
    toEditor: "Move to editor",
    dragHint: "Drag to the editor to move this terminal there",
    dropSplit: "Split here",
    dropPanel: "Move to panel",
    starting: "Starting",
    none: "No terminals open.",
    needsInstall: "Terminals need one more package. Run this, then restart pcode ide:",
    exited: (code: number) => `The process exited with code ${code}.`,
  },
  chat: {
    label: "Assistant",
    inputLabel: "Tell the assistant what you want",
    placeholder: "Ask anything, / for actions",
    send: "Send",
    stop: "Stop",
    stopHint: "esc to stop",
    modelNow: "Current model",
    modeLabel: "What kind of request",
    modes: { build: "Build", fix: "Fix failing tests" },
    modeHints: {
      build: "Works directly in your folder. You can undo it.",
      fix: "Works on a copy until the tests pass, then you choose whether to apply it. Needs a pytest suite.",
    },
    fixPlaceholder: "Describe what is broken",
    fixReady: "Tests pass on a copy. Your files have not changed yet.",
    apply: "Apply to my project",
    working: "Working",
    plan: "Plan",
    approve: "Go ahead",
    approveHint: "Nothing has changed yet. Say what to do differently, or go ahead.",
    noDetails: "Nothing to show yet.",
    emptyTitle: "What should we build?",
    emptyBody: "Describe it in plain words. I plan it, write it, run it and check it. You can undo any change.",
    starters: ["explain", "design", "tests", "bugs", "review", "run"],
    approveTitle: "Run this command?",
    allow: "Run it",
    deny: "Do not run",
    allowed: "You allowed this.",
    denied: "Not run.",
    review: "Review changes",
    undo: "Undo changes",
    status: {
      done: "Finished",
      planned: "Plan ready",
      failed: "Not finished",
      stopped: "Stopped",
      cancelled: "Stopped by you",
      quota: "Usage limit reached",
      error: "Something went wrong",
    } as Record<string, string>,
    summary: (files: number, tokens: number) =>
      `${files} file${files === 1 ? "" : "s"} changed, ${tokens.toLocaleString("en-US")} tokens`,
  },
  graph: {
    title: "Memory Graph",
    open: "Open the Memory Graph",
    canvasLabel: (nodes: number, edges: number) =>
      `Graph of ${nodes} items and ${edges} links. The list beside it holds the same items.`,
    scopeLabel: "What to show",
    scopes: { session: "Current Session", project: "Full Project" },
    search: "Search memory",
    kindsLabel: "Types to show",
    kinds: { session: "Session", episode: "Episode", file: "File", symbol: "Symbol" },
    saved: (tokens: number) => `About ${tokens.toLocaleString("en-US")} tokens saved`,
    savedHint:
      "This session's own estimate: characters folded out of prompts, minus the memory added and any re-reads, divided by 4. It is not a measured comparison.",
    shown: (shown: number, total: number) => `${shown} of ${total} shown`,
    loading: "Loading memory",
    failed: "Could not load memory.",
    empty: "Nothing here yet. Memory fills in after the first request.",
    noMatch: "Nothing matches.",
    pick: "Pick an item to see it here.",
    details: "Item details",
    list: "Items in the graph",
    languages: "Python, JavaScript and TypeScript show symbols. Other files show as files. Calls are matched by name.",
    noScripts: "Python shows symbols. JavaScript and TypeScript show as files because Tree-sitter is not installed.",
    partial: "This file has a syntax error, so some symbols may be missing.",
    turn: (n: number) => `Turn ${n}`,
    result: "Result",
    check: "Checks",
    agents: "Agents",
    tokens: "Tokens",
    when: "When",
    none: "None",
    changed: "Changed files",
    jump: "Jump to this turn",
    openFile: "Open file",
    openAt: (lines: string) => `Open at line ${lines}`,
  },
  sessions: {
    untitled: "New session",
    open: "Show sessions",
    close: "Hide sessions",
    create: "New session",
    label: "Sessions",
    loading: "Loading sessions",
    empty: "Nothing saved yet. A session appears here after its first request.",
    busy: "Sessions can change once this request finishes.",
    failed: "Could not load sessions.",
    unsaved: "Not saved yet",
    rename: (title: string) => `Rename ${title}`,
    renameLabel: "Session name",
    delete: (title: string) => `Delete ${title}`,
    confirm: "Delete this session? Your code stays as it is.",
    confirmYes: "Delete",
    confirmNo: "Keep",
    turns: (n: number) => `${n} turn${n === 1 ? "" : "s"}`,
  },
  commands: {
    label: "Commands",
    // `prompt` makes it a premade request: it goes to the assistant, and any words after the name narrow it.
    list: [
      { name: "help", hint: "List every command" },
      { name: "clear", hint: "Start a new conversation" },
      { name: "resume", args: "[n]", hint: "Continue an earlier conversation" },
      { name: "undo", hint: "Put back what the last request changed" },
      { name: "diff", hint: "Review the last changes" },
      { name: "plan", hint: "Show the current plan" },
      { name: "stop", hint: "Stop the current request" },
      { name: "approve", hint: "Run the plan that is waiting" },
      { name: "mode", args: "[plan|ask|auto]", hint: "Plan first, ask before commands, or do not ask" },
      { name: "effort", args: "[fast|normal|think|deep]", hint: "How hard the assistant thinks" },
      { name: "private", args: "[on|off]", hint: "What Private covers, or change it. A change starts a new conversation" },
      { name: "think", args: "<request>", needsArg: true, hint: "Use more effort for this one request" },
      { name: "model", args: "[fast|quality id]", hint: "Show or change the models" },
      { name: "usage", hint: "Tokens used so far" },
      { name: "status", hint: "Folder, provider, models and mode" },
      { name: "preview", hint: "Show the app that is running" },
      { name: "folder", hint: "Open another folder" },
      { name: "fix", args: "<what is broken>", needsArg: true, hint: "Fix failing tests on a copy, then choose to apply" },
      { name: "explain", args: "[what]", hint: "Explain this project", prompt: "Explain what this project does and how it is organised." },
      { name: "review", args: "[what]", hint: "List bugs and risks, changing nothing", prompt: "What bugs or risks do you see in this code? List them, most serious first. Do not change anything." },
      { name: "design", args: "[what]", hint: "Make the interface look better", prompt: "Improve how this project looks. Make the interface clean and consistent, and keep what already works." },
      { name: "tests", args: "[what]", hint: "Add tests and run them", prompt: "Add tests for the main code, and run them." },
      { name: "bugs", args: "[what]", hint: "Find and fix bugs", prompt: "Find and fix any bugs, and run the tests to check." },
      { name: "refactor", args: "[what]", hint: "Clean up without changing behaviour", prompt: "Clean up the code without changing what it does, and run the tests to check." },
      { name: "docs", args: "[what]", hint: "Write a README", prompt: "Write a README that explains what this project does and how to run it." },
      { name: "run", hint: "Start the project", prompt: "Start this project so I can see it running." },
    ] as SlashCommand[],
    tips: [
      ["!command", "Runs a command in this folder"],
      ["Esc", "Stops the current request"],
      ["Up", "Brings back what you typed"],
      ["Tab", "Completes a command"],
      ["Ctrl+`", "Shows or hides the terminal"],
    ],
    helpHeads: { actions: "Commands", prompts: "Premade requests", tips: "Keys" },
    unknown: (name: string) => `Unknown command /${name}. Try /help.`,
    busy: "Still working on the last request. /stop stops it.",
    idle: "Nothing is running.",
    noUndo: "Nothing to undo.",
    noPlan: "No plan yet.",
    noResume: "No earlier conversations in this folder.",
    noSession: "No such conversation.",
    pickResume: "Earlier conversations here:",
    modeIs: (mode: string) => `Mode: ${mode}`,
    modeUsage: "Use /mode plan, /mode ask or /mode auto.",
    noPending: "No plan is waiting. Switch to /mode plan and ask for something.",
    effortIs: (level: string) => `Effort: ${level}`,
    effortUsage: "Use /effort fast, normal, think or deep.",
    privateUsage: "Use /private on or /private off.",
    thinkUsage: "Say what you want: /think how should this be structured",
    modelUsage: "Use /model fast <id> or /model quality <id>.",
    fixUsage: "Say what is broken: /fix the total is wrong",
    status: (rows: [string, string][]) => rows.map(([k, v]) => `${k.padEnd(11)}${v}`).join("\n"),
    statusRows: { folder: "Folder", provider: "Provider", mode: "Mode", effort: "Effort", private: "Private", conversation: "Chat", usage: "Usage" },
  },
};

export type SlashCommand = { name: string; hint: string; args?: string; needsArg?: boolean; prompt?: string };
