"use client";

import { useState } from "react";
import { IDE } from "@/lib/content";
import type { Item, Live } from "@/lib/ide";
import { Codicon } from "./Codicon";

const P = IDE.pill;

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

type Props = {
  live: Live;
  busy: boolean;
  done: Extract<Item, { kind: "done" }> | null;
  canUndo: boolean;
  onStop: () => void;
  onLive: () => void;
  onReview: () => void;
  onUndo: () => void;
};

/** Floats over the editor: what runs now, then what changed until it is dismissed. Only real state. */
export function RunPill({ live, busy, done, canUndo, onStop, onLive, onReview, onUndo }: Props) {
  const [dismissed, setDismissed] = useState(0);

  if (busy && live.running) {
    const phase = live.asking ? P.needs : IDE.live.phase(live.label, live.phase, live.path).replace(`${live.label}: `, "");
    return (
      <div className="ide-pill" role="status" aria-label={P.label} data-state={live.asking ? "needs" : "working"}>
        <span className="ide-pill__dot" aria-hidden="true" />
        <span className="ide-pill__text">{P.working(live.label || IDE.chat.working, phase, clock(live.seconds))}</span>
        <button type="button" onClick={onLive}>
          <Codicon name="pulse" />
          {P.live}
        </button>
        <button type="button" onClick={onStop}>
          <Codicon name="debug-stop" />
          {P.stop}
        </button>
      </div>
    );
  }

  if (!done || done.question || !done.files.length || dismissed === live.id) return null;
  const counts = Object.values(done.counts ?? {});
  const added = counts.reduce((n, c) => n + c.added, 0);
  const removed = counts.reduce((n, c) => n + c.removed, 0);
  return (
    <div className="ide-pill" role="status" aria-label={P.label} data-state={done.status === "done" ? "done" : "failed"}>
      <Codicon name={done.status === "done" ? "pass" : "warning"} />
      <span className="ide-pill__text">
        {P.changed(done.files.length)}
        {counts.length ? <span className="ide-pill__counts">{IDE.live.counts(added, removed)}</span> : null}
      </span>
      <button type="button" onClick={onReview}>
        {P.review}
      </button>
      {canUndo ? (
        <button type="button" onClick={onUndo}>
          {P.undo}
        </button>
      ) : null}
      <button type="button" aria-label={P.dismiss} title={P.dismiss} onClick={() => setDismissed(live.id)}>
        <Codicon name="close" />
      </button>
    </div>
  );
}
