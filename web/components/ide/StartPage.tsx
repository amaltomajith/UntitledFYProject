"use client";

import { IDE } from "@/lib/content";
import type { IdeState } from "@/lib/ide";
import { Codicon } from "./Codicon";

export const PROMPT_EVENT = "pcode:prompt";
const STARTERS = IDE.chat.starters.map((name) => IDE.commands.list.find((c) => c.name === name)!);
const KEYS = IDE.settings.shortcuts.slice(0, 4);

const leaf = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;
const pretty = (id: string) => id.split("/").pop() ?? id;

type Props = {
  state: IdeState;
  workspace: string;
  onSettings: () => void;
  onPalette: () => void;
};

/** What the editor shows with no tab open: where you are, which model answers, and how to start. */
export function StartPage({ state, workspace, onSettings, onPalette }: Props) {
  const project = state.project ?? "";
  const local = state.private.on;
  const provider = local ? IDE.start.local : state.provider_names?.[state.provider ?? ""] ?? state.provider ?? "";
  const starter = (name: string) =>
    window.dispatchEvent(new CustomEvent(PROMPT_EVENT, { detail: { workspace, text: `/${name} ` } }));

  return (
    <div className="ide-start">
      <div className="ide-start__inner">
        <p className="ide-label">workspace.open({JSON.stringify(leaf(project))})</p>
        <h2>{IDE.start.title(leaf(project))}</h2>
        <p className="ide-start__path">{project}</p>
        <p className="ide-start__lead">{IDE.start.lead}</p>

        <section className="ide-start__model" aria-label={IDE.start.model}>
          <div>
            <p className="ide-label">{IDE.start.model}</p>
            <p className="ide-start__provider">
              <Codicon name={local ? "lock" : "cloud"} />
              {provider}
            </p>
            <p className="ide-start__tiers">
              {Object.entries(state.models)
                .map(([tier, id]) => `${tier === "quality" ? IDE.picker.quality : IDE.picker.fast}: ${pretty(id)}`)
                .join("   ")}
            </p>
          </div>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onSettings}>
            {IDE.start.fix}
          </button>
        </section>

        <section aria-label={IDE.start.try}>
          <p className="ide-label">{IDE.start.try}</p>
          <ul className="ide-start__starters">
            {STARTERS.map((command) => (
              <li key={command.name}>
                <button type="button" onClick={() => starter(command.name)}>
                  <span className="ide-start__slash">/{command.name}</span>
                  <span>{command.hint}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label={IDE.start.keys}>
          <p className="ide-label">{IDE.start.keys}</p>
          <dl className="ide-shortcuts ide-shortcuts--compact">
            {KEYS.map(([keys, what]) => (
              <div key={keys}>
                <dt>
                  {keys.split("+").map((key, i) => (
                    <kbd key={i}>{key}</kbd>
                  ))}
                </dt>
                <dd>
                  {keys === "Ctrl+Shift+P" ? (
                    <button type="button" className="ide-start__link" onClick={onPalette}>
                      {what}
                    </button>
                  ) : (
                    what
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </div>
  );
}
