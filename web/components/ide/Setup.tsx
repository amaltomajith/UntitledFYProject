"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { ArrowLeft, Cloud, Cpu, Folder } from "@phosphor-icons/react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, type IdeState, type IdeClient } from "@/lib/ide";
import { LocalSetup } from "./LocalSetup";
import { KeyForm, PROVIDERS } from "./Providers";

type Listing = { path: string; parent: string | null; folders: string[] };

const message = (e: unknown) => (e instanceof IdeError ? e.message : IDE.setup.failed);
const leaf = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/** Turn Private on with a local model. Works before a folder is open, so it is the no-key path. */
export async function turnOnLocal(client: IdeClient, model: string): Promise<IdeState> {
  await post(client, "local/choose", { model });
  return post<IdeState>(client, "private", { on: true });
}

function KeyStep({ client, state, onDone }: { client: IdeClient; state: IdeState; onDone: (s: IdeState) => void }) {
  const [path, setPath] = useState<"pick" | "local" | "cloud">("pick");

  if (path === "local") {
    return (
      <section className="ide-setup__card ide-setup__card--wide panel" aria-labelledby="setup-local">
        <button type="button" className="ide-setup__back" onClick={() => setPath("pick")}>
          <ArrowLeft size={14} aria-hidden="true" />
          {IDE.setup.back}
        </button>
        <h1 id="setup-local">{IDE.local.title}</h1>
        <LocalSetup client={client} privateOn={state.private.on} onUse={async (model) => onDone(await turnOnLocal(client, model))} />
      </section>
    );
  }

  if (path === "cloud") {
    return (
      <section className="ide-setup__card ide-setup__card--wide panel" aria-labelledby="setup-cloud">
        <button type="button" className="ide-setup__back" onClick={() => setPath("pick")}>
          <ArrowLeft size={14} aria-hidden="true" />
          {IDE.setup.back}
        </button>
        <h1 id="setup-cloud">{IDE.setup.cloudTitle}</h1>
        <KeyForm client={client} state={state} onSaved={(next) => onDone(next)} />
      </section>
    );
  }

  return (
    <section className="ide-setup__pick" aria-labelledby="setup-title">
      <header>
        <p className="ide-label">compactcode.setup()</p>
        <h1 id="setup-title">{IDE.setup.keyTitle}</h1>
        <p className="muted">{IDE.setup.keyBody}</p>
      </header>
      <div className="ide-setup__paths">
        <button type="button" className="ide-path" data-lead="true" onClick={() => setPath("local")}>
          <span className="ide-path__tag">{IDE.setup.localTag}</span>
          <span className="ide-path__title">
            <Cpu size={18} aria-hidden="true" />
            {IDE.setup.localTitle}
          </span>
          <span className="ide-path__body">{IDE.setup.localBody}</span>
          <span className="ide-path__go">{IDE.setup.localStart}</span>
        </button>
        <button type="button" className="ide-path" onClick={() => setPath("cloud")}>
          <span className="ide-path__tag">{IDE.setup.cloudTag}</span>
          <span className="ide-path__title">
            <Cloud size={18} aria-hidden="true" />
            {IDE.setup.cloudTitle}
          </span>
          <span className="ide-path__body">{IDE.setup.cloudBody}</span>
          <span className="ide-path__go">{PROVIDERS.map((p) => state.provider_names?.[p] ?? p).join(" · ")}</span>
        </button>
      </div>
    </section>
  );
}

function FolderStep({ client, start, onDone }: { client: IdeClient; start: string; onDone: (s: IdeState) => void }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [name, setName] = useState("");
  const nameId = useId();
  const [error, setError] = useState<string | null>(null);

  const go = useCallback(
    async (path: string) => {
      setError(null);
      try {
        setListing(await ideCall<Listing>(client, `browse?path=${encodeURIComponent(path)}`));
      } catch (err) {
        setError(message(err));
      }
    },
    [client],
  );

  // A folder named on the command line waits here, so opening it is one click.
  useEffect(() => {
    void go(start);
  }, [go, start]);

  // Opening takes a moment; a second click used to start a second open of the same folder.
  const [opening, setOpening] = useState(false);
  const act = async (run: () => Promise<IdeState>) => {
    if (opening) return;
    setOpening(true);
    setError(null);
    try {
      onDone(await run());
    } catch (err) {
      setError(message(err));
    } finally {
      setOpening(false);
    }
  };

  const here = listing?.path ?? "";

  return (
    <section className="ide-setup__card panel">
      <h1>{IDE.setup.folderTitle}</h1>
      <p className="muted">{IDE.setup.folderBody}</p>
      <div className="ide-browse">
        <p className="ide-browse__path">{here || IDE.setup.places}</p>
        <ul>
          {listing?.parent !== null && listing?.parent !== undefined ? (
            <li>
              <button type="button" onClick={() => go(listing.parent ?? "")}>
                <ArrowLeft size={14} aria-hidden="true" />
                {IDE.setup.up}
              </button>
            </li>
          ) : null}
          {listing?.folders.map((folder) => (
            <li key={folder}>
              <button type="button" onClick={() => go(folder)}>
                <Folder size={14} aria-hidden="true" />
                {leaf(folder)}
              </button>
            </li>
          ))}
          {listing && listing.folders.length === 0 ? <li className="ide-browse__none">{IDE.setup.noFolders}</li> : null}
        </ul>
      </div>
      {here ? (
        <>
          <button
            type="button"
            className="btn btn--primary"
            disabled={opening}
            aria-busy={opening}
            onClick={() => act(() => post<IdeState>(client, "open", { path: here }))}
          >
            {opening ? IDE.setup.opening : IDE.setup.openHere(leaf(here))}
          </button>
          <form
            className="ide-setup__new"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) void act(() => post<IdeState>(client, "create", { parent: here, name: name.trim() }));
            }}
          >
            <div className="field">
              <label htmlFor={nameId}>{IDE.setup.newLabel}</label>
              <input id={nameId} value={name} placeholder={IDE.setup.newPlaceholder} onChange={(e) => setName(e.target.value)} />
            </div>
            <button type="submit" className="btn btn--ghost" disabled={!name.trim() || opening}>
              {IDE.setup.create}
            </button>
          </form>
        </>
      ) : null}
      {error ? (
        <p className="ide-setup__error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

type Props = { client: IdeClient; state: IdeState; onChange: (s: IdeState) => void; onClose?: () => void };

export function Setup({ client, state, onChange, onClose }: Props) {
  const hasKey = Object.values(state.keys).some(Boolean) || state.private.on;
  return (
    <main className="ide-setup">
      {hasKey ? <FolderStep client={client} start={state.waiting_folder ?? ""} onDone={onChange} /> : <KeyStep client={client} state={state} onDone={onChange} />}
      {onClose ? (
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          {IDE.setup.cancel}
        </button>
      ) : null}
    </main>
  );
}
