"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import {
  ApiError,
  cancelRun,
  eventsUrl,
  getDemoRun,
  getProviders,
  startRun,
  type Health,
  type Provider,
} from "@/lib/api";
import { CONSOLE } from "@/lib/content";
import { EMPTY_RUN, hitQuota, reduceRun, type RunEvent, type RunRecord } from "@/lib/runEvents";
import { useServer } from "@/lib/useServer";
import { Outcome } from "./Outcome";
import { RunBoard } from "./RunBoard";
import { RunPlayer } from "./RunPlayer";
import { LocalRun, Notice, Skeleton } from "./States";

const REPO_KEY = "pcode.repo";

function rememberedRepo() {
  try {
    return window.localStorage.getItem(REPO_KEY) ?? "";
  } catch {
    return "";
  }
}

function rememberRepo(repo: string) {
  try {
    window.localStorage.setItem(REPO_KEY, repo);
  } catch {
    // only a convenience
  }
}

function Recorded() {
  const [run, setRun] = useState<RunRecord | null | undefined>(undefined);

  useEffect(() => {
    getDemoRun().then(setRun);
  }, []);

  return (
    <>
      <LocalRun />
      {run === undefined ? <Skeleton /> : null}
      {run === null ? (
        <Notice title={CONSOLE.demoPending}>
          <p className="muted">{CONSOLE.demoPendingBody}</p>
        </Notice>
      ) : null}
      {run ? (
        <>
          <div className="recorded">
            <span className="tag tag--strong">{CONSOLE.recorded}</span>
            <p className="muted">{CONSOLE.recordedNote}</p>
            <p className="recorded__task">{run.task}</p>
          </div>
          <RunPlayer run={run} autoplay />
        </>
      ) : null}
    </>
  );
}

type Busy = { run: string } | null;

