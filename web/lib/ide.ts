// Client for `pcode ide`: REST for files, a WebSocket for session events and approvals.

"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { apiBase } from "./api";

const TOKEN_KEY = "pcode-ide-token";
const MAX_LINES = 2000;
const RETRY_MS = 1500;

export type Mode = "plan" | "ask" | "auto";
export type Effort = "fast" | "normal" | "think" | "deep";

export type IdeState = {
  keys: Record<string, boolean>;
  key_help: Record<string, string>;
  provider: string | null;
  project: string | null;
  session_id: string | null;
  session_title: string;
  mode: Mode;
  effort: Effort;
  pending_plan: boolean;
  private: { on: boolean; claim: string; outside: string[]; warning: string; setup: boolean };
  busy: boolean;
  models: Record<string, string>;
  can_undo: boolean;
  tokens: number;
  seq: number;
};

export type SavedChat = { id: string; saved: number; title: string; turns: number };
export type Turn = { status: string; reason: string; verified: boolean; question: boolean; tokens: number; files: string[] };
export type TermLine = { kind: "cmd" | "out" | "sys"; text: string };

export type Item =
  | { kind: "user"; text: string; turn: number | null }
  | { kind: "plan"; steps: string[] }
  | { kind: "work"; label: string; details: string[] }
  | { kind: "approval"; id: string; command: string; reason: string; answer: boolean | null }
  | { kind: "reply"; text: string }
  | { kind: "check"; ok: boolean; verified: boolean; summary: string }
  | { kind: "note"; tone: "info" | "warn" | "bad"; text: string }
  | { kind: "output"; text: string }
  | { kind: "sessions"; rows: SavedChat[] }
  | { kind: "help" }
  | {
      kind: "done";
      status: string;
      reason: string;
      files: string[];
      verified: boolean;
      tokens: number;
      question: boolean;
      fixRun: string | null;
    };

export type LiveRow =
  | { kind: "agent"; agent: string; label: string; since: number; thinking: string }
  | { kind: "event"; tone: "good" | "bad" | "info"; text: string };

export type LiveEdit = {
  path: string;
  action: string;
  added: number;
  removed: number;
  hunks: { line: number; end: number; lines: string[] }[];
  more: boolean;
};

export type Live = {
  id: number;
  running: boolean;
  writing: boolean;             // this request can change files, so the tab opens for it
  coding: boolean;              // the agent at work is one that writes code
  asking: boolean;              // waiting for the user to answer an approval
  label: string;
  phase: string;
  path: string;
  seconds: number;
  silent: number;
  rate: number | null;
  step: number;
  maxSteps: number;
  code: { path: string; text: string } | null;
  rows: LiveRow[];
  edits: LiveEdit[];
  result: { status: string; reason: string } | null;
};

export const NO_LIVE: Live = {
  id: 0,
  running: false,
  writing: false,
  coding: false,
  asking: false,
  label: "",
  phase: "waiting",
  path: "",
  seconds: 0,
  silent: 0,
  rate: null,
  step: 0,
  maxSteps: 0,
  code: null,
  rows: [],
  edits: [],
  result: null,
};

const WRITERS = ["Builder", "Debugger", "TestWriter"];
const OPENS_LIVE = ["Planner", ...WRITERS];

export type View = {
  live: Live;
  items: Item[];
  busy: boolean;
  requestTokens: number;
  totalTokens: number;
  terminal: TermLine[];
  checks: { ok: boolean; summary: string }[];
  preview: string | null;
  changed: Record<string, string>;
  fileTick: number;
  lastFile: string | null;
  graphTick: number;
  touched: { path: string; n: number } | null;
  seq: number;
};

export const EMPTY_VIEW: View = {
  live: NO_LIVE,
  items: [],
  busy: false,
  requestTokens: 0,
  totalTokens: 0,
  terminal: [],
  checks: [],
  preview: null,
  changed: {},
  fileTick: 0,
  lastFile: null,
  graphTick: 0,
  touched: null,
  seq: 0,
};

type Event = Record<string, any> & { type: string; seq?: number };

function addToWork(items: Item[], line: string): Item[] {
  const last = items[items.length - 1];
  if (last?.kind !== "work") return items;
  return [...items.slice(0, -1), { ...last, details: [...last.details, line] }];
}

