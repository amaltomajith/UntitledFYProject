"use client";

import { ArrowUpRight } from "@phosphor-icons/react";
import { CTA_LABEL, GITHUB_URL, NAV } from "@/lib/content";
import { scrollToHash } from "./ScrollDirector";

export function Nav() {
  return (
    <header className="nav">
      <div className="nav__inner">
        <a href="#hero" onClick={scrollToHash} className="wordmark" aria-label="Puppeteer-Code, back to top">
          <span className="wordmark__mark" aria-hidden="true" />
          Puppeteer-Code
        </a>
        <nav aria-label="Sections" className="nav__links">
          {NAV.map((item) => (
            <a key={item.href} href={item.href} onClick={scrollToHash} className="nav__link">
              {item.label}
              <span className="nav__plus" aria-hidden="true" />
            </a>
          ))}
        </nav>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="btn btn--primary btn--sm">
          {CTA_LABEL}
          <ArrowUpRight size={14} weight="bold" aria-hidden="true" />
        </a>
      </div>
    </header>
  );
}
