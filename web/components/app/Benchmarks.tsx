"use client";

import { useCallback, useEffect, useState } from "react";
import { getResults, type Benchmark, type BenchRow, type BuildBench, type ReasoningBench, type Results, type Training } from "@/lib/api";
import { BENCH } from "@/lib/content";
import { BarList, Columns, Line, type Bar } from "./Charts";
import { Notice, Skeleton } from "./States";

const OURS = "heuristic";
const fmt = (n: number) => Math.round(n).toLocaleString("en-US");
const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
const methodName = (m: string) => BENCH.methods[m] ?? m;

function tasksFor(rows: BenchRow[], method: string) {
  return new Set(rows.filter((r) => r.method === method).map((r) => r.task));
}

function shared(b: Benchmark) {
  const ours = b.rows.filter((r) => r.method === OURS);
  return b.methods
    .filter((m) => m !== OURS && tasksFor(b.rows, m).size > 0)
    .map((m) => {
      const key = (r: BenchRow) => `${r.seed}:${r.task}`;
      const theirs = new Map(b.rows.filter((r) => r.method === m).map((r) => [key(r), r]));
      const pairs = ours.filter((r) => theirs.has(key(r))).map((r) => [r, theirs.get(key(r))!] as const);
      return {
        method: m,
        tasks: pairs.length,
        ours: mean(pairs.map(([a]) => a.tokens)),
        theirs: mean(pairs.map(([, t]) => t.tokens)),
        oursPrompt: mean(pairs.map(([a]) => a.max_prompt_chars)),
        theirsPrompt: mean(pairs.map(([, t]) => t.max_prompt_chars)),
      };
    })
    .filter((row) => row.tasks > 0);
}

