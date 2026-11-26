"use client";

import { memo, useEffect, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { IDE } from "@/lib/content";
import type { Span } from "./highlight";
import { Codicon } from "./Codicon";

const COPIED_MS = 1400;

function useHighlight(code: string, lang: string) {
  const [lines, setLines] = useState<Span[][] | null>(null);
  useEffect(() => {
    let live = true;
    if (!lang) return;
    void import("./highlight").then(({ highlight }) => {
      if (live) setLines(highlight(code, lang));
    });
    return () => {
      live = false;
    };
  }, [code, lang]);
  return lines;
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const lines = useHighlight(code, lang);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), COPIED_MS);
    } catch {
      // The clipboard can be blocked; the code is still there to select.
    }
  };

  return (
    <figure className="ide-codeblock">
      <figcaption>
        <span className="ide-label">{lang || "text"}</span>
        <button type="button" onClick={() => void copy()} aria-label={`${IDE.chat.copy} ${IDE.chat.code(lang)}`}>
          <Codicon name={copied ? "check" : "copy"} />
          {copied ? IDE.chat.copied : IDE.chat.copy}
        </button>
      </figcaption>
      <pre>
        <code>
          {lines
            ? lines.map((line, i) => (
                <span key={i} className="ide-codeblock__line">
                  {line.map((span, j) => (span.cls ? <span key={j} className={span.cls}>{span.text}</span> : span.text))}
                  {i < lines.length - 1 ? "\n" : null}
                </span>
              ))
            : code}
        </code>
      </pre>
    </figure>
  );
}

const textOf = (node: ReactNode): string => {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  return "";
};

// Links open outside the IDE; images and raw HTML are never rendered, so a reply cannot load anything.
const COMPONENTS: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  img: ({ alt }) => <span className="ide-md__img">{alt ? `[${alt}]` : ""}</span>,
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const lang = /language-([\w+-]+)/.exec(className ?? "")?.[1] ?? "";
    const text = textOf(children);
    if (!lang && !text.includes("\n")) return <code>{children}</code>;
    return <CodeBlock code={text.replace(/\n$/, "")} lang={lang} />;
  },
  table: ({ children }) => (
    <div className="ide-md__table">
      <table>{children}</table>
    </div>
  ),
};

/** A reply as Markdown with GitHub tables and lists. No raw HTML: react-markdown escapes it by default. */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="ide-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  );
});
