"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { ArrowClockwise, X } from "@phosphor-icons/react";
import { Command } from "@/components/Command";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, readToken, useIde } from "@/lib/ide";
import { BottomPanel } from "./BottomPanel";
import { Chat } from "./Chat";
import { FileTree } from "./FileTree";
import { Setup } from "./Setup";

const Loading = () => <div className="ide-pane__note">{IDE.editor.loading}</div>;
const CodeEditor = dynamic(() => import("./CodeEditor"), { ssr: false, loading: Loading });
const DiffEditor = dynamic(() => import("./CodeEditor").then((m) => m.DiffEditor), { ssr: false, loading: Loading });

type FileTab = { kind: "file"; path: string; content: string | null; saved: string; reason?: string };
type Tab = FileTab | { kind: "changes" } | { kind: "preview" };
type Changes = { request: string | null; files: { path: string; before: string | null; after: string | null }[] };
type FileReply = { path: string; content: string | null; reason?: string };

const tabId = (tab: Tab) => (tab.kind === "file" ? tab.path : tab.kind);
const tabName = (tab: Tab) => (tab.kind === "file" ? tab.path.split("/").pop() : IDE.editor[tab.kind]);

function ChangesPane({ token, tick }: { token: string; tick: number }) {
  const [changes, setChanges] = useState<Changes | null>(null);
  const [picked, setPicked] = useState(0);

  useEffect(() => {
    ideCall<Changes>(token, "changes").then(setChanges, () => setChanges({ request: null, files: [] }));
    setPicked(0);
  }, [token, tick]);

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

function Workspace({ token }: { token: string }) {
  const { view, state, setState, link, send, refresh } = useIde(token);
  const [files, setFiles] = useState<string[]>([]);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [panelError, setPanelError] = useState<string | null>(null);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;
  const project = state?.project ?? null;

  const loadTree = useCallback(async () => {
    try {
      setFiles((await ideCall<{ files: string[] }>(token, "tree")).files);
    } catch {
      setFiles([]);
    }
  }, [token]);

  const reloadTabs = useCallback(async () => {
    for (const tab of tabsRef.current) {
      if (tab.kind !== "file" || tab.content !== tab.saved) continue;
      try {
        const reply = await ideCall<FileReply>(token, `file?path=${encodeURIComponent(tab.path)}`);
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
  }, [token]);

  useEffect(() => {
    if (!project) return;
    void loadTree();
    void reloadTabs();
  }, [project, view.fileTick, loadTree, reloadTabs]);

  useEffect(() => {
    if (!view.preview) return;
    setTabs((all) => (all.some((t) => t.kind === "preview") ? all : [...all, { kind: "preview" }]));
    setActive("preview");
  }, [view.preview]);

  const show = (tab: Tab) => {
    setTabs((all) => (all.some((t) => tabId(t) === tabId(tab)) ? all : [...all, tab]));
    setActive(tabId(tab));
  };

  const openFile = async (path: string) => {
    if (tabs.some((t) => tabId(t) === path)) {
      setActive(path);
      return;
    }
    try {
      const reply = await ideCall<FileReply>(token, `file?path=${encodeURIComponent(path)}`);
      show({ kind: "file", path, content: reply.content, saved: reply.content ?? "", reason: reply.reason });
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const closeTab = (id: string) => {
    const left = tabs.filter((t) => tabId(t) !== id);
    setTabs(left);
    if (active === id) setActive(left.length ? tabId(left[left.length - 1]) : null);
  };

  const save = async (tab: FileTab) => {
    if (tab.content === null || tab.content === tab.saved) return;
    try {
      await ideCall(token, "file", { method: "PUT", body: JSON.stringify({ path: tab.path, content: tab.content }) });
      setTabs((all) => all.map((t) => (t.kind === "file" && t.path === tab.path ? { ...t, saved: tab.content ?? "" } : t)));
      setPanelError(null);
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const undo = async () => {
    try {
      await post(token, "undo");
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const applyFix = async (runId: string) => {
    try {
      await post(token, "fix/apply", { run_id: runId });
      await refresh();
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const runCommand = async (command: string) => {
    setPanelError(null);
    try {
      await post(token, "command", { command });
    } catch (e) {
      setPanelError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  const setMode = async (mode: "ask" | "auto") => setState(await post(token, "mode", { mode }));

  if (link === "denied") {
    return <NoToken />;
  }
  if (!state) {
    return <div className="ide-setup muted">{link === "lost" ? IDE.lost : IDE.connecting}</div>;
  }
  if (!project || picking) {
    return (
      <Setup
        token={token}
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

  return (
    <div className="ide">
      <header className="ide__bar">
        <Link href="/" className="wordmark" aria-label={IDE.home}>
          <span className="wordmark__mark" aria-hidden="true" />
          <span className="ide__brand">CompactCode</span>
        </Link>
        <button type="button" className="ide__project" title={project} onClick={() => setPicking(true)} disabled={view.busy}>
          {project.split(/[\\/]/).filter(Boolean).pop()}
        </button>
        <span className="ide__spacer" />
        {link === "lost" ? (
          <span className="ide__lost" role="status">
            {IDE.lost}
          </span>
        ) : null}
        <span className="ide__tokens" title={IDE.tokensHint}>
          {IDE.tokens(view.totalTokens || state.tokens)}
        </span>
        <div className="ide__mode" role="group" aria-label={IDE.mode.label}>
          {(["ask", "auto"] as const).map((mode) => (
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
      </header>
      <div className="ide__small">
        <p>{IDE.small}</p>
        <Command label={IDE.smallCommand} cmd="pcode" />
      </div>
      <div className="ide__body">
        <aside className="ide__left" aria-label={IDE.tree.label}>
          <div className="ide__head">
            <span>{IDE.tree.title}</span>
            <button type="button" aria-label={IDE.tree.refresh} onClick={() => void loadTree()}>
              <ArrowClockwise size={13} aria-hidden="true" />
            </button>
          </div>
          <FileTree
            files={files}
            changed={view.changed}
            active={tab?.kind === "file" ? tab.path : null}
            onOpen={(path) => void openFile(path)}
          />
        </aside>
        <div className="ide__centre">
          <main className="ide-main">
            <div className="ide-tabs" role="tablist" aria-label={IDE.editor.tabs}>
              {tabs.map((t) => {
                const id = tabId(t);
                const dirty = t.kind === "file" && t.content !== t.saved;
                return (
                  <span key={id} className="ide-tab-wrap" data-active={id === active}>
                    <button type="button" role="tab" aria-selected={id === active} className="ide-tab" onClick={() => setActive(id)}>
                      {tabName(t)}
                      {dirty ? <span className="ide-tab__dirty"> ({IDE.editor.unsaved})</span> : null}
                    </button>
                    <button type="button" className="ide-tab__close" aria-label={IDE.editor.close(String(tabName(t)))} onClick={() => closeTab(id)}>
                      <X size={11} aria-hidden="true" />
                    </button>
                  </span>
                );
              })}
            </div>
            {locked && tab?.kind === "file" ? <p className="ide-lock">{IDE.editor.locked}</p> : null}
            <div className="ide-pane">
              {tab === null ? <div className="ide-pane__note">{IDE.editor.empty}</div> : null}
              {tab?.kind === "changes" ? <ChangesPane token={token} tick={view.fileTick} /> : null}
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
              {tab?.kind === "preview" && !view.preview ? <div className="ide-pane__note">{IDE.editor.noPreview}</div> : null}
              {tab?.kind === "file" && tab.content === null ? (
                <div className="ide-pane__note">{tab.reason === "binary" ? IDE.editor.binary : IDE.editor.tooLarge}</div>
              ) : null}
              {tab?.kind === "file" && tab.content !== null ? (
                <CodeEditor
                  path={tab.path}
                  value={tab.content}
                  readOnly={locked}
                  onChange={(content) =>
                    setTabs((all) => all.map((t) => (t.kind === "file" && t.path === tab.path ? { ...t, content } : t)))
                  }
                  onSave={() => void save(tabsRef.current.find((t) => tabId(t) === tab.path) as FileTab)}
                />
              ) : null}
            </div>
          </main>
          <BottomPanel view={view} error={panelError} onCommand={(c) => void runCommand(c)} />
        </div>
        <Chat
          view={view}
          canUndo={state.can_undo}
          onSend={(message, mode) => send({ type: "send", message, mode })}
          onCancel={() => send({ type: "cancel" })}
          onAnswer={(id, approved) => send({ type: "answer", id, approved })}
          onUndo={() => void undo().then(refresh)}
          onReview={() => show({ kind: "changes" })}
          onApply={(runId) => void applyFix(runId)}
        />
      </div>
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

export function Ide() {
  const [token, setToken] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    setToken(readToken());
  }, []);

  if (token === undefined) return <div className="ide-setup muted">{IDE.connecting}</div>;
  if (token === null) return <NoToken />;
  return <Workspace token={token} />;
}
