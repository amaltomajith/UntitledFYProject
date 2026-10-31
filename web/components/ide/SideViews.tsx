"use client";

import { useCallback, useEffect, useState } from "react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, type View } from "@/lib/ide";
import { Codicon } from "./Codicon";
import { FileIcon } from "./FileIcon";

const DEBOUNCE_MS = 250;

export type SideView = "files" | "search" | "git" | "tests";

export const SIDE_VIEWS: { id: SideView; icon: string }[] = [
  { id: "files", icon: "files" },
  { id: "search", icon: "search" },
  { id: "git", icon: "source-control" },
  { id: "tests", icon: "beaker" },
];

export function ActivityBar({ view, open, onPick }: { view: SideView; open: boolean; onPick: (view: SideView) => void }) {
  return (
    <nav className="ide-activity" aria-label={IDE.activity.label}>
      {SIDE_VIEWS.map(({ id, icon }) => (
        <button
          key={id}
          type="button"
          aria-label={IDE.activity[id]}
          aria-pressed={open && view === id}
          data-tip={IDE.activity[id]}
          onClick={() => onPick(id)}
        >
          <Codicon name={icon} />
        </button>
      ))}
    </nav>
  );
}

type Hit = { path: string; line: number; text: string };
const leaf = (path: string) => path.split("/").pop() ?? path;

export function SearchView({ token, onOpen }: { token: string; onOpen: (path: string, line: number) => void }) {
  const S = IDE.search;
  const [text, setText] = useState("");
  const [reply, setReply] = useState<{ q: string; hits: Hit[]; truncated: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const q = text.trim();

  useEffect(() => {
    if (!q) {
      setReply(null);
      return;
    }
    let stale = false;
    const timer = window.setTimeout(async () => {
      try {
        const found = await ideCall<{ hits: Hit[]; truncated: boolean }>(token, `search?q=${encodeURIComponent(q)}`);
        if (stale) return;
        setReply({ q, ...found });
        setError(null);
      } catch (e) {
        if (!stale) setError(e instanceof IdeError ? e.message : IDE.setup.failed);
      }
    }, DEBOUNCE_MS);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [q, token]);

  const files = [...new Set(reply?.hits.map((hit) => hit.path))];

  return (
    <div className="ide-side">
      <input
        className="ide-side__input"
        value={text}
        aria-label={S.label}
        placeholder={S.placeholder}
        spellCheck={false}
        onChange={(e) => setText(e.target.value)}
      />
      {error ? <p className="ide-side__note" role="alert">{error}</p> : null}
      {!error && !q ? <p className="ide-side__note">{S.empty}</p> : null}
      {!error && q && reply?.q !== q ? <p className="ide-side__note">{S.searching}</p> : null}
      {!error && reply?.q === q && !reply.hits.length ? <p className="ide-side__note">{S.none}</p> : null}
      {reply?.q === q && reply.hits.length ? (
        <div className="ide-side__list">
          <p className="ide-side__note">{S.count(reply.hits.length, files.length)}</p>
          {files.map((path) => (
            <section key={path}>
              <h3 title={path}>
                <FileIcon name={leaf(path)} />
                <span>{path}</span>
              </h3>
              <ul>
                {reply.hits
                  .filter((hit) => hit.path === path)
                  .map((hit) => (
                    <li key={hit.line}>
                      <button type="button" title={hit.text} onClick={() => onOpen(hit.path, hit.line)}>
                        <span>{hit.line}</span>
                        <code>{hit.text}</code>
                      </button>
                    </li>
                  ))}
              </ul>
            </section>
          ))}
          {reply.truncated ? <p className="ide-side__note">{S.truncated}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

type GitReply = { repo: boolean; branch: string; files: { path: string; status: string }[] };

export function GitView({ token, tick, onOpen }: { token: string; tick: number; onOpen: (path: string) => void }) {
  const G = IDE.git;
  const [git, setGit] = useState<GitReply | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setGit(await ideCall<GitReply>(token, "git"));
      setError(null);
    } catch (e) {
      setError(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load, tick]);

  return (
    <div className="ide-side">
      <div className="ide-side__bar">
        <span title={git?.branch}>
          {git?.branch ? <Codicon name="git-branch" /> : null}
          {git?.branch}
        </span>
        <button type="button" aria-label={G.refresh} title={G.refresh} onClick={() => void load()}>
          <Codicon name="refresh" />
        </button>
      </div>
      {error ? <p className="ide-side__note" role="alert">{error}</p> : null}
      {!error && !git ? <p className="ide-side__note">{IDE.editor.loading}</p> : null}
      {!error && git && !git.repo ? <p className="ide-side__note">{G.notRepo}</p> : null}
      {!error && git?.repo && !git.files.length ? <p className="ide-side__note">{G.clean}</p> : null}
      {git?.files.length ? (
        <ul className="ide-side__list">
          {git.files.map((file) => (
            <li key={file.path}>
              <button
                type="button"
                className="ide-tree__row"
                title={file.path}
                disabled={file.status === "D"}
                onClick={() => onOpen(file.path)}
              >
                <FileIcon name={leaf(file.path)} />
                <span>{file.path}</span>
                <span className="ide-tree__mark" title={G.status[file.status]}>
                  {file.status}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function TestsView({ checks }: { checks: View["checks"] }) {
  if (!checks.length) return <p className="ide-side__note">{IDE.testing.none}</p>;
  return (
    <ul className="ide-checks">
      {checks.map((check, i) => (
        <li key={i} data-ok={check.ok}>
          <Codicon name={check.ok ? "pass" : "error"} />
          <span>{check.summary}</span>
        </li>
      ))}
    </ul>
  );
}
