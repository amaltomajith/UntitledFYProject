"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { IDE } from "@/lib/content";
import { FONT_SIZES, useEditorPrefs, writePrefs } from "@/lib/editorPrefs";
import { IdeError, ideCall, post, type Effort, type IdeClient, type IdeState, type Mode, type Price } from "@/lib/ide";
import { LocalSetup } from "./LocalSetup";
import { KeyForm, PROVIDERS, TestResult, useKeyTest } from "./Providers";
import { useUpdates } from "./Updates";

const S = IDE.settings;
export type SettingsSection = "models" | "local" | "assistant" | "updates" | "editor" | "keys";
const SECTIONS: SettingsSection[] = ["models", "local", "assistant", "updates", "editor", "keys"];

const message = (e: unknown) => (e instanceof IdeError ? e.message : IDE.setup.failed);

type CatalogModel = { id: string; price: Price };

type Props = {
  client: IdeClient;
  state: IdeState;
  busy: boolean;
  section: SettingsSection;
  onSection: (section: SettingsSection) => void;
  onState: (state: IdeState) => void;
};

function Tiers({ client, state, busy, onState }: Pick<Props, "client" | "state" | "busy" | "onState">) {
  const [models, setModels] = useState<CatalogModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const provider = state.provider;

  useEffect(() => {
    if (!provider || provider === "local") return;
    let live = true;
    setModels(null);
    setError(null);
    ideCall<{ models: CatalogModel[] }>(client, `models?provider=${encodeURIComponent(provider)}`).then(
      (reply) => live && setModels(reply.models),
      (e) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [client, provider]);

  if (!state.project) return null;
  if (error) return <p className="ide-setup__error">{S.catalogFailed} {error}</p>;
  if (!models) return <p className="ide-setup__hint" aria-busy="true">{S.catalog}</p>;

  const pick = async (tier: string, model: string) => {
    setError(null);
    try {
      onState(await post<IdeState>(client, "model", { tier, model }));
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <div className="ide-settings__tiers">
      {(["fast", "quality"] as const).map((tier) => {
        const now = state.models[tier];
        const list = models.some((m) => m.id === now) ? models : [{ id: now, price: state.prices?.[tier] ?? null }, ...models];
        return (
          <div className="field" key={tier}>
            <label htmlFor={`${id}-${tier}`}>{tier === "fast" ? S.fast : S.quality}</label>
            <select id={`${id}-${tier}`} value={now} disabled={busy} onChange={(e) => void pick(tier, e.target.value)}>
              {list.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </select>
            <span className="ide-setup__hint">{S.price(list.find((m) => m.id === now)?.price ?? null)}</span>
          </div>
        );
      })}
      <p className="ide-setup__hint">{S.tiersHint}</p>
    </div>
  );
}

function ModelsAndKeys({ client, state, busy, onState }: Pick<Props, "client" | "state" | "busy" | "onState">) {
  const [editing, setEditing] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { tests, test } = useKeyTest(client);
  const names = state.provider_names ?? {};

  const act = async (work: () => Promise<IdeState>) => {
    setError(null);
    try {
      onState(await work());
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <div className="ide-settings__body">
      {state.private.on ? <p className="ide-settings__note">{S.privateOn}</p> : null}
      <ul className="ide-providers">
        {PROVIDERS.map((name) => {
          const saved = state.keys[name];
          const active = !state.private.on && state.provider === name;
          return (
            <li key={name} className="ide-provider" data-active={active}>
              <div className="ide-provider__head">
                <span className="ide-provider__name">{names[name] ?? name}</span>
                <span className="ide-provider__status" data-saved={saved}>
                  {active ? S.active : saved ? IDE.setup.savedEnds(state.key_hints?.[name] ?? null) : S.noKey}
                </span>
                <span className="ide__spacer" />
                {saved && !active && !state.private.on ? (
                  <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void act(() => post(client, "provider", { provider: name }))}>
                    {S.use}
                  </button>
                ) : null}
                {saved ? (
                  <button type="button" className="btn btn--ghost btn--sm" disabled={tests[name] === "running"} onClick={() => void test(name)}>
                    {IDE.setup.testKey}
                  </button>
                ) : null}
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(editing === name ? null : name)}>
                  {saved ? S.replace : S.add}
                </button>
                {saved ? (
                  <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => setRemoving(name)}>
                    {S.remove}
                  </button>
                ) : null}
              </div>
              <TestResult result={tests[name]} />
              {removing === name ? (
                <div className="ide-provider__confirm" role="alertdialog" aria-label={S.removeAsk(names[name] ?? name)}>
                  <p>{S.removeAsk(names[name] ?? name)}</p>
                  <button
                    type="button"
                    className="btn btn--primary btn--sm"
                    onClick={() =>
                      void act(async () => {
                        setRemoving(null);
                        return ideCall<IdeState>(client, `keys/${name}`, { method: "DELETE" });
                      })
                    }
                  >
                    {S.remove}
                  </button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setRemoving(null)}>
                    {IDE.local.cancel}
                  </button>
                </div>
              ) : null}
              {editing === name ? (
                <KeyForm
                  client={client}
                  state={state}
                  initial={name}
                  onSaved={(next) => {
                    setEditing(null);
                    onState(next);
                  }}
                />
              ) : null}
            </li>
          );
        })}
      </ul>
      {error ? (
        <p className="ide-setup__error" role="alert">
          {error}
        </p>
      ) : null}
      {!state.private.on ? <Tiers client={client} state={state} busy={busy} onState={onState} /> : null}
    </div>
  );
}

function Assistant({ client, state, busy, onState }: Pick<Props, "client" | "state" | "busy" | "onState">) {
  const [error, setError] = useState<string | null>(null);
  const set = async (path: string, body: unknown) => {
    setError(null);
    try {
      onState(await post<IdeState>(client, path, body));
    } catch (e) {
      setError(message(e));
    }
  };
  return (
    <div className="ide-settings__body">
      <h3 className="ide-label">{S.mode}</h3>
      <div className="ide-choices" role="group" aria-label={S.mode}>
        {(["plan", "ask", "auto"] as Mode[]).map((mode) => (
          <button key={mode} type="button" aria-pressed={state.mode === mode} disabled={busy} onClick={() => void set("mode", { mode })}>
            <span>{IDE.mode[mode]}</span>
            <span className="ide-choices__hint">{IDE.mode.hints[mode]}</span>
          </button>
        ))}
      </div>
      <h3 className="ide-label">{S.effort}</h3>
      <div className="ide-choices" role="group" aria-label={S.effort}>
        {(["fast", "normal", "think", "deep"] as Effort[]).map((level) => (
          <button key={level} type="button" aria-pressed={state.effort === level} disabled={busy} onClick={() => void set("effort", { level })}>
            <span>{level}</span>
            <span className="ide-choices__hint">{S.effortHints[level]}</span>
          </button>
        ))}
      </div>
      {error ? <p className="ide-setup__error">{error}</p> : null}
    </div>
  );
}

function Editor() {
  const prefs = useEditorPrefs();
  const id = useId();
  return (
    <div className="ide-settings__body">
      <div className="field">
        <label htmlFor={`${id}-size`}>{S.fontSize}</label>
        <select id={`${id}-size`} value={prefs.fontSize} onChange={(e) => writePrefs({ ...prefs, fontSize: Number(e.target.value) })}>
          {FONT_SIZES.map((size) => (
            <option key={size} value={size}>
              {size}px
            </option>
          ))}
        </select>
      </div>
      <label className="ide-check-row">
        <input type="checkbox" checked={prefs.wrap} onChange={(e) => writePrefs({ ...prefs, wrap: e.target.checked })} />
        {S.wrap}
      </label>
      <p className="ide-setup__hint">{S.editorNote}</p>
    </div>
  );
}

function Shortcuts() {
  return (
    <dl className="ide-shortcuts">
      {S.shortcuts.map(([keys, what]) => (
        <div key={keys}>
          <dt>
            {keys.split("+").map((key, i) => (
              <kbd key={i}>{key}</kbd>
            ))}
          </dt>
          <dd>{what}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Settings({ client, state, busy, section, onSection, onState }: Props) {
  const updates = useUpdates();
  const pickLocal = useCallback(
    async (model: string) => {
      await post(client, "local/choose", { model });
      onState(state.private.on ? await ideCall<IdeState>(client, "state") : await post<IdeState>(client, "private", { on: true }));
    },
    [client, onState, state.private.on],
  );

  return (
    <div className="ide-settings">
      <nav className="ide-settings__nav" aria-label={S.title}>
        <p className="ide-label">{S.title}</p>
        {SECTIONS.map((name) => (
          <button key={name} type="button" aria-current={section === name ? "page" : undefined} onClick={() => onSection(name)}>
            {S.sections[name]}
          </button>
        ))}
      </nav>
      <section className="ide-settings__main" aria-labelledby="settings-heading">
        <h2 id="settings-heading">{S.sections[section]}</h2>
        {section === "models" ? <ModelsAndKeys client={client} state={state} busy={busy} onState={onState} /> : null}
        {section === "local" ? <LocalSetup client={client} privateOn={state.private.on} onUse={pickLocal} /> : null}
        {section === "assistant" ? <Assistant client={client} state={state} busy={busy} onState={onState} /> : null}
        {section === "updates" ? (
          <div className="ide-settings__body">
            <p className="muted">{IDE.update.what}</p>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => updates?.setOpen(true)}>
              {S.updatesOpen}
            </button>
          </div>
        ) : null}
        {section === "editor" ? <Editor /> : null}
        {section === "keys" ? <Shortcuts /> : null}
      </section>
    </div>
  );
}
