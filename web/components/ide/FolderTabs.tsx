"use client";

import { useState, type KeyboardEvent } from "react";
import { IDE } from "@/lib/content";
import { Codicon } from "./Codicon";

const COPY = IDE.folders;

export type FolderRow = { id: string; project: string | null };
export type FolderStatus = {
  project: string | null;
  busy: boolean;
  doing: "working" | "waiting" | "needs" | null;
  private: boolean;
  unsaved: number;
};

type Props = {
  folders: FolderRow[];
  saved: string[];
  active: string | null;
  status: Record<string, FolderStatus>;
  error: string | null;
  onPick: (id: string) => void;
  onAdd: () => void;
  onClose: (id: string) => void;
  onReopen: (path: string) => void;
  onForget: (path: string) => void;
};

const parts = (path: string) => path.split(/[\\/]/).filter(Boolean);
const leaf = (path: string) => parts(path).pop() ?? path;

/** Folders with one name add the folder above, so two `app` tabs can be told apart. */
function nameOf(path: string, all: string[]) {
  const same = all.filter((other) => leaf(other).toLowerCase() === leaf(path).toLowerCase());
  return same.length > 1 ? parts(path).slice(-2).join("/") : leaf(path);
}

export function FolderTabs({ folders, saved, active, status, error, onPick, onAdd, onClose, onReopen, onForget }: Props) {
  const [asking, setAsking] = useState<string | null>(null);
  const rows = folders.map((folder) => ({ ...folder, project: status[folder.id]?.project ?? folder.project }));
  const paths = rows.flatMap((row) => (row.project ? [row.project] : [])).concat(saved);

  const move = (e: KeyboardEvent) => {
    const at = rows.findIndex((row) => row.id === active);
    const steps: Record<string, number> = {
      ArrowRight: (at + 1) % rows.length,
      ArrowLeft: (at - 1 + rows.length) % rows.length,
      Home: 0,
      End: rows.length - 1,
    };
    if (!(e.key in steps) || !(e.target as HTMLElement).matches('[role="tab"]')) return;
    e.preventDefault();
    const next = rows[steps[e.key]].id;
    onPick(next);
    document.getElementById(`folder-tab-${next}`)?.focus();
  };

  const close = (id: string) => {
    if (status[id]?.unsaved) setAsking(id);
    else onClose(id);
  };

  return (
    <div className="ide-folder-bar">
      <div className="ide-folder-tabs" role="tablist" aria-label={COPY.label} onKeyDown={move}>
        {rows.map((row) => {
          const now = status[row.id];
          const name = row.project ? nameOf(row.project, paths) : COPY.none;
          if (asking === row.id && now?.unsaved) {
            return (
              <span key={row.id} className="ide-folder-tab ide-folder-tab--ask" data-active={row.id === active}>
                <span role="alert">{COPY.unsaved(now.unsaved)}</span>
                <button
                  type="button"
                  onClick={() => {
                    setAsking(null);
                    onClose(row.id);
                  }}
                >
                  {COPY.discard}
                </button>
                <button type="button" autoFocus onClick={() => setAsking(null)}>
                  {COPY.keep}
                </button>
              </span>
            );
          }
          return (
            <span key={row.id} className="ide-folder-tab" data-active={row.id === active}>
              <button
                id={`folder-tab-${row.id}`}
                type="button"
                role="tab"
                aria-selected={row.id === active}
                aria-controls={`folder-${row.id}`}
                tabIndex={row.id === active ? 0 : -1}
                title={row.project ?? COPY.none}
                onClick={() => onPick(row.id)}
              >
                <Codicon name={now?.private ? "lock" : "folder"} />
                {now?.private ? <span className="sr-only">{COPY.lock}</span> : null}
                <span className="ide-folder-tab__name">{name}</span>
                {now?.doing ? <span className="ide-folder-tab__doing" data-doing={now.doing}>{COPY[now.doing]}</span> : null}
                {now?.unsaved ? <span className="ide-tab__dirty" title={IDE.editor.unsaved} aria-label={IDE.editor.unsaved}>●</span> : null}
              </button>
              <button
                type="button"
                className="ide-tab__close"
                aria-label={COPY.close(name)}
                title={now?.busy ? COPY.busy : COPY.close(name)}
                disabled={now?.busy}
                onClick={() => close(row.id)}
              >
                <Codicon name="close" />
              </button>
            </span>
          );
        })}
      </div>
      {saved.length ? (
        <div className="ide-folder-saved" role="group" aria-label={COPY.saved}>
          {saved.map((path) => (
            <span key={path} className="ide-folder-tab" data-saved="true">
              <button type="button" title={COPY.reopen(path)} onClick={() => onReopen(path)}>
                <Codicon name="folder" />
                <span className="ide-folder-tab__name">{nameOf(path, paths)}</span>
              </button>
              <button
                type="button"
                className="ide-tab__close"
                aria-label={COPY.forget(nameOf(path, paths))}
                title={COPY.forget(nameOf(path, paths))}
                onClick={() => onForget(path)}
              >
                <Codicon name="close" />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <button type="button" className="ide-folder-add" aria-label={COPY.add} title={COPY.add} onClick={onAdd}>
        <Codicon name="add" />
      </button>
      {error ? <p className="ide-folder-error" role="alert">{error}</p> : null}
    </div>
  );
}
