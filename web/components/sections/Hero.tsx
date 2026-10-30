"use client";

import { useEffect, useRef, type CSSProperties } from "react";
import { ArrowRight, ArrowUpRight } from "@phosphor-icons/react";
import { CTA_LABEL, GITHUB_URL, HERO } from "@/lib/content";
import { gsap } from "@/lib/gsap";
import { scene } from "@/lib/sceneState";
import { scrollToHash } from "../ScrollDirector";

const delay = (ms: number) => ({ "--d": `${ms}ms` }) as CSSProperties;

export function Hero() {
  const content = useRef<HTMLDivElement>(null);

  // As the hero scrolls away the copy lifts and softens while the tiles take over.
  useEffect(() => {
    if (scene.reduced || !content.current) return;
    const ctx = gsap.context(() => {
      gsap.to(content.current, {
        autoAlpha: 0,
        y: -70,
        filter: "blur(8px)",
        ease: "none",
        scrollTrigger: { trigger: "#hero", start: "top top", end: "65% top", scrub: true },
      });
    });
    return () => ctx.revert();
  }, []);

  return (
    <section id="hero" data-chapter="0" className="hero">
      <div ref={content} className="container hero__content">
        <div className="hero__left">
          <p className="hero__eyebrow reveal" style={delay(60)}>
            {HERO.eyebrow}
          </p>
          <h1 className="display-1">
            {HERO.lines.map((line, i) => (
              <span key={line} className="line-mask">
                <span className="line" style={delay(160 + i * 120)}>
                  {line}
                </span>
              </span>
            ))}
          </h1>
        </div>
        <div className="hero__right">
          <p className="hero__sub reveal" style={delay(520)}>
            {HERO.sub}
          </p>
          <div className="hero__ctas reveal" style={delay(640)}>
            <a href={GITHUB_URL} target="_blank" rel="noreferrer" className="btn btn--primary">
              {CTA_LABEL}
              <ArrowUpRight size={16} weight="bold" aria-hidden="true" />
            </a>
            <a href="#mcp" onClick={scrollToHash} className="btn btn--ghost">
              {HERO.secondary}
              <ArrowRight size={16} weight="bold" aria-hidden="true" />
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
