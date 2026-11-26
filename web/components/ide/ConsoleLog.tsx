"use client";

import { useEffect, useRef, useState } from "react";
import { IDE } from "@/lib/content";
import type { ConsoleLine } from "@/lib/ide";
import { Codicon } from "./Codicon";

const C = IDE.console;
const COPIED_MS = 1400;

/** Every step of the conversation as a numbered log line, written from the engine's own events. */
export function ConsoleLog({ lines, busy }: { lines: ConsoleLine[]; busy: boolean }) {
  const end = useRef<HTMLDivElement>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [lines.length]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(lines.map((line) => line.text).join("\n"));
      setCopied(true);
      window.setTimeout(() => setCopied(false), COPIED_MS);
    } catch {
      // A blocked clipboard: the lines can still be selected by hand.
    }
  };

  if (!lines.length) return <p className="ide-console__empty">{C.empty}</p>;

  return (
    <div className="ide-console">
      <div className="ide-console__bar">
        <span className="ide-label">{lines.length} lines</span>
        <button type="button" onClick={() => void copy()}>
          <Codicon name={copied ? "check" : "copy"} />
          {copied ? C.copied : C.copy}
        </button>
      </div>
      <ol className="ide-console__lines" aria-live="off">
        {lines.map((line, i) => (
          <li key={i} data-tone={line.tone}>
            <span className="ide-console__no" aria-hidden="true">
              {i + 1}
            </span>
            <span className="ide-console__text">{line.text}</span>
          </li>
        ))}
        {busy ? (
          <li data-tone="dim" className="ide-console__cursor" aria-hidden="true">
            <span className="ide-console__no">{lines.length + 1}</span>
            <span className="ide-console__text">_</span>
          </li>
        ) : null}
      </ol>
      <div ref={end} />
    </div>
  );
}
