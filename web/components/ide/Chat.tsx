"use client";

import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowUp, Stop } from "@phosphor-icons/react";
import { IDE, type SlashCommand } from "@/lib/content";
import { when, type Item, type SavedChat, type View } from "@/lib/ide";
import { looksLikeCommand, SlashMenu, usePrompt } from "./Slash";

type Mode = "build" | "fix";

type Props = {
  active: boolean;                 // a hidden folder's chat leaves the keyboard alone
  header: ReactNode;
  status: ReactNode;
  jump: { turn: number } | null;
  onJumped: () => void;
  view: View;
  model: string;
  canUndo: boolean;
  claim: string | null;
  canApprove: boolean;
  onApprove: () => void;
  onSend: (text: string, mode: Mode) => void;
  onSlash: (line: string) => void;
  onShell: (command: string) => void;
  onCancel: () => void;
  onAnswer: (id: string, approved: boolean) => void;
  onUndo: () => void;
  onReview: () => void;
  onApply: (runId: string) => void;
  onResume: (id: string) => void;
};

const FRAMES = ["|", "/", "-", "\\"];
const STARTERS = IDE.chat.starters.map((name) => IDE.commands.list.find((c) => c.name === name)!);

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

const prettyModel = (id: string) => id.split("/").pop() ?? id;

function Line({ glyph, tone, children }: { glyph: string; tone?: string; children: ReactNode }) {
  return (
    <div className="ide-line">
      <span className="ide-g" data-tone={tone} aria-hidden="true">
        {glyph}
      </span>
      <div className="ide-line__body">{children}</div>
    </div>
  );
}

function Done({ item, last, ...props }: { item: Extract<Item, { kind: "done" }>; last: boolean } & Pick<Props, "canUndo" | "onUndo" | "onReview" | "onApply" | "canApprove" | "onApprove">) {
  const text = IDE.chat.status[item.status] ?? item.status;
  const failed = item.status !== "done" && item.status !== "planned";
  if (item.question) return <p className="ide-done__meta">{IDE.tokens(item.tokens)}</p>;
  return (
    <div className="ide-done">
      <Line glyph={failed ? "✗" : "✓"} tone={failed ? "bad" : "good"}>
        <p className="ide-done__line">
          {text}
          {item.reason && item.status !== "done" && !item.reason.startsWith(text) ? `: ${item.reason}` : ""}
        </p>
        {item.fixRun ? <p className="ide-done__meta">{IDE.chat.fixReady}</p> : null}
        {item.status === "planned" && last && props.canApprove ? (
          <>
            <p className="ide-done__meta">{IDE.chat.approveHint}</p>
            <div className="ide-done__actions">
              <button type="button" className="btn btn--primary btn--sm" onClick={props.onApprove}>
                {IDE.chat.approve}
              </button>
            </div>
          </>
        ) : null}
        {!item.fixRun && item.status !== "planned" ? (
          <p className="ide-done__meta">{IDE.chat.summary(item.files.length, item.tokens)}</p>
        ) : null}
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
      </Line>
    </div>
  );
}

