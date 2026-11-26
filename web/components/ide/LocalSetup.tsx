"use client";

import { useCallback, useEffect, useState } from "react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, LOCAL_EVENT, post, type IdeClient } from "@/lib/ide";
import { Codicon } from "./Codicon";

const L = IDE.local;

type Offer = {
  name: string;
  size: number;
  needs: number;
  tools: "native" | "text";
  note: string;
  tested: boolean;
  fit: "gpu" | "split" | "cpu" | "too_big" | "unknown";
  fit_text: string;
  installed: boolean;
  recommended: boolean;
};

type Pull = { model: string; status: string; done: number; total: number; state: string; message: string };

export type LocalStatus = {
  ollama: { found: boolean; version: string | null };
  install: { command: string; runs_here: boolean; shell: string; state: string; lines: string[]; code?: number | null };
  hardware: { memory: number | null; disk_free: number | null; gpu: { name: string; memory: number; shared: boolean } | null };
  models: Offer[];
  installed: string[];
  chosen: string | null;
  pull: Pull | null;
  downloading_note: string;
};

const message = (e: unknown) => (e instanceof IdeError ? e.message : IDE.setup.failed);

type Props = {
  client: IdeClient;
  privateOn: boolean;
  onUse: (model: string) => Promise<void>;
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="ide-local__stat">
      <span className="ide-label">{label}</span>
      <span>{value}</span>
    </div>
  );
}

