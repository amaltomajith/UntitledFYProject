// The agent pool, mirrored from puppeteer_code/agents.py (+ the Explorer fan-out in explore.py).
// One 3D tile per agent; the order sets the tile index shown in the hover chip.

export type Tier = "zero-token" | "fast" | "quality" | "injected";

export type IconName =
  | "MagnifyingGlass"
  | "TreeStructure"
  | "Code"
  | "Flask"
  | "Bug"
  | "Broom"
  | "Eye"
  | "PencilLine"
  | "BookOpen"
  | "GitBranch"
  | "Flag"
  | "Power";

export type Agent = { name: string; icon: IconName; tier: Tier };

export const AGENTS: Agent[] = [
  { name: "RepoScout", icon: "MagnifyingGlass", tier: "zero-token" },
  { name: "Planner", icon: "TreeStructure", tier: "fast" },
  { name: "Coder", icon: "Code", tier: "quality" },
  { name: "TestRunner", icon: "Flask", tier: "zero-token" },
  { name: "Debugger", icon: "Bug", tier: "quality" },
  { name: "Linter", icon: "Broom", tier: "zero-token" },
  { name: "Reviewer", icon: "Eye", tier: "fast" },
  { name: "TestWriter", icon: "PencilLine", tier: "injected" },
  { name: "DocsSearch", icon: "BookOpen", tier: "injected" },
  { name: "Explorer", icon: "GitBranch", tier: "injected" },
  { name: "Concluder", icon: "Flag", tier: "fast" },
  { name: "Terminator", icon: "Power", tier: "fast" },
];

// Roles used by the "How it works" choreography (indices into AGENTS).
export const ROLE = {
  coder: 2,
  testRunner: 3,
  injected: [7, 8, 9],
  shielded: [6, 10, 11],
} as const;

export const pad2 = (n: number) => String(n).padStart(2, "0");