function Sessions({ rows, onResume }: { rows: SavedChat[]; onResume: (id: string) => void }) {
  return (
    <section className="ide-sessions" aria-label={IDE.commands.pickResume}>
      <p>{IDE.commands.pickResume}</p>
      <ol>
        {rows.map((row) => (
          <li key={row.id}>
            <button type="button" onClick={() => onResume(row.id)}>
              <span>{when(row.saved)}</span>
              <span>{row.title}</span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

function HelpRows({ title, rows }: { title: string; rows: [ReactNode, string][] }) {
  return (
    <>
      <h3>{title}</h3>
      <dl>
        {rows.map(([name, hint], i) => (
          <Fragment key={i}>
            <dt>{name}</dt>
            <dd>{hint}</dd>
          </Fragment>
        ))}
      </dl>
    </>
  );
}

function Help() {
  const { list, tips, helpHeads } = IDE.commands;
  const row = (c: SlashCommand): [ReactNode, string] => [
    <>
      /{c.name}
      {c.args ? <span> {c.args}</span> : null}
    </>,
    c.hint,
  ];
  return (
    <section className="ide-help" aria-label={IDE.commands.label}>
      <HelpRows title={helpHeads.actions} rows={list.filter((c) => !c.prompt).map(row)} />
      <HelpRows title={helpHeads.prompts} rows={list.filter((c) => c.prompt).map(row)} />
      <HelpRows title={helpHeads.tips} rows={tips.map(([key, text]) => [key, text])} />
    </section>
  );
}

type EntryProps = Omit<Props, "active" | "header" | "status" | "jump" | "onJumped" | "view" | "model" | "onSend" | "onSlash" | "onShell" | "onCancel">;

function Entry({ item, last, running, ...props }: { item: Item; last: boolean; running: boolean } & EntryProps) {
  switch (item.kind) {
    case "user":
      return (
        <div className="ide-user" data-turn={item.turn ?? undefined}>
          <p>{item.text}</p>
        </div>
      );
    case "reply":
      return (
        <p className="ide-msg">
          <Rich text={item.text} />
        </p>
      );
    case "plan":
      return (
        <section className="ide-plan-box" aria-label={IDE.chat.plan}>
          <h3 className="ide-plan__title">{IDE.chat.plan}</h3>
          <ol className="ide-plan">
            {item.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </section>
      );
    case "work":
      return (
        <details className="ide-work">
          <summary>
            <span className="ide-g" data-tone={running ? "live" : "dim"} aria-hidden="true">
              ●
            </span>
            {item.label}
          </summary>
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
        <Line glyph={item.ok ? "✓" : "✗"} tone={item.ok ? "good" : "bad"}>
          <p className="ide-check">{item.summary}</p>
        </Line>
      );
    case "note":
      return (
        <Line glyph={item.tone === "info" ? "·" : "!"} tone={item.tone === "bad" ? "bad" : item.tone === "warn" ? "live" : "dim"}>
          <p className="ide-note" data-tone={item.tone}>
            {item.text}
          </p>
        </Line>
      );
    case "output":
      return <pre className="ide-output">{item.text}</pre>;
    case "sessions":
      return <Sessions rows={item.rows} onResume={props.onResume} />;
    case "help":
      return <Help />;
    case "approval":
      return (
        <section className="ide-approve" aria-label={IDE.chat.approveTitle}>
          <h3>{IDE.chat.approveTitle}</h3>
          <code>{item.command}</code>
          {item.reason ? <p className="ide-approve__reason">{item.reason}</p> : null}
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
      return (
        <Done
          item={item}
          last={last}
          canUndo={props.canUndo}
          onUndo={props.onUndo}
          onReview={props.onReview}
          onApply={props.onApply}
          canApprove={props.canApprove}
          onApprove={props.onApprove}
        />
      );
  }
}

function Working({ label }: { label: string }) {
  const start = useRef(Date.now());
  const [tick, setTick] = useState(0);
  const [still, setStill] = useState(false);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setStill(reduced);
    const timer = window.setInterval(() => setTick((n) => n + 1), reduced ? 1000 : 120);
    return () => window.clearInterval(timer);
  }, []);

  const seconds = Math.floor((Date.now() - start.current) / 1000);
  return (
    <p className="ide-working">
      <span className="ide-g" data-tone="live" aria-hidden="true">
        {still ? "*" : FRAMES[tick % FRAMES.length]}
      </span>
      {label}
      <span aria-hidden="true" className="ide-working__meta">
        {" "}
        ({seconds}s, {IDE.chat.stopHint})
      </span>
    </p>
  );
}

function Welcome({ onPick }: { onPick: (name: string) => void }) {
  return (
    <div className="ide-welcome">
      <h2>{IDE.chat.emptyTitle}</h2>
      <p>{IDE.chat.emptyBody}</p>
      <ul>
        {STARTERS.map((command) => (
          <li key={command.name}>
            <button type="button" onClick={() => onPick(command.name)}>
              <span>/{command.name}</span>
              <span>{command.hint}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

const typingSomewhere = () => {
  const el = document.activeElement as HTMLElement | null;
  return !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
};

// Esc belongs to whatever runs in a terminal, such as vim.
const inTerminal = () => !!document.activeElement?.closest(".xterm");

export function Chat({ active, header, status, jump, onJumped, view, model, onSend, onSlash, onShell, onCancel, ...props }: Props) {
  const [text, setText] = useState("");
  const inputId = useId();
  const [mode, setMode] = useState<Mode>("build");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLDivElement>(null);

  const box = usePrompt(inputId, text, setText, (line) => {
    if (looksLikeCommand(line)) onSlash(line);
    else if (line.startsWith("!") && line.length > 1) onShell(line.slice(1).trim());
    else if (view.busy) return false;
    else onSend(line, mode);
  });

  useEffect(() => {
    if (jump) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    end.current?.scrollIntoView({ behavior: motion, block: "end" });
  }, [view.items, view.busy, jump]);

  useEffect(() => {
    if (!jump) return;
    const turn = log.current?.querySelector<HTMLElement>(`[data-turn="${jump.turn}"]`);
    if (!turn) return;      // the session is still loading: the next feed brings it here
    turn.scrollIntoView({ block: "start" });
    turn.dataset.jumped = "true";
    const timer = window.setTimeout(() => delete turn.dataset.jumped, 1600);
    onJumped();
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jump, view.items]);

  useEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    if (text) input.current.style.height = `${input.current.scrollHeight}px`;
  }, [text]);

  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.key === "Escape" && view.busy && !inTerminal()) onCancel();
      if (e.key === "/" && !typingSomewhere()) {
        e.preventDefault();
        input.current?.focus();
        setText("/");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, view.busy, onCancel]);

  const label = [...view.items].reverse().find((item) => item.kind === "work");

  return (
    <aside className="ide-chat" aria-label={IDE.chat.label}>
      <div className="ide-chat__head">
        {header}
        {status}
        {props.claim ? <p className="ide-chat__claim">{props.claim}</p> : null}
      </div>
      <div ref={log} className="ide-chat__log" role="log" aria-live="polite">
        {view.items.length === 0 ? (
          <Welcome
            onPick={(name) => {
              setText(`/${name} `);
              input.current?.focus();
            }}
          />
        ) : (
          view.items.map((item, i) => (
            <Entry
              key={i}
              item={item}
              last={i === view.items.length - 1}
              running={view.busy && i === view.items.length - 1}
              {...props}
            />
          ))
        )}
        {view.busy ? <Working label={label?.kind === "work" ? label.label : IDE.chat.working} /> : null}
        <div ref={end} />
      </div>
      <form
        className="ide-chat__form"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) box.run(text.trim());
        }}
      >
        <SlashMenu id={inputId} items={box.items} index={box.index} onPick={box.pick} />
        <div className="ide-prompt">
          <label htmlFor={inputId} className="sr-only">
            {IDE.chat.inputLabel}
          </label>
          <textarea
            ref={input}
            id={inputId}
            rows={1}
            value={text}
            spellCheck={false}
            placeholder={mode === "fix" ? IDE.chat.fixPlaceholder : IDE.chat.placeholder}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={box.onKeyDown}
            {...box.combo}
          />
          <div className="ide-prompt__foot">
            <div className="ide-chat__modes" role="group" aria-label={IDE.chat.modeLabel}>
              {(["build", "fix"] as const).map((name) => (
                <button key={name} type="button" aria-pressed={mode === name} title={IDE.chat.modeHints[name]} onClick={() => setMode(name)}>
                  {IDE.chat.modes[name]}
                </button>
              ))}
            </div>
            <span className="ide-prompt__model" title={`${IDE.chat.modelNow}: ${model}`}>
              {prettyModel(model)}
            </span>
            {view.busy ? (
              <button type="button" className="ide-prompt__send" aria-label={IDE.chat.stop} title={IDE.chat.stop} onClick={onCancel}>
                <Stop size={14} weight="fill" aria-hidden="true" />
              </button>
            ) : (
              <button type="submit" className="ide-prompt__send" aria-label={IDE.chat.send} title={IDE.chat.send} disabled={!text.trim()}>
                <ArrowUp size={14} weight="bold" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </form>
    </aside>
  );
}
