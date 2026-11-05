// Each terminal lives here, outside React, so it survives StrictMode, tab switches and moves between
// the panel and the editor. A slot adopts the terminal's host <div>; only killing it disposes anything.

"use client";

import type { FitAddon } from "@xterm/addon-fit";
import type { Terminal } from "@xterm/xterm";
import { apiBase } from "@/lib/api";
import type { IdeClient } from "@/lib/ide";

const RETRY_MS = 1500;
const ACK_EVERY = 5000;
const SCROLLBACK = 5000;
const TAKEN_OVER = 4409;
const REFUSED = [4401, 4404];
const SELECTION = "rgba(169, 155, 255, 0.3)";
const ANSI = {
  black: "#0c0c0c",
  red: "#f2928a",
  green: "#7dcf9a",
  yellow: "#e5c07b",
  blue: "#7aa2f7",
  magenta: "#c3a6ff",
  cyan: "#7fd1d9",
  white: "#d4d4d4",
  brightBlack: "#6b6b6b",
  brightRed: "#ffb4ab",
  brightGreen: "#a4e4b9",
  brightYellow: "#f0d9a0",
  brightBlue: "#a3c0ff",
  brightMagenta: "#ddc9ff",
  brightCyan: "#a9e6ec",
  brightWhite: "#ffffff",
};

type Entry = {
  id: string;
  workspace: string;
  term: Terminal;
  fit: FitAddon;
  host: HTMLDivElement;
  socket: WebSocket | null;
  offset: number;           // terminal bytes received, so a reconnect asks only for what it missed
  unacked: number;
  replay: number;           // bytes of history still to come; never answered and never acknowledged
  muted: boolean;
  stopped: boolean;
  timer?: number;
  rendered: boolean;
};

type Config = { client: IdeClient; windowsBuild: number | null; onExit: (id: string, code: number | null) => void };

const configs = new Map<string, Config>();  // one per open folder, so a tab never reads another's
const entries = new Map<string, Promise<Entry>>();
const encoder = new TextEncoder();

export const configure = (next: Config) => {
  configs.set(next.client.workspace, next);
};

const addons = () =>
  Promise.all([
    import("@xterm/xterm"),
    import("@xterm/addon-fit"),
    import("@xterm/addon-web-links"),
    import("@xterm/addon-unicode11"),
    document.fonts.ready,
  ]);

function theme() {
  const css = getComputedStyle(document.querySelector(".ide") ?? document.documentElement);
  const read = (name: string) => css.getPropertyValue(name).trim();
  return {
    ...ANSI,
    background: read("--well"),
    foreground: read("--ink"),
    cursor: read("--accent-hi"),
    cursorAccent: read("--well"),
    selectionBackground: SELECTION,
  };
}

function sendSize(entry: Entry) {
  if (entry.socket?.readyState === WebSocket.OPEN) {
    entry.socket.send(JSON.stringify({ t: "resize", cols: entry.term.cols, rows: entry.term.rows }));
  }
}

function fit(entry: Entry) {
  const parent = entry.host.parentElement;
  if (entry.rendered && parent && parent.clientWidth > 0 && parent.clientHeight > 0) entry.fit.fit();
}

function received(entry: Entry, data: ArrayBuffer) {
  const size = data.byteLength;
  entry.offset += size;
  const history = entry.replay > 0;
  entry.replay = Math.max(0, entry.replay - size);
  entry.term.write(new Uint8Array(data), () => {
    if (history) {
      if (entry.replay === 0) entry.muted = false;
      return;
    }
    entry.unacked += size;
    if (entry.unacked >= ACK_EVERY && entry.socket?.readyState === WebSocket.OPEN) {
      entry.socket.send(JSON.stringify({ t: "ack", n: entry.unacked }));
      entry.unacked = 0;
    }
  });
}

function control(entry: Entry, text: string) {
  const message = JSON.parse(text);
  if (message.t === "attached") {
    if (message.reset) entry.term.reset();
    entry.offset = message.offset;
    entry.unacked = 0;
    entry.replay = message.replay;
    entry.muted = message.replay > 0;
    fit(entry);
    sendSize(entry);
  } else if (message.t === "exit") {
    entry.stopped = true;
    configs.get(entry.workspace)?.onExit(entry.id, message.code);
  }
}

function connect(entry: Entry) {
  const config = configs.get(entry.workspace);
  if (!config || entry.stopped) return;
  const { token, workspace } = config.client;
  const base = apiBase() || window.location.origin;
  const socket = new WebSocket(`${base.replace(/^http/, "ws")}/api/ide/terminals/${entry.id}/ws`);
  socket.binaryType = "arraybuffer";
  entry.socket = socket;
  socket.onopen = () => socket.send(JSON.stringify({ token, workspace, offset: entry.offset }));
  socket.onmessage = (message) => {
    if (typeof message.data === "string") control(entry, message.data);
    else received(entry, message.data);
  };
  socket.onclose = (closed) => {
    if (entry.stopped || closed.code === TAKEN_OVER || REFUSED.includes(closed.code)) return;
    entry.timer = window.setTimeout(() => connect(entry), RETRY_MS);
  };
}