function PerTask({ b }: { b: Benchmark }) {
  const methods = b.methods.filter((m) => tasksFor(b.rows, m).size > 0);
  const tasks = [...new Set(b.rows.map((r) => r.task))].sort();
  const cell = (task: string, method: string) => {
    const rows = b.rows.filter((r) => r.task === task && r.method === method);
    return rows.length ? fmt(mean(rows.map((r) => r.tokens))) : "Not run";
  };
  return (
    <details className="more">
      <summary>{BENCH.perTask}</summary>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={BENCH.perTask}>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{BENCH.perTaskColumns.task}</th>
              {methods.map((m) => (
                <th key={m} scope="col" className="num">
                  {methodName(m)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t}>
                <th scope="row" className="mono">{t}</th>
                {methods.map((m) => (
                  <td key={m} className="num">
                    {cell(t, m)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function BenchSection({ b }: { b: Benchmark }) {
  const methods = b.methods.filter((m) => b.summary[m]);
  const bars = (value: (m: string) => number): Bar[] =>
    methods
      .map((m) => ({
        label: methodName(m),
        value: value(m),
        note: BENCH.taskCount(tasksFor(b.rows, m).size),
        emphasis: m === OURS,
      }))
      .sort((x, y) => x.value - y.value);
  const counts = new Set(methods.map((m) => tasksFor(b.rows, m).size));
  const pairs = shared(b);
  const c = BENCH.sharedColumns;

  return (
    <section className="panel bench" aria-label={`${b.dataset} ${b.model ?? ""}`}>
      <header className="bench__head">
        <h3 className="bench__title">
          {b.dataset}
          {b.model ? <span className="bench__model"> {b.model}</span> : null}
        </h3>
        <span className="tag" data-partial={b.partial || undefined}>
          {b.partial ? BENCH.partial : BENCH.complete}
        </span>
        <p className="muted bench__facts">
          {[
            b.provider,
            b.split ? `${b.split} split` : null,
            BENCH.tasks(b.tasks_measured, b.tasks_planned),
            BENCH.seeds(b.seeds?.length ?? 1),
            BENCH.runs(b.runs),
            b.provider_errors ? BENCH.excluded(b.provider_errors) : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>
      <div className="bench__charts">
        <BarList title={BENCH.tokens} bars={bars((m) => b.summary[m].tokens.mean)} format={fmt} />
        <BarList title={BENCH.prompt} bars={bars((m) => b.summary[m].max_prompt_chars.mean)} format={fmt} />
      </div>
      {counts.size > 1 ? <p className="muted muted--sm">{BENCH.uneven}</p> : null}
      {pairs.length ? (
        <>
          <h4 className="label bench__sub">{BENCH.sharedTitle}</h4>
          <div className="table-wrap" tabIndex={0} role="region" aria-label={BENCH.sharedTitle}>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">{c.method}</th>
                  <th scope="col" className="num">{c.tasks}</th>
                  <th scope="col" className="num">{c.ours}</th>
                  <th scope="col" className="num">{c.theirs}</th>
                  <th scope="col" className="num">{c.oursPrompt}</th>
                  <th scope="col" className="num">{c.theirsPrompt}</th>
                </tr>
              </thead>
              <tbody>
                {pairs.map((p) => (
                  <tr key={p.method}>
                    <th scope="row">{methodName(p.method)}</th>
                    <td className="num">{p.tasks}</td>
                    <td className="num">{fmt(p.ours)}</td>
                    <td className="num">{fmt(p.theirs)}</td>
                    <td className="num">{fmt(p.oursPrompt)}</td>
                    <td className="num">{fmt(p.theirsPrompt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <PerTask b={b} />
    </section>
  );
}

function TrainingSection({ t }: { t: Training }) {
  const ok = t.episodes.filter((e) => e.success).length;
  const tokens = t.episodes.map((e) => ({ x: e.episode + 1, y: e.tokens, label: `Episode ${e.episode + 1}: ${fmt(e.tokens)} tokens` }));
  const returns = t.episodes.map((e) => ({
    x: e.episode + 1,
    y: e.return,
    label: `Episode ${e.episode + 1}: return ${e.return.toFixed(2)}`,
  }));
  return (
    <section className="panel bench" aria-label={BENCH.trainTitle}>
      <header className="bench__head">
        <h3 className="bench__title">{BENCH.trainTitle}</h3>
        <p className="muted bench__facts">
          {[t.provider, t.lr !== null ? `learning rate ${t.lr}` : null, t.note].filter(Boolean).join(" · ")}
        </p>
      </header>
      <p className="muted">{BENCH.trainLead(t.episodes.length, ok)}</p>
      <div className="bench__charts">
        <Columns title={BENCH.trainTokens} points={tokens} format={fmt} xName={BENCH.episode} />
        <Line title={BENCH.trainReturn} points={returns} xName={BENCH.episode} />
      </div>
      <details className="more">
        <summary>{BENCH.trainTable}</summary>
        <div className="table-wrap" tabIndex={0} role="region" aria-label={BENCH.trainTable}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col" className="num">{BENCH.episode}</th>
                <th scope="col">{BENCH.perTaskColumns.task}</th>
                <th scope="col" className="num">{BENCH.trainTokens}</th>
                <th scope="col" className="num">{BENCH.trainReturn}</th>
              </tr>
            </thead>
            <tbody>
              {t.episodes.map((e) => (
                <tr key={e.episode}>
                  <td className="num">{e.episode + 1}</td>
                  <td className="mono">{e.task}</td>
                  <td className="num">{fmt(e.tokens)}</td>
                  <td className="num">{e.return.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

const R = BENCH.reasoning;
const REASONING_OURS = "puppeteer";
const pct = (x: number | null) => (x === null ? "n/a" : `${(x * 100).toFixed(0)}%`);
const usd = (x: number | null) => (x === null ? "n/a" : x < 0.001 ? `$${x.toFixed(5)}` : `$${x.toFixed(4)}`);
const pValue = (p: number) => (p < 0.001 ? "< 0.001" : p.toFixed(3));

function ReasoningDataset({ name, d, methods }: { name: string; d: ReasoningBench["summary"][string]; methods: string[] }) {
  const ms = methods.filter((m) => d.methods[m]);
  const c = R.columns;
  const pc = R.pairedColumns;
  const bars: Bar[] = ms
    .map((m) => ({
      label: R.methods[m] ?? m,
      value: d.methods[m].tokens ?? 0,
      note: `${pct(d.methods[m].accuracy)} correct`,
      emphasis: m === REASONING_OURS,
    }))
    .sort((x, y) => x.value - y.value);
  const paired = Object.entries(d.paired);
  const title = R.datasets[name] ?? name;
  return (
    <div className="bench__dataset">
      <h4 className="label bench__sub">{title}</h4>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={title}>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{c.method}</th>
              <th scope="col" className="num">{c.correct}</th>
              <th scope="col" className="num">{c.accuracy}</th>
              <th scope="col" className="num">{c.tokens}</th>
              <th scope="col" className="num">{c.calls}</th>
              <th scope="col" className="num">{c.cost}</th>
              <th scope="col" className="num">{c.perCorrect}</th>
            </tr>
          </thead>
          <tbody>
            {ms.map((m) => {
              const s = d.methods[m];
              return (
                <tr key={m} data-ours={m === REASONING_OURS || undefined}>
                  <th scope="row">{R.methods[m] ?? m}</th>
                  <td className="num">{`${s.correct} of ${s.n}`}</td>
                  <td className="num">{`${pct(s.accuracy)} (${pct(s.ci95[0])} to ${pct(s.ci95[1])})`}</td>
                  <td className="num">{s.tokens === null ? "n/a" : fmt(s.tokens)}</td>
                  <td className="num">{s.calls === null ? "n/a" : s.calls.toFixed(1)}</td>
                  <td className="num">{usd(s.cost_usd)}</td>
                  <td className="num">{s.tokens_per_correct === null ? "n/a" : fmt(s.tokens_per_correct)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {name === "all" ? <BarList title={R.tokensChart} bars={bars} format={fmt} /> : null}
      {paired.length ? (
        <details className="more">
          <summary>{R.paired}</summary>
          <div className="table-wrap" tabIndex={0} role="region" aria-label={`${title}: ${R.paired}`}>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">{pc.against}</th>
                  <th scope="col" className="num">{pc.shared}</th>
                  <th scope="col" className="num">{pc.ours}</th>
                  <th scope="col" className="num">{pc.theirs}</th>
                  <th scope="col" className="num">{pc.p}</th>
                </tr>
              </thead>
              <tbody>
                {paired.map(([m, p]) => (
                  <tr key={m}>
                    <th scope="row">{R.methods[m] ?? m}</th>
                    <td className="num">{p.shared}</td>
                    <td className="num">{p.ours_only}</td>
                    <td className="num">{p.theirs_only}</td>
                    <td className="num">{pValue(p.p_mcnemar)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  );
}

function ReasoningSection({ r }: { r: ReasoningBench }) {
  const order = ["all", ...r.datasets];
  const n = Object.values(r.tasks).reduce((a, b) => a + b, 0);
  const cfg = r.config as { thinking?: string; max_steps?: number };
  return (
    <section className="panel bench" aria-label={`${BENCH.groups.reasoning.title}, ${r.model ?? ""}`}>
      <header className="bench__head">
        <h3 className="bench__title">
          {r.datasets.map((d) => R.datasets[d] ?? d).join(", ")}
          {r.model ? <span className="bench__model"> {r.model}</span> : null}
        </h3>
        <span className="tag" data-partial={r.partial || undefined}>
          {r.partial ? BENCH.partial : BENCH.complete}
        </span>
        <p className="muted bench__facts">
          {[
            r.provider,
            BENCH.taskCount(n),
            BENCH.seeds(r.seeds?.length ?? 1),
            R.facts(cfg.thinking ?? "default", cfg.max_steps ?? 4),
            r.excluded ? BENCH.excluded(r.excluded) : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>
      {order.filter((d) => r.summary[d]).map((d) => (
        <ReasoningDataset key={d} name={d} d={r.summary[d]} methods={r.methods} />
      ))}
      <p className="muted muted--sm">{R.caveats}</p>
    </section>
  );
}

function NotRun() {
  return (
    <section className="panel bench" aria-label={R.notRunTitle}>
      <h3 className="bench__title">{R.notRunTitle}</h3>
      <ul className="bench__list muted">
        {R.notRun.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </section>
  );
}

function BuildSection({ runs }: { runs: BuildBench[] }) {
  const c = BENCH.build.columns;
  const first = runs[0];
  const rows = runs.flatMap((b) => b.methods.filter((m) => b.summary[m]).map((m) => ({ b, m, s: b.summary[m] })));
  return (
    <section className="panel bench" aria-label={BENCH.groups.build.title}>
      <header className="bench__head">
        <h3 className="bench__title">
          {first.dataset}
          {first.models.length ? <span className="bench__model"> {first.models.join(", ")}</span> : null}
        </h3>
        <span className="tag">{BENCH.complete}</span>
        <p className="muted bench__facts">
          {[first.provider, BENCH.taskCount(first.tasks), BENCH.seeds(first.seeds ?? 1),
            ...runs.map((b) => (b.provider_errors ? BENCH.excluded(b.provider_errors) : null))]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>
      <div className="table-wrap" tabIndex={0} role="region" aria-label={first.dataset}>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{c.limit}</th>
              <th scope="col">{c.method}</th>
              <th scope="col" className="num">{c.passed}</th>
              <th scope="col" className="num">{c.tests}</th>
              <th scope="col" className="num">{c.tokens}</th>
              <th scope="col" className="num">{c.cost}</th>
              <th scope="col" className="num">{c.claims}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ b, m, s }) => (
              <tr key={`${b.id}:${m}`}>
                <td>{`${fmt(b.token_limit)} (${BENCH.build.role[b.role] ?? b.role})`}</td>
                <th scope="row">{methodName(m)}</th>
                <td className="num">{`${Math.round(s.success * s.runs)} of ${s.runs}`}</td>
                <td className="num">{pct(s.tests_passed)}</td>
                <td className="num">{fmt(s.tokens)}</td>
                <td className="num">{usd(s.cost_usd)}</td>
                <td className="num">{s.false_claims}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Group({ title, body, children }: { title: string; body: string; children: React.ReactNode }) {
  return (
    <section className="bench-group" aria-label={title}>
      <h2 className="bench-group__title">{title}</h2>
      <p className="muted bench-group__body">{body}</p>
      {children}
    </section>
  );
}

export function Benchmarks() {
  const [data, setData] = useState<Results | null | undefined>(undefined);

  const load = useCallback(() => {
    setData(undefined);
    getResults().then(setData);
  }, []);

  useEffect(load, [load]);

  const benches = [...(data?.benchmarks ?? [])].sort((a, b) => b.tasks_measured - a.tasks_measured);
  const build = [...(data?.build ?? [])].sort((a, b) => a.token_limit - b.token_limit);
  const reasoning = data?.reasoning ?? [];
  const G = BENCH.groups;
  return (
    <>
      <header className="page-head">
        <h1 className="page-title">{BENCH.title}</h1>
        <p className="page-lead">{BENCH.lead}</p>
      </header>
      {data === undefined ? <Skeleton rows={6} /> : null}
      {data === null ? (
        <Notice title={BENCH.loadError}>
          <button type="button" className="btn btn--ghost btn--sm notice__action" onClick={load}>
            {BENCH.retry}
          </button>
        </Notice>
      ) : null}
      {data && !benches.length && !build.length && !reasoning.length ? (
        <Notice title={BENCH.empty}>
          <p className="muted">{BENCH.emptyBody}</p>
        </Notice>
      ) : null}
      {data ? (
        <Group title={G.reasoning.title} body={G.reasoning.body}>
          {reasoning.map((r) => (
            <ReasoningSection key={r.id} r={r} />
          ))}
          <NotRun />
        </Group>
      ) : null}
      {build.length ? (
        <Group title={G.build.title} body={G.build.body}>
          <BuildSection runs={build} />
        </Group>
      ) : null}
      {benches.length ? (
        <Group title={G.fix.title} body={G.fix.body}>
          {benches.map((b) => (
            <BenchSection key={b.id} b={b} />
          ))}
          {data?.training.filter((t) => t.episodes.length).map((t) => <TrainingSection key={t.id} t={t} />)}
        </Group>
      ) : null}
      {data ? <p className="muted">{BENCH.costNote}</p> : null}
    </>
  );
}
