"use client";

import { useRef, useState } from "react";
import { applyRun } from "@/lib/api";
import { RUN_STATUS, RUN_VIEW } from "@/lib/content";
import type { RunRecord } from "@/lib/runEvents";

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff" tabIndex={0} aria-label={RUN_VIEW.diff}>
      {diff.split("\n").map((line, i) => {
        const kind =
          line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")
            ? "file"
            : line.startsWith("@@")
              ? "hunk"
              : line.startsWith("+")
                ? "add"
                : line.startsWith("-")
                  ? "del"
                  : "ctx";
        return (
          <span key={i} className="diff__line" data-kind={kind}>
            {line || " "}
          </span>
        );
      })}
    </pre>
  );
}

function ApplyControl({ runId, repo }: { runId: string; repo: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [state, setState] = useState<"idle" | "applying" | "applied">("idle");
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setState("applying");
    setError(null);
    try {
      await applyRun(runId);
      setState("applied");
    } catch (e) {
      setState("idle");
      setError(e instanceof Error ? e.message : RUN_VIEW.applyFailed);
    }
    dialog.current?.close();
  };

  if (state === "applied") return <p role="status">{RUN_VIEW.applied}</p>;

  return (
    <div className="apply">
      <button type="button" className="btn btn--primary" onClick={() => dialog.current?.showModal()}>
        {RUN_VIEW.apply}
      </button>
      {error ? (
        <p role="alert" className="form-error">
          {RUN_VIEW.applyFailed}: {error}
        </p>
      ) : null}
      <dialog ref={dialog} className="dialog" aria-labelledby="apply-title">
        <h2 id="apply-title" className="dialog__title">
          {RUN_VIEW.applyTitle}
        </h2>
        <p className="muted">{RUN_VIEW.applyBody}</p>
        <code className="dialog__path">{repo}</code>
        <div className="dialog__actions">
          <button type="button" className="btn btn--ghost" onClick={() => dialog.current?.close()}>
            {RUN_VIEW.applyCancel}
          </button>
          <button type="button" className="btn btn--primary" onClick={confirm} disabled={state === "applying"}>
            {state === "applying" ? RUN_VIEW.applying : RUN_VIEW.applyConfirm}
          </button>
        </div>
      </dialog>
    </div>
  );
}

export function Outcome({ result, applyId }: { result: RunRecord; applyId?: string }) {
  const { final } = result;
  const tests = final.final_tests;
  const diff = final.diff ?? "";
  const stopped = result.status === "cancelled" && !result.success;
  const headline = result.success ? RUN_VIEW.outcomeOk : stopped ? RUN_VIEW.outcomeStopped : RUN_VIEW.outcomeBad;
  return (
    <section className="panel outcome" aria-label={RUN_VIEW.diff}>
      <div className="outcome__head">
        <h2 className="outcome__title" data-ok={result.success || undefined}>
          {headline}
        </h2>
        <p className="muted">
          {[
            RUN_STATUS[result.status] ?? result.status,
            tests ? `${tests.passed} of ${tests.total} tests` : null,
            final.diff_stat ? `${final.diff_stat[0]} file(s) +${final.diff_stat[1]} -${final.diff_stat[2]}` : null,
            result.wall_s !== undefined ? `${result.wall_s}s` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      {final.regressions?.length ? (
        <p className="form-error">
          {RUN_VIEW.regressions}: {final.regressions.join(", ")}
        </p>
      ) : null}
      {final.integrity_violations?.length ? (
        <p className="form-error">
          {RUN_VIEW.integrity}: {final.integrity_violations.join(", ")}
        </p>
      ) : null}
      <h3 className="label">{RUN_VIEW.diff}</h3>
      {diff.trim() ? <DiffView diff={diff} /> : <p className="muted">{RUN_VIEW.diffEmpty}</p>}
      {applyId && result.success && diff.trim() ? <ApplyControl runId={applyId} repo={result.repo} /> : null}
    </section>
  );
}
