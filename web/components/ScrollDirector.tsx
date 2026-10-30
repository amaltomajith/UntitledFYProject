"use client";

import { useEffect } from "react";
import Lenis from "lenis";
import { gsap, ScrollTrigger } from "@/lib/gsap";
import { scene } from "@/lib/sceneState";

/** Smooth scroll plus the triggers that feed the 3D scene and the chapter indicator. */
export function ScrollDirector() {
  useEffect(() => {
    let lenis: Lenis | null = null;
    let tick: ((time: number) => void) | null = null;

    if (!scene.reduced) {
      lenis = new Lenis({ lerp: 0.1, smoothWheel: true });
      scene.lenis = lenis;
      lenis.on("scroll", ScrollTrigger.update);
      tick = (time) => lenis?.raf(time * 1000);
      gsap.ticker.add(tick);
      gsap.ticker.lagSmoothing(0);
    }

    const ctx = gsap.context(() => {
      if (!scene.reduced) {
        const feed = (trigger: string, start: string, end: string, key: "heroOut" | "about" | "howIn" | "how" | "capIn") =>
          ScrollTrigger.create({ trigger, start, end, onUpdate: (s) => void (scene[key] = s.progress) });
        feed("#hero", "top top", "bottom top", "heroOut");
        feed("#about", "top top", "bottom bottom", "about");
        feed("#how", "top bottom", "top top", "howIn");
        feed("#how", "top top", "bottom bottom", "how");
        feed("#capabilities", "top bottom", "top 25%", "capIn");
      }
      document.querySelectorAll<HTMLElement>("[data-chapter]").forEach((el) => {
        const idx = Number(el.dataset.chapter);
        ScrollTrigger.create({
          trigger: el,
          start: "top 55%",
          end: "bottom 55%",
          onToggle: (s) => {
            if (s.isActive) window.dispatchEvent(new CustomEvent("chapter", { detail: idx }));
          },
        });
      });
    });

    document.fonts?.ready.then(() => ScrollTrigger.refresh());

    return () => {
      ctx.revert();
      if (tick) gsap.ticker.remove(tick);
      lenis?.destroy();
      scene.lenis = null;
    };
  }, []);

  return null;
}

/** Anchor clicks go through Lenis so the jump is smooth and ScrollTrigger stays in sync. */
export function scrollToHash(e: React.MouseEvent<HTMLAnchorElement>) {
  const href = e.currentTarget.getAttribute("href");
  if (!href?.startsWith("#") || !scene.lenis) return;
  const target = document.querySelector<HTMLElement>(href);
  if (!target) return;
  e.preventDefault();
  scene.lenis.scrollTo(target, { duration: 1.4 });
}
