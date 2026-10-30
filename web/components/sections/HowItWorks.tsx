"use client";

import { useEffect, useRef } from "react";
import { HOW } from "@/lib/content";
import { pad2 } from "@/lib/agents";
import { ScrollTrigger } from "@/lib/gsap";
import { scene } from "@/lib/sceneState";

const TOTAL = 12;

/** Chapter 02. Sticky step list; the scene moves the agent tiles in step with it. */
export function HowItWorks() {
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const steps = Array.from(el.querySelectorAll<HTMLElement>("[data-step]"));
    const num = el.querySelector<HTMLElement>("[data-pass-num]");
    const line = el.querySelector<HTMLElement>("[data-pass-line]");

    const setPass = (passed: number) => {
      if (num) num.textContent = `${passed}/${TOTAL}`;
      if (line) {
        line.style.transform = `scaleX(${passed / TOTAL})`;
        line.dataset.done = String(passed === TOTAL);
      }
    };

    if (scene.reduced) {
      steps.forEach((s) => (s.dataset.state = "active"));
      setPass(TOTAL);
      return;
    }

    let last = -1;
    let lastPass = -1;
    const render = (p: number) => {
      const stepF = p * steps.length;
      const k = Math.min(steps.length - 1, Math.floor(stepF));
      if (k !== last) {
        last = k;
        steps.forEach((s, i) => (s.dataset.state = i < k ? "done" : i === k ? "active" : "todo"));
      }
      // The walkthrough in docs/CODING_ASSISTANT.md: 10/12 before the fix, 11/12 after the first patch, 12/12 after the debug pass.
      const f = k === steps.length - 1 ? stepF - k : 0;
      const passed = 10 + (f > 0.3 ? 1 : 0) + (f > 0.65 ? 1 : 0);
      if (passed !== lastPass) {
        lastPass = passed;
        setPass(passed);
      }
    };

    const st = ScrollTrigger.create({
      trigger: el,
      start: "top top",
      end: "bottom bottom",
      onUpdate: (s) => render(s.progress),
    });
    render(st.progress);
    return () => st.kill();
  }, []);

  return (
    <section ref={root} id="how" data-chapter="2" className="how">
      <div className="how__stage">
        <div className="container how__grid">
          <div className="how__text">
            <span className="chip">
              <span className="chip__i">02</span>
              <span className="chip__t">{HOW.chip}</span>
            </span>
            <h2 className="display-3">{HOW.title}</h2>
            <ol className="steps">
              {HOW.steps.map((s, i) => (
                <li key={s.verb} className="step" data-step={i} data-state={i === 0 ? "active" : "todo"}>
                  <span className="step__n">{pad2(i + 1)}</span>
                  <div>
                    <h3 className="step__verb">{s.verb}</h3>
                    <div className="step__more">
                      <p className="step__body">{s.body}</p>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
            <div className="pass">
              <div className="pass__row">
                <span className="pass__label">{HOW.passLabel}</span>
                <span className="pass__num" data-pass-num>
                  10/{TOTAL}
                </span>
              </div>
              <div className="pass__rail">
                <div className="pass__line" data-pass-line style={{ transform: `scaleX(${10 / TOTAL})` }} />
              </div>
              <p className="pass__note">{HOW.episodeLabel}</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
