"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post } from "@/lib/ide";
import { configure, dispose, disposeAll, focusTerminal, writeNote } from "./store";

const LAYOUT_KEY = "pcode-terminal-groups";
const DEFAULT_KEY = "pcode-terminal-default";
export const DRAG_TYPE = "application/x-pcode-terminal";

export type TerminalRow = {
  id: string;
  name: string;
  shell: string;
  icon: string;
  cols: number;
  rows: number;
  alive: boolean;
  exit_code: number | null;
};
export type ShellRow = { id: string; name: string; icon: string; default: boolean };
type Shells = { shells: ShellRow[]; install: string | null; windows_build: number | null };
type Options = {
  token: string;
  project: string | null;
  wanted: boolean;              // the Terminal tab is showing, so an empty panel starts a shell
  onDock: (id: string) => void;
  onRemoved: (id: string) => void;
};

function stored(storage: () => Storage, key: string) {
  try {
    return storage().getItem(key);
  } catch {
    return null;
  }
}

function store(storage: () => Storage, key: string, value: string) {
  try {
    storage().setItem(key, value);
  } catch {
    // Remembering the layout is a convenience; the panel works without it.
  }
}

const session = () => sessionStorage;
const local = () => localStorage;
const without = (groups: string[][], id: string) => groups.map((g) => g.filter((t) => t !== id)).filter((g) => g.length);

