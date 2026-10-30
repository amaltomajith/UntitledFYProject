"use client";

import { ArrowUpRight } from "@phosphor-icons/react";
import { Command } from "@/components/Command";
import { CTA_LABEL, GITHUB_URL, START } from "@/lib/content";

export function GetStarted() {
  return (
    <section id="start" data-chapter="5" className="start">
      <div className="container start__inner">
        <div className="start__grid">
          <div>
            <h2 className="display-3">{START.title}</h2>
            <p className="lead">{START.note}</p>

            <ol className="cmds">
              {START.commands.map((c) => (
                <Command key={c.label} {...c} />
              ))}
            </ol>
          </div>

          <div className="results">
            <h3 className="results__title">{START.results.title}</h3>
            <p className="results__body">{START.results.body}</p>
            <dl className="metrics">
              {START.results.stats.map((s) => (
                <div key={s.label} className="metric">
                  <dt>{s.label}</dt>
                  <dd>{s.value}</dd>
                </div>
              ))}
            </dl>
            <p className="results__caveat">{START.results.caveat}</p>
          </div>
        </div>

        <div className="closing">
          <p className="display-2 closing__title">{START.closing}</p>
          <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="btn btn--primary">
            {CTA_LABEL}
            <ArrowUpRight size={16} weight="bold" aria-hidden="true" />
          </a>
        </div>
      </div>
    </section>
  );
}
