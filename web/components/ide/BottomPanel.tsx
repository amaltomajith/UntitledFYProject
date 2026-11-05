"use client";

import { useEffect, useRef } from "react";
import { IDE } from "@/lib/content";
import type { View } from "@/lib/ide";
import { Codicon } from "./Codicon";
import { TerminalActions, TerminalArea } from "./terminal/TerminalPanel";
import type { Terminals } from "./terminal/useTerminals";

export type BottomTab = "terminal" | "output";

type Props = {
  view: View;
  terminals: Terminals;
  tab: BottomTab;
  onTab: (tab: BottomTab) => void;
  error: string | null;
  prompt: string;
  maximised: boolean;
  onMaximise: () => void;
  onClose: () => void;
};

function Output({ view, error, prompt }: Pick<Props, "view" | "error" | "prompt">) {
  const log = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [view.terminal]);

  return (
    <div className="ide-term">
      <pre ref={log} tabIndex={0} aria-label={IDE.bottom.outputLabel}>
        {view.terminal.length ? null : <span data-kind="sys">{IDE.bottom.noOutput}</span>}
        {view.terminal.map((line, i) =>
          line.kind === "cmd" ? (
            <span key={i}>
              <span data-kind="prompt">{prompt}</span> {line.text}
            </span>
          ) : (
            <span key={i} data-kind={line.kind}>
              {line.text}
            </span>
          ),
        )}
      </pre>
      {error ? (
        <p className="ide-term__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function BottomPanel({ view, terminals, tab, onTab, error, prompt, maximised, onMaximise, onClose }: Props) {
  return (
    <section className="ide-bottom" aria-label={IDE.bottom.label}>
      <div className="ide-bottom__bar">
        <div role="tablist" className="ide-bottom__tabs">
          {(["terminal", "output"] as const).map((name) => (
            <button key={name} type="button" role="tab" aria-selected={tab === name} onClick={() => onTab(name)}>
              {IDE.bottom[name]}
            </button>
          ))}
        </div>
        <span className="ide__spacer" />
        {tab === "terminal" ? <TerminalActions t={terminals} /> : null}
        <button
          type="button"
          className="ide-bottom__tool"
          aria-label={maximised ? IDE.bottom.restore : IDE.bottom.maximise}
          title={maximised ? IDE.bottom.restore : IDE.bottom.maximise}
          onClick={onMaximise}
        >
          <Codicon name={maximised ? "chevron-down" : "chevron-up"} />
        </button>
        <button type="button" className="ide-bottom__tool" aria-label={IDE.bottom.close} title={IDE.bottom.close} onClick={onClose}>
          <Codicon name="close" />
        </button>
      </div>
      {tab === "terminal" ? <TerminalArea t={terminals} /> : <Output view={view} error={error} prompt={prompt} />}
    </section>
  );
}
