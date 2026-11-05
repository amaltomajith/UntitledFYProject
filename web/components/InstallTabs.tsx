"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Command } from "@/components/Command";
import { INSTALL } from "@/lib/content";

type TabId = (typeof INSTALL.tabs)[number]["id"];

/** Windows gets cmd (it works in both shells); everything else gets the curl line. */
function guess(): TabId {
  const ua = typeof navigator === "undefined" ? "" : navigator.userAgent;
  return /Windows/i.test(ua) ? "cmd" : "unix";
}

export function InstallTabs({ source = false }: { source?: boolean }) {
  const [tab, setTab] = useState<TabId>("unix");
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const base = useId();

  useEffect(() => setTab(guess()), []);

  const index = INSTALL.tabs.findIndex((t) => t.id === tab);
  const current = INSTALL.tabs[index];

  const onKey = (e: KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (index + step + INSTALL.tabs.length) % INSTALL.tabs.length;
    setTab(INSTALL.tabs[next].id);
    buttons.current[next]?.focus();
  };

  return (
    <div className="install">
      <div className="install__tabs" role="tablist" aria-label={INSTALL.label} onKeyDown={onKey}>
        {INSTALL.tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              buttons.current[i] = el;
            }}
            id={`${base}-${t.id}`}
            type="button"
            role="tab"
            aria-selected={t.id === tab}
            aria-controls={`${base}-panel`}
            tabIndex={t.id === tab ? 0 : -1}
            className="install__tab"
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div id={`${base}-panel`} role="tabpanel" aria-labelledby={`${base}-${tab}`}>
        <ol className="cmds cmds--tight install__cmds">
          <Command label={current.label} cmd={current.cmd} prompt={current.prompt} />
        </ol>
        <p className="install__then">
          {INSTALL.then} <code>{INSTALL.launch}</code>. {INSTALL.thenAfter}
        </p>
        <p className="install__note muted">{INSTALL.note}</p>
        <p className="install__note muted">{INSTALL.uninstall}</p>
      </div>
      {source ? (
        <details className="install__source">
          <summary>{INSTALL.source.title}</summary>
          <ol className="cmds cmds--tight">
            {INSTALL.source.commands.map((c) => (
              <Command key={c.label} {...c} />
            ))}
          </ol>
        </details>
      ) : null}
    </div>
  );
}
