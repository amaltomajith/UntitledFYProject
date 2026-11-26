// Syntax colours for code blocks in the chat, with the editor's own parsers. Loaded only when a reply has code.

import { classHighlighter, highlightCode } from "@lezer/highlight";
import { cssLanguage } from "@codemirror/lang-css";
import { htmlLanguage } from "@codemirror/lang-html";
import { javascriptLanguage, jsxLanguage, tsxLanguage, typescriptLanguage } from "@codemirror/lang-javascript";
import { jsonLanguage } from "@codemirror/lang-json";
import { pythonLanguage } from "@codemirror/lang-python";

const PARSERS = {
  py: pythonLanguage.parser,
  python: pythonLanguage.parser,
  js: javascriptLanguage.parser,
  javascript: javascriptLanguage.parser,
  mjs: javascriptLanguage.parser,
  jsx: jsxLanguage.parser,
  ts: typescriptLanguage.parser,
  typescript: typescriptLanguage.parser,
  tsx: tsxLanguage.parser,
  json: jsonLanguage.parser,
  css: cssLanguage.parser,
  html: htmlLanguage.parser,
} as const;

export type Span = { text: string; cls: string };

/** Lines of classed spans, or null for a language the editor does not know. Text is never turned into HTML. */
export function highlight(code: string, lang: string): Span[][] | null {
  const parser = PARSERS[lang.toLowerCase() as keyof typeof PARSERS];
  if (!parser) return null;
  const lines: Span[][] = [[]];
  highlightCode(
    code,
    parser.parse(code),
    classHighlighter,
    (text, cls) => lines[lines.length - 1].push({ text, cls }),
    () => lines.push([]),
  );
  return lines;
}
