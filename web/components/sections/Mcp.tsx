"use client";

import { useEffect, useRef } from "react";
import { ArrowDown } from "@phosphor-icons/react";
import { MCP } from "@/lib/content";
import { gsap, ScrollTrigger } from "@/lib/gsap";
import { scene } from "@/lib/sceneState";

/**
 * Chapter 02. Sticky stage: the call on one side, the result on the other, and the slot between
 * them holds the 3D tiles, stacked into a single tool. The slot is measured so the stack lands on it.
 */
export function Mcp() {
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = root.current;
    if (scene.reduced || !el) return;
    const stage = el.querySelector<HTMLElement>(".mcp__stage");
    const slot = el.querySelector<HTMLElement>("[data-mcp-slot]");

    const measure = () => {
      if (!stage || !slot) return;
      const s = slot.getBoundingClientRect();
      const top = stage.getBoundingClientRect().top;
      scene.mcpX = (s.left + s.width / 2) / window.innerWidth - 0.5;
      scene.mcpY = 0.5 - (s.top - top + s.height / 2) / window.innerHeight;
      scene.mcpS = Math.min(s.width, s.height) / window.innerHeight;
    };
    measure();
    ScrollTrigger.addEventListener("refresh", measure);

    const ctx = gsap.context(() => {
      gsap
        .timeline({
          defaults: { ease: "none" },
          scrollTrigger: { trigger: el, start: "top 40%", end: "bottom bottom", scrub: 0.2 },
        })
        .from(".mcp__head > *", { autoAlpha: 0, y: 24, stagger: 0.05, duration: 0.18 }, 0)
        .from(".mcp__panel--call", { autoAlpha: 0, x: -36, duration: 0.18 }, 0.2)
        .from(".mcp__panel--result", { autoAlpha: 0, x: 36, duration: 0.18 }, 0.42)
        .from(".mcp__line", { autoAlpha: 0, stagger: 0.05, duration: 0.08 }, 0.5)
        .from(".mcp__foot", { autoAlpha: 0, duration: 0.1 }, 0.72)
        .to({}, { duration: 0.1 });
    }, el);

    return () => {
      ScrollTrigger.removeEventListener("refresh", measure);
      ctx.revert();
    };
  }, []);

  return (
    <section ref={root} id="mcp" data-chapter="2" className="mcp">
      <div className="mcp__stage">
        <div className="container mcp__inner">
          <div className="mcp__head">
            <span className="chip">
              <span className="chip__i">02</span>
              <span className="chip__t">{MCP.chip}</span>
            </span>
            <h2 className="display-3">{MCP.title}</h2>
            <ul className="mcp__clients">
              {MCP.clients.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>

          <div className="mcp__flow">
            <div className="mcp__panel mcp__panel--call">
              <p className="mcp__label">{MCP.call.label}</p>
              <pre className="mcp__code">
                <span className="mcp__fn">{MCP.call.fn}</span>(
                {MCP.call.args.map((a) => `\n  ${a},`).join("")}
                {"\n)"}
              </pre>
            </div>

            <div className="mcp__slot" data-mcp-slot aria-hidden="true">
              <ArrowDown size={22} className="mcp__arrow" />
            </div>

            <div className="mcp__panel mcp__panel--result">
              <p className="mcp__label">{MCP.result.label}</p>
              <ul className="mcp__code">
                {MCP.result.lines.map((l) => (
                  <li key={l} className="mcp__line">
                    {l}
                  </li>
                ))}
              </ul>
              <p className="mcp__note">{MCP.result.note}</p>
            </div>
          </div>

          <p className="mcp__foot">{MCP.foot}</p>
        </div>
      </div>
    </section>
  );
}
