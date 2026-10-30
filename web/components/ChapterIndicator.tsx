"use client";

import { useEffect, useRef } from "react";
import { CHAPTERS } from "@/lib/content";
import { pad2 } from "@/lib/agents";
import { scene } from "@/lib/sceneState";

const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%/";

/** Bottom-left "00 | CHAPTER WELCOME" badge; the label decodes when the chapter changes. */
export function ChapterIndicator() {
  const num = useRef<HTMLSpanElement>(null);
  const label = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    let timer = 0;
    const onChapter = (e: Event) => {
      const idx = (e as CustomEvent<number>).detail;
      const final = CHAPTERS[idx]?.toUpperCase() ?? "";
      if (num.current) num.current.textContent = pad2(idx);
      window.clearInterval(timer);
      if (!label.current) return;
      if (scene.reduced) {
        label.current.textContent = final;
        return;
      }
      let frame = 0;
      const frames = 12;
      timer = window.setInterval(() => {
        frame++;
        const settled = Math.floor((frame / frames) * final.length);
        let out = "";
        for (let i = 0; i < final.length; i++) {
          out += i < settled || final[i] === " " ? final[i] : GLYPHS[(Math.random() * GLYPHS.length) | 0];
        }
        if (label.current) label.current.textContent = out;
        if (frame >= frames) window.clearInterval(timer);
      }, 30);
    };
    window.addEventListener("chapter", onChapter);
    return () => {
      window.removeEventListener("chapter", onChapter);
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="chapter" aria-hidden="true">
      <span ref={num} className="chapter__num">
        00
      </span>
      <span className="chapter__box">
        <span className="chapter__kicker">Chapter</span>
        <span ref={label} className="chapter__label">
          WELCOME
        </span>
      </span>
    </div>
  );
}
