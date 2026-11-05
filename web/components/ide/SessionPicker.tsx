"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, when, type IdeState, type SavedChat, type IdeClient } from "@/lib/ide";
import { Codicon } from "./Codicon";

const S = IDE.sessions;

type Props = {
  client: IdeClient;
  shown: boolean;                  // a hidden folder's drawer leaves Esc alone
  state: IdeState;
  busy: boolean;
  onState: (state: IdeState) => void;
  onResume: (id: string) => Promise<void>;
  onNew: () => Promise<void>;
  onGraph: () => void;
};

const failure = (e: unknown) => (e instanceof IdeError ? e.message : S.failed);

export function SessionPicker({ client, shown, state, busy, onState, onResume, onNew, onGraph }: Props) {
  const [open, setOpen] = useState(false);
  const drawerId = useId();
  const [rows, setRows] = useState<SavedChat[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const title = state.session_title || S.untitled;
  const active = state.session_id;

  const load = useCallback(async () => {
    try {
      setRows((await ideCall<{ sessions: SavedChat[] }>(client, "sessions")).sessions);
    } catch (e) {
      setRows([]);
      setError(failure(e));
    }
  }, [client]);

  useEffect(() => {
    if (open) void load();
  }, [open, load, active, state.private.on, busy]);

  const close = useCallback(() => {
    setOpen(false);
    setRenaming(null);
    setDeleting(null);
    toggle.current?.focus();
  }, []);

  // Capture, so Esc closes this first and does not also stop a running request.
  useEffect(() => {
    if (!open || !shown) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || (e.target as HTMLElement).closest(".ide-drawer__rename")) return;
      e.preventDefault();
      close();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, shown, close]);

  const act = async (work: () => Promise<void>) => {
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(failure(e));
      setOpen(true);
    }
  };

  const rename = (id: string) =>
    act(async () => {
      if (draft.trim()) onState(await post<IdeState>(client, `sessions/${encodeURIComponent(id)}/rename`, { name: draft }));
      setRenaming(null);
      await load();
    });

  const remove = (id: string) =>
    act(async () => {
      onState(await ideCall<IdeState>(client, `sessions/${encodeURIComponent(id)}`, { method: "DELETE" }));
      setDeleting(null);
      await load();
    });

  const pick = (id: string) =>
    act(async () => {
      if (id !== active) await onResume(id);
      close();
    });

  const saved = rows?.some((row) => row.id === active);

  return (
    <>
      <div className="ide-session-bar">
        <h2 title={title}>{title}</h2>
        <button type="button" aria-label={IDE.graph.open} title={IDE.graph.open} onClick={onGraph}>
          <Codicon name="type-hierarchy" />
        </button>
        <button
          ref={toggle}
          type="button"
          aria-expanded={open}
          aria-controls={drawerId}
          aria-label={open ? S.close : S.open}
          title={open ? S.close : S.open}
          onClick={() => (open ? close() : setOpen(true))}
        >
          <Codicon name="history" />
        </button>
        <button type="button" aria-label={S.create} title={busy ? S.busy : S.create} disabled={busy} onClick={() => void act(onNew)}>
          <Codicon name="add" />
        </button>
      </div>
      {open ? (
        <section id={drawerId} className="ide-drawer" aria-label={S.label}>
          {busy ? <p className="ide-drawer__note">{S.busy}</p> : null}
          {error ? (
            <p className="ide-drawer__note" data-tone="bad" role="alert">
              {error}
            </p>
          ) : null}
          {rows === null ? <p className="ide-drawer__note">{S.loading}</p> : null}
          {rows?.length === 0 && !error ? <p className="ide-drawer__note">{S.empty}</p> : null}
          {rows?.length ? (
            <ol>
              {!saved ? (
                <li className="ide-drawer__row" data-active="true">
                  <span className="ide-drawer__open">
                    <span className="ide-drawer__title">{S.untitled}</span>
                    <span className="ide-drawer__meta">{S.unsaved}</span>
                  </span>
                </li>
              ) : null}
              {rows.map((row) => (
                <li key={row.id} className="ide-drawer__row" data-active={row.id === active}>
                  {deleting === row.id ? (
                    <div className="ide-drawer__confirm">
                      <p>{S.confirm}</p>
                      <div>
                        <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => void remove(row.id)}>
                          {S.confirmYes}
                        </button>
                        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDeleting(null)}>
                          {S.confirmNo}
                        </button>
                      </div>
                    </div>
                  ) : renaming === row.id ? (
                    <input
                      className="ide-drawer__rename"
                      aria-label={S.renameLabel}
                      value={draft}
                      maxLength={60}
                      autoFocus
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={() => setRenaming(null)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void rename(row.id);
                        if (e.key !== "Escape") return;
                        e.stopPropagation();
                        setRenaming(null);
                      }}
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        className="ide-drawer__open"
                        aria-current={row.id === active ? "true" : undefined}
                        disabled={busy}
                        onClick={() => void pick(row.id)}
                      >
                        <span className="ide-drawer__title">{row.title}</span>
                        <span className="ide-drawer__meta">
                          {when(row.saved)}, {S.turns(row.turns)}
                        </span>
                      </button>
                      <span className="ide-drawer__tools">
                        <button
                          type="button"
                          aria-label={S.rename(row.title)}
                          title={S.rename(row.title)}
                          disabled={busy}
                          onClick={() => {
                            setDraft(row.title);
                            setDeleting(null);
                            setRenaming(row.id);
                          }}
                        >
                          <Codicon name="edit" />
                        </button>
                        <button
                          type="button"
                          aria-label={S.delete(row.title)}
                          title={S.delete(row.title)}
                          disabled={busy}
                          onClick={() => {
                            setRenaming(null);
                            setDeleting(row.id);
                          }}
                        >
                          <Codicon name="trash" />
                        </button>
                      </span>
                    </>
                  )}
                </li>
              ))}
            </ol>
          ) : null}
        </section>
      ) : null}
    </>
  );
}