async function create(id: string, workspace: string): Promise<Entry> {
  const [{ Terminal }, { FitAddon }, { WebLinksAddon }, { Unicode11Addon }] = await addons();
  const fonts = getComputedStyle(document.documentElement).getPropertyValue("--f-mono");
  const windowsBuild = configs.get(workspace)?.windowsBuild;
  const term = new Terminal({
    allowProposedApi: true,
    cursorBlink: true,
    fontFamily: fonts,
    fontSize: 13,
    lineHeight: 1.2,
    scrollback: SCROLLBACK,
    theme: theme(),
    windowsPty: windowsBuild ? { backend: "conpty", buildNumber: windowsBuild } : undefined,
  });
  const fitter = new FitAddon();
  term.loadAddon(fitter);
  term.loadAddon(new WebLinksAddon());
  term.loadAddon(new Unicode11Addon());
  term.unicode.activeVersion = "11";

  const host = document.createElement("div");
  host.className = "ide-xterm";
  const entry: Entry = {
    id, workspace, term, fit: fitter, host, socket: null, offset: 0, unacked: 0, replay: 0, muted: false, stopped: false, rendered: false,
  };

  const send = (bytes: Uint8Array) => {
    if (!entry.muted && entry.socket?.readyState === WebSocket.OPEN) entry.socket.send(bytes);
  };
  term.onData((data) => send(encoder.encode(data)));
  term.onBinary((data) => send(Uint8Array.from(data, (c) => c.charCodeAt(0))));
  term.onResize(() => sendSize(entry));
  // As in Windows Terminal: Ctrl+C copies a selection and otherwise interrupts; Ctrl+V pastes natively.
  term.attachCustomKeyEventHandler((event) => {
    if (event.type !== "keydown" || !event.ctrlKey || event.altKey || event.metaKey) return true;
    if (event.key === "c" && term.hasSelection()) {
      void navigator.clipboard?.writeText(term.getSelection());
      term.clearSelection();
      return false;
    }
    if (event.key === "v" || event.key === "`") return false;
    return true;
  });
  connect(entry);
  return entry;
}

const open = (id: string, workspace: string) => {
  let entry = entries.get(id);
  if (!entry) {
    entry = create(id, workspace);
    entries.set(id, entry);
  }
  return entry;
};

async function render(entry: Entry) {
  entry.term.open(entry.host);
  entry.rendered = true;
  try {
    const { WebglAddon } = await import("@xterm/addon-webgl");
    const gl = new WebglAddon();
    gl.onContextLoss(() => gl.dispose());
    entry.term.loadAddon(gl);
  } catch {
    // The DOM renderer stays in charge when WebGL is not available.
  }
}

/** Puts the terminal in `slot` and returns what undoes that. The terminal itself keeps running. */
export function mount(id: string, workspace: string, slot: HTMLElement, focus: boolean) {
  let gone = false;
  let observer: ResizeObserver | null = null;
  let frame = 0;

  void open(id, workspace).then(async (entry) => {
    if (gone) return;
    slot.appendChild(entry.host);
    if (!entry.rendered) await render(entry);
    if (gone) return;
    observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => fit(entry));
    });
    observer.observe(slot);
    fit(entry);
    entry.term.refresh(0, entry.term.rows - 1);
    if (focus) entry.term.focus();
  });

  return () => {
    gone = true;
    observer?.disconnect();
    cancelAnimationFrame(frame);
    void entries.get(id)?.then((entry) => {
      if (entry.host.parentElement === slot) entry.host.remove();
    });
  };
}

export async function focusTerminal(id: string) {
  (await entries.get(id))?.term.focus();
}

export async function writeNote(id: string, text: string) {
  (await entries.get(id))?.term.write(`\r\n\x1b[90m${text}\x1b[0m\r\n`);
}

export async function dispose(id: string) {
  const pending = entries.get(id);
  entries.delete(id);
  const entry = await pending;
  if (!entry) return;
  entry.stopped = true;
  window.clearTimeout(entry.timer);
  entry.socket?.close();
  entry.term.dispose();
  entry.host.remove();
}

/** Only this folder's terminals: the other tabs keep theirs. */
export async function disposeAll(workspace: string) {
  const held = await Promise.all([...entries.values()]);
  await Promise.all(held.filter((entry) => entry.workspace === workspace).map((entry) => dispose(entry.id)));
}
