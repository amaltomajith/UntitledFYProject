// One reducer for every view of a run: live server-sent events, a saved run, and the hosted demo.

export type TestSummary = {
  passed: number;
  total: number;
  pass_rate: number;
  failing?: string[];
  error?: string;
};

export type RunStep = {
  step: number;
  agent: string;
  model: string;
  tokens: number;
  seconds: number;
  content: string;
  success: boolean;
  error?: string | null;
  verdict?: { action?: string; reason?: string } | null;
  prompt_chars: number;
  pass_rate: number;
  best_pass_rate: number;
  rollbacks: number;
  rollback_reason?: string | null;
};

export type RunRecord = {
  id?: string;
  task: string;
  repo: string;
  status: string;
  success: boolean;
  steps: RunStep[];
  final: {
    baseline_tests?: TestSummary;
    final_tests?: TestSummary;
    diff?: string;
    diff_stat?: [number, number, number];
    regressions?: string[];
    integrity_violations?: string[];
    lint_clean?: boolean | null;
  };
  usage?: { total_tokens?: number; cost_usd?: number | null };
  policy?: string;
  provider?: string;
  wall_s?: number;
  config?: { max_steps?: number };
};

export type RunEvent =
  | { type: "start"; task: string; baseline: TestSummary; max_steps: number; seq?: number }
  | { type: "select"; step: number; agent: string; seq?: number }
  | ({ type: "step"; seq?: number } & RunStep)
  | { type: "end"; result: RunRecord; seq?: number }
  | { type: "error"; message: string; seq?: number };

export type RunView = {
  phase: "idle" | "running" | "done" | "error";
  task: string;
  maxSteps: number;
  baseline: TestSummary | null;
  steps: RunStep[];
  acting: string | null;
  result: RunRecord | null;
  error: string | null;
};

export const EMPTY_RUN: RunView = {
  phase: "idle",
  task: "",
  maxSteps: 0,
  baseline: null,
  steps: [],
  acting: null,
  result: null,
  error: null,
};

export function reduceRun(view: RunView, e: RunEvent): RunView {
  switch (e.type) {
    case "start":
      return { ...EMPTY_RUN, phase: "running", task: e.task, baseline: e.baseline, maxSteps: e.max_steps };
    case "select":
      return { ...view, acting: shortName(e.agent) };
    case "step":
      return { ...view, acting: null, steps: [...view.steps, e] };
    case "end":
      return { ...view, phase: "done", acting: null, result: e.result };
    case "error":
      return { ...view, phase: "error", acting: null, error: e.message };
  }
}

/** Rebuild the event stream from a saved run, so replay goes through the same reducer. */
export function eventsFromRun(run: RunRecord): RunEvent[] {
  const baseline = run.final?.baseline_tests ?? { passed: 0, total: 0, pass_rate: 0 };
  const events: RunEvent[] = [
    { type: "start", task: run.task, baseline, max_steps: run.config?.max_steps ?? run.steps.length },
  ];
  for (const s of run.steps) {
    events.push({ type: "select", step: s.step, agent: s.agent });
    events.push({ type: "step", ...s });
  }
  events.push({ type: "end", result: run });
  return events;
}

export const viewAt = (events: RunEvent[], count: number) => events.slice(0, count).reduce(reduceRun, EMPTY_RUN);

/** The engine names agents by class ("CoderAgent"); the UI uses the short name. */
export const shortName = (agent: string) => agent.replace(/Agent$/, "");

export const runTokens = (steps: RunStep[]) => steps.reduce((sum, s) => sum + (s.tokens || 0), 0);

export const hitQuota = (steps: RunStep[]) => steps.some((s) => /rate.?limit|\b429\b|quota/i.test(s.error ?? ""));

export function testsNow(view: RunView): { passed: number; total: number } | null {
  if (view.result?.final?.final_tests) {
    const { passed, total } = view.result.final.final_tests;
    return { passed, total };
  }
  if (!view.baseline) return null;
  const { total } = view.baseline;
  const rate = view.steps.at(-1)?.best_pass_rate;
  return { passed: rate === undefined ? view.baseline.passed : Math.round(rate * total), total };
}
