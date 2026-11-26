"use client";

import { useEffect, useId, useState } from "react";
import { ArrowUpRight } from "@phosphor-icons/react";
import { IDE } from "@/lib/content";
import { IdeError, post, type IdeClient, type IdeState } from "@/lib/ide";

export const PROVIDERS = ["nebius", "groq", "openai", "anthropic", "gemini", "openrouter"] as const;

const message = (e: unknown) => (e instanceof IdeError ? e.message : IDE.setup.failed);

type Test = { ok: boolean; models?: number; message?: string };

/** Test a saved key: the server lists the provider's models with it and says how many it saw. */
export function useKeyTest(client: IdeClient) {
  const [tests, setTests] = useState<Record<string, Test | "running">>({});
  const test = async (provider: string) => {
    setTests((all) => ({ ...all, [provider]: "running" }));
    try {
      const result = await post<Test>(client, "keys/test", { provider });
      setTests((all) => ({ ...all, [provider]: result }));
    } catch (e) {
      setTests((all) => ({ ...all, [provider]: { ok: false, message: message(e) } }));
    }
  };
  return { tests, test };
}

export function TestResult({ result }: { result: Test | "running" | undefined }) {
  if (!result) return null;
  if (result === "running") return <span className="ide-key__test">{IDE.setup.testing}</span>;
  return (
    <span className="ide-key__test" data-ok={result.ok} role="status">
      {result.ok ? IDE.setup.tested(result.models ?? 0) : result.message}
    </span>
  );
}

type FormProps = {
  client: IdeClient;
  state: IdeState;
  initial?: string;
  onSaved: (state: IdeState, provider: string) => void;
};

/** Choose one of the six providers, paste its key, and test it. A saved key is never shown again. */
export function KeyForm({ client, state, initial = "nebius", onSaved }: FormProps) {
  const [provider, setProvider] = useState(initial);
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { tests, test } = useKeyTest(client);
  const id = useId();
  const names = state.provider_names ?? {};

  useEffect(() => setProvider(initial), [initial]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim() || saving) return;
    setError(null);
    setSaving(true);
    try {
      const next = await post<IdeState>(client, "keys", { provider, key });
      setKey("");
      onSaved(next, provider);
      void test(provider);
    } catch (err) {
      setError(message(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="ide-key" onSubmit={save}>
      <fieldset className="ide-key__providers">
        <legend className="ide-label">{IDE.setup.provider}</legend>
        {PROVIDERS.map((name) => (
          <label key={name} className="ide-key__provider" data-picked={provider === name}>
            <input type="radio" name={`${id}-provider`} value={name} checked={provider === name} onChange={() => setProvider(name)} />
            <span className="ide-key__name">{names[name] ?? name}</span>
            <span className="ide-key__hint">
              {state.keys[name] ? IDE.setup.savedEnds(state.key_hints?.[name] ?? null) : IDE.setup.providerHints[name]}
            </span>
          </label>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor={`${id}-key`}>{IDE.setup.key}</label>
        <input
          id={`${id}-key`}
          type="password"
          value={key}
          autoComplete="off"
          spellCheck={false}
          placeholder={state.keys[provider] ? IDE.setup.savedEnds(state.key_hints?.[provider] ?? null) : ""}
          onChange={(e) => setKey(e.target.value)}
        />
      </div>
      {error ? (
        <p className="ide-setup__error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="ide-setup__row">
        <button type="submit" className="btn btn--primary btn--sm" disabled={!key.trim() || saving} aria-busy={saving}>
          {IDE.setup.saveKey}
        </button>
        {state.keys[provider] ? (
          <button type="button" className="btn btn--ghost btn--sm" disabled={tests[provider] === "running"} onClick={() => void test(provider)}>
            {IDE.setup.testKey}
          </button>
        ) : null}
        <a href={state.key_help[provider]} target="_blank" rel="noreferrer" className="btn btn--ghost btn--sm">
          {IDE.setup.getKey}
          <ArrowUpRight size={13} weight="bold" aria-hidden="true" />
        </a>
        <TestResult result={tests[provider]} />
      </div>
      <p className="ide-setup__hint">{IDE.setup.keyHint}</p>
    </form>
  );
}
