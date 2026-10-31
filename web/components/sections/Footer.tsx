import { CTA_LABEL, FOOTER, GITHUB_URL } from "@/lib/content";

export function Footer() {
  return (
    <footer className="footer">
      <div className="container footer__inner">
        <span className="wordmark">
          <span className="wordmark__mark" aria-hidden="true" />
          CompactCode
        </span>
        <p className="footer__line">{FOOTER.line}</p>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="footer__link">
          {CTA_LABEL}
        </a>
      </div>
    </footer>
  );
}
