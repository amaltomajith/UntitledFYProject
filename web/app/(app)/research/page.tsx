import type { Metadata } from "next";
import Link from "next/link";
import { RESEARCH } from "@/lib/content";

export const metadata: Metadata = { title: "Research · Puppeteer-Code" };

export default function ResearchPage() {
  return (
    <>
      <header className="page-head">
        <h1 className="page-title">{RESEARCH.title}</h1>
        <p className="page-lead">{RESEARCH.lead}</p>
      </header>
      <div className="docs__body docs__body--solo">
        {RESEARCH.sections.map((s) => (
          <section key={s.title} className="doc">
            <h2 className="doc__title">{s.title}</h2>
            {s.body.map((p) => (
              <p key={p} className="doc__text">
                {p}
              </p>
            ))}
            {s.formula ? <code className="doc__formula">{s.formula}</code> : null}
          </section>
        ))}
        <section className="doc">
          <h2 className="doc__title">{RESEARCH.limits.title}</h2>
          <ul className="doc__list">
            {RESEARCH.limits.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <Link href="/benchmarks" className="btn btn--ghost btn--sm notice__action">
            {RESEARCH.link}
          </Link>
        </section>
      </div>
    </>
  );
}
