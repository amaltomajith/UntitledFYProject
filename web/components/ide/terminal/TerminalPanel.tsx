"use client";

import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState, type DragEvent, type MouseEvent } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { IDE } from "@/lib/content";
import { Codicon } from "../Codicon";
import { Menu, type MenuItem } from "../Menu";
import { mount } from "./store";
import { DRAG_TYPE, type ShellRow, type TerminalRow, type Terminals } from "./useTerminals";

const T = IDE.terminal;
const ICONS: Record<string, string> = {
  powershell: "terminal-powershell",
  cmd: "terminal-cmd",
  "git-bash": "terminal-bash",
  linux: "terminal-linux",
  bash: "terminal-bash",
  zsh: "terminal",
  fish: "terminal",
  terminal: "terminal",
};

export const shellIcon = (icon: string) => ICONS[icon] ?? "terminal";

export function TerminalSlot({ id, focus }: { id: string; focus: boolean }) {
  const slot = useRef<HTMLDivElement>(null);

  useEffect(() => mount(id, slot.current as HTMLDivElement, focus), [id, focus]);

  return <div ref={slot} className="ide-slot" />;
}

const carries = (e: DragEvent) => e.dataTransfer.types.includes(DRAG_TYPE);

function dragFrom(t: Terminals, id: string) {
  return (e: DragEvent) => {
    e.dataTransfer.setData(DRAG_TYPE, id);
    e.dataTransfer.effectAllowed = "move";
    t.setDragging(id);
  };
}

export function dropInto(onDrop: (id: string) => void) {
  return {
    onDragOver: (e: DragEvent) => {
      if (carries(e)) e.preventDefault();
    },
    onDrop: (e: DragEvent) => {
      if (!carries(e)) return;
      e.preventDefault();
      onDrop(e.dataTransfer.getData(DRAG_TYPE));
    },
  };
}

type Popup = { x: number; y: number; items: MenuItem[] };

function rowMenu(t: Terminals, row: TerminalRow, onRename: (id: string) => void): MenuItem[] {
  return [
    { label: T.rename, onPick: () => onRename(row.id) },
    {
      label: T.split,
      onPick: () => {
        t.select(row.id);
        void t.create(row.shell, true);
      },
    },
    { label: T.toEditor, onPick: () => t.dock(row.id) },
    { label: T.kill, onPick: () => void t.kill(row.id), danger: true },
  ];
}

function Rename({ row, onDone }: { row: TerminalRow; onDone: (name: string | null) => void }) {
  const [name, setName] = useState(row.name);
  return (
    <input
      className="ide-rail__rename"
      value={name}
      aria-label={T.rename}
      autoFocus
      maxLength={60}
      onChange={(e) => setName(e.target.value)}
      onBlur={() => onDone(name.trim() || null)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onDone(name.trim() || null);
        if (e.key === "Escape") onDone(null);
      }}
    />
  );
}

type RailProps = {
  t: Terminals;
  editing: string | null;
  setEditing: (id: string | null) => void;
  onMenu: (e: MouseEvent, row: TerminalRow) => void;
};

function Rail({ t, editing, setEditing, onMenu }: RailProps) {
  const byId = new Map(t.rows.map((row) => [row.id, row]));

  return (
    <ul className="ide-rail" aria-label={T.list}>
      {t.groups.flatMap((group) =>
        group.map((id, i) => {
          const row = byId.get(id);
          if (!row) return null;
          return (
            <li
              key={id}
              data-active={t.active === id}
              data-joined={group.length > 1 ? (i === 0 ? "first" : i === group.length - 1 ? "last" : "middle") : undefined}
              draggable={editing !== id}
              onDragStart={dragFrom(t, id)}
              onDragEnd={() => t.setDragging(null)}
              {...dropInto((moved) => t.reorder(moved, id))}
              onContextMenu={(e) => onMenu(e, row)}
            >
              {editing === id ? (
                <Rename
                  row={row}
                  onDone={(name) => {
                    setEditing(null);
                    if (name && name !== row.name) void t.rename(id, name);
                  }}
                />
              ) : (
                <>
                  <button type="button" className="ide-rail__pick" title={row.name} onClick={() => t.select(id)}>
                    <Codicon name={shellIcon(row.icon)} />
                    <span>{row.name}</span>
                  </button>
                  <span className="ide-rail__tools">
                    <button
                      type="button"
                      aria-label={T.splitNamed(row.name)}
                      title={T.split}
                      onClick={() => {
                        t.select(id);
                        void t.create(row.shell, true);
                      }}
                    >
                      <Codicon name="split-horizontal" />
                    </button>
                    <button type="button" aria-label={T.killNamed(row.name)} title={T.kill} onClick={() => void t.kill(id)}>
                      <Codicon name="trash" />
                    </button>
                  </span>
                </>
              )}
            </li>
          );
        }),
      )}
    </ul>
  );
}

