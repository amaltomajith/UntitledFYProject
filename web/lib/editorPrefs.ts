"use client";

// Editor preferences for this browser. A blocked localStorage just means the defaults.

import { useEffect, useState } from "react";

export type EditorPrefs = { fontSize: number; wrap: boolean };

const KEY = "pcode-editor-prefs";
const EVENT = "pcode:editor-prefs";
export const DEFAULT_PREFS: EditorPrefs = { fontSize: 13, wrap: false };
export const FONT_SIZES = [11, 12, 13, 14, 15, 16, 18];

export function readPrefs(): EditorPrefs {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return {
      fontSize: FONT_SIZES.includes(saved.fontSize) ? saved.fontSize : DEFAULT_PREFS.fontSize,
      wrap: saved.wrap === true,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

export function writePrefs(prefs: EditorPrefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    // Not remembered after a reload; the open editors still change below.
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: prefs }));
}

export function useEditorPrefs(): EditorPrefs {
  const [prefs, setPrefs] = useState<EditorPrefs>(DEFAULT_PREFS);
  useEffect(() => {
    setPrefs(readPrefs());
    const hear = (e: Event) => setPrefs((e as CustomEvent<EditorPrefs>).detail);
    window.addEventListener(EVENT, hear);
    return () => window.removeEventListener(EVENT, hear);
  }, []);
  return prefs;
}
