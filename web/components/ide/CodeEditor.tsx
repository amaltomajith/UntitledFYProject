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
  { tag: [tags.keyword, tags.operatorKeyword, tags.modifier], color: "var(--syn-keyword)" },
  { tag: [tags.string, tags.special(tags.string), tags.regexp], color: "var(--syn-string)" },
  { tag: [tags.comment, tags.meta], color: "var(--ink-3)", fontStyle: "italic" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--syn-number)" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName)], color: "var(--syn-function)" },
  { tag: [tags.className, tags.typeName], color: "var(--syn-type)" },
  { tag: [tags.tagName, tags.heading], color: "var(--syn-keyword)", fontWeight: "600" },
  { tag: tags.attributeName, color: "var(--ink-2)" },
]);

const theme = EditorView.theme(
  {
    "&": { height: "100%", fontSize: "13px", backgroundColor: "transparent", color: "var(--ink)" },
    ".cm-scroller": { fontFamily: "var(--f-mono)", lineHeight: "1.6", overflow: "auto" },
    ".cm-content": { caretColor: "var(--ink)", paddingBlock: "10px" },
    ".cm-gutters": { backgroundColor: "transparent", color: "var(--ink-3)", border: "none" },
    ".cm-activeLine": { backgroundColor: "rgba(255, 255, 255, 0.04)" },
    ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--ink)" },
    ".cm-cursor": { borderLeftColor: "var(--ink)" },
    "&.cm-focused": { outline: "none" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "var(--select) !important",
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
  reveal?: { line: number } | null;
};

export default function CodeEditor({ path, value, readOnly, onChange, onSave, reveal }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const lock = useRef(new Compartment());
  const handlers = useRef({ onChange, onSave });
  handlers.current = { onChange, onSave };
  const crlf = useRef(false);

  useEffect(() => {
    crlf.current = value.includes("\r\n");
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
            if (!update.docChanged) return;
            const text = update.state.doc.toString();
            handlers.current.onChange(crlf.current ? text.replace(/\n/g, "\r\n") : text);
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
    // CodeMirror keeps \n internally, so a CRLF file is not a change.
    const editor = view.current;
    if (!editor || editor.state.doc.toString() === value.replace(/\r\n?/g, "\n")) return;
    editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
  }, [value]);

  useEffect(() => {
    view.current?.dispatch({
      effects: lock.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    });
  }, [readOnly]);

  useEffect(() => {
    const editor = view.current;
    if (!editor || !reveal) return;
    const line = editor.state.doc.line(Math.min(reveal.line, editor.state.doc.lines));
    editor.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
    editor.focus();
  }, [reveal]);

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
