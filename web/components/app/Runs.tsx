"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, getDemoRun, getRun, listRuns, type RunRow } from "@/lib/api";
import { CONSOLE, RUNS, RUN_STATUS } from "@/lib/content";
import type { RunRecord } from "@/lib/runEvents";
import { useServer } from "@/lib/useServer";
import { RunPlayer } from "./RunPlayer";
import { LocalRun, Notice, Skeleton } from "./States";

const fmt = (n: number | null) => (n === null || n === undefined ? "n/a" : n.toLocaleString("en-US"));

function providerLabel(p: string) {
  if (p === "mock" || p === "sim") return RUNS.notReal;
  if (p === "unknown") return RUNS.unknown;
  return p;
}

function when(iso: string) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function RunTable({ rows }: { rows: RunRow[] }) {
  const c = RUNS.columns;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={RUNS.title}>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">{c.task}</th>
            <th scope="col">{c.result}</th>
            <th scope="col">{c.provider}</th>
            <th scope="col" className="num">{c.tests}</th>
            <th scope="col" className="num">{c.tokens}</th>
            <th scope="col" className="num">{c.steps}</th>
            <th scope="col">{c.when}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <th scope="row" className="table__task">
                <Link href={`/runs/view?id=${encodeURIComponent(r.id)}`}>{r.task}</Link>
              </th>
              <td>{r.success ? "Tests pass" : (RUN_STATUS[r.status ?? ""] ?? "Not fixed")}</td>
              <td>{providerLabel(r.provider)}</td>
              <td className="num">{r.total ? `${r.passed}/${r.total}` : "n/a"}</td>
              <td className="num">{fmt(r.tokens)}</td>
              <td className="num">{r.steps}</td>
              <td>{when(r.created)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Runs() {
  const { server } = useServer();
  const [rows, setRows] = useState<RunRow[] | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(() => {
    setError(false);
    setRows(null);
    listRuns()
      .then((d) => setRows(d.runs))
      .catch(() => setError(true));
  }, []);

  useEffect(() => {
    if (server.status === "live") load();
  }, [server.status, load]);

  return (
    <>
      <header className="page-head">
        <h1 className="page-title">{RUNS.title}</h1>
        <p className="page-lead">{RUNS.lead}</p>
      </header>
      {server.status === "checking" ? <Skeleton rows={5} /> : null}
      {server.status === "offline" ? (
        <>
          <Notice title={RUNS.offlineTitle}>
            <p className="muted">{RUNS.offlineBody}</p>
            <Link href="/runs/view?id=demo" className="btn btn--ghost btn--sm notice__action">
              {RUNS.demoLink}
            </Link>
          </Notice>
          <LocalRun />
        </>
      ) : null}
      {server.status === "live" && error ? (
        <Notice title={RUNS.error}>
          <button type="button" className="btn btn--ghost btn--sm notice__action" onClick={load}>
            {RUNS.retry}
          </button>
        </Notice>
      ) : null}
      {server.status === "live" && !error && rows === null ? <Skeleton rows={5} /> : null}
      {rows?.length === 0 ? (
        <Notice title={RUNS.empty}>
          <p className="muted">{RUNS.emptyBody}</p>
          <Link href="/ide" className="btn btn--primary btn--sm notice__action">
            {RUNS.emptyAction}
          </Link>
        </Notice>
      ) : null}
      {rows?.length ? <RunTable rows={rows} /> : null}
    </>
  );
}

type Loaded = { state: "loading" } | { state: "missing"; demo: boolean } | { state: "error" } | { state: "ok"; run: RunRecord };

export function RunDetail() {
  const id = useSearchParams()?.get("id") ?? null;
  const { server } = useServer();
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });

  useEffect(() => {
    if (!id) return;
    if (id === "demo") {
      getDemoRun().then((run) => setLoaded(run ? { state: "ok", run } : { state: "missing", demo: true }));
      return;
    }
    if (server.status === "checking") return;
    if (server.status === "offline") {
      setLoaded({ state: "missing", demo: false });
      return;
    }
    getRun(id)
      .then((run) => setLoaded(run.steps ? { state: "ok", run } : { state: "missing", demo: false }))
      .catch((e) => setLoaded(e instanceof ApiError && e.status === 404 ? { state: "missing", demo: false } : { state: "error" }));
  }, [id, server.status]);

  const demo = id === "demo";
  return (
    <>
      <header className="page-head">
        <Link href="/runs" className="back">
          {RUNS.back}
        </Link>
        <h1 className="page-title page-title--task">
          {loaded.state === "ok" ? loaded.run.task : demo ? CONSOLE.recorded : RUNS.title}
        </h1>
        {loaded.state === "ok" ? (
          <p className="page-lead">
            {demo ? `${CONSOLE.recorded}. ` : ""}
            {[providerLabel(loaded.run.provider ?? "unknown"), loaded.run.policy].filter(Boolean).join(" · ")}
          </p>
        ) : null}
      </header>
      {!id ? <Notice title={RUNS.noRun} /> : null}
      {id && loaded.state === "loading" ? <Skeleton rows={5} /> : null}
      {loaded.state === "error" ? <Notice title={RUNS.loadError} /> : null}
      {loaded.state === "missing" ? (
        <Notice title={loaded.demo ? CONSOLE.demoPending : RUNS.notFound}>
          <p className="muted">{loaded.demo ? CONSOLE.demoPendingBody : RUNS.notFoundBody}</p>
        </Notice>
      ) : null}
      {loaded.state === "ok" ? (
        <RunPlayer run={loaded.run} applyId={!demo && server.status === "live" ? id ?? undefined : undefined} />
      ) : null}
    </>
  );
}
