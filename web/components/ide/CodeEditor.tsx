"use client";

import { useEffect, useRef } from "react";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { bracketMatching, HighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { unifiedMergeView } from "@codemirror/merge";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { drawSelection, EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";

const highlight = HighlightStyle.define([
  { tag: [tags.keyword, tags.operatorKeyword, tags.modifier], color: "var(--accent-hi)" },
  { tag: [tags.string, tags.special(tags.string), tags.regexp], color: "var(--good)" },
  { tag: [tags.comment, tags.meta], color: "var(--ink-3)", fontStyle: "italic" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--bad)" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName), tags.className, tags.typeName], color: "#ffffff" },
  { tag: [tags.tagName, tags.heading], color: "var(--accent-hi)", fontWeight: "600" },
  { tag: tags.attributeName, color: "var(--ink-2)" },
]);

const theme = EditorView.theme(
  {
    "&": { height: "100%", fontSize: "13.5px", backgroundColor: "transparent", color: "var(--ink)" },
    ".cm-scroller": { fontFamily: "var(--f-mono)", lineHeight: "1.6", overflow: "auto" },
    ".cm-content": { caretColor: "var(--accent-hi)", paddingBlock: "10px" },
    ".cm-gutters": { backgroundColor: "transparent", color: "var(--ink-3)", border: "none" },
    ".cm-activeLine": { backgroundColor: "rgba(180, 170, 240, 0.06)" },
    ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--ink)" },
    ".cm-cursor": { borderLeftColor: "var(--accent-hi)" },
    "&.cm-focused": { outline: "none" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "rgba(91, 61, 245, 0.45) !important",
    },
    ".cm-changedLine": { backgroundColor: "rgba(126, 226, 168, 0.1) !important" },
    ".cm-deletedChunk": { backgroundColor: "rgba(255, 180, 180, 0.1)" },
    ".cm-insertedLine": { backgroundColor: "rgba(126, 226, 168, 0.1) !important" },
    ".cm-deletedLine": { backgroundColor: "rgba(255, 180, 180, 0.1) !important" },
  },
  { dark: true },
);

function language(path: string): Extension {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "py") return python();
  if (["js", "jsx", "mjs", "cjs"].includes(ext)) return javascript({ jsx: true });
  if (["ts", "tsx"].includes(ext)) return javascript({ jsx: true, typescript: true });
  if (["html", "htm"].includes(ext)) return html();
  if (ext === "css") return css();
  if (ext === "json") return json();
  if (["md", "markdown"].includes(ext)) return markdown();
  return [];
}

const base = (path: string): Extension[] => [
  lineNumbers(),
  drawSelection(),
  highlightActiveLine(),
  history(),
  indentOnInput(),
  bracketMatching(),
  language(path),
  syntaxHighlighting(highlight),
  theme,
];

type Props = {
  path: string;
  value: string;
  readOnly: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
};

export default function CodeEditor({ path, value, readOnly, onChange, onSave }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const lock = useRef(new Compartment());
  const handlers = useRef({ onChange, onSave });
  handlers.current = { onChange, onSave };

  useEffect(() => {
    const editor = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          ...base(path),
          lock.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
          keymap.of([
            { key: "Mod-s", preventDefault: true, run: () => (handlers.current.onSave(), true) },
            ...defaultKeymap,
            ...historyKeymap,
            indentWithTab,
          ]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) handlers.current.onChange(update.state.doc.toString());
          }),
          EditorView.contentAttributes.of({ "aria-label": `Code of ${path}` }),
        ],
      }),
    });
    view.current = editor;
    return () => editor.destroy();
    // A new file gets a new editor; `value` and `readOnly` are synced below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEffect(() => {
    const editor = view.current;
    if (!editor || editor.state.doc.toString() === value) return;
    editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
  }, [value]);

  useEffect(() => {
    view.current?.dispatch({
      effects: lock.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    });
  }, [readOnly]);

  return <div ref={host} className="ide-code" />;
}

export function DiffEditor({ path, before, after }: { path: string; before: string; after: string }) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const editor = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: after,
        extensions: [
          ...base(path),
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
          unifiedMergeView({ original: before, mergeControls: false, highlightChanges: false, gutter: true }),
          EditorView.contentAttributes.of({ "aria-label": `Changes in ${path}` }),
        ],
      }),
    });
    return () => editor.destroy();
  }, [path, before, after]);

  return <div ref={host} className="ide-code" />;
}