export function useTerminals({ token, project, wanted, onDock, onRemoved }: Options) {
  const [rows, setRows] = useState<TerminalRow[]>([]);
  const [groups, setGroups] = useState<string[][]>([]);
  const [docked, setDocked] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [shells, setShells] = useState<ShellRow[]>([]);
  const [install, setInstall] = useState<string | null>(null);
  const [defaultShell, setDefault] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const started = useRef(false);
  const groupsRef = useRef(groups);
  groupsRef.current = groups;
  const handlers = useRef({ onDock, onRemoved });
  handlers.current = { onDock, onRemoved };

  const fail = (e: unknown) => setError(e instanceof IdeError ? e.message : IDE.setup.failed);

  const forget = useCallback((id: string) => {
    void dispose(id);
    const left = without(groupsRef.current, id);
    setRows((all) => all.filter((row) => row.id !== id));
    setGroups(left);
    setDocked((all) => all.filter((t) => t !== id));
    setActive((now) => (now === id ? (left[left.length - 1]?.[0] ?? null) : now));
    handlers.current.onRemoved(id);
  }, []);

  useEffect(() => {
    if (!project) return;
    let stale = false;
    started.current = false;
    setReady(false);
    void (async () => {
      try {
        await disposeAll();
        const [found, list] = await Promise.all([
          ideCall<Shells>(token, "shells"),
          ideCall<{ terminals: TerminalRow[] }>(token, "terminals"),
        ]);
        if (stale) return;
        configure({
          token,
          windowsBuild: found.windows_build,
          onExit: (id, code) => {
            if (code) void writeNote(id, IDE.terminal.exited(code));
            else forget(id);
          },
        });
        const live = list.terminals;
        const known = new Set(live.map((t) => t.id));
        const saved: string[][] = JSON.parse(stored(session, LAYOUT_KEY) ?? "[]");
        const kept = saved.map((g) => g.filter((id) => known.has(id))).filter((g) => g.length);
        const placed = new Set(kept.flat());
        const next = [...kept, ...live.filter((t) => !placed.has(t.id)).map((t) => [t.id])];
        const preferred = stored(local, DEFAULT_KEY);
        setShells(found.shells);
        setInstall(found.install);
        setDefault(found.shells.some((s) => s.id === preferred) ? preferred : (found.shells.find((s) => s.default)?.id ?? null));
        setRows(live);
        setGroups(next);
        setDocked([]);
        setActive(next[0]?.[0] ?? null);
        setError(null);
        if (live.length) started.current = true;
        setReady(true);
      } catch (e) {
        if (!stale) fail(e);
      }
    })();
    return () => {
      stale = true;
    };
  }, [token, project, forget]);

  useEffect(() => {
    if (ready) store(session, LAYOUT_KEY, JSON.stringify(groups));
  }, [groups, ready]);

  // A drop can remove the row it started from, and a removed row never fires its own dragend.
  useEffect(() => {
    if (!dragging) return;
    const stop = () => setDragging(null);
    window.addEventListener("drop", stop);
    window.addEventListener("dragend", stop);
    return () => {
      window.removeEventListener("drop", stop);
      window.removeEventListener("dragend", stop);
    };
  }, [dragging]);

  const create = useCallback(
    async (shell?: string | null, split = false) => {
      setError(null);
      try {
        const row = await post<TerminalRow>(token, "terminals", { shell: shell ?? defaultShell ?? undefined });
        setRows((all) => [...all, row]);
        setGroups((all) => {
          const home = split && active ? all.findIndex((g) => g.includes(active)) : -1;
          if (home < 0) return [...all, [row.id]];
          return all.map((g, i) => (i === home ? [...g.slice(0, g.indexOf(active as string) + 1), row.id, ...g.slice(g.indexOf(active as string) + 1)] : g));
        });
        setActive(row.id);
      } catch (e) {
        fail(e);
      }
    },
    [token, defaultShell, active],
  );

  useEffect(() => {
    if (!ready || !wanted || started.current || install || !shells.length) return;
    started.current = true;
    if (!rows.length) void create();
  }, [ready, wanted, install, shells, rows, create]);

  const kill = async (id: string) => {
    try {
      await ideCall(token, `terminals/${id}`, { method: "DELETE" });
    } catch (e) {
      if (!(e instanceof IdeError && e.status === 404)) return fail(e);
    }
    forget(id);
  };

  const rename = async (id: string, name: string) => {
    try {
      const row = await post<TerminalRow>(token, `terminals/${id}/rename`, { name });
      setRows((all) => all.map((t) => (t.id === id ? row : t)));
    } catch (e) {
      fail(e);
    }
  };

  /** Drops `id` before `target`: inside the group when they share one, otherwise as its own group. */
  const reorder = (id: string, target: string) => {
    if (id === target) return;
    setDocked((all) => all.filter((t) => t !== id));
    setGroups((all) => {
      const shared = all.find((g) => g.includes(id) && g.includes(target));
      const left = without(all, id);
      const home = left.findIndex((g) => g.includes(target));
      if (home < 0) return all;
      if (!shared) return [...left.slice(0, home), [id], ...left.slice(home)];
      return left.map((g, i) => (i === home ? [...g.slice(0, g.indexOf(target)), id, ...g.slice(g.indexOf(target))] : g));
    });
  };

  /** Dropped on the terminal area: joins the group on show. A terminal from the editor comes back. */
  const join = (id: string) => {
    if (docked.includes(id)) handlers.current.onRemoved(id);
    setDocked((all) => all.filter((t) => t !== id));
    setGroups((all) => {
      const home = all.find((g) => active !== null && g.includes(active));
      if (!home) return [...without(all, id), [id]];
      if (home.includes(id)) return all;
      const left = without(all, id);
      return left.map((g) => (g.includes(active as string) ? [...g, id] : g));
    });
    setActive(id);
  };

  const dock = (id: string) => {
    const left = without(groupsRef.current, id);
    setGroups(left);
    setDocked((all) => [...all, id]);
    setActive((now) => (now === id ? (left[left.length - 1]?.[0] ?? null) : now));
    handlers.current.onDock(id);
  };

  const undock = (id: string) => {
    handlers.current.onRemoved(id);
    setDocked((all) => all.filter((t) => t !== id));
    setGroups((all) => [...all, [id]]);
    setActive(id);
  };

  const select = (id: string) => {
    setActive(id);
    void focusTerminal(id);
  };

  const chooseDefault = (id: string) => {
    setDefault(id);
    store(local, DEFAULT_KEY, id);
  };

  return {
    rows, groups, docked, active, shells, install, defaultShell, ready, error, dragging,
    setDragging, create, kill, rename, reorder, join, dock, undock, select, chooseDefault,
  };
}

export type Terminals = ReturnType<typeof useTerminals>;