export function LocalSetup({ client, privateOn, onUse }: Props) {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [pull, setPull] = useState<Pull | null>(null);
  const [using, setUsing] = useState<string | null>(null);

  const load = useCallback(
    async (refresh = false) => {
      try {
        const next = await ideCall<LocalStatus>(client, `local${refresh ? "?refresh=true" : ""}`);
        setStatus(next);
        setLines(next.install.lines);
        setPull(next.pull);
        setError(null);
      } catch (e) {
        setError(message(e));
      }
    },
    [client],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const hear = (e: Event) => {
      const event = (e as CustomEvent).detail as Record<string, unknown>;
      if (event.type === "local_pull") {
        setPull(event as unknown as Pull);
        if (event.state !== "running") void load();
      } else if (event.type === "local_install") {
        if (typeof event.line === "string") setLines((all) => [...all, event.line as string].slice(-200));
        if (event.state) void load(true);
      }
    };
    window.addEventListener(LOCAL_EVENT, hear);
    return () => window.removeEventListener(LOCAL_EVENT, hear);
  }, [load]);

  const act = async (work: () => Promise<unknown>) => {
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(message(e));
    }
  };

  if (!status) {
    return (
      <div className="ide-local" aria-busy="true">
        <p className="ide-local__loading">{error ?? L.checking}</p>
        <div className="ide-skeleton" />
        <div className="ide-skeleton" />
      </div>
    );
  }

  const { hardware: hw, install } = status;
  const installing = install.state === "running";
  const running = pull?.state === "running";

  return (
    <div className="ide-local">
      <section className="ide-local__block" aria-label="Ollama">
        {status.ollama.found ? (
          <p className="ide-local__ok">
            <Codicon name="pass" />
            {L.found(status.ollama.version)}
          </p>
        ) : (
          <>
            <p className="muted">{L.missing}</p>
            {install.runs_here ? (
              asking || installing ? (
                <div className="ide-local__ask">
                  <p>{L.installAsk}</p>
                  <code className="ide-local__cmd">{install.command}</code>
                  {installing ? null : (
                    <div className="ide-setup__row">
                      <button
                        type="button"
                        className="btn btn--primary btn--sm"
                        onClick={() =>
                          void act(async () => {
                            setLines([]);
                            await post(client, "local/install");
                            setAsking(false);
                            await load();
                          })
                        }
                      >
                        {L.confirm}
                      </button>
                      <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAsking(false)}>
                        {L.cancel}
                      </button>
                    </div>
                  )}
                </div>
              ) : (
                <button type="button" className="btn btn--primary btn--sm" onClick={() => setAsking(true)}>
                  {L.install}
                </button>
              )
            ) : (
              <div className="ide-local__ask">
                <p>{L.installYourself}</p>
                <code className="ide-local__cmd">{install.command}</code>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => void load(true)}>
                  {L.checkAgain}
                </button>
              </div>
            )}
            {lines.length ? (
              <pre className="ide-local__log" aria-live="polite" aria-label={L.installing}>
                {lines.join("\n")}
              </pre>
            ) : null}
            {install.state === "failed" ? <p className="ide-setup__error">{L.installFailed}</p> : null}
          </>
        )}
      </section>

      <section className="ide-local__block" aria-label={L.hardware}>
        <h3 className="ide-label">{L.hardware}</h3>
        <div className="ide-local__stats">
          <Stat label={L.memory} value={hw.memory ? L.gb(hw.memory) : L.unknown} />
          <Stat
            label={L.gpu}
            value={hw.gpu ? `${hw.gpu.name}, ${L.gb(hw.gpu.memory)}${hw.gpu.shared ? `, ${L.shared}` : ""}` : L.unknown}
          />
          <Stat label={L.disk} value={hw.disk_free ? L.gb(hw.disk_free) : L.unknown} />
        </div>
        {hw.gpu ? null : <p className="ide-setup__hint">{L.noGpu}</p>}
      </section>

      <section className="ide-local__block" aria-label={L.models}>
        <h3 className="ide-label">{L.models}</h3>
        <p className="ide-setup__hint">{L.modelsHint}</p>
        {status.models.some((m) => m.recommended) ? null : <p className="ide-setup__hint">{L.none}</p>}
        <ul className="ide-local__models">
          {status.models.map((m) => {
            const here = pull?.model === m.name ? pull : null;
            const inUse = privateOn && status.chosen === m.name;
            return (
              <li key={m.name} className="ide-local__model" data-recommended={m.recommended} data-fit={m.fit}>
                <div className="ide-local__name">
                  <code>{m.name}</code>
                  {m.recommended ? <span className="ide-tag" data-tone="accent">{L.recommended}</span> : null}
                  {m.installed ? <span className="ide-tag">{L.installedTag}</span> : null}
                </div>
                <p className="ide-local__meta">
                  <span data-fit={m.fit}>{m.fit_text}</span>
                  <span>{L.size(m.size)}</span>
                  <span>{L.needs(m.needs)}</span>
                </p>
                <p className="ide-local__note">
                  {m.note} {m.tested ? L.tested : L.untested}.{m.tools === "text" ? ` ${L.textTools}.` : ""}
                </p>
                {here && here.state === "running" ? (
                  <div className="ide-local__pull">
                    <progress max={here.total || 1} value={here.done} aria-label={`${L.downloading} ${m.name}`} />
                    <span>{L.progress(here.done, here.total)}</span>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => void act(() => post(client, "local/cancel"))}
                    >
                      {L.stop}
                    </button>
                    <p className="ide-setup__hint">{status.downloading_note}</p>
                  </div>
                ) : null}
                {here && here.state !== "running" && here.state !== "done" ? (
                  <p className="ide-setup__error" role="alert">
                    {here.message}
                  </p>
                ) : null}
                <div className="ide-local__actions">
                  {m.installed ? (
                    <button
                      type="button"
                      className={inUse ? "btn btn--ghost btn--sm" : "btn btn--primary btn--sm"}
                      disabled={inUse || using !== null}
                      aria-busy={using === m.name}
                      onClick={() =>
                        void act(async () => {
                          setUsing(m.name);
                          try {
                            await onUse(m.name);
                            await load();
                          } finally {
                            setUsing(null);
                          }
                        })
                      }
                    >
                      {inUse ? L.using : L.use}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className={m.recommended ? "btn btn--primary btn--sm" : "btn btn--ghost btn--sm"}
                      disabled={!status.ollama.found || running || m.fit === "too_big"}
                      onClick={() =>
                        void act(async () => {
                          setPull(await post<Pull>(client, "local/pull", { model: m.name }));
                        })
                      }
                    >
                      {L.download}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
          {status.installed
            .filter((name) => !status.models.some((m) => m.name === name))
            .map((name) => (
              <li key={name} className="ide-local__model">
                <div className="ide-local__name">
                  <code>{name}</code>
                  <span className="ide-tag">{L.installedTag}</span>
                </div>
                <p className="ide-local__note">{L.untested}.</p>
                <div className="ide-local__actions">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={(privateOn && status.chosen === name) || using !== null}
                    onClick={() => void act(async () => onUse(name).then(() => load()))}
                  >
                    {privateOn && status.chosen === name ? L.using : L.use}
                  </button>
                </div>
              </li>
            ))}
        </ul>
        <p className="ide-setup__hint">{L.privateNote}</p>
      </section>
      {error ? (
        <p className="ide-setup__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
