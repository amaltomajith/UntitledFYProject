"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, type IdeClient } from "@/lib/ide";
import { Codicon } from "./Codicon";

const U = IDE.update;
const NOTIFY_KEY = "pcode-update-notify";
const LATER_KEY = "pcode-update-later";
const POLL_MS = 30 * 60 * 1000; // the server checks GitHub at most once a day; this only reads its cache
export const UPDATE_EVENT = "pcode:update-available";

export type UpdateState = {
  current: string;
  latest: string | null;
  available: boolean;
  notes: string;
  url: string;
  checked_at: number | null;
  error: string | null;
  auto: boolean;
  auto_blocked: "private" | "env" | null;
  kind: "source" | "uv-tool" | "pip";
  can_apply: boolean;
  blocked: "busy" | "source" | null;
};

// Remembered per browser; a blocked localStorage only means the defaults.
function read(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Nothing to do: the choice is simply not remembered.
  }
}

function useUpdateState(server: IdeClient) {
  const [state, setState] = useState<UpdateState | null>(null);
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState<"check" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [notify, setNotify] = useState(false);
  const [later, setLater] = useState<string | null>(null);
  const told = useRef<string | null>(null);
  const notifyRef = useRef(false);
  notifyRef.current = notify;

  useEffect(() => {
    setNotify(read(NOTIFY_KEY) === "on");
    setLater(read(LATER_KEY));
  }, []);

  // A desktop notification only when an update is new to this tab and the tab is in the background.
  const tell = useCallback((next: UpdateState) => {
    if (!next.available || !next.latest || told.current === next.latest) return;
    const first = told.current === null;
    told.current = next.latest;
    if (first && !document.hidden) return;
    if (!notifyRef.current || !document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
    const note = new Notification(U.notifyTitle(next.latest), {
      body: U.notifyBody,
      tag: "compactcode-update",
    });
    note.onclick = () => {
      window.focus();
      setOpen(true);
      note.close();
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const next = await ideCall<UpdateState>(server, "update");
      setState(next);
      tell(next);
    } catch {
      // The update item simply stays as it was; the IDE shows its own connection state.
    }
  }, [server, tell]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    const found = () => void load();
    window.addEventListener(UPDATE_EVENT, found);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(UPDATE_EVENT, found);
    };
  }, [load]);

  const run = async (kind: "check" | "apply", work: () => Promise<void>) => {
    setWorking(kind);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof IdeError ? e.message : IDE.setup.failed);
    } finally {
      setWorking(null);
    }
  };

  const check = () =>
    run("check", async () => {
      const next = await post<UpdateState>(server, "update/check");
      setState(next);
      told.current = next.latest;
    });

  const apply = () =>
    run("apply", async () => {
      await post(server, "update/apply");
      setRestarting(true);
    });

  const setAuto = (on: boolean) =>
    run("check", async () =>
      setState(
        await ideCall<UpdateState>(server, "update/settings", {
          method: "PUT",
          body: JSON.stringify({ auto: on }),
        }),
      ),
    );

  const setNotifications = async (on: boolean) => {
    setError(null);
    if (on) {
      if (!("Notification" in window)) return setError(U.notifyUnsupported);
      const allowed = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
      if (allowed !== "granted") return setError(U.notifyDenied);
    }
    setNotify(on);
    write(NOTIFY_KEY, on ? "on" : "off");
  };

  const dismiss = () => {
    if (!state?.latest) return;
    setLater(state.latest);
    write(LATER_KEY, state.latest);
  };

  const banner = Boolean(state?.available && state.latest !== later && !restarting);

  return {
    state,
    open,
    setOpen,
    working,
    error,
    restarting,
    notify,
    banner,
    check,
    apply,
    setAuto,
    setNotifications,
    dismiss,
  };
}

type Updates = ReturnType<typeof useUpdateState>;
const UpdatesContext = createContext<Updates | null>(null);

export function UpdatesProvider({ server, children }: { server: IdeClient; children: ReactNode }) {
  const updates = useUpdateState(server);
  return (
    <UpdatesContext.Provider value={updates}>
      {children}
    </UpdatesContext.Provider>
  );
}

export function useUpdates() {
  return useContext(UpdatesContext);
}

/** The status bar item: the version, or an accent "Update to x" when one is waiting. */
export function UpdateStatus() {
  const updates = useUpdates();
  if (!updates?.state) return null;
  const { state, setOpen } = updates;
  return (
    <button
      type="button"
      className="ide-status__update"
      data-available={state.available || undefined}
      title={state.available ? U.newer(state.latest ?? "") : U.versionHint}
      aria-haspopup="dialog"
      onClick={() => setOpen(true)}
    >
      <Codicon name={state.available ? "cloud-download" : "versions"} />
      {state.available && state.latest ? U.available(state.latest) : U.version(state.current)}
    </button>
  );
}

