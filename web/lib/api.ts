// Client for `pcode serve`. Same origin in production; `next dev` on :3000 talks to :8765.

import type { RunRecord } from "./runEvents";

export type Health = {
  ok: boolean;
  keys: Record<string, boolean>;
  tavily: boolean;
  default_provider: string;
  active_run: string | null;
};

export type Provider = {
  id: string;
  needs_key: boolean;
  key_set: boolean;
  key_name: string | null;
  tiers: Record<string, string>;
  real: boolean;
};

export type RunRow = {
  id: string;
  task: string;
  repo: string;
  provider: string;
  policy: string | null;
  status: string | null;
  success: boolean | null;
  steps: number;
  tokens: number | null;
  passed: number | null;
  total: number | null;
  wall_s: number | null;
  created: string;
};

export type StartRequest = {
  task: string;
  repo: string;
  provider: string;
  policy: string;
  max_steps: number;
  tdd: boolean;
};

export type SummaryCell = { mean: number; std: number; n: number };
export type MethodSummary = {
  success: SummaryCell;
  tokens: SummaryCell;
  steps: SummaryCell;
  max_prompt_chars: SummaryCell;
  runs: number;
};

export type BenchRow = {
  method: string;
  task: string;
  seed: number;
  success: boolean;
  tokens: number;
  steps: number;
  max_prompt_chars: number;
  wall_s: number;
};

export type Benchmark = {
  id: string;
  provider: string;
  dataset: string;
  split: string | null;
  model: string | null;
  methods: string[];
  seeds: number[] | null;
  partial: boolean;
  tasks_planned: number;
  tasks_measured: number;
  tasks_all_methods: number;
  runs: number;
  provider_errors: number;
  summary: Record<string, MethodSummary>;
  rows: BenchRow[];
};

export type Episode = {
  episode: number;
  task: string;
  success: boolean;
  tokens: number;
  steps: number;
  return: number;
  terminal_reward: number;
};

export type Training = { id: string; provider: string; lr: number | null; note: string | null; episodes: Episode[] };

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public run?: string,
  ) {
    super(message);
  }
}

export function apiBase() {
  if (typeof window === "undefined") return "";
  return window.location.port === "3000" ? `http://${window.location.hostname}:8765` : "";
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (res.ok) return (await res.json()) as T;
  const body = await res.json().catch(() => null);
  const detail = body?.detail;
  if (typeof detail === "object" && detail) {
    throw new ApiError(res.status, detail.code ?? "error", detail.message ?? "The server refused this.", detail.run);
  }
  throw new ApiError(res.status, "error", typeof detail === "string" ? detail : `The server answered ${res.status}.`);
}

/** Null means no local server: the hosted site, or `pcode serve` is not running. */
export async function getHealth(): Promise<Health | null> {
  try {
    const health = await call<Health>("/api/health");
    return health?.ok ? health : null;
  } catch {
    return null;
  }
}

export const getProviders = () => call<{ providers: Provider[]; default: string; policies: string[] }>("/api/providers");
export const listRuns = () => call<{ runs: RunRow[] }>("/api/runs");
export const getRun = (id: string) => call<RunRecord & { id: string; error?: string }>(`/api/runs/${encodeURIComponent(id)}`);
export const startRun = (req: StartRequest) =>
  call<{ id: string }>("/api/runs", { method: "POST", body: JSON.stringify(req) });
export const cancelRun = (id: string) => call(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
export const applyRun = (id: string) =>
  call<{ applied: boolean; repo: string }>(`/api/runs/${encodeURIComponent(id)}/apply`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });

export const eventsUrl = (id: string) => `${apiBase()}/api/runs/${encodeURIComponent(id)}/events`;

async function staticJson<T>(name: string): Promise<T | null> {
  try {
    const res = await fetch(`/data/${name}.json`);
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

/** The real run exported for the hosted site, or null until one exists. */
export const getDemoRun = () => staticJson<RunRecord & { id: string }>("demo-run");

export async function getResults(): Promise<{ benchmarks: Benchmark[]; training: Training[] } | null> {
  const live = await call<{ benchmarks: Benchmark[]; training: Training[] }>("/api/benchmarks").catch(() => null);
  if (live?.benchmarks.length) return live;
  const [benchmarks, training] = await Promise.all([
    staticJson<Benchmark[]>("benchmarks"),
    staticJson<Training[]>("training"),
  ]);
  return benchmarks ? { benchmarks, training: training ?? [] } : live;
}
