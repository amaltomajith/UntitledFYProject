"use client";

import { useCallback, useEffect, useState } from "react";
import { getResults, type Benchmark, type BenchRow, type Training } from "@/lib/api";
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
        <h2 className="bench__title">
          {b.dataset}
          {b.model ? <span className="bench__model"> {b.model}</span> : null}
        </h2>
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
          <h3 className="label bench__sub">{BENCH.sharedTitle}</h3>
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
        <h2 className="bench__title">{BENCH.trainTitle}</h2>
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

export function Benchmarks() {
  const [data, setData] = useState<{ benchmarks: Benchmark[]; training: Training[] } | null | undefined>(undefined);

  const load = useCallback(() => {
    setData(undefined);
    getResults().then(setData);
  }, []);

  useEffect(load, [load]);

  const benches = [...(data?.benchmarks ?? [])].sort((a, b) => b.tasks_measured - a.tasks_measured);
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
      {data && !benches.length ? (
        <Notice title={BENCH.empty}>
          <p className="muted">{BENCH.emptyBody}</p>
        </Notice>
      ) : null}
      {benches.map((b) => (
        <BenchSection key={b.id} b={b} />
      ))}
      {data?.training.filter((t) => t.episodes.length).map((t) => <TrainingSection key={t.id} t={t} />)}
      {benches.length ? <p className="muted">{BENCH.costPending}</p> : null}
    </>
  );
}
