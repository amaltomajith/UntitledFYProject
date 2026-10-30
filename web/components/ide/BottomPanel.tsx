"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle, WarningCircle } from "@phosphor-icons/react";
import { IDE } from "@/lib/content";
import type { View } from "@/lib/ide";

type Props = {
  view: View;
  error: string | null;
  onCommand: (command: string) => void;
};

export function BottomPanel({ view, error, onCommand }: Props) {
  const [tab, setTab] = useState<"terminal" | "checks">("terminal");
  const [command, setCommand] = useState("");
  const log = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [view.terminal, tab]);

  return (
    <section className="ide-bottom" aria-label={IDE.bottom.label}>
      <div className="ide-tabs" role="tablist">
        {(["terminal", "checks"] as const).map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className="ide-tab"
            onClick={() => setTab(name)}
          >
            {IDE.bottom[name]}
          </button>
        ))}
      </div>
      {tab === "terminal" ? (
        <div className="ide-term">
          <pre ref={log} tabIndex={0} aria-label={IDE.bottom.output}>
            {view.terminal.join("") || IDE.bottom.empty}
          </pre>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!command.trim() || view.busy) return;
              onCommand(command.trim());
              setCommand("");
            }}
          >
            <span aria-hidden="true">$</span>
            <label htmlFor="ide-command" className="sr-only">
              {IDE.bottom.commandLabel}
            </label>
            <input
              id="ide-command"
              value={command}
              spellCheck={false}
              autoComplete="off"
              disabled={view.busy}
              placeholder={view.busy ? IDE.bottom.busy : IDE.bottom.placeholder}
              onChange={(e) => setCommand(e.target.value)}
            />
          </form>
          {error ? (
            <p className="ide-term__error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
      ) : (
        <ul className="ide-checks">
          {view.checks.length === 0 ? <li className="muted">{IDE.bottom.noChecks}</li> : null}
          {view.checks.map((check, i) => (
            <li key={i} data-ok={check.ok}>
              {check.ok ? <CheckCircle size={14} aria-hidden="true" /> : <WarningCircle size={14} aria-hidden="true" />}
              {check.summary}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
