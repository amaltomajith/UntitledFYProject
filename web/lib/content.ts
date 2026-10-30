// All visible copy lives here. House rules: no em or en dashes, no invented numbers.

export const GITHUB_URL = "https://github.com/amaltomajith/UntitledFYProject";
export const CTA_LABEL = "View on GitHub";

export const NAV = [
  { href: "#about", label: "About" },
  { href: "#how", label: "How it works" },
  { href: "#capabilities", label: "Capabilities" },
  { href: "#start", label: "Get started" },
];

export const CHAPTERS = ["Welcome", "About", "How it works", "Capabilities", "Get started"];

export const HERO = {
  eyebrow: "A multi-agent coding assistant on Nebius Token Factory",
  lines: ["Bugs found.", "Tests green."],
  sub: "A learned policy picks each agent. Your test suite judges every edit, and you get a verified diff.",
  secondary: "How it works",
};

export const ABOUT = {
  chip: "About",
  line1: "Every step chosen.",
  line2: "Every edit tested.",
  sub: "Puppeteer-Code fixes bugs in Python repositories. It works on a sandbox copy and hands you a diff only after your tests pass.",
  m1: "A fixed pipeline runs every agent, every time.",
  m2: ["Puppeteer-Code", "picks", "the next one,"],
  m3: "and stops when the tests pass.",
};

export const HOW = {
  chip: "How it works",
  title: "One episode, five moves.",
  steps: [
    {
      verb: "Inject",
      body: "The registry adds TestWriter, DocsSearch or Explorer when the state calls for them.",
    },
    {
      verb: "Shield",
      body: "Hard rules remove pointless or unsafe actions before the policy sees them.",
    },
    {
      verb: "Pick",
      body: "The policy chooses the next agent. Heuristic, LLM, or a linear policy trained with REINFORCE.",
    },
    {
      verb: "Act",
      body: "The agent works in a throw-away git sandbox and sees a bounded snapshot, never the whole transcript.",
    },
    {
      verb: "Judge",
      body: "Tests decide. An improvement becomes a checkpoint. A regression rolls back.",
    },
  ],
  episodeLabel: "Example episode from the design notes",
  passLabel: "Tests passing",
};

export const CAPABILITIES = {
  title: "Built to be checked.",
  sub: "Six parts keep each fix cheap, bounded and verifiable.",
  cells: {
    routing: {
      title: "Tiered routing on Nebius",
      body: "Fast models plan, review and summarise. Quality models write patches. Tool agents spend zero tokens.",
    },
    context: {
      title: "Bounded context",
      body: "Each agent sees the task, a test digest and the relevant source, filled to a fixed budget. Prompts do not grow with the step count.",
    },
    scouting: {
      title: "AST scouting",
      body: "A stdlib ast index ranks symbols by traceback frames, calls from failing tests and task terms.",
    },
    sandbox: {
      title: "Sandbox and rollback",
      body: "Every improvement is a git commit. A syntax error, a regression or no progress resets to the best checkpoint, with the reason recorded.",
    },
    explore: {
      title: "Parallel exploration",
      body: "After a stall, three worktrees try different models, temperatures and strategies. The best candidate is promoted.",
    },
    reward: {
      title: "Rewards from tests",
      body: "Credit for a change in passing tests goes to the agent that wrote the code.",
    },
  },
};

export const START = {
  title: "Run it on your repo.",
  note: "Set NEBIUS_API_KEY in .env first. Groq and an offline mock provider also work.",
  commands: [
    { label: "Install", cmd: "pip install -e ." },
    { label: "Check models", cmd: "pcode models" },
    { label: "Fix a bug", cmd: 'pcode fix "mean() is wrong for even lists" --repo path/to/repo' },
  ],
  results: {
    title: "Benchmark results",
    body: "PCodeBench-Mini has 37 bug-fix tasks with hidden tests. Results will be published after the Nebius run.",
    metrics: ["pass@1", "$ per fix", "Tokens", "Steps"],
    pending: "Pending",
  },
  closing: "Let the tests decide.",
};

export const FOOTER = {
  line: "Built on Puppeteer++. B.Tech final-year thesis, Christ University.",
};