function RunForm({ onStart, onWatch }: { onStart: (id: string) => void; onWatch: (id: string) => void }) {
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [policies, setPolicies] = useState<string[]>(["heuristic"]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [task, setTask] = useState("");
  const [repo, setRepo] = useState("");
  const [provider, setProvider] = useState("");
  const [policy, setPolicy] = useState("heuristic");
  const [maxSteps, setMaxSteps] = useState(12);
  const [tdd, setTdd] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);

  useEffect(() => {
    setRepo(rememberedRepo());
    getProviders()
      .then((data) => {
        setProviders(data.providers);
        setPolicies(data.policies);
        const usable = data.providers.find((p) => p.real && p.key_set) ?? data.providers.find((p) => p.id === data.default);
        setProvider(usable?.id ?? data.providers[0]?.id ?? "");
      })
      .catch((e: Error) => setLoadError(e.message));
  }, []);

  const chosen = providers?.find((p) => p.id === provider);
  const blocked = chosen ? chosen.needs_key && !chosen.key_set : false;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStarting(true);
    setError(null);
    setBusy(null);
    try {
      rememberRepo(repo.trim());
      const { id } = await startRun({ task: task.trim(), repo: repo.trim(), provider, policy, max_steps: maxSteps, tdd });
      onStart(id);
    } catch (err) {
      if (err instanceof ApiError && err.code === "run_active") setBusy({ run: err.run ?? "" });
      else setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  };

  if (loadError) {
    return (
      <Notice title={CONSOLE.failedTitle}>
        <p className="muted">{loadError}</p>
      </Notice>
    );
  }
  if (!providers) return <Skeleton rows={4} />;

  return (
    <form className="panel form" onSubmit={submit}>
      <div className="field field--wide">
        <label htmlFor="task">{CONSOLE.form.task}</label>
        <textarea
          id="task"
          required
          rows={3}
          maxLength={4000}
          value={task}
          placeholder={CONSOLE.form.taskPlaceholder}
          aria-describedby="task-hint"
          onChange={(e) => setTask(e.target.value)}
        />
        <p id="task-hint" className="field__hint">
          {CONSOLE.form.taskHint}
        </p>
      </div>
      <div className="field field--wide">
        <label htmlFor="repo">{CONSOLE.form.repo}</label>
        <input
          id="repo"
          required
          value={repo}
          spellCheck={false}
          autoComplete="off"
          placeholder={CONSOLE.form.repoPlaceholder}
          aria-describedby="repo-hint"
          onChange={(e) => setRepo(e.target.value)}
        />
        <p id="repo-hint" className="field__hint">
          {CONSOLE.form.repoHint}
        </p>
      </div>
      <div className="field">
        <label htmlFor="provider">{CONSOLE.form.provider}</label>
        <select id="provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
          {providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="policy">{CONSOLE.form.policy}</label>
        <select id="policy" value={policy} onChange={(e) => setPolicy(e.target.value)}>
          {policies.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="steps">{CONSOLE.form.maxSteps}</label>
        <input
          id="steps"
          type="number"
          min={1}
          max={40}
          value={maxSteps}
          onChange={(e) => setMaxSteps(Number(e.target.value))}
        />
      </div>
      <label className="check">
        <input type="checkbox" checked={tdd} onChange={(e) => setTdd(e.target.checked)} />
        {CONSOLE.form.tdd}
      </label>
      <div className="form__foot">
        {blocked && chosen?.key_name ? (
          <p role="alert" className="form-error">
            {CONSOLE.noKey(chosen.key_name)}
          </p>
        ) : null}
        {chosen && !chosen.real ? <p className="field__hint">{CONSOLE.mockNote}</p> : null}
        {error ? (
          <p role="alert" className="form-error">
            {error}
          </p>
        ) : null}
        {busy ? (
          <p role="alert" className="form-error">
            {CONSOLE.busy}{" "}
            <button type="button" className="linkish" onClick={() => onWatch(busy.run)}>
              {CONSOLE.busyAction}
            </button>
          </p>
        ) : null}
        <button type="submit" className="btn btn--primary" disabled={starting || blocked}>
          {starting ? CONSOLE.form.starting : CONSOLE.form.submit}
        </button>
      </div>
    </form>
  );
}

function Live({ health, onLost }: { health: Health; onLost: () => void }) {
  const [runId, setRunId] = useState<string | null>(health.active_run);
  const [view, dispatch] = useReducer(reduceRun, EMPTY_RUN);
  const [lost, setLost] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const source = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!runId) return;
    const es = new EventSource(eventsUrl(runId));
    source.current = es;
    let finished = false;
    es.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as RunEvent;
      setLost(false);
      dispatch(event);
      if (event.type === "end" || event.type === "error") {
        finished = true;
        es.close();
      }
    };
    es.onerror = () => {
      if (!finished) setLost(true);
    };
    return () => es.close();
  }, [runId]);

  const reset = () => {
    setRunId(null);
    setCancelling(false);
    setLost(false);
    dispatch({ type: "start", task: "", baseline: { passed: 0, total: 0, pass_rate: 0 }, max_steps: 0 });
  };

  if (!runId) return <RunForm onStart={setRunId} onWatch={setRunId} />;

  const stop = async () => {
    setCancelling(true);
    try {
      await cancelRun(runId);
    } catch {
      setCancelling(false);
      onLost();
    }
  };

  return (
    <>
      <div className="runbar">
        <p className="runbar__task">{view.task || runId}</p>
        {view.phase === "running" ? (
          <button type="button" className="btn btn--ghost btn--sm" onClick={stop} disabled={cancelling}>
            {cancelling ? CONSOLE.cancelling : CONSOLE.cancel}
          </button>
        ) : null}
        {view.phase === "done" || view.phase === "error" ? (
          <button type="button" className="btn btn--primary btn--sm" onClick={reset}>
            {CONSOLE.newRun}
          </button>
        ) : null}
      </div>
      {lost ? (
        <p role="alert" className="form-error">
          {CONSOLE.lost}
        </p>
      ) : null}
      {view.phase === "error" ? (
        <Notice title={CONSOLE.failedTitle}>
          <p className="muted">{view.error}</p>
        </Notice>
      ) : null}
      {hitQuota(view.steps) ? (
        <p role="alert" className="form-error">
          {CONSOLE.quota}
        </p>
      ) : null}
      <RunBoard view={view} />
      {view.result ? <Outcome result={view.result} applyId={runId} /> : null}
    </>
  );
}

export function Console() {
  const { server, refresh } = useServer();
  return (
    <>
      <header className="page-head">
        <h1 className="page-title">{CONSOLE.title}</h1>
        <p className="page-lead">{CONSOLE.lead}</p>
      </header>
      {server.status === "checking" ? <Skeleton rows={4} /> : null}
      {server.status === "offline" ? <Recorded /> : null}
      {server.status === "live" ? <Live health={server.health} onLost={refresh} /> : null}
    </>
  );
}
