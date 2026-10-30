"use client";

import { useEffect, useRef } from "react";
import { ABOUT } from "@/lib/content";
import { gsap } from "@/lib/gsap";
import { scene } from "@/lib/sceneState";
import { GlitchType, Highlight, Words, setGlitch } from "../text/effects";

/**
 * Chapter 01. A tall section with a sticky stage; one scrubbed timeline plays two slides:
 * the headline whose second line focuses word by word, then the manifesto with the
 * highlighted word and the glitch-typed last line. The tiles form a ring around it (scene).
 */
export function About() {
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = root.current;
    if (scene.reduced || !el) return;
    const glitch = el.querySelector<HTMLElement>("[data-glitch]");
    const ctx = gsap.context(() => {
      const blurIn = { filter: "blur(0px)", autoAlpha: 1, y: 0 };
      const tl = gsap.timeline({
        defaults: { ease: "none" },
        scrollTrigger: { trigger: el, start: "top top", end: "bottom bottom", scrub: 0.6 },
      });
      tl.fromTo(".about-a .chip", { autoAlpha: 0, y: 14 }, { autoAlpha: 1, y: 0, duration: 0.04 }, 0)
        .fromTo(".about-a .l1 .bw", { filter: "blur(14px)", autoAlpha: 0, y: 18 }, { ...blurIn, stagger: 0.015, duration: 0.07 }, 0.01)
        .fromTo(".about-a .l2 .bw", { filter: "blur(18px)", autoAlpha: 0.12, y: 0 }, { ...blurIn, stagger: 0.035, duration: 0.1 }, 0.09)
        .fromTo(".about-a .sub", { autoAlpha: 0, y: 16 }, { autoAlpha: 1, y: 0, duration: 0.05 }, 0.25)
        .to(".about-a", { autoAlpha: 0, y: -50, filter: "blur(10px)", duration: 0.07 }, 0.4)
        .fromTo(".about-b", { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.02 }, 0.45)
        .fromTo(".about-b .m1 .bw", { filter: "blur(14px)", autoAlpha: 0.1, y: 0 }, { ...blurIn, stagger: 0.018, duration: 0.07 }, 0.46)
        .fromTo(".about-b .m2 .bw", { filter: "blur(16px)", autoAlpha: 0.1, y: 0 }, { ...blurIn, stagger: 0.03, duration: 0.08 }, 0.56)
        .fromTo(".about-b .hl-box", { scaleX: 0 }, { scaleX: 1, duration: 0.05, ease: "power2.out" }, 0.66);
      if (glitch) {
        setGlitch(glitch, 0);
        const proxy = { p: 0 };
        tl.to(proxy, { p: 1, duration: 0.2, onUpdate: () => setGlitch(glitch, proxy.p) }, 0.72);
      }
      tl.to({}, { duration: 0.08 });
    }, el);
    return () => {
      ctx.revert();
      if (glitch) setGlitch(glitch, 1);
    };
  }, []);

  return (
    <section ref={root} id="about" data-chapter="1" className="about">
      <div className="about__stage">
        <div className="slide about-a">
          <span className="chip">
            <span className="chip__i">01</span>
            <span className="chip__t">{ABOUT.chip}</span>
          </span>
          <h2 className="display-2">
            <span className="l1 block">
              <Words text={ABOUT.line1} />
            </span>
            <span className="l2 block">
              <Words text={ABOUT.line2} />
            </span>
          </h2>
          <p className="sub">{ABOUT.sub}</p>
        </div>

        <div className="slide about-b">
          <p className="manifesto">
            <span className="m1 block">
              <Words text={ABOUT.m1} />
            </span>
            <span className="m2 block">
              <Words text={ABOUT.m2[0]} /> <Highlight>{ABOUT.m2[1]}</Highlight> <Words text={ABOUT.m2[2]} />
            </span>
            <span className="m3 block">
              <GlitchType text={ABOUT.m3} />
            </span>
          </p>
        </div>
      </div>
    </section>
  );
}
