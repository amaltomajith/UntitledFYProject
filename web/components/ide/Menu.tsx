"use client";

import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Codicon } from "./Codicon";

export type MenuItem =
  | { separator: true }
  | {
      separator?: false;
      label: string;
      onPick: () => void;
      hint?: string;
      icon?: string;
      checked?: boolean;
      danger?: boolean;
      keep?: boolean;       // opens another list in the same menu
    };

type Props = { x: number; y: number; items: MenuItem[]; label: string; onClose: () => void };

const EDGE = 8;

/** Fixed to the viewport so a panel's overflow cannot clip it. Keeps inside the window. */
export function Menu({ x, y, items, label, onClose }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState({ left: x, top: y });

  useLayoutEffect(() => {
    const rect = box.current!.getBoundingClientRect();
    setAt({
      left: Math.max(EDGE, Math.min(x, window.innerWidth - rect.width - EDGE)),
      top: y + rect.height > window.innerHeight - EDGE ? Math.max(EDGE, y - rect.height) : y,
    });
  }, [x, y, items.length]);

  useEffect(() => {
    box.current?.querySelector("button")?.focus();
  }, [items]);

  useEffect(() => {
    const away = (e: Event) => {
      if (!box.current?.contains(e.target as Node)) onClose();
    };
    window.addEventListener("mousedown", away);
    window.addEventListener("blur", onClose);
    return () => {
      window.removeEventListener("mousedown", away);
      window.removeEventListener("blur", onClose);
    };
  }, [onClose]);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const rows = [...box.current!.querySelectorAll("button")];
    const here = rows.indexOf(document.activeElement as HTMLButtonElement);
    rows[(here + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length]?.focus();
  };

  return (
    <div ref={box} role="menu" aria-label={label} className="ide-menu" style={at} onKeyDown={onKeyDown}>
      {items.map((item, i) =>
        item.separator ? (
          <hr key={i} />
        ) : (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            data-danger={item.danger}
            onClick={() => {
              item.onPick();
              if (!item.keep) onClose();
            }}
          >
            <span className="ide-menu__icon">
              {item.checked ? <Codicon name="check" /> : item.icon ? <Codicon name={item.icon} /> : null}
            </span>
            <span>{item.label}</span>
            {item.hint ? <span className="ide-menu__hint">{item.hint}</span> : null}
          </button>
        ),
      )}
    </div>
  );
}