const addToTerminal = (view: View, line: TermLine) => [...view.terminal, line].slice(-MAX_LINES);

function closeApprovals(items: Item[]): Item[] {
  return items.map((it) => (it.kind === "approval" && it.answer === null ? { ...it, answer: false } : it));
}

/** A saved conversation as a feed. A turn from before results were kept has only its text. */
function feedFrom(history: { role: string; content: string }[], turns: Partial<Turn>[]): Item[] {
  let turn = 0;
  return history.flatMap((message): Item[] => {
    if (message.role === "user") return [{ kind: "user", text: message.content, turn: ++turn }];
    const reply: Item = { kind: "reply", text: message.content };
    const result = turns[turn - 1];
    return result?.status ? [reply, { ...(result as Turn), kind: "done", fixRun: null }] : [reply];
  });
}

const addRow = (live: Live, row: LiveRow): Live => ({ ...live, rows: [...live.rows, row] });

/** The Live tab's share of an event. `activity` and the two text events are never replayed. */
function liveEvent(live: Live, event: Event): Live {
  switch (event.type) {
    case "user":
      return { ...NO_LIVE, id: live.id + 1, running: true };
    case "agent":
      return addRow(
        {
          ...live,
          running: true,
          writing: live.writing || OPENS_LIVE.includes(event.agent),
          coding: WRITERS.includes(event.agent),
          label: event.label ?? event.agent,
          phase: "waiting",
          path: "",
          code: null,
          step: event.step ?? live.step,
          maxSteps: event.max_steps ?? live.maxSteps,
        },
        { kind: "agent", agent: event.agent, label: event.label ?? event.agent, since: live.seconds, thinking: "" },
      );
    case "activity":
      if (!live.running) return live;
      return {
        ...live,
        label: event.label || live.label,
        phase: event.phase,
        path: event.path,
        seconds: event.seconds,
        silent: event.silent,
        rate: event.rate,
      };
    case "approval_request":
      return { ...live, asking: true };
    case "approval_answered":
      return { ...live, asking: false };
    case "thinking_delta": {
      const at = live.rows.map((row) => row.kind).lastIndexOf("agent");
      if (at < 0) return live;
      return { ...live, rows: live.rows.map((row, i) => (i === at ? { ...row, thinking: event.text } : row)) };
    }
    case "code_delta":
      return { ...live, code: { path: event.path, text: event.text } };
    case "file_diff":
      return {
        ...live,
        code: null,
        edits: [
          ...live.edits,
          { path: event.path, action: event.action, added: event.added, removed: event.removed, hunks: event.hunks, more: event.more },
        ],
      };
    case "verify":
      return addRow(live, { kind: "event", tone: event.ok ? "good" : "bad", text: event.summary });
    case "rollback":
      return addRow(live, { kind: "event", tone: "bad", text: `Went back: ${event.reason}` });
    case "notice":
      return addRow(live, { kind: "event", tone: "info", text: event.text });
    case "done":
      return { ...live, running: false, code: null, result: { status: event.status, reason: event.reason ?? "" } };
    case "opened":
    case "cleared":
    case "resumed":
      return { ...NO_LIVE, id: 0 };
    default:
      return live;
  }
}

