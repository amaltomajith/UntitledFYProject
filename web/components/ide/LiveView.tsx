"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { IDE } from "@/lib/content";
import type { Live, LiveEdit, LiveRow } from "@/lib/ide";
import { Codicon } from "./Codicon";
import { FileIcon } from "./FileIcon";

const COPY = IDE.live;
const elapsed = (seconds: number) => (seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`);
const QUIET_NOTE_SECONDS = 15;
const GENERATING = ["thinking", "writing"];

/** Keeps a scroller at its end while its end is in view, so reading further up is left alone. */
function useFollow(change: unknown) {
  const box = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);

  useEffect(() => {
    if (!box.current || !end.current) return;
    const watcher = new IntersectionObserver(([entry]) => (atEnd.current = entry.isIntersecting), { root: box.current });
    watcher.observe(end.current);
    return () => watcher.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (atEnd.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [change]);

  return { box, end };
}

function rowDuration(row: LiveRow, next: LiveRow | undefined, live: Live) {
  if (row.kind !== "agent") return null;
  const until = next?.kind === "agent" ? next.since : live.seconds;
  return until > row.since ? elapsed(until - row.since) : null;
}

function Timeline({ live }: { live: Live }) {
  const steps = live.rows.filter((row) => row.kind === "agent");
  return (
    <ol className="ide-live__rows" aria-label={COPY.timeline}>
      {live.rows.map((row, i) => {
        if (row.kind === "agent") {
          const running = live.running && steps[steps.length - 1] === row;
          const next = live.rows.slice(i + 1).find((other) => other.kind === "agent");
          return (
            <li key={i} className="ide-live__row" data-running={running}>
              <Codicon name={running ? "circle-large" : "check"} />
              <div>
                <span className="ide-live__agent">{row.label}</span>
                <span className="ide-live__state">
                  {running ? COPY.working : COPY.finished}
                  {rowDuration(row, next, live) ? `, ${rowDuration(row, next, live)}` : ""}
                </span>
                {row.thinking ? (
                  <details className="ide-live__thinking" open={running}>
                    <summary>{COPY.thinking}</summary>
                    <p>{row.thinking}</p>
                  </details>
                ) : null}
              </div>
            </li>
          );
        }
        return (
          <li key={i} className="ide-live__row" data-tone={row.tone}>
            <Codicon name={row.tone === "good" ? "pass" : row.tone === "bad" ? "error" : "info"} />
            <span className="ide-live__state">{row.text}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Hunks({ edit, onOpen }: { edit: LiveEdit; onOpen: (path: string, line: number, end: number) => void }) {
  return (
    <section className="ide-live__edit" aria-label={edit.path}>
      <h3>
        <FileIcon name={edit.path.split("/").pop() ?? edit.path} />
        <span>{edit.path}</span>
        <span className="ide-live__count">{COPY.counts(edit.added, edit.removed)}</span>
      </h3>
      {edit.hunks.map((hunk, i) => {
        const lines = hunk.lines.map((line, n) => (
          <span key={n} data-sign={line[0]}>
            {line}
          </span>
        ));
        return edit.action === "deleted" ? (
          <pre key={i}>{lines}</pre>
        ) : (
          <button key={i} type="button" title={COPY.open(hunk.line)} onClick={() => onOpen(edit.path, hunk.line, hunk.end)}>
            <pre>{lines}</pre>
          </button>
        );
      })}
      {edit.more ? <p className="ide-live__more">{COPY.more}</p> : null}
    </section>
  );
}

function Headline({ live }: { live: Live }) {
  if (live.asking) return <>{COPY.asking}</>;
  if (live.running) return <>{COPY.phase(live.label, live.phase, live.path)}</>;
  if (live.result) return <>{COPY.result(live.result.status)}</>;
  return <>{COPY.empty}</>;
}

export function LiveView({ live, onOpen }: { live: Live; onOpen: (path: string, line: number, end: number) => void }) {
  const [only, setOnly] = useState<string | null>(null);
  const { box, end } = useFollow(`${live.code?.text.length}:${live.edits.length}`);
  const files = new Map<string, { added: number; removed: number }>();
  for (const edit of live.edits) {
    const seen = files.get(edit.path) ?? { added: 0, removed: 0 };
    files.set(edit.path, { added: seen.added + edit.added, removed: seen.removed + edit.removed });
  }
  const edits = only ? live.edits.filter((edit) => edit.path === only) : live.edits;
  const quiet = live.running && !live.asking && live.silent >= QUIET_NOTE_SECONDS;
  const waitingForCode = live.coding && live.running && !live.asking && !live.code && (live.phase === "writing" || (live.phase === "waiting" && quiet));

  useEffect(() => setOnly(null), [live.id]);

  if (!live.id) return <div className="ide-pane__note">{COPY.empty}</div>;
  return (
    <div className="ide-live">
      <header className="ide-live__head">
        <h2 role="status">
          <Headline live={live} />
        </h2>
        <p>
          {[
            live.seconds ? elapsed(live.seconds) : null,
            live.maxSteps ? COPY.step(live.step, live.maxSteps) : null,
            live.running && !live.asking && live.rate && GENERATING.includes(live.phase) ? COPY.rate(live.rate) : null,
          ]
            .filter(Boolean)
            .join(", ")}
        </p>
        {quiet ? <p>{COPY.quiet(elapsed(live.silent))}</p> : null}
        {live.result?.reason && live.result.reason.replace(".", "") !== COPY.result(live.result.status) ? <p>{live.result.reason}</p> : null}
      </header>
      <div className="ide-live__body">
        <Timeline live={live} />
        <div className="ide-live__code">
          {files.size ? (
            <div className="ide-live__files" role="group" aria-label={COPY.files}>
              {[...files].map(([path, count]) => (
                <button key={path} type="button" aria-pressed={only === path} onClick={() => setOnly(only === path ? null : path)}>
                  {path.split("/").pop()} <span>{COPY.counts(count.added, count.removed)}</span>
                </button>
              ))}
            </div>
          ) : null}
          <div className="ide-live__scroll" ref={box} tabIndex={0} aria-label={COPY.code}>
            {edits.map((edit, i) => (
              <Hunks key={i} edit={edit} onOpen={onOpen} />
            ))}
            {live.code ? (
              <section className="ide-live__edit" aria-label={live.code.path}>
                <h3>
                  <FileIcon name={live.code.path.split("/").pop() ?? live.code.path} />
                  <span>{live.code.path}</span>
                  <span className="ide-live__count">{COPY.arriving}</span>
                </h3>
                <pre>{live.code.text}</pre>
              </section>
            ) : null}
            {waitingForCode ? <p className="ide-live__wait">{COPY.noStream}</p> : null}
            {!edits.length && !live.code && !waitingForCode ? <p className="ide-live__wait">{COPY.noEdits}</p> : null}
            <div ref={end} aria-hidden="true" className="ide-live__end" />
          </div>
        </div>
      </div>
    </div>
  );
}

/** One line under the session bar. On a phone, where the editor is hidden, this line is the view. */
export function LiveLine({ live, onOpen }: { live: Live; onOpen: () => void }) {
  if (!live.running) return null;
  return (
    <button type="button" className="ide-live-line" title={COPY.openTab} onClick={onOpen}>
      <span role="status">{live.asking ? COPY.asking : COPY.phase(live.label, live.phase, live.path)}</span>
      <span>{live.seconds ? elapsed(live.seconds) : null}</span>
    </button>
  );
}
