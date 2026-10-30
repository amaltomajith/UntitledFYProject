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
  eyebrow: "A coding assistant that plugs into yours",
  lines: ["Out of tokens", "again?"],
  sub: "Send your failing tests to us over MCP. You get back a diff that passes, and your assistant keeps its tokens.",
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
    { label: "Add to Claude Code", cmd: "claude mcp add puppeteer-code -- python -m puppeteer_code mcp" },
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
