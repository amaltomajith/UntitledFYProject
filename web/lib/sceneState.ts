// One mutable object shared by the scroll director (writer) and the WebGL scene (reader).
// Continuous values never go through React state, so scrolling never re-renders the tree.

import type Lenis from "lenis";

export const scene = {
  heroOut: 0, // 0..1 while the hero scrolls away (tiles move from carousel into the ring)
  about: 0, // 0..1 across the pinned About chapter
  mcpIn: 0, // 0..1 while the MCP chapter scrolls in (ring -> single stack)
  mcpX: 0, // centre of the stack slot, as a fraction of the viewport from its centre
  mcpY: 0,
  mcpS: 0.26, // slot size as a fraction of viewport height
  howIn: 0, // 0..1 while "How it works" scrolls in (stack -> agent grid)
  how: 0, // 0..1 across the pinned "How it works" chapter
  capIn: 0, // 0..1 while Capabilities scrolls in (grid -> deep background)
  hovered: -1,
  chipEl: null as HTMLElement | null,
  reduced: typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  lenis: null as Lenis | null,
};

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
export const smooth = (v: number) => {
  const t = clamp01(v);
  return t * t * (3 - 2 * t);
};
/** Remap v from [a, b] to [0, 1], clamped. */
export const range = (v: number, a: number, b: number) => clamp01((v - a) / (b - a));
