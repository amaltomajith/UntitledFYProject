"use client";

import { useEffect, useRef } from "react";
import { ArrowCounterClockwise, Check, GitBranch, MagnifyingGlass } from "@phosphor-icons/react";
import { AGENTS } from "@/lib/agents";
import { CAPABILITIES } from "@/lib/content";
import { gsap, ScrollTrigger } from "@/lib/gsap";
import { scene } from "@/lib/sceneState";

const C = CAPABILITIES.cells;

const TIERS = [
  { name: "No LLM", agents: ["RepoScout", "TestRunner", "Linter", "DocsSearch"] },
  { name: "Fast models", agents: ["Planner", "Reviewer", "Concluder", "Terminator"] },
  { name: "Strong models", agents: ["Coder", "Debugger", "TestWriter"] },
];

const CONTEXT = ["Task", "Test digest", "Relevant source", "Callers", "Last 3 rollbacks", "Diff"];

const TIMELINE = [
  { ok: true, label: "Checkpoint" },
  { ok: true, label: "Checkpoint" },
  { ok: false, label: "Regression, rolled back" },
  { ok: true, label: "Checkpoint" },
];

const VARIANTS = ["Model tier", "Temperature", "Strategy"];

const REWARD = [
  ["r =", "pass rate"],
  ["+ 0.5 ×", "success"],
  ["− 0.5 ×", "regression"],
  ["− 1 ×", "integrity violation"],
  ["+ 0.1 ×", "lint clean"],
];

/** Moves the border spotlight to the pointer. */
function spot(e: React.PointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--mx", `${e.clientX - r.left}px`);
  el.style.setProperty("--my", `${e.clientY - r.top}px`);
}

export function Capabilities() {
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = root.current;
    if (scene.reduced || !el) return;
    const ctx = gsap.context(() => {
      const cells = gsap.utils.toArray<HTMLElement>(".cell");
      gsap.set(cells, { autoAlpha: 0, y: 36 });
      ScrollTrigger.batch(cells, {
        start: "top 88%",
        once: true,
        onEnter: (batch) =>
          gsap.to(batch, { autoAlpha: 1, y: 0, stagger: 0.08, duration: 1, ease: "expo.out", overwrite: true }),
      });
    }, el);
    return () => ctx.revert();
  }, []);

  // AGENTS is the source of truth for names; keep the tier lists honest if it changes.
  const known = new Set(AGENTS.map((a) => a.name));

  return (
    <section ref={root} id="capabilities" data-chapter="4" className="caps">
      <div className="container">
        <h2 className="display-3">{CAPABILITIES.title}</h2>

        <div className="bento">
          <article className="cell cell--routing" onPointerMove={spot}>
            <h3 className="cell__title">{C.routing.title}</h3>
            <p className="cell__body">{C.routing.body}</p>
            <div className="tiers">
              {TIERS.map((tier) => (
                <div key={tier.name} className="tier">
                  <span className="tier__name">{tier.name}</span>
                  <ul className="tier__agents">
                    {tier.agents.filter((a) => known.has(a)).map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </article>

          <article className="cell cell--context" onPointerMove={spot}>
            <h3 className="cell__title">{C.context.title}</h3>
            <p className="cell__body">{C.context.body}</p>
            <ol className="budget" aria-label="Snapshot contents in priority order">
              {CONTEXT.map((item, i) => (
                <li key={item} style={{ width: `${100 - i * 9}%` }}>
                  {item}
                </li>
              ))}
            </ol>
          </article>

          <article className="cell cell--scouting" onPointerMove={spot}>
            <MagnifyingGlass size={36} weight="light" className="cell__icon" aria-hidden="true" />
            <h3 className="cell__title">{C.scouting.title}</h3>
            <p className="cell__body">{C.scouting.body}</p>
          </article>

          <article className="cell cell--sandbox" onPointerMove={spot}>
            <h3 className="cell__title">{C.sandbox.title}</h3>
            <p className="cell__body">{C.sandbox.body}</p>
            <ol className="commits" aria-label="Example checkpoint history">
              {TIMELINE.map((c, i) => (
                <li key={i} className={c.ok ? "commit" : "commit commit--rollback"}>
                  <span className="commit__dot">
                    {c.ok ? (
                      <Check size={14} weight="bold" aria-hidden="true" />
                    ) : (
                      <ArrowCounterClockwise size={14} weight="bold" aria-hidden="true" />
                    )}
                  </span>
                  <span className="commit__label">{c.label}</span>
                </li>
              ))}
            </ol>
          </article>

          <article className="cell cell--explore" onPointerMove={spot}>
            <h3 className="cell__title">{C.explore.title}</h3>
            <p className="cell__body">{C.explore.body}</p>
            <ul className="variants" aria-label="What the three worktrees vary">
              {VARIANTS.map((v) => (
                <li key={v}>
                  <GitBranch size={18} weight="regular" aria-hidden="true" />
                  {v}
                </li>
              ))}
            </ul>
          </article>

          <article className="cell cell--reward" onPointerMove={spot}>
            <h3 className="cell__title">{C.reward.title}</h3>
            <p className="cell__body">{C.reward.body}</p>
            <div className="formula" aria-label="Reward: pass rate plus 0.5 times success, minus 0.5 times regression, minus 1 times integrity violation, plus 0.1 times lint clean">
              {REWARD.map(([op, term]) => (
                <div key={term} className="formula__row" aria-hidden="true">
                  <span className="formula__op">{op}</span>
                  <span className="formula__term">{term}</span>
                </div>
              ))}
            </div>
          </article>
        </div>
      </div>
    </section>
  );
}
