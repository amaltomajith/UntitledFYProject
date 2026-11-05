"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { Group, Panel, Separator, useDefaultLayout, usePanelRef, type LayoutStorage } from "react-resizable-panels";
import { Command } from "@/components/Command";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, readToken, useIde, type IdeState, type Mode, type IdeClient } from "@/lib/ide";
import { BottomPanel, type BottomTab } from "./BottomPanel";
import { MemoryGraph } from "./MemoryGraph";
import { LiveLine, LiveView } from "./LiveView";
import { Chat } from "./Chat";
import { Codicon } from "./Codicon";
import { FileIcon } from "./FileIcon";
import { FileTree } from "./FileTree";
import { FolderTabs, type FolderRow, type FolderStatus } from "./FolderTabs";
import { Setup } from "./Setup";
import { ActivityBar, GitView, SearchView, TestsView, type SideView } from "./SideViews";
import { disposeAll } from "./terminal/store";
import { dropInto, shellIcon, TerminalSlot } from "./terminal/TerminalPanel";
import { DRAG_TYPE, useTerminals } from "./terminal/useTerminals";
import { SessionPicker } from "./SessionPicker";
import { useCommands } from "./useCommands";

const Loading = () => <div className="ide-pane__note">{IDE.editor.loading}</div>;
const CodeEditor = dynamic(() => import("./CodeEditor"), { ssr: false, loading: Loading });
const DiffEditor = dynamic(() => import("./CodeEditor").then((m) => m.DiffEditor), { ssr: false, loading: Loading });

type FileTab = { kind: "file"; path: string; content: string | null; saved: string; reason?: string };
type Tab = FileTab | { kind: "changes" } | { kind: "preview" } | { kind: "graph" } | { kind: "live" } | { kind: "terminal"; id: string };
type Changes = { request: string | null; files: { path: string; before: string | null; after: string | null }[] };
type FileReply = { path: string; content: string | null; reason?: string };

type Listing = { folders: FolderRow[]; saved: string[] };

const SIDE_MIN = 170;
const RETRY_MS = 1500;
const FOLDER_KEY = "pcode-ide-folder";
const tabId = (tab: Tab) => (tab.kind === "file" ? tab.path : tab.kind === "terminal" ? `terminal:${tab.id}` : tab.kind);

