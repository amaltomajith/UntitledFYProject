"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { IDE } from "@/lib/content";
import { Codicon } from "./Codicon";
import { FileIcon } from "./FileIcon";

const P = IDE.palette;
const MAX_ROWS = 60;

export type PaletteCommand = { id: string; label: string; hint?: string; keys?: string; run: () => void };

type Row =
  | { kind: "file"; id: string; path: string; score: number }
  | { kind: "command"; id: string; command: PaletteCommand; score: number };

/** Letters of the query in order inside the text. Higher is better: early, adjacent and in the file name. */
export function fuzzy(query: string, text: string): number {
  if (!query) return 1;
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  const name = t.lastIndexOf("/") + 1;
  let at = -1;
  let score = 0;
  for (const ch of q) {
    const next = t.indexOf(ch, at + 1);
    if (next < 0) return 0;
    score += next === at + 1 ? 3 : 1;
    if (next >= name) score += 2;
    at = next;
  }
  if (t.slice(name).startsWith(q)) score += 10;
  return score - t.length / 200;
}

type Props = {
  open: boolean;
  initial: string;
  files: string[];
  commands: PaletteCommand[];
  onOpenFile: (path: string) => void;
  onClose: () => void;
};

export function CommandPalette({ open, initial, files, commands, onOpenFile, onClose }: Props) {
  const [query, setQuery] = useState(initial);
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    setQuery(initial);
    setIndex(0);
    window.setTimeout(() => input.current?.focus(), 0);
  }, [open, initial]);

  const asCommands = query.startsWith(">");
  const text = asCommands ? query.slice(1).trim() : query.trim();

  const rows = useMemo<Row[]>(() => {
    if (asCommands) {
      return commands
        .map((command) => ({ kind: "command" as const, id: command.id, command, score: fuzzy(text, command.label) }))
        .filter((row) => row.score > 0)
        .sort((a, b) => (text ? b.score - a.score : 0))
        .slice(0, MAX_ROWS);
    }
    return files
      .map((path) => ({ kind: "file" as const, id: path, path, score: fuzzy(text, path) }))
      .filter((row) => row.score > 0)
      .sort((a, b) => b.score - a.score || a.path.localeCompare(b.path))
      .slice(0, MAX_ROWS);
  }, [asCommands, text, files, commands]);

  useEffect(() => setIndex(0), [query]);

  useEffect(() => {
    list.current?.querySelector(`[data-index="${index}"]`)?.scrollIntoView({ block: "nearest" });
  }, [index]);

  if (!open) return null;

  const pick = (row: Row | undefined) => {
    if (!row) return;
    onClose();
    if (row.kind === "file") onOpenFile(row.path);
    else row.command.run();
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setIndex((at) => (rows.length ? (at + step + rows.length) % rows.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(rows[index]);
    }
  };

  const optionId = (i: number) => `${id}-row-${i}`;

  return (
    <div className="ide-palette__scrim" onMouseDown={onClose}>
      <div className="ide-palette" role="dialog" aria-modal="true" aria-label={P.label} onMouseDown={(e) => e.stopPropagation()}>
        <input
          ref={input}
          className="ide-palette__input"
          value={query}
          role="combobox"
          aria-expanded="true"
          aria-controls={`${id}-list`}
          aria-activedescendant={rows.length ? optionId(index) : undefined}
          aria-label={asCommands ? P.commands : P.files}
          placeholder={asCommands ? P.commands : `${P.files}  (${P.hint})`}
          spellCheck={false}
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <p className="ide-label ide-palette__group">{asCommands ? P.groups.command : P.groups.file}</p>
        <ul ref={list} id={`${id}-list`} role="listbox" className="ide-palette__list" aria-label={asCommands ? P.groups.command : P.groups.file}>
          {rows.length === 0 ? <li className="ide-palette__none">{P.none}</li> : null}
          {rows.map((row, i) => (
            <li
              key={row.id}
              id={optionId(i)}
              role="option"
              aria-selected={i === index}
              data-index={i}
              className="ide-palette__row"
              onMouseMove={() => setIndex(i)}
              onClick={() => pick(row)}
            >
              {row.kind === "file" ? (
                <>
                  <FileIcon name={row.path.split("/").pop() ?? row.path} />
                  <span className="ide-palette__name">{row.path.split("/").pop()}</span>
                  <span className="ide-palette__hint">{row.path.includes("/") ? row.path.slice(0, row.path.lastIndexOf("/")) : ""}</span>
                </>
              ) : (
                <>
                  <Codicon name="chevron-right" />
                  <span className="ide-palette__name">{row.command.label}</span>
                  <span className="ide-palette__hint">{row.command.hint ?? ""}</span>
                  {row.command.keys ? <kbd>{row.command.keys}</kbd> : null}
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
