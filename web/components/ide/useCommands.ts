"use client";

import { IDE } from "@/lib/content";
import { IdeError, ideCall, post, type Effort, type IdeState, type Mode, type SavedChat, type View } from "@/lib/ide";

const C = IDE.commands;
const EFFORTS: Effort[] = ["fast", "normal", "think", "deep"];

type Context = {
  token: string;
  view: View;
  state: IdeState;
  setState: (state: IdeState) => void;
  send: (message: Record<string, unknown>) => void;
  local: (event: { type: string } & Record<string, unknown>) => void;
  refresh: () => Promise<void>;
  setMode: (mode: Mode) => Promise<void>;
  showChanges: () => void;
  showPreview: () => void;
  pickFolder: () => void;
};

export function useCommands(context: Context) {
  const { token, view, state, setState, send, local, refresh } = context;

  const earlier = async () => {
    const { sessions } = await ideCall<{ sessions: SavedChat[] }>(token, "sessions");
    return sessions.filter((row) => row.id !== state.session_id);
  };

  const resume = async (id: string) => {
    await post(token, "resume", { id });
    await refresh();
  };

  const showResume = async (say: (text: string) => void) => {
    const rows = (await earlier()).slice(0, 10);
    if (rows.length) local({ type: "sessions", rows });
    else say(C.noResume);
  };

  const run = async (line: string) => {
    const text = line.trim();
    const name = text.slice(1).split(/\s/)[0].toLowerCase();
    const rest = text.slice(name.length + 1).trim();
    const args = rest ? rest.split(/\s+/) : [];
    const say = (output: string) => local({ type: "print", text: output });
    const command = C.list.find((c) => c.name === name);
    if (!command) return say(C.unknown(name));

    if (command.prompt) {
      if (view.busy) return say(C.busy);
      return send({ type: "send", message: rest ? `${command.prompt} Focus on: ${rest}.` : command.prompt, mode: "build" });
    }

    try {
      switch (name) {
        case "help":
          return local({ type: "help" });
        case "clear":
          return view.busy ? say(C.busy) : void (await post(token, "clear"));
        case "resume": {
          if (view.busy) return say(C.busy);
          if (!args.length) return await showResume(say);
          const row = (await earlier())[Number(args[0]) - 1];
          return row ? await resume(row.id) : say(C.noSession);
        }
        case "undo":
          if (view.busy) return say(C.busy);
          if (!state.can_undo) return say(C.noUndo);
          await post(token, "undo");
          return await refresh();
        case "diff":
          return context.showChanges();
        case "plan": {
          const plan = [...view.items].reverse().find((item) => item.kind === "plan");
          return say(plan?.kind === "plan" ? plan.steps.map((step, i) => `${i + 1}. ${step}`).join("\n") : C.noPlan);
        }
        case "stop":
          return view.busy ? send({ type: "cancel" }) : say(C.idle);
        case "mode":
          if (args[0] === "plan" || args[0] === "ask" || args[0] === "auto") await context.setMode(args[0]);
          else if (args.length) return say(C.modeUsage);
          return say(C.modeIs(args[0] ?? state.mode));
        case "approve":
          if (view.busy) return say(C.busy);
          if (!state.pending_plan) return say(C.noPending);
          return setState(await post<IdeState>(token, "plan/approve"));
        case "effort": {
          if (args.length && !EFFORTS.includes(args[0] as Effort)) return say(C.effortUsage);
          if (args.length) setState(await post<IdeState>(token, "effort", { level: args[0] }));
          return say(C.effortIs(args[0] ?? state.effort));
        }
        case "private": {
          if (args.length && args[0] !== "on" && args[0] !== "off") return say(C.privateUsage);
          if (args.length && view.busy) return say(C.busy);
          const next = args.length ? await post<IdeState>(token, "private", { on: args[0] === "on" }) : state;
          if (args.length) setState(next);
          if (!next.private.on) return say(IDE.private.off);
          const { claim, outside, warning, setup } = next.private;
          return say([claim, IDE.private.outside(outside), IDE.private.noPreview, warning, setup ? IDE.private.setup : ""].filter(Boolean).join("\n"));
        }
        case "think":
          if (!rest) return say(C.thinkUsage);
          return view.busy ? say(C.busy) : send({ type: "send", message: rest, mode: "build", effort: "think" });
        case "model": {
          if (args.length !== 0 && args.length !== 2) return say(C.modelUsage);
          const next = args.length ? await post<IdeState>(token, "model", { tier: args[0], model: args[1] }) : state;
          if (args.length) setState(next);
          return say(Object.entries(next.models).map(([tier, id]) => `${tier}: ${id}`).join("\n"));
        }
        case "usage":
          return say(IDE.tokens(view.totalTokens || state.tokens));
        case "status":
          return say(
            C.status([
              [C.statusRows.folder, state.project ?? ""],
              [C.statusRows.provider, state.provider ?? ""],
              ...Object.entries(state.models),
              [C.statusRows.mode, state.mode],
              [C.statusRows.effort, state.effort],
              [C.statusRows.private, state.private.on ? state.private.claim : "off"],
              [C.statusRows.conversation, state.session_id ?? ""],
              [C.statusRows.usage, IDE.tokens(view.totalTokens || state.tokens)],
            ]),
          );
        case "preview":
          return view.preview ? context.showPreview() : say(state.private.on ? IDE.private.noPreview : IDE.editor.noPreview);
        case "folder":
          return view.busy ? say(C.busy) : context.pickFolder();
        case "fix":
          if (!rest) return say(C.fixUsage);
          return view.busy ? say(C.busy) : send({ type: "send", message: rest, mode: "fix" });
      }
    } catch (e) {
      say(e instanceof IdeError ? e.message : IDE.setup.failed);
    }
  };

  return { run, resume };
}
