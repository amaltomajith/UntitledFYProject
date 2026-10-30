// Client for `pcode ide`: REST for files, a WebSocket for session events and approvals.

"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { apiBase } from "./api";

const TOKEN_KEY = "pcode-ide-token";
const MAX_LINES = 2000;
const RETRY_MS = 1500;

export type IdeState = {
  keys: Record<string, boolean>;
  key_help: Record<string, string>;
  provider: string | null;
  project: string | null;
  session_id: string | null;
  mode: "ask" | "auto";
  busy: boolean;
  models: Record<string, string>;
  can_undo: boolean;
  tokens: number;
  seq: number;
};

export type Item =
  | { kind: "user"; text: string }
  | { kind: "plan"; steps: string[] }
  | { kind: "work"; label: string; details: string[] }
  | { kind: "approval"; id: string; command: string; reason: string; answer: boolean | null }
  | { kind: "reply"; text: string }
  | { kind: "check"; ok: boolean; verified: boolean; summary: string }
  | { kind: "note"; tone: "info" | "warn" | "bad"; text: string }
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

export type View = {
  items: Item[];
  busy: boolean;
  requestTokens: number;
  totalTokens: number;
  terminal: string[];
  checks: { ok: boolean; summary: string }[];
  preview: string | null;
  changed: Record<string, string>;
  fileTick: number;
  lastFile: string | null;
  seq: number;
};

export const EMPTY_VIEW: View = {
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
  seq: 0,
};

type Event = Record<string, any> & { type: string; seq?: number };

function addToWork(items: Item[], line: string): Item[] {
  const last = items[items.length - 1];
  if (last?.kind !== "work") return items;
  return [...items.slice(0, -1), { ...last, details: [...last.details, line] }];
}

function closeApprovals(items: Item[]): Item[] {
  return items.map((it) => (it.kind === "approval" && it.answer === null ? { ...it, answer: false } : it));
}

/** Turns one engine event into view state. Pure, so a reconnect can replay the log. */
export function applyEvent(view: View, event: Event): View {
  const next = { ...view, seq: event.seq ?? view.seq };
  const { items } = view;
  switch (event.type) {
    case "opened":
      return { ...EMPTY_VIEW, totalTokens: view.totalTokens, seq: next.seq, fileTick: view.fileTick + 1 };
    case "user":
      return { ...next, busy: true, requestTokens: 0, changed: {}, items: [...items, { kind: "user", text: event.text }] };
    case "agent":
      return { ...next, busy: true, items: [...items, { kind: "work", label: event.label ?? event.agent, details: [] }] };
    case "plan":
      return { ...next, items: [...items, { kind: "plan", steps: event.steps }] };
    case "tool_start": {
      const detail = event.args?.path ?? event.args?.command ?? event.args?.pattern ?? "";
      return { ...next, items: addToWork(items, `${event.tool} ${detail}`.trim()) };
    }
    case "tool_end":
      return event.ok ? next : { ...next, items: addToWork(items, String(event.summary).split("\n")[0]) };
    case "file_changed":
      return {
        ...next,
        fileTick: view.fileTick + 1,
        lastFile: event.path,
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
      return { ...next, terminal: [...view.terminal, `$ ${event.command}\n`].slice(-MAX_LINES) };
    case "command_output":
      return { ...next, terminal: [...view.terminal, event.text].slice(-MAX_LINES) };
    case "command_end":
      return { ...next, fileTick: view.fileTick + 1 };
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

export async function ideCall<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/ide/${path}`, {
      ...init,
      headers: { "content-type": "application/json", "x-pcode-token": token },
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

export const post = <T,>(token: string, path: string, body?: unknown) =>
  ideCall<T>(token, path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

function socketUrl() {
  const base = apiBase() || window.location.origin;
  return `${base.replace(/^http/, "ws")}/api/ide/ws`;
}

export type Link = "connecting" | "open" | "lost" | "denied";

export function useIde(token: string) {
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
      setState(await ideCall<IdeState>(token, "state"));
    } catch (e) {
      if (e instanceof IdeError && e.status === 401) setLink("denied");
    }
  }, [token]);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;

    const connect = () => {
      const ws = new WebSocket(socketUrl());
      socket.current = ws;
      ws.onopen = () => ws.send(JSON.stringify({ token, since: seq.current }));
      ws.onmessage = (message) => {
        const event = JSON.parse(message.data) as Event;
        if (event.type === "hello") {
          setLink("open");
          setState(event.state);
        } else {
          dispatch(event);
          if (event.type === "done" || event.type === "undone" || event.type === "opened") void refresh();
        }
      };
      ws.onclose = (closed) => {
        if (stop) return;
        if (closed.code === 4401) {
          setLink("denied");
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
  }, [token, refresh]);

  const send = useCallback((message: Record<string, unknown>) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(message));
  }, []);

  return { view, state, setState, link, send, refresh };
}
