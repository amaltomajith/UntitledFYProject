"use client";

import { useEffect, useMemo, useState } from "react";
import { Pause, Play } from "@phosphor-icons/react";
import { RUN_VIEW } from "@/lib/content";
import { eventsFromRun, viewAt, type RunRecord } from "@/lib/runEvents";
import { Outcome } from "./Outcome";
import { RunBoard } from "./RunBoard";

/** Step through a saved run. `autoplay` starts from the beginning, otherwise it opens on the end. */
export function RunPlayer({ run, autoplay, applyId }: { run: RunRecord; autoplay?: boolean; applyId?: string }) {
  const total = run.steps.length;
  const events = useMemo(() => eventsFromRun(run), [run]);
  const [at, setAt] = useState(total);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (autoplay && window.matchMedia("(prefers-reduced-motion: no-preference)").matches) {
      setAt(0);
      setPlaying(true);
    }
  }, [autoplay]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setAt((n) => {
        if (n + 1 >= total) setPlaying(false);
        return Math.min(n + 1, total);
      });
    }, 800);
    return () => window.clearInterval(timer);
  }, [playing, total]);

  const toggle = () => {
    if (!playing && at >= total) setAt(0);
    setPlaying(!playing);
  };

  // Events are start, then select + step per step, then end.
  const view = viewAt(events, at >= total ? events.length : 1 + at * 2);

  return (
    <>
      <div className="player">
        <button type="button" className="btn btn--ghost btn--sm" onClick={toggle}>
          {playing ? <Pause size={14} weight="bold" aria-hidden="true" /> : <Play size={14} weight="bold" aria-hidden="true" />}
          {playing ? RUN_VIEW.pause : RUN_VIEW.play}
        </button>
        <input
          type="range"
          className="player__slider"
          min={0}
          max={total}
          value={at}
          aria-label={RUN_VIEW.stepSlider}
          aria-valuetext={RUN_VIEW.stepOf(at, total)}
          onChange={(e) => {
            setPlaying(false);
            setAt(Number(e.target.value));
          }}
        />
        <span className="player__pos">{RUN_VIEW.stepOf(at, total)}</span>
      </div>
      <RunBoard view={view} />
      {view.result ? <Outcome result={view.result} applyId={applyId} /> : null}
    </>
  );
}
