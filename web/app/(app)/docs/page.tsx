import type { Metadata } from "next";
import { Command } from "@/components/Command";
import { DOCS } from "@/lib/content";

export const metadata: Metadata = { title: "Docs · CompactCode" };

export default function DocsPage() {
  return (
    <>
      <header className="page-head">
        <h1 className="page-title">{DOCS.title}</h1>
        <p className="page-lead">{DOCS.lead}</p>
      </header>
      <div className="docs">
        <nav className="docs__toc" aria-label={DOCS.title}>
          {DOCS.sections.map((s) => (
            <a key={s.id} href={`#${s.id}`}>
              {s.title}
            </a>
          ))}
          <a href="#limits">{DOCS.limits.title}</a>
          <a href="#trouble">{DOCS.trouble.title}</a>
        </nav>
        <div className="docs__body">
          {DOCS.sections.map((s) => (
            <section key={s.id} id={s.id} className="doc">
              <h2 className="doc__title">{s.title}</h2>
              <p className="doc__text">{s.body}</p>
              <ol className="cmds cmds--tight">
                {s.commands.map((c) => (
                  <Command key={c.label} {...c} />
                ))}
              </ol>
              {s.note ? <p className="muted">{s.note}</p> : null}
            </section>
          ))}
          <section id="limits" className="doc">
            <h2 className="doc__title">{DOCS.limits.title}</h2>
            <ul className="doc__list">
              {DOCS.limits.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
          <section id="trouble" className="doc">
            <h2 className="doc__title">{DOCS.trouble.title}</h2>
            <dl className="faq">
              {DOCS.trouble.items.map((item) => (
                <div key={item.q}>
                  <dt>{item.q}</dt>
                  <dd>{item.a}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
      </div>
    </>
  );
}
