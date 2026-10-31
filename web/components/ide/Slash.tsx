"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { IDE, type SlashCommand } from "@/lib/content";

const COMMANDS = IDE.commands.list;
const MAX_HISTORY = 100;

/** The menu shows while the first word is still being typed. */
export function matchCommands(text: string): SlashCommand[] {
  const typed = text.match(/^\/([a-z]*)$/i);
  if (!typed) return [];
  return COMMANDS.filter((c) => c.name.startsWith(typed[1].toLowerCase()));
}

/** `/etc/hosts is broken` is a sentence, and `/usr/bin/env` is a program, so neither looks like a command. */
export const looksLikeCommand = (line: string) => /^\/[a-z]+(\s|$)/i.test(line);

/** The chat box's keys: the slash menu and the Up arrow history. `submit` returns false to keep the text in the box. */
export function usePrompt(id: string, value: string, setValue: (v: string) => void, submit: (line: string) => boolean | void) {
  const [index, setIndex] = useState(0);
  const [closed, setClosed] = useState(false);
  const past = useRef<string[]>([]);
  const back = useRef(-1);
  const recalled = useRef(false);
  const items = closed ? [] : matchCommands(value);

  useEffect(() => {
    setIndex(0);
    setClosed(recalled.current);
    recalled.current = false;
  }, [value]);

  const run = (line: string) => {
    if (submit(line) === false) return;
    past.current = [...past.current.filter((l) => l !== line), line].slice(-MAX_HISTORY);
    back.current = -1;
    setValue("");
  };

  const fill = (command: SlashCommand) => setValue(`/${command.name} `);
  const pick = (command: SlashCommand) => (command.needsArg ? fill(command) : run(`/${command.name}`));

  const recall = (to: number) => {
    back.current = to;
    recalled.current = true;
    setValue(to < 0 ? "" : past.current[past.current.length - 1 - to]);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (items.length) {
      const move = { ArrowDown: 1, ArrowUp: items.length - 1 }[e.key];
      if (move) {
        e.preventDefault();
        setIndex((i) => (i + move) % items.length);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        fill(items[index]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setClosed(true);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        pick(items[index]);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (value.trim()) run(value.trim());
    } else if (e.key === "ArrowUp" && !value.includes("\n") && back.current < past.current.length - 1) {
      e.preventDefault();
      recall(back.current + 1);
    } else if (e.key === "ArrowDown" && back.current >= 0) {
      e.preventDefault();
      recall(back.current - 1);
    }
  };

  const combo = {
    role: "combobox",
    "aria-expanded": items.length > 0,
    "aria-controls": `${id}-menu`,
    "aria-autocomplete": "list" as const,
    "aria-activedescendant": items.length ? `${id}-${items[index].name}` : undefined,
  };

  return { items, index, onKeyDown, pick, run, combo };
}

type MenuProps = { id: string; items: SlashCommand[]; index: number; onPick: (command: SlashCommand) => void };

export function SlashMenu({ id, items, index, onPick }: MenuProps) {
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    list.current?.children[index]?.scrollIntoView({ block: "nearest" });
  }, [index]);

  return (
    <ul ref={list} id={`${id}-menu`} role="listbox" aria-label={IDE.commands.label} className="ide-slash" hidden={!items.length}>
      {items.map((command, i) => (
        <li
          key={command.name}
          id={`${id}-${command.name}`}
          role="option"
          aria-selected={i === index}
          onMouseDown={(e) => {
            e.preventDefault();
            onPick(command);
          }}
        >
          <span className="ide-slash__name">
            /{command.name}
            {command.args ? <span className="ide-slash__args"> {command.args}</span> : null}
          </span>
          <span className="ide-slash__hint">{command.hint}</span>
        </li>
      ))}
    </ul>
  );
}
