"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ArrowUpRight, Folder } from "@phosphor-icons/react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, type IdeState } from "@/lib/ide";

type Listing = { path: string; parent: string | null; folders: string[] };

const message = (e: unknown) => (e instanceof IdeError ? e.message : IDE.setup.failed);
const leaf = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

function KeyStep({ token, state, onDone }: { token: string; state: IdeState; onDone: (s: IdeState) => void }) {
  const [provider, setProvider] = useState("nebius");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      onDone(await post<IdeState>(token, "keys", { provider, key }));
      setKey("");
    } catch (err) {
      setError(message(err));
    }
  };

  return (
    <form className="ide-setup__card panel" onSubmit={save}>
      <h1>{IDE.setup.keyTitle}</h1>
      <p className="muted">{IDE.setup.keyBody}</p>
      <div className="field">
        <label htmlFor="ide-provider">{IDE.setup.provider}</label>
        <select id="ide-provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="nebius">{IDE.setup.nebius}</option>
          <option value="groq">{IDE.setup.groq}</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="ide-key">{IDE.setup.key}</label>
        <input id="ide-key" type="password" value={key} autoComplete="off" onChange={(e) => setKey(e.target.value)} />
      </div>
      {error ? (
        <p className="ide-setup__error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="ide-setup__row">
        <button type="submit" className="btn btn--primary" disabled={!key.trim()}>
          {IDE.setup.saveKey}
        </button>
        <a href={state.key_help[provider]} target="_blank" rel="noreferrer" className="btn btn--ghost">
          {IDE.setup.getKey}
          <ArrowUpRight size={14} weight="bold" aria-hidden="true" />
        </a>
      </div>
      <p className="ide-setup__hint">{IDE.setup.keyHint}</p>
    </form>
  );
}

function FolderStep({ token, onDone }: { token: string; onDone: (s: IdeState) => void }) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const go = useCallback(
    async (path: string) => {
      setError(null);
      try {
        setListing(await ideCall<Listing>(token, `browse?path=${encodeURIComponent(path)}`));
      } catch (err) {
        setError(message(err));
      }
    },
    [token],
  );

  useEffect(() => {
    void go("");
  }, [go]);

  const act = async (run: () => Promise<IdeState>) => {
    setError(null);
    try {
      onDone(await run());
    } catch (err) {
      setError(message(err));
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
            onClick={() => act(() => post<IdeState>(token, "open", { path: here }))}
          >
            {IDE.setup.openHere(leaf(here))}
          </button>
          <form
            className="ide-setup__new"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) void act(() => post<IdeState>(token, "create", { parent: here, name: name.trim() }));
            }}
          >
            <div className="field">
              <label htmlFor="ide-new">{IDE.setup.newLabel}</label>
              <input id="ide-new" value={name} placeholder={IDE.setup.newPlaceholder} onChange={(e) => setName(e.target.value)} />
            </div>
            <button type="submit" className="btn btn--ghost" disabled={!name.trim()}>
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

type Props = { token: string; state: IdeState; onChange: (s: IdeState) => void; onClose?: () => void };

export function Setup({ token, state, onChange, onClose }: Props) {
  const hasKey = Object.values(state.keys).some(Boolean);
  return (
    <main className="ide-setup">
      {hasKey ? <FolderStep token={token} onDone={onChange} /> : <KeyStep token={token} state={state} onDone={onChange} />}
      {onClose ? (
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          {IDE.setup.cancel}
        </button>
      ) : null}
    </main>
  );
}
