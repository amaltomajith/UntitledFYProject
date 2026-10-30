"use client";

import { useState } from "react";
import { ArrowUpRight, Check, Copy } from "@phosphor-icons/react";
import { CTA_LABEL, GITHUB_URL, START } from "@/lib/content";

type CopyState = "idle" | "copied" | "failed";

function Command({ label, cmd }: { label: string; cmd: string }) {
  const [state, setState] = useState<CopyState>("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      setState("copied");
    } catch {
      setState("failed");
    }
    window.setTimeout(() => setState("idle"), 1800);
  };

  return (
    <li className="cmd">
      <span className="cmd__label">{label}</span>
      <code className="cmd__code">
        <span className="cmd__prompt" aria-hidden="true">
          $
        </span>
        {cmd}
      </code>
      <button type="button" className="cmd__copy" onClick={copy} aria-label={`Copy the ${label.toLowerCase()} command`}>
        {state === "copied" ? <Check size={16} weight="bold" /> : <Copy size={16} />}
      </button>
      <span className="sr-only" aria-live="polite">
        {state === "copied" ? "Copied" : state === "failed" ? "Copy failed, select the command and copy it manually" : ""}
      </span>
      {state === "failed" ? <span className="cmd__error">Copy blocked. Select the text instead.</span> : null}
    </li>
  );
}

export function GetStarted() {
  return (
    <section id="start" data-chapter="5" className="start">
      <div className="container start__inner">
        <div className="start__grid">
          <div>
            <h2 className="display-3">{START.title}</h2>
            <p className="lead">{START.note}</p>

            <ol className="cmds">
              {START.commands.map((c) => (
                <Command key={c.label} {...c} />
              ))}
            </ol>
          </div>

          <div className="results">
            <h3 className="results__title">{START.results.title}</h3>
            <p className="results__body">{START.results.body}</p>
            <dl className="metrics">
              {START.results.stats.map((s) => (
                <div key={s.label} className="metric">
                  <dt>{s.label}</dt>
                  <dd>{s.value}</dd>
                </div>
              ))}
            </dl>
            <p className="results__caveat">{START.results.caveat}</p>
          </div>
        </div>

        <div className="closing">
          <p className="display-2 closing__title">{START.closing}</p>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="btn btn--primary">
            {CTA_LABEL}
            <ArrowUpRight size={16} weight="bold" aria-hidden="true" />
          </a>
        </div>
      </div>
    </section>
  );
}