/** Turns one engine event into view state. Pure, so a reconnect can replay the log. */
export function applyEvent(view: View, event: Event): View {
  const live = liveEvent(view.live, event);
  const next = { ...view, live, seq: event.seq ?? view.seq };
  const { items } = view;
  switch (event.type) {
    case "opened":
      return {
        ...EMPTY_VIEW,
        totalTokens: view.totalTokens,
        seq: next.seq,
        fileTick: view.fileTick + 1,
        graphTick: view.graphTick + 1,
      };
    case "user":
      return {
        ...next,
        busy: true,
        requestTokens: 0,
        changed: {},
        items: [...items, { kind: "user", text: event.text, turn: event.turn ?? null }],
      };
    case "agent":
      return { ...next, busy: true, items: [...items, { kind: "work", label: event.label ?? event.agent, details: [] }] };
    case "plan":
      return { ...next, items: [...items, { kind: "plan", steps: event.steps }] };
    case "tool_start": {
      const detail = event.args?.path ?? event.args?.command ?? event.args?.pattern ?? "";
      const terminal = event.args?.command ? addToTerminal(view, { kind: "cmd", text: `${event.args.command}\n` }) : view.terminal;
      const touched = event.args?.path ? { path: event.args.path, n: (view.touched?.n ?? 0) + 1 } : view.touched;
      return { ...next, terminal, touched, items: addToWork(items, `${event.tool} ${detail}`.trim()) };
    }
    case "tool_end":
      return event.ok ? next : { ...next, items: addToWork(items, String(event.summary).split("\n")[0]) };
    case "file_changed":
      return {
        ...next,
        fileTick: view.fileTick + 1,
        lastFile: event.path,
        touched: { path: event.path, n: (view.touched?.n ?? 0) + 1 },
        changed: { ...view.changed, [event.path]: event.action },
      };
    case "text_delta": {
      const last = items[items.length - 1];
      if (last?.kind === "reply") {
        return { ...next, items: [...items.slice(0, -1), { ...last, text: last.text + event.text }] };
      }
      return { ...next, items: [...items, { kind: "reply", text: event.text }] };
    }
    case "notice":
      return { ...next, items: [...items, { kind: "note", tone: "warn", text: event.text }] };
    case "memory":
      return { ...next, items: [...items, { kind: "note", tone: "info", text: event.text }] };
    case "graph_delta":
      return { ...next, graphTick: view.graphTick + 1 };
    case "effort":
      return { ...next, items: [...items, { kind: "note", tone: "info", text: `${event.level}: ${event.how}` }] };
    case "error":
      return { ...next, items: [...items, { kind: "note", tone: "bad", text: event.message }] };
    case "verify":
      return {
        ...next,
        checks: [...view.checks, { ok: event.ok, summary: event.summary }],
        items: [...items, { kind: "check", ok: event.ok, verified: event.verified, summary: event.summary }],
      };
    case "rollback":
      return {
        ...next,
        fileTick: view.fileTick + 1,
        items: [...items, { kind: "note", tone: "warn", text: `Went back: ${event.reason}` }],
      };
    case "approval_request":
      return {
        ...next,
        items: [...items, { kind: "approval", id: event.id, command: event.command, reason: event.reason, answer: null }],
      };
    case "approval_answered":
      return {
        ...next,
        items: items.map((it) => (it.kind === "approval" && it.id === event.id ? { ...it, answer: event.approved } : it)),
      };
    case "command_start":
      return { ...next, terminal: addToTerminal(view, { kind: "cmd", text: `${event.command}\n` }) };
    case "command_output":
      return { ...next, terminal: addToTerminal(view, { kind: "out", text: event.text }) };
    case "command_end":
      return {
        ...next,
        fileTick: view.fileTick + 1,
        terminal: event.code ? addToTerminal(view, { kind: "sys", text: `Exited with code ${event.code}.\n` }) : view.terminal,
      };
    case "print":
      return { ...next, items: [...items, { kind: "output", text: event.text }] };
    case "sessions":
      return { ...next, items: [...items, { kind: "sessions", rows: event.rows }] };
    case "help":
      return { ...next, items: [...items, { kind: "help" }] };
    case "cleared":
      return { ...next, items: [], changed: {}, requestTokens: 0, graphTick: view.graphTick + 1 };
    case "resumed":
      return { ...next, changed: {}, graphTick: view.graphTick + 1, items: feedFrom(event.history, event.turns ?? []) };
    case "preview":
      return { ...next, preview: event.url };
    case "usage":
      return { ...next, requestTokens: event.tokens, totalTokens: event.total };
    case "applied":
      return {
        ...next,
        fileTick: view.fileTick + 1,
        items: [
          ...items.map((it) => (it.kind === "done" && it.fixRun ? { ...it, fixRun: null, files: event.files } : it)),
          { kind: "note", tone: "info", text: `Applied to ${event.files.length} file${event.files.length === 1 ? "" : "s"}. You can undo this.` },
        ],
      };
    case "undone":
      return {
        ...next,
        fileTick: view.fileTick + 1,
        graphTick: view.graphTick + 1,
        changed: {},
        items: [...items, { kind: "note", tone: "info", text: undoText(event.restored, event.kept) }],
      };
    case "done":
      return {
        ...next,
        busy: false,
        fileTick: view.fileTick + 1,
        items: [
          ...closeApprovals(items),
          {
            kind: "done",
            status: event.status,
            reason: event.reason,
            files: event.files,
            verified: event.verified,
            tokens: view.requestTokens,
            question: event.question === true,
            fixRun: event.fix_run ?? null,
          },
        ],
      };
    default:
      return next;
  }
}

