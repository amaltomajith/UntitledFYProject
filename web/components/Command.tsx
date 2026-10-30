"use client";

import { useState } from "react";
import { Check, Copy } from "@phosphor-icons/react";

type CopyState = "idle" | "copied" | "failed";

export function Command({ label, cmd }: { label: string; cmd: string }) {
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
