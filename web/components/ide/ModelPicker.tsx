"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { CaretDown } from "@phosphor-icons/react";
import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, type IdeClient, type IdeState, type Price } from "@/lib/ide";
import { PROVIDERS } from "./Providers";
import type { LocalStatus } from "./LocalSetup";

const P = IDE.picker;
const message = (e: unknown) => (e instanceof IdeError ? e.message : IDE.setup.failed);
const pretty = (id: string) => id.split("/").pop() ?? id;
const priceText = (price: Price) => (price ? `$${price[0]} / $${price[1]}` : "");

type Row = { id: string; price: Price };

type Props = {
  client: IdeClient;
  state: IdeState;
  busy: boolean;
  onState: (state: IdeState) => void;
  onManage: () => void;
};

/** The strong model, in the prompt card. Lists the providers that have a key, and models on this computer. */
export function ModelPicker({ client, state, busy, onState, onManage }: Props) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<string>(state.private.on ? "local" : state.provider ?? "nebius");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const current = state.models.quality ?? "";
  const keyed: string[] = PROVIDERS.filter((p) => state.keys[p]);

  useEffect(() => {
    if (open) setSource(state.private.on ? "local" : state.provider ?? keyed[0] ?? "local");
    // Only when the menu opens: picking a source inside it must not snap back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setRows(null);
    setError(null);
    // A provider with no catalog (the scripted model behind `--provider mock`) lists only its own models.
    const own = (): Row[] =>
      [...new Set([state.models.fast, state.models.quality].filter((m): m is string => Boolean(m)))].map((id) => ({ id, price: null }));
    const load =
      source === "local"
        ? ideCall<LocalStatus>(client, "local").then((s) => s.installed.map((id) => ({ id, price: null })))
        : !(PROVIDERS as readonly string[]).includes(source)
          ? Promise.resolve(own())
          : ideCall<{ models: Row[] }>(client, `models?provider=${encodeURIComponent(source)}`).then((r) => r.models);
    load.then(
      (found) => live && setRows(found),
      (e) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [open, source, client]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node) && !button.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [open]);

  const choose = async (id: string) => {
    setError(null);
    try {
      if (source === "local") {
        await post(client, "local/choose", { model: id });
        onState(state.private.on ? await ideCall<IdeState>(client, "state") : await post<IdeState>(client, "private", { on: true }));
      } else {
        if (state.private.on) onState(await post<IdeState>(client, "private", { on: false }));
        if (state.provider !== source) onState(await post<IdeState>(client, "provider", { provider: source }));
        onState(await post<IdeState>(client, "model", { tier: "quality", model: id }));
      }
      setOpen(false);
      button.current?.focus();
    } catch (e) {
      setError(message(e));
    }
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      setOpen(false);
      button.current?.focus();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const items = [...(box.current?.querySelectorAll<HTMLButtonElement>("[data-row]") ?? [])];
    if (!items.length) return;
    e.preventDefault();
    const at = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
  };

  const shown = (rows ?? []).filter((row) => row.id.toLowerCase().includes(filter.toLowerCase())).slice(0, 80);
  const names = state.provider_names ?? {};

  return (
    <div className="ide-picker">
      <button
        ref={button}
        type="button"
        className="ide-prompt__model"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={busy}
        title={busy ? P.busy : P.open(current)}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{pretty(current) || P.label}</span>
        <CaretDown size={11} weight="bold" aria-hidden="true" />
      </button>
      {open ? (
        <div ref={box} className="ide-picker__menu" role="dialog" aria-label={P.label} onKeyDown={onKeyDown}>
          <div className="ide-picker__sources" role="group" aria-label={P.providers}>
            {(state.provider && !state.private.on && !keyed.includes(state.provider) ? [...keyed, state.provider] : keyed).map((name) => (
              <button key={name} type="button" aria-pressed={source === name} onClick={() => setSource(name)}>
                {names[name] ?? name}
              </button>
            ))}
            <button type="button" aria-pressed={source === "local"} onClick={() => setSource("local")}>
              {P.local}
            </button>
          </div>
          <input
            className="ide-picker__filter"
            value={filter}
            placeholder={P.search}
            aria-label={P.search}
            aria-controls={listId}
            autoFocus
            onChange={(e) => setFilter(e.target.value)}
          />
          <div id={listId} className="ide-picker__list">
            {error ? <p className="ide-picker__note" data-tone="bad">{error}</p> : null}
            {!error && rows === null ? <p className="ide-picker__note">{P.loading}</p> : null}
            {rows !== null && !shown.length ? <p className="ide-picker__note">{source === "local" ? P.noLocal : P.none}</p> : null}
            {shown.map((row) => (
              <button
                key={row.id}
                type="button"
                data-row
                aria-current={row.id === current && (source === "local") === state.private.on ? "true" : undefined}
                onClick={() => void choose(row.id)}
              >
                <span className="ide-picker__id">{row.id}</span>
                <span className="ide-picker__price">{priceText(row.price)}</span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="ide-picker__manage"
            onClick={() => {
              setOpen(false);
              onManage();
            }}
          >
            {P.manage}
          </button>
        </div>
      ) : null}
    </div>
  );
}
