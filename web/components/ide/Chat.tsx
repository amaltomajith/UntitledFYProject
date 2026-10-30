"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, CheckCircle, Stop, WarningCircle } from "@phosphor-icons/react";
import { IDE } from "@/lib/content";
import type { Item, View } from "@/lib/ide";

type Mode = "build" | "fix";

type Props = {
  view: View;
  canUndo: boolean;
  onSend: (text: string, mode: Mode) => void;
  onCancel: () => void;
  onAnswer: (id: string, approved: boolean) => void;
  onUndo: () => void;
  onReview: () => void;
  onApply: (runId: string) => void;
};

/** Models write `code` and **bold** even when asked for plain text. Nothing else is interpreted. */
function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/).map((part, i) => {
        if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
        return part;
      })}
    </>
  );
}

function Done({ item, last, ...props }: { item: Extract<Item, { kind: "done" }>; last: boolean } & Pick<Props, "canUndo" | "onUndo" | "onReview" | "onApply">) {
  const text = IDE.chat.status[item.status] ?? item.status;
  const failed = item.status !== "done";
  if (item.question) return <p className="ide-done__meta">{IDE.tokens(item.tokens)}</p>;
  return (
    <div className="ide-done" data-failed={failed}>
      <p className="ide-done__line">
        {failed ? <WarningCircle size={15} aria-hidden="true" /> : <CheckCircle size={15} aria-hidden="true" />}
        {text}
        {item.reason && item.status !== "done" ? `: ${item.reason}` : ""}
      </p>
      {item.fixRun ? <p className="ide-done__meta">{IDE.chat.fixReady}</p> : null}
      {!item.fixRun ? <p className="ide-done__meta">{IDE.chat.summary(item.files.length, item.tokens)}</p> : null}
      {item.fixRun ? (
        <div className="ide-done__actions">
          <button type="button" className="btn btn--primary btn--sm" onClick={() => props.onApply(item.fixRun as string)}>
            {IDE.chat.apply}
          </button>
        </div>
      ) : null}
      {last && item.files.length > 0 && props.canUndo ? (
        <div className="ide-done__actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={props.onReview}>
            {IDE.chat.review}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={props.onUndo}>
            {IDE.chat.undo}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Entry({ item, last, ...props }: { item: Item; last: boolean } & Omit<Props, "view" | "onSend" | "onCancel">) {
  switch (item.kind) {
    case "user":
      return <p className="ide-msg ide-msg--user">{item.text}</p>;
    case "reply":
      return (
        <p className="ide-msg">
          <Rich text={item.text} />
        </p>
      );
    case "plan":
      return (
        <section className="ide-plan" aria-label={IDE.chat.plan}>
          <h3>{IDE.chat.plan}</h3>
          <ol>
            {item.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      );
    case "work":
      return (
        <details className="ide-work">
          <summary>{item.label}</summary>
          {item.details.length ? (
            <ul>
              {item.details.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          ) : (
            <p>{IDE.chat.noDetails}</p>
          )}
        </details>
      );
    case "check":
      return (
        <p className="ide-check" data-ok={item.ok}>
          {item.ok ? <CheckCircle size={15} aria-hidden="true" /> : <WarningCircle size={15} aria-hidden="true" />}
          {item.summary}
        </p>
      );
    case "note":
      return (
        <p className="ide-note" data-tone={item.tone}>
          {item.text}
        </p>
      );
    case "approval":
      return (
        <section className="ide-approve" aria-label={IDE.chat.approveTitle}>
          <h3>{IDE.chat.approveTitle}</h3>
          <code>{item.command}</code>
          {item.answer === null ? (
            <div className="ide-approve__actions">
              <button type="button" className="btn btn--primary btn--sm" onClick={() => props.onAnswer(item.id, true)}>
                {IDE.chat.allow}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => props.onAnswer(item.id, false)}>
                {IDE.chat.deny}
              </button>
            </div>
          ) : (
            <p className="ide-approve__result">{item.answer ? IDE.chat.allowed : IDE.chat.denied}</p>
          )}
        </section>
      );
    case "done":
      return <Done item={item} last={last} canUndo={props.canUndo} onUndo={props.onUndo} onReview={props.onReview} onApply={props.onApply} />;
  }
}

export function Chat({ view, onSend, onCancel, ...props }: Props) {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("build");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    end.current?.scrollIntoView({ behavior: motion, block: "end" });
  }, [view.items]);

  const submit = (value: string) => {
    if (!value.trim() || view.busy) return;
    onSend(value.trim(), mode);
    setText("");
  };

  return (
    <aside className="ide-chat" aria-label={IDE.chat.label}>
      <div className="ide-chat__log" role="log" aria-live="polite">
        {view.items.length === 0 ? (
          <div className="ide-chat__empty">
            <h2>{IDE.chat.emptyTitle}</h2>
            <p>{IDE.chat.emptyBody}</p>
            <ul>
              {IDE.chat.examples.map((example) => (
                <li key={example}>
                  <button type="button" onClick={() => setText(example)}>
                    {example}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          view.items.map((item, i) => <Entry key={i} item={item} last={i === view.items.length - 1} {...props} />)
        )}
        {view.busy ? <p className="ide-chat__busy">{IDE.chat.working}</p> : null}
        <div ref={end} />
      </div>
      <form
        className="ide-chat__form"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        <label htmlFor="ide-input" className="sr-only">
          {IDE.chat.inputLabel}
        </label>
        <textarea
          id="ide-input"
          rows={3}
          value={text}
          placeholder={mode === "fix" ? IDE.chat.fixPlaceholder : IDE.chat.placeholder}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(text);
            }
          }}
        />
        <div className="ide-chat__modes" role="group" aria-label={IDE.chat.modeLabel}>
          {(["build", "fix"] as const).map((name) => (
            <button key={name} type="button" aria-pressed={mode === name} title={IDE.chat.modeHints[name]} onClick={() => setMode(name)}>
              {IDE.chat.modes[name]}
            </button>
          ))}
        </div>
        {view.busy ? (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
            <Stop size={14} weight="fill" aria-hidden="true" />
            {IDE.chat.stop}
          </button>
        ) : (
          <button type="submit" className="btn btn--primary btn--sm" disabled={!text.trim()}>
            {IDE.chat.send}
            <ArrowUp size={14} weight="bold" aria-hidden="true" />
          </button>
        )}
      </form>
    </aside>
  );
}