export function UpdateBanner() {
  const updates = useUpdates();
  if (!updates?.state || !updates.banner) return null;
  const { state, apply, setOpen, dismiss, working } = updates;
  return (
    <div className="ide-update-banner" role="status">
      <Codicon name="cloud-download" />
      <span>{U.banner(state.latest ?? "")}</span>
      {state.can_apply ? (
        <button type="button" className="ide-update-banner__primary" disabled={working !== null} onClick={() => void apply()}>
          {working === "apply" ? U.applying : U.apply}
        </button>
      ) : null}
      <button type="button" onClick={() => setOpen(true)}>
        {U.notes}
      </button>
      <span className="ide__spacer" />
      <button type="button" onClick={dismiss}>
        {U.later}
      </button>
    </div>
  );
}

/** Rendered inside .ide-shell, which holds the IDE's colour tokens. */
export function UpdatePanel() {
  const updates = useUpdates();
  const dialog = useRef<HTMLDivElement>(null);
  const open = Boolean(updates?.open);

  useEffect(() => {
    if (open) dialog.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }, [open]);

  if (!updates || !open || !updates.state) return null;
  const { state, working, error, restarting, notify, check, apply, setAuto, setNotifications, setOpen } = updates;
  const checked = state.checked_at ? U.checked(U.ago(Date.now() / 1000 - state.checked_at)) : U.never;
  const autoNote = state.auto_blocked === "private" ? U.autoPrivate : state.auto_blocked === "env" ? U.autoEnv : null;

  return (
    <>
      <div className="ide-update-scrim" onClick={() => setOpen(false)} aria-hidden="true" />
      <div
        ref={dialog}
        className="ide-update"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ide-update-title"
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            setOpen(false);
          }
        }}
      >
        <header className="ide-update__head">
          <h2 id="ide-update-title">{U.title}</h2>
          <button type="button" aria-label={U.close} onClick={() => setOpen(false)}>
            <Codicon name="close" />
          </button>
        </header>
        <dl className="ide-update__versions">
          <div>
            <dt>{U.current}</dt>
            <dd>{state.current}</dd>
          </div>
          <div>
            <dt>{U.latest}</dt>
            <dd>{state.latest ?? "?"}</dd>
          </div>
        </dl>
        {restarting ? (
          <p className="ide-update__msg" role="status">
            {U.restarting}
          </p>
        ) : (
          <>
            <p className="ide-update__msg" role="status">
              {state.available ? U.newer(state.latest ?? "") : state.latest ? U.upToDate : ""} {checked}
            </p>
            {state.error ? <p className="ide-update__error">{state.error}</p> : null}
            {error ? <p className="ide-update__error">{error}</p> : null}
            {state.available && state.notes ? (
              <section className="ide-update__notes" aria-label={U.notes}>
                <h3>{U.notes}</h3>
                <pre>{state.notes}</pre>
                {state.url ? (
                  <a href={state.url} target="_blank" rel="noreferrer">
                    {U.release}
                  </a>
                ) : null}
              </section>
            ) : null}
            {state.available && state.blocked ? <p className="ide-update__msg">{U.blocked[state.blocked]}</p> : null}
            <div className="ide-update__actions">
              {state.available ? (
                <button
                  type="button"
                  className="ide-update__primary"
                  disabled={!state.can_apply || working !== null}
                  onClick={() => void apply()}
                >
                  {working === "apply" ? U.applying : U.apply}
                </button>
              ) : null}
              <button type="button" disabled={working !== null} onClick={() => void check()}>
                {working === "check" ? U.checking : U.check}
              </button>
            </div>
            <label className="ide-update__toggle">
              <input type="checkbox" checked={state.auto} disabled={working !== null} onChange={(e) => void setAuto(e.target.checked)} />
              <span>
                {U.auto}
                {autoNote ? <small>{autoNote}</small> : null}
              </span>
            </label>
            <label className="ide-update__toggle">
              <input type="checkbox" checked={notify} onChange={(e) => void setNotifications(e.target.checked)} />
              <span>
                {U.notify}
                <small>{U.notifyHint}</small>
              </span>
            </label>
            <p className="ide-update__fine">{U.what}</p>
          </>
        )}
      </div>
    </>
  );
}