// Saved panel sizes are a convenience; a blocked localStorage just means the defaults.
const layouts: LayoutStorage = {
  getItem: (key) => {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  setItem: (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch {
      // Nothing to do: the layout is simply not remembered.
    }
  },
};

/** `C:\work\app>` on Windows, `app $` elsewhere. A deep path keeps its drive and last folder. */
function terminalPrompt(project: string) {
  const parts = project.split(/[\\/]/).filter(Boolean);
  const leaf = parts[parts.length - 1] ?? project;
  if (!/^[A-Za-z]:/.test(project)) return `${leaf} $`;
  return `${project.length > 36 ? `${parts[0]}\\...\\${leaf}` : project}>`;
}

function ChangesPane({ client, tick }: { client: IdeClient; tick: number }) {
  const [changes, setChanges] = useState<Changes | null>(null);
  const [picked, setPicked] = useState(0);

  useEffect(() => {
    ideCall<Changes>(client, "changes").then(setChanges, () => setChanges({ request: null, files: [] }));
    setPicked(0);
  }, [client, tick]);

  if (!changes) return <Loading />;
  if (!changes.files.length) return <div className="ide-pane__note">{IDE.editor.noChanges}</div>;
  const file = changes.files[Math.min(picked, changes.files.length - 1)];
  return (
    <div className="ide-changes">
      <ul aria-label={IDE.editor.changedFiles}>
        {changes.files.map((f, i) => (
          <li key={f.path}>
            <button type="button" aria-current={f.path === file.path ? "true" : undefined} onClick={() => setPicked(i)}>
              {f.path}
            </button>
          </li>
        ))}
      </ul>
      <DiffEditor path={file.path} before={file.before ?? ""} after={file.after ?? ""} />
    </div>
  );
}

type WorkspaceProps = {
  client: IdeClient;
  active: boolean;
  onStatus: (status: FolderStatus) => void;
  onGone: () => void;
};

function Workspace({ client, active: shown, onStatus, onGone }: WorkspaceProps) {
  const { view, state, setState, link, send, refresh, local } = useIde(client);
  const [files, setFiles] = useState<string[]>([]);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [panelError, setPanelError] = useState<string | null>(null);
  const [side, setSide] = useState<SideView>("files");
  const [sideOpen, setSideOpen] = useState(true);
  const [bottomOpen, setBottomOpen] = useState(true);
  const [maximised, setMaximised] = useState(false);
  const [bottomTab, setBottomTab] = useState<BottomTab>("terminal");
  const [reveal, setReveal] = useState<{ path: string; line: number; end?: number } | null>(null);
  const [jump, setJump] = useState<{ turn: number } | null>(null);
  const sidePanel = usePanelRef();
  const bottomPanel = usePanelRef();
  const editorPanel = usePanelRef();
  const mainLayout = useDefaultLayout({ id: "ide-main", storage: layouts });
  const centreLayout = useDefaultLayout({ id: "ide-centre", storage: layouts });
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const project = state?.project ?? null;
  const shownRun = useRef(0);           // the request whose Live tab has opened, or that the user moved away from

  const show = (tab: Tab) => {
    setTabs((all) => (all.some((t) => tabId(t) === tabId(tab)) ? all : [...all, tab]));
    setActive(tabId(tab));
  };

  const dropTab = (id: string) => {
    const left = tabsRef.current.filter((t) => tabId(t) !== id);
    setTabs(left);
    setActive((now) => (now === id ? (left.length ? tabId(left[left.length - 1]) : null) : now));
  };

  const terminals = useTerminals({
    client,
    project,
    wanted: shown && bottomOpen && bottomTab === "terminal",
    onDock: (id) => show({ kind: "terminal", id }),
    onRemoved: (id) => dropTab(`terminal:${id}`),
  });

  const toggleBottom = useCallback(() => {
    const panel = bottomPanel.current;
    if (panel?.isCollapsed()) panel.expand();
    else panel?.collapse();
  }, [bottomPanel]);

  const toggleMaximised = () => {
    const panel = editorPanel.current;
    if (panel?.isCollapsed()) panel.expand();
    else panel?.collapse();
  };

  useEffect(() => {
    if (!shown) return;
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.key !== "`") return;
      e.preventDefault();
      if (e.shiftKey) {
        if (bottomPanel.current?.isCollapsed()) bottomPanel.current.expand();
        setBottomTab("terminal");
        void terminals.create();
      } else {
        toggleBottom();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, toggleBottom, terminals.create, bottomPanel]);

  const pickView = (next: SideView) => {
    const panel = sidePanel.current;
    if (next === side && sideOpen) panel?.collapse();
    else if (!sideOpen) panel?.expand();
    setSide(next);
  };

  const loadTree = useCallback(async () => {
    try {
      setFiles((await ideCall<{ files: string[] }>(client, "tree")).files);
    } catch {
      setFiles([]);
    }
  }, [client]);

  const reloadTabs = useCallback(async () => {
    for (const tab of tabsRef.current) {
      if (tab.kind !== "file" || tab.content !== tab.saved) continue;
      try {
        const reply = await ideCall<FileReply>(client, `file?path=${encodeURIComponent(tab.path)}`);
        setTabs((all) =>
          all.map((t) =>
            t.kind === "file" && t.path === tab.path && t.content === t.saved
              ? { ...t, content: reply.content, saved: reply.content ?? "", reason: reply.reason }
              : t,
          ),
        );
      } catch (e) {
        if (e instanceof IdeError && e.status === 404) {
          setTabs((all) => all.filter((t) => tabId(t) !== tab.path));
        }
      }
    }
  }, [client]);

  useEffect(() => {
    if (!project) return;
    void loadTree();
    void reloadTabs();
  }, [project, view.fileTick, loadTree, reloadTabs]);

  useEffect(() => {
    if (!view.live.writing || shownRun.current === view.live.id) return;
    shownRun.current = view.live.id;
    setTabs((all) => (all.some((t) => t.kind === "live") ? all : [...all, { kind: "live" }]));
    setActive("live");
  }, [view.live.writing, view.live.id]);

  const asking = Boolean(state?.pending_plan) || view.items.some((item) => item.kind === "approval" && item.answer === null);
  const doing = asking ? "needs" : !view.busy ? null : view.live.phase === "queued" ? "waiting" : "working";
  const unsaved = tabs.filter((t) => t.kind === "file" && t.content !== t.saved).length;
  const isPrivate = Boolean(state?.private.on);

  useEffect(() => {
    onStatus({ project, busy: view.busy, doing, private: isPrivate, unsaved });
  }, [project, view.busy, doing, isPrivate, unsaved, onStatus]);

  useEffect(() => {
    if (link === "gone") onGone();
  }, [link, onGone]);

  useEffect(() => {
    if (!view.preview) return;
    setTabs((all) => (all.some((t) => t.kind === "preview") ? all : [...all, { kind: "preview" }]));
    setActive("preview");
  }, [view.preview]);

  const openFile = async (path: string, line?: number, end?: number) => {
    shownRun.current = view.live.id;
    if (line) setReveal({ path, line, end });
    if (tabs.some((t) => tabId(t) === path)) {
      setActive(path);
      return;
    }
    try {
      const reply = await ideCall<FileReply>(client, `file?path=${encodeURIComponent(path)}`);
      show({ kind: "file", path, content: reply.content, saved: reply.content ?? "", reason: reply.reason });
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const closeTab = (tab: Tab) => {
    if (tab.kind === "terminal") void terminals.kill(tab.id);
    else dropTab(tabId(tab));
  };

  const save = async (tab: FileTab) => {
    if (tab.content === null || tab.content === tab.saved) return;
    try {
      await ideCall(client, "file", { method: "PUT", body: JSON.stringify({ path: tab.path, content: tab.content }) });
      setTabs((all) => all.map((t) => (t.kind === "file" && t.path === tab.path ? { ...t, saved: tab.content ?? "" } : t)));
      setPanelError(null);
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const undo = async () => {
    try {
      await post(client, "undo");
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const jumpTo = async (session: string, turn: number) => {
    if (session !== state?.session_id) await commands.resume(session);
    setJump({ turn });
  };

  const applyFix = async (runId: string) => {
    try {
      await post(client, "fix/apply", { run_id: runId });
      await refresh();
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const runCommand = async (command: string) => {
    setPanelError(null);
    setBottomTab("output");
    if (bottomPanel.current?.isCollapsed()) bottomPanel.current.expand();
    try {
      await post(client, "command", { command });
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const setMode = async (mode: Mode) => setState(await post(client, "mode", { mode }));
  const setPrivate = (on: boolean) => void commands.run(`/private ${on ? "on" : "off"}`);

  const commands = useCommands({
    client,
    view,
    state: state as IdeState,
    setState,
    send,
    local,
    refresh,
    setMode,
    showChanges: () => show({ kind: "changes" }),
    showPreview: () => show({ kind: "preview" }),
    pickFolder: () => setPicking(true),
  });

  if (link === "denied") {
    return <NoToken />;
  }
  if (!state) {
    return <div className="ide-setup muted">{link === "lost" ? IDE.lost : IDE.connecting}</div>;
  }
  if (!project || picking) {
    return (
      <Setup
        client={client}
        state={state}
        onChange={(next) => {
          setState(next);
          if (next.project) {
            setPicking(false);
            setTabs([]);
            setActive(null);
          }
        }}
        onClose={picking ? () => setPicking(false) : undefined}
      />
    );
  }

  const tab = tabs.find((t) => tabId(t) === active) ?? null;
  const locked = view.busy;
  const rowOf = (id: string) => terminals.rows.find((row) => row.id === id);
  const tabName = (t: Tab) => {
    if (t.kind === "file") return t.path.split("/").pop() ?? t.path;
    if (t.kind === "terminal") return rowOf(t.id)?.name ?? IDE.editor.terminal;
    return IDE.editor[t.kind];
  };
  const dragging = terminals.dragging;
  const canDock = dragging !== null && !terminals.docked.includes(dragging);

  return (
    <div className="ide">
      <div className="ide__small">
        <p>{IDE.small}</p>
        <Command label={IDE.smallCommand} cmd="pcode" />
      </div>
      <div className="ide__body">
        <ActivityBar view={side} open={sideOpen} onPick={pickView} onGraph={() => show({ kind: "graph" })} />
        <Group
          orientation="horizontal"
          id={`ide-main-${client.workspace}`}
          className="ide__panels"
          defaultLayout={mainLayout.defaultLayout}
          onLayoutChanged={mainLayout.onLayoutChanged}
        >
          <Panel
            id="side"
            panelRef={sidePanel}
            defaultSize={240}
            minSize={SIDE_MIN}
            maxSize="45%"
            collapsible
            groupResizeBehavior="preserve-pixel-size"
            onResize={(size) => setSideOpen(size.inPixels > 0)}
          >
            <aside className="ide__left" aria-label={IDE.activity[side]}>
              <div className="ide__head">
                <span>{IDE.activity[side]}</span>
                {side === "files" ? (
                  <button type="button" aria-label={IDE.tree.refresh} title={IDE.tree.refresh} onClick={() => void loadTree()}>
                    <Codicon name="refresh" />
                  </button>
                ) : null}
              </div>
              {side === "files" ? (
                <FileTree
                  files={files}
                  changed={view.changed}
                  active={tab?.kind === "file" ? tab.path : null}
                  onOpen={(path) => void openFile(path)}
                />
              ) : null}
              {side === "search" ? <SearchView client={client} onOpen={(path, line) => void openFile(path, line)} /> : null}
              {side === "git" ? <GitView client={client} tick={view.fileTick} onOpen={(path) => void openFile(path)} /> : null}
              {side === "tests" ? <TestsView checks={view.checks} /> : null}
            </aside>
          </Panel>
          <Separator className="ide-sash" />
          <Panel id="centre" minSize={320}>
            <Group
              orientation="vertical"
              id={`ide-centre-${client.workspace}`}
              defaultLayout={centreLayout.defaultLayout}
              onLayoutChanged={centreLayout.onLayoutChanged}
            >
              <Panel
                id="editor"
                panelRef={editorPanel}
                minSize={120}
                collapsible
                onResize={(size) => setMaximised(size.inPixels === 0)}
              >
                <main className="ide-main">
                  <div className="ide-tabs" role="tablist" aria-label={IDE.editor.tabs}>
                    {tabs.map((t) => {
                      const id = tabId(t);
                      const dirty = t.kind === "file" && t.content !== t.saved;
                      const name = tabName(t);
                      return (
                        <span
                          key={id}
                          className="ide-tab-wrap"
                          data-active={id === active}
                          draggable={t.kind === "terminal"}
                          onDragStart={(e) => {
                            if (t.kind !== "terminal") return;
                            e.dataTransfer.setData(DRAG_TYPE, t.id);
                            e.dataTransfer.effectAllowed = "move";
                            terminals.setDragging(t.id);
                          }}
                          onDragEnd={() => terminals.setDragging(null)}
                        >
                          <button
                            type="button"
                            role="tab"
                            aria-selected={id === active}
                            className="ide-tab"
                            onClick={() => {
                              shownRun.current = view.live.id;
                              setActive(id);
                            }}
                          >
                            {t.kind === "file" ? <FileIcon name={name} /> : null}
                            {t.kind === "terminal" ? <Codicon name={shellIcon(rowOf(t.id)?.icon ?? "terminal")} /> : null}
                            {t.kind === "graph" ? <Codicon name="type-hierarchy" /> : null}
                            {t.kind === "live" ? <Codicon name="pulse" /> : null}
                            {name}
                            {dirty ? <span className="ide-tab__dirty" title={IDE.editor.unsaved} aria-label={IDE.editor.unsaved}>●</span> : null}
                          </button>
                          {t.kind === "terminal" ? (
                            <button
                              type="button"
                              className="ide-tab__close"
                              aria-label={IDE.editor.toPanel(name)}
                              title={IDE.editor.toPanel(name)}
                              onClick={() => terminals.undock(t.id)}
                            >
                              <Codicon name="arrow-down" />
                            </button>
                          ) : null}
                          <button type="button" className="ide-tab__close" aria-label={IDE.editor.close(name)} onClick={() => closeTab(t)}>
                            <Codicon name="close" />
                          </button>
                        </span>
                      );
                    })}
                  </div>
                  {locked && tab?.kind === "file" ? <p className="ide-lock">{IDE.editor.locked}</p> : null}
                  <div className="ide-pane">
                    {tab === null ? <div className="ide-pane__note">{IDE.editor.empty}</div> : null}
                    {tab?.kind === "changes" ? <ChangesPane client={client} tick={view.fileTick} /> : null}
                    {tab?.kind === "graph" ? (
                      <MemoryGraph
                        client={client}
                        active={shown}
                        tick={view.graphTick}
                        touched={view.touched}
                        onOpen={(path, line, end) => void openFile(path, line, end)}
                        onJump={jumpTo}
                      />
                    ) : null}
                    {tab?.kind === "live" ? <LiveView live={view.live} onOpen={(path, line, end) => void openFile(path, line, end)} /> : null}
                    {tab?.kind === "preview" && view.preview ? (
                      <div className="ide-preview">
                        <p>
                          <a href={view.preview} target="_blank" rel="noreferrer">
                            {view.preview}
                          </a>
                        </p>
                        <iframe title={IDE.editor.preview} src={view.preview} sandbox="allow-scripts allow-forms allow-same-origin allow-modals" />
                      </div>
                    ) : null}
                    {tab?.kind === "preview" && !view.preview ? <div className="ide-pane__note">{state.private.on ? IDE.private.noPreview : IDE.editor.noPreview}</div> : null}
                    {tab?.kind === "file" && tab.content === null ? (
                      <div className="ide-pane__note">{tab.reason === "binary" ? IDE.editor.binary : IDE.editor.tooLarge}</div>
                    ) : null}
                    {tab?.kind === "file" && tab.content !== null ? (
                      <CodeEditor
                        path={tab.path}
                        value={tab.content}
                        readOnly={locked}
                        reveal={reveal?.path === tab.path ? reveal : null}
                        onChange={(content) =>
                          setTabs((all) => all.map((t) => (t.kind === "file" && t.path === tab.path ? { ...t, content } : t)))
                        }
                        onSave={() => void save(tabsRef.current.find((t) => tabId(t) === tab.path) as FileTab)}
                      />
                    ) : null}
                    {tab?.kind === "terminal" ? <TerminalSlot key={tab.id} id={tab.id} workspace={client.workspace} focus /> : null}
                    {canDock ? (
                      <div className="ide-drop" {...dropInto(terminals.dock)}>
                        <span>{IDE.editor.dropEditor}</span>
                      </div>
                    ) : null}
                  </div>
                </main>
              </Panel>
              <Separator className="ide-sash" />
              <Panel
                id="bottom"
                panelRef={bottomPanel}
                defaultSize="34%"
                minSize={120}
                collapsible
                onResize={(size) => setBottomOpen(size.inPixels > 0)}
              >
                <BottomPanel
                  view={view}
                  terminals={terminals}
                  tab={bottomTab}
                  onTab={setBottomTab}
                  error={panelError}
                  prompt={terminalPrompt(project)}
                  maximised={maximised}
                  onMaximise={toggleMaximised}
                  onClose={toggleBottom}
                />
              </Panel>
            </Group>
          </Panel>
          <Separator className="ide-sash" />
          <Panel id="chat" defaultSize={380} minSize={300} maxSize="50%" groupResizeBehavior="preserve-pixel-size">
            <Chat
              active={shown}
              header={
                <SessionPicker
                  client={client}
                  shown={shown}
                  state={state}
                  busy={view.busy}
                  onState={setState}
                  onResume={commands.resume}
                  onNew={() => commands.run("/clear")}
                  onGraph={() => show({ kind: "graph" })}
                />
              }
              status={<LiveLine live={view.live} onOpen={() => show({ kind: "live" })} />}
              jump={jump}
              onJumped={() => setJump(null)}
              view={view}
              model={state.models.quality ?? state.provider ?? ""}
              canUndo={state.can_undo}
              claim={state.private.on ? [state.private.claim, state.private.warning].filter(Boolean).join(" ") : null}
              canApprove={state.pending_plan}
              onApprove={() => void commands.run("/approve")}
              onSend={(message, mode) => send({ type: "send", message, mode })}
              onSlash={(line) => void commands.run(line)}
              onShell={(command) => void runCommand(command)}
              onCancel={() => send({ type: "cancel" })}
              onAnswer={(id, approved) => send({ type: "answer", id, approved })}
              onUndo={() => void undo().then(refresh)}
              onReview={() => show({ kind: "changes" })}
              onApply={(runId) => void applyFix(runId)}
              onResume={(id) => void commands.resume(id).catch((e) => setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed))}
            />
          </Panel>
        </Group>
      </div>
      <footer className="ide-status" aria-label={IDE.status.label}>
        <span className="ide-status__link" data-state={link} role="status">
          <Codicon name={link === "open" ? "pass" : "warning"} />
          {link === "open" ? IDE.status.connected : IDE.lost}
        </span>
        <span className="ide__spacer" />
        <button type="button" title={IDE.status.terminalHint} aria-pressed={bottomOpen} onClick={toggleBottom}>
          <Codicon name="terminal" />
          {IDE.status.terminal}
        </button>
        <button
          type="button"
          className="ide-status__private"
          aria-pressed={state.private.on}
          title={state.private.on ? IDE.private.hintOn : IDE.private.hintOff}
          disabled={view.busy}
          onClick={() => setPrivate(!state.private.on)}
        >
          <Codicon name="lock" />
          {IDE.private.label}
        </button>
        <span className="ide__tokens" title={state.private.on ? IDE.tokensHintLocal : IDE.tokensHint}>
          {IDE.tokens(view.totalTokens || state.tokens)}
        </span>
        <div className="ide__mode" role="group" aria-label={IDE.mode.label}>
          {(["plan", "ask", "auto"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={state.mode === mode}
              title={IDE.mode.hints[mode]}
              disabled={view.busy}
              onClick={() => void setMode(mode)}
            >
              {IDE.mode[mode]}
            </button>
          ))}
        </div>
      </footer>
    </div>
  );
}

function NoToken() {
  return (
    <main className="ide-setup">
      <section className="ide-setup__card panel">
        <h1>{IDE.noToken.title}</h1>
        <p className="muted">{IDE.noToken.body}</p>
        <ol className="cmds cmds--tight">
          {IDE.noToken.commands.map((c) => (
            <Command key={c.label} {...c} />
          ))}
        </ol>
        <Link href="/" className="btn btn--ghost btn--sm">
          {IDE.noToken.home}
        </Link>
      </section>
    </main>
  );
}

function remembered() {
  try {
    return sessionStorage.getItem(FOLDER_KEY);
  } catch {
    return null;
  }
}

function remember(id: string) {
  try {
    sessionStorage.setItem(FOLDER_KEY, id);
  } catch {
    // Coming back to this folder after a reload is a convenience; the first one is fine too.
  }
}

type FolderProps = {
  token: string;
  id: string;
  active: boolean;
  onStatus: (id: string, status: FolderStatus) => void;
  onGone: () => void;
};

// A hidden folder stays laid out, because the panels read size 0 as collapsed and xterm cannot measure it.
function Folder({ token, id, active, onStatus, onGone }: FolderProps) {
  const client = useMemo(() => ({ token, workspace: id }), [token, id]);
  const report = useCallback((status: FolderStatus) => onStatus(id, status), [id, onStatus]);
  return (
    <div id={`folder-${id}`} className="ide-folder" role="tabpanel" aria-labelledby={`folder-tab-${id}`} data-active={active}>
      <Workspace client={client} active={active} onStatus={report} onGone={onGone} />
    </div>
  );
}

function Folders({ token }: { token: string }) {
  const server = useMemo(() => ({ token, workspace: "" }), [token]);
  const [list, setList] = useState<Listing | null>(null);
  const [active, setActive] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, FolderStatus>>({});
  const [reopened, setReopened] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);

  const load = useCallback(async () => {
    const found = await ideCall<Listing>(server, "workspaces");
    setList(found);
    setError(null);
    setActive((now) => {
      const wanted = [now, remembered()].find((id) => found.folders.some((folder) => folder.id === id));
      return wanted ?? found.folders[0].id;
    });
  }, [server]);

  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const attempt = async () => {
      try {
        await load();
      } catch (e) {
        if (stop) return;
        if (e instanceof IdeError && e.status === 401) setDenied(true);
        else {
          setError(IDE.lost);
          timer = window.setTimeout(attempt, RETRY_MS);
        }
      }
    };
    void attempt();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [load]);

  useEffect(() => {
    if (active) remember(active);
  }, [active]);

  const report = useCallback((id: string, next: FolderStatus) => setStatus((all) => ({ ...all, [id]: next })), []);
  const projectOf = (folder: FolderRow) => status[folder.id]?.project ?? folder.project;

  const act = async (work: () => Promise<void>) => {
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const close = (id: string) =>
    act(async () => {
      const left = await ideCall<Listing>(server, `workspaces/${id}`, { method: "DELETE" });
      const at = list?.folders.findIndex((folder) => folder.id === id) ?? 0;
      await disposeAll(id);
      setList(left);
      setStatus(({ [id]: _closed, ...rest }) => rest);
      // Closing the last folder leaves an empty tab under the same id, so it starts again.
      if (left.folders.some((folder) => folder.id === id)) setReopened((all) => ({ ...all, [id]: (all[id] ?? 0) + 1 }));
      setActive((now) => (now === id ? left.folders[Math.min(at, left.folders.length - 1)].id : now));
    });

  const opened = (reply: Listing & { id: string }) => {
    setList(reply);
    setActive(reply.id);
  };

  const add = () =>
    act(async () => {
      const empty = list?.folders.find((folder) => !projectOf(folder));
      if (empty) setActive(empty.id);
      else opened(await post<Listing & { id: string }>(server, "workspaces", {}));
    });

  const reopen = (path: string) =>
    act(async () => {
      const empty = list?.folders.find((folder) => !projectOf(folder));
      opened(await post<Listing & { id: string }>(server, "workspaces", { path }));
      if (empty) await close(empty.id);
    });

  const forget = (path: string) => act(async () => setList(await post<Listing>(server, "workspaces/forget", { path })));

  if (denied) return <NoToken />;
  if (!list) return <div className="ide-setup muted">{error ?? IDE.connecting}</div>;

  return (
    <div className="ide-shell">
      <header className="ide__bar">
        <Link href="/" className="wordmark" aria-label={IDE.home}>
          <span className="wordmark__mark" aria-hidden="true" />
          <span className="ide__brand">CompactCode</span>
        </Link>
        <FolderTabs
          folders={list.folders}
          saved={list.saved}
          active={active}
          status={status}
          error={error}
          onPick={setActive}
          onAdd={() => void add()}
          onClose={(id) => void close(id)}
          onReopen={(path) => void reopen(path)}
          onForget={(path) => void forget(path)}
        />
      </header>
      <div className="ide-folders">
        {list.folders.map((folder) => (
          <Folder
            key={`${folder.id}:${reopened[folder.id] ?? 0}`}
            token={token}
            id={folder.id}
            active={folder.id === active}
            onStatus={report}
            onGone={() => void load().catch(() => setError(IDE.lost))}
          />
        ))}
      </div>
    </div>
  );
}

export function Ide() {
  const [token, setToken] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    setToken(readToken());
  }, []);

  if (token === undefined) return <div className="ide-setup muted">{IDE.connecting}</div>;
  if (token === null) return <NoToken />;
  return <Folders token={token} />;
}
