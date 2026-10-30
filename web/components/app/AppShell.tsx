"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowUpRight } from "@phosphor-icons/react";
import { APP_NAV, CTA_LABEL, FOOTER, GITHUB_URL, SERVER_STATUS } from "@/lib/content";
import { useServer } from "@/lib/useServer";

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { server } = useServer();

  return (
    <div className="app">
      <div className="cone cone--quiet" aria-hidden="true" />
      <a href="#app-content" className="skip-link">
        Skip to content
      </a>
      <header className="app__bar">
        <Link href="/" className="wordmark" aria-label="Puppeteer-Code home">
          <span className="wordmark__mark" aria-hidden="true" />
          Puppeteer-Code
        </Link>
        <nav aria-label="App" className="app__links">
          {APP_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="app__link"
              aria-current={path?.startsWith(item.href) ? "page" : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <span className="app__status" data-status={server.status} role="status">
          <span className="app__dot" aria-hidden="true" />
          {SERVER_STATUS[server.status]}
        </span>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="btn btn--ghost btn--sm app__gh">
          {CTA_LABEL}
          <ArrowUpRight size={14} weight="bold" aria-hidden="true" />
        </a>
      </header>
      <main id="app-content" className="app__main container">
        {children}
      </main>
      <footer className="app__foot container">{FOOTER.line}</footer>
    </div>
  );
}