export function TerminalArea({ t }: { t: Terminals }) {
  const [popup, setPopup] = useState<Popup | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const group = t.groups.find((g) => t.active !== null && g.includes(t.active)) ?? t.groups[0];
  const count = t.rows.length - t.docked.length;
  const rail = count > 1;

  const openMenu = (e: MouseEvent, row: TerminalRow) => {
    e.preventDefault();
    setPopup({ x: e.clientX, y: e.clientY, items: rowMenu(t, row, setEditing) });
  };

  if (t.install) {
    return (
      <div className="ide-terms__note">
        <p>{T.needsInstall}</p>
        <code>{t.install}</code>
      </div>
    );
  }
  if (!t.ready) return <div className="ide-terms__note">{t.error ?? T.starting}</div>;
  if (!group) {
    return (
      <div className="ide-terms__note" {...dropInto(t.join)}>
        <p>{t.error ?? T.none}</p>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => void t.create()}>
          {T.create}
        </button>
      </div>
    );
  }

  const dragged = t.dragging;
  const canDrop = dragged !== null && (t.docked.includes(dragged) || !(group.length === 1 && group[0] === dragged));

  return (
    <div className="ide-terms">
      {t.error ? (
        <p className="ide-terms__error" role="alert">
          {t.error}
        </p>
      ) : null}
      <Group orientation="horizontal" id="ide-terminals" key={rail ? "rail" : "alone"}>
        <Panel id="terminal-view" minSize={160}>
          <div className="ide-terms__view">
            <Group orientation="horizontal" id={`ide-split-${group.length}`} key={group.join()}>
              {group.flatMap((id, i) => [
                i > 0 ? <Separator key={`sash-${id}`} className="ide-sash" /> : null,
                <Panel key={id} id={id} minSize={120}>
                  <div
                    className="ide-terms__one"
                    data-active={group.length > 1 && t.active === id}
                    onMouseDownCapture={() => {
                      if (t.active !== id) t.select(id);
                    }}
                  >
                    <TerminalSlot id={id} focus={t.active === id} />
                  </div>
                </Panel>,
              ])}
            </Group>
            {canDrop ? (
              <div className="ide-drop" {...dropInto(t.join)}>
                <span>{dragged !== null && t.docked.includes(dragged) ? T.dropPanel : T.dropSplit}</span>
              </div>
            ) : null}
          </div>
        </Panel>
        {rail ? <Separator className="ide-sash" /> : null}
        {rail ? (
          <Panel id="terminal-rail" defaultSize={160} minSize={46} maxSize="50%" groupResizeBehavior="preserve-pixel-size">
            <Rail t={t} editing={editing} setEditing={setEditing} onMenu={openMenu} />
          </Panel>
        ) : null}
      </Group>
      {popup ? <Menu x={popup.x} y={popup.y} items={popup.items} label={T.list} onClose={() => setPopup(null)} /> : null}
    </div>
  );
}

function shellRows(t: Terminals, pick: (shell: ShellRow) => void, checked?: string | null): MenuItem[] {
  return t.shells.map((shell) => ({
    label: shell.name,
    hint: shell.id === t.defaultShell ? T.isDefault : undefined,
    icon: shellIcon(shell.icon),
    checked: checked === shell.id,
    onPick: () => pick(shell),
  }));
}

export function TerminalActions({ t }: { t: Terminals }) {
  const [menu, setMenu] = useState<"new" | "split" | "default" | null>(null);
  const [popup, setPopup] = useState<Popup | null>(null);
  const [renaming, setRenaming] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const row = t.rows.find((r) => r.id === t.active);

  if (t.install) return null;

  const items: MenuItem[] =
    menu === "split"
      ? shellRows(t, (s) => void t.create(s.id, true))
      : menu === "default"
        ? shellRows(t, (s) => t.chooseDefault(s.id), t.defaultShell)
        : [
            ...shellRows(t, (s) => void t.create(s.id)),
            { separator: true },
            { label: T.splitWith, onPick: () => setMenu("split"), keep: true },
            { label: T.chooseDefault, onPick: () => setMenu("default"), keep: true },
          ];
  const box = anchor.current?.getBoundingClientRect();

  return (
    <div className="ide-bottom__tools">
      {row && renaming ? (
        <span className="ide-bottom__name">
          <Rename
            row={row}
            onDone={(name) => {
              setRenaming(false);
              if (name && name !== row.name) void t.rename(row.id, name);
            }}
          />
        </span>
      ) : null}
      {row && !renaming ? (
        <span
          className="ide-bottom__name"
          title={T.dragHint}
          draggable
          onDragStart={dragFrom(t, row.id)}
          onDragEnd={() => t.setDragging(null)}
          onContextMenu={(e) => {
            e.preventDefault();
            setPopup({ x: e.clientX, y: e.clientY, items: rowMenu(t, row, () => setRenaming(true)) });
          }}
        >
          <Codicon name={shellIcon(row.icon)} />
          {row.name}
        </span>
      ) : null}
      <span className="ide-split-button">
        <button type="button" aria-label={T.create} title={T.create} onClick={() => void t.create()}>
          <Codicon name="add" />
        </button>
        <button
          ref={anchor}
          type="button"
          aria-label={T.choose}
          title={T.choose}
          aria-haspopup="menu"
          aria-expanded={menu !== null}
          onClick={() => setMenu(menu ? null : "new")}
        >
          <Codicon name="chevron-down" />
        </button>
      </span>
      <button type="button" aria-label={T.split} title={T.split} disabled={!row} onClick={() => row && void t.create(row.shell, true)}>
        <Codicon name="split-horizontal" />
      </button>
      <button type="button" aria-label={T.kill} title={T.kill} disabled={!row} onClick={() => row && void t.kill(row.id)}>
        <Codicon name="trash" />
      </button>
      {menu && box ? (
        <Menu x={box.left} y={box.bottom + 2} items={items} label={T.choose} onClose={() => setMenu(null)} />
      ) : null}
      {popup ? <Menu x={popup.x} y={popup.y} items={popup.items} label={T.list} onClose={() => setPopup(null)} /> : null}
    </div>
  );
}
