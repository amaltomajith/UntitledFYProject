import { Fragment } from "react";

/* Text effects driven by scroll. Each renders its final, readable state in HTML; the
   scroll timelines (About, Hero) apply the start state only when motion is allowed. */

/** Splits text into word spans (`.bw`) for the blur-to-focus reveal. */
export function Words({ text }: { text: string }) {
  const words = text.split(" ");
  return (
    <>
      {words.map((w, i) => (
        <Fragment key={i}>
          <span className="bw">{w}</span>
          {i < words.length - 1 ? " " : null}
        </Fragment>
      ))}
    </>
  );
}

/** A word with a solid accent box behind it that wipes in from the left. */
export function Highlight({ children }: { children: string }) {
  return (
    <span className="hl bw">
      <span className="hl-box" aria-hidden="true" />
      <span className="hl-text">{children}</span>
    </span>
  );
}

/** Characters that type in behind a block cursor, preceded by flickering dither cells. */
export function GlitchType({ text }: { text: string }) {
  const words = text.split(" ");
  let idx = 0;
  return (
    <span className="glitch" data-glitch aria-label={text}>
      {words.map((w, wi) => (
        <Fragment key={wi}>
          <span className="gw" aria-hidden="true">
            {Array.from(w).map((ch) => (
              <span key={idx} className="gc" data-i={idx++}>
                {ch}
              </span>
            ))}
          </span>
          {wi < words.length - 1 ? " " : null}
        </Fragment>
      ))}
    </span>
  );
}

const cache = new WeakMap<HTMLElement, HTMLElement[]>();

/** p = 0: nothing shown. p = 1: fully typed. In between: typed | cursor | 3 dither cells | hidden. */
export function setGlitch(root: HTMLElement, p: number) {
  let cells = cache.get(root);
  if (!cells) {
    cells = Array.from(root.querySelectorAll<HTMLElement>(".gc"));
    cache.set(root, cells);
  }
  const n = cells.length;
  const head = p >= 1 ? n : Math.floor(p * (n + 1));
  for (let i = 0; i < n; i++) {
    let cls = "gc";
    if (i >= head) {
      if (p <= 0 || i > head + 3) cls = "gc gc--off";
      else if (i === head) cls = "gc gc--cursor";
      else cls = `gc gc--d gc--d${((i + head) % 3) + 1}`;
    }
    if (cells[i].className !== cls) cells[i].className = cls;
  }
}