export const when = (saved: number) =>
  new Date(saved * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

function undoText(restored: string[], kept: string[]) {
  const put = `Put back ${restored.length} file${restored.length === 1 ? "" : "s"}.`;
  return kept.length ? `${put} Kept ${kept.join(", ")}: you changed it afterwards.` : put;
}

export function readToken(): string | null {
  const match = window.location.hash.match(/token=([\w-]+)/);
  try {
    if (match) {
      sessionStorage.setItem(TOKEN_KEY, match[1]);
      window.history.replaceState(null, "", window.location.pathname);
      return match[1];
    }
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return match ? match[1] : null;
  }
}

export class IdeError extends Error {
  constructor(message: string, public code: string, public status: number) {
    super(message);
  }
}

/** Who is asking: the launch's token, and the open folder. An empty workspace is the whole IDE. */
export type IdeClient = { token: string; workspace: string };

export async function ideCall<T>(client: IdeClient, path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/ide/${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        "x-pcode-token": client.token,
        ...(client.workspace ? { "x-pcode-workspace": client.workspace } : {}),
      },
    });
  } catch {
    throw new IdeError("Lost the connection to pcode ide.", "offline", 0);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    throw new IdeError(detail?.message ?? "That did not work.", detail?.code ?? "error", res.status);
  }
  return body as T;
}

export const post = <T,>(client: IdeClient, path: string, body?: unknown) =>
  ideCall<T>(client, path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

function socketUrl() {
  const base = apiBase() || window.location.origin;
  return `${base.replace(/^http/, "ws")}/api/ide/ws`;
}

export type Link = "connecting" | "open" | "lost" | "denied" | "gone";

export function useIde(client: IdeClient) {
  const [view, dispatch] = useReducer(
    (state: View, action: Event | { type: "reset" }) => (action.type === "reset" ? EMPTY_VIEW : applyEvent(state, action)),
    EMPTY_VIEW,
  );
  const [state, setState] = useState<IdeState | null>(null);
  const [link, setLink] = useState<Link>("connecting");
  const socket = useRef<WebSocket | null>(null);
  const seq = useRef(0);
  seq.current = view.seq;

  const refresh = useCallback(async () => {
    try {
      setState(await ideCall<IdeState>(client, "state"));
    } catch (e) {
      if (e instanceof IdeError && e.status === 401) setLink("denied");
    }
  }, [client]);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;

    const connect = () => {
      const ws = new WebSocket(socketUrl());
      socket.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ token: client.token, workspace: client.workspace, since: seq.current }));
      ws.onmessage = (message) => {
        const event = JSON.parse(message.data) as Event;
        if (event.type === "hello") {
          setLink("open");
          setState(event.state);
        } else {
          dispatch(event);
          if (["done", "undone", "opened", "cleared", "resumed"].includes(event.type)) void refresh();
        }
      };
      ws.onclose = (closed) => {
        if (stop) return;
        if (closed.code === 4401) {
          setLink("denied");
          return;
        }
        if (closed.code === 4405) {
          setLink("gone");
          return;
        }
        setLink("lost");
        timer = window.setTimeout(connect, RETRY_MS);
      };
    };

    connect();
    return () => {
      stop = true;
      window.clearTimeout(timer);
      socket.current?.close();
    };
  }, [client, refresh]);

  const send = useCallback((message: Record<string, unknown>) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(message));
  }, []);

  // Slash command output stays in this tab: it is never sent to the server or to the model.
  const local = useCallback((event: Event) => dispatch(event), []);

  return { view, state, setState, link, send, refresh, local };
}
