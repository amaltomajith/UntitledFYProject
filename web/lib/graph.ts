// The Memory Graph: its data, and the drawing, which is pure so it can be read apart from React.

import type { SimulationNodeDatum } from "d3-force";

export type NodeKind = "session" | "episode" | "file" | "symbol";
export const NODE_KINDS: NodeKind[] = ["session", "episode", "file", "symbol"];

export type GraphNode = {
  id: string;
  kind: NodeKind;
  label: string;
  file: string | null;
  line: number | null;
  end: number | null;
  session: string | null;
  sub?: string | null;
  turn?: number;
};

export type GraphEdge = { src: string; dst: string; kind: string };

export type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total: number;
  truncated: boolean;
  saved: number;
  scripts: boolean;
};

export type NodeDetail = GraphNode & {
  links: GraphEdge[];
  signature?: string;
  doc?: string;
  partial?: boolean;
  prompt?: string;
  agents?: string[];
  check?: string;
  files?: string[];
  tokens?: number;
  at?: number;
};

export type SimNode = GraphNode & SimulationNodeDatum;
export type SimLink = { source: SimNode; target: SimNode; kind: string };
export type Camera = { x: number; y: number; k: number };
export type Colors = Record<NodeKind | "edge" | "hot" | "text" | "halo" | "bad", string>;

export const RADIUS: Record<NodeKind, number> = { session: 11, episode: 8, file: 7, symbol: 4.5 };
const ARROW = 5;
const LABEL_CHARS = 28;
export const PULSE_MS = 1200;
const FIT_PAD = 50;
const FIT_MIN = 0.3;
const FIT_MAX = 2;

export function readColors(el: Element): Colors {
  const css = getComputedStyle(el);
  const token = (name: string) => css.getPropertyValue(`--graph-${name}`).trim();
  return {
    session: token("session"),
    episode: token("episode"),
    file: token("file"),
    symbol: token("symbol"),
    edge: token("edge"),
    hot: token("hot"),
    text: token("text"),
    halo: token("halo"),
    bad: token("bad"),
  };
}

/** Shape carries the kind as well as colour: hexagon, diamond, square, circle. */
function trace(ctx: CanvasRenderingContext2D, kind: NodeKind, x: number, y: number, r: number) {
  ctx.beginPath();
  if (kind === "symbol") {
    ctx.arc(x, y, r, 0, Math.PI * 2);
  } else if (kind === "file") {
    ctx.rect(x - r, y - r, r * 2, r * 2);
  } else {
    const sides = kind === "session" ? 6 : 4;
    for (let i = 0; i < sides; i++) {
      const angle = (Math.PI * 2 * i) / sides + (kind === "session" ? Math.PI / 6 : 0);
      ctx[i ? "lineTo" : "moveTo"](x + r * Math.cos(angle), y + r * Math.sin(angle));
    }
    ctx.closePath();
  }
}

function drawEdge(ctx: CanvasRenderingContext2D, link: SimLink, hot: boolean, colors: Colors, k: number) {
  const { source: a, target: b } = link;
  const dx = (b.x ?? 0) - (a.x ?? 0);
  const dy = (b.y ?? 0) - (a.y ?? 0);
  const length = Math.hypot(dx, dy);
  if (length < 1) return;
  const ux = dx / length;
  const uy = dy / length;
  const tipX = (b.x ?? 0) - ux * (RADIUS[b.kind] + 2);
  const tipY = (b.y ?? 0) - uy * (RADIUS[b.kind] + 2);
  ctx.strokeStyle = ctx.fillStyle = hot ? colors.hot : colors.edge;
  ctx.lineWidth = (hot ? 1.4 : 0.8) / k;
  ctx.beginPath();
  ctx.moveTo(a.x ?? 0, a.y ?? 0);
  ctx.lineTo(tipX, tipY);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(tipX, tipY);
  ctx.lineTo(tipX - ux * ARROW - uy * ARROW * 0.5, tipY - uy * ARROW + ux * ARROW * 0.5);
  ctx.lineTo(tipX - ux * ARROW + uy * ARROW * 0.5, tipY - uy * ARROW - ux * ARROW * 0.5);
  ctx.closePath();
  ctx.fill();
  if (hot) {
    ctx.font = `${9 / k}px sans-serif`;
    ctx.fillStyle = colors.text;
    ctx.fillText(link.kind, ((a.x ?? 0) + (b.x ?? 0)) / 2, ((a.y ?? 0) + (b.y ?? 0)) / 2);
  }
}

function shortLabel(label: string) {
  return label.length > LABEL_CHARS ? `${label.slice(0, LABEL_CHARS - 1)}...` : label;
}

export type Scene = {
  width: number;
  height: number;
  camera: Camera;
  nodes: SimNode[];
  links: SimLink[];
  colors: Colors;
  hover: string | null;
  selected: string | null;
  pulses: Map<string, number>;
  now: number;
};

export function draw(ctx: CanvasRenderingContext2D, scene: Scene) {
  const { width, height, camera, nodes, links, colors, hover, selected, pulses, now } = scene;
  const focus = hover ?? selected;
  ctx.clearRect(0, 0, width, height);
  ctx.save();
  ctx.translate(camera.x, camera.y);
  ctx.scale(camera.k, camera.k);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  for (const link of links) {
    drawEdge(ctx, link, link.source.id === focus || link.target.id === focus, colors, camera.k);
  }

  for (const node of nodes) {
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const r = RADIUS[node.kind];
    const started = pulses.get(node.id);
    if (started !== undefined && now - started < PULSE_MS) {
      const age = (now - started) / PULSE_MS;
      ctx.globalAlpha = 1 - age;
      ctx.strokeStyle = colors.hot;
      ctx.lineWidth = 2 / camera.k;
      ctx.beginPath();
      ctx.arc(x, y, r + 4 + age * 12, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    trace(ctx, node.kind, x, y, r);
    const failed = node.kind === "episode" && node.sub !== "done" && node.sub !== "planned";
    ctx.fillStyle = failed ? colors.bad : colors[node.kind];
    ctx.fill();
    if (node.id === focus) {
      ctx.strokeStyle = colors.text;
      ctx.lineWidth = 1.6 / camera.k;
      ctx.stroke();
    }
  }

  ctx.font = `${10 / camera.k}px sans-serif`;
  for (const node of nodes) {
    const wanted = node.id === focus || node.kind === "session" || camera.k > 1.4 || (node.kind !== "symbol" && camera.k > 0.9);
    if (!wanted) continue;
    const text = shortLabel(node.kind === "file" ? (node.label.split("/").pop() ?? node.label) : node.label);
    const y = (node.y ?? 0) + RADIUS[node.kind] + 8 / camera.k;
    ctx.lineWidth = 3 / camera.k;
    ctx.strokeStyle = colors.halo;
    ctx.strokeText(text, node.x ?? 0, y);
    ctx.fillStyle = colors.text;
    ctx.fillText(text, node.x ?? 0, y);
  }
  ctx.restore();
}

/** A camera that shows every node, for as long as the user has not moved it themselves. */
export function fitted(nodes: SimNode[], width: number, height: number): Camera {
  const xs = nodes.map((node) => node.x ?? 0);
  const ys = nodes.map((node) => node.y ?? 0);
  const left = Math.min(...xs);
  const right = Math.max(...xs);
  const top = Math.min(...ys);
  const bottom = Math.max(...ys);
  const k = Math.min(FIT_MAX, Math.max(FIT_MIN, Math.min(width / (right - left + FIT_PAD * 2), height / (bottom - top + FIT_PAD * 2))));
  return { x: width / 2 - ((left + right) / 2) * k, y: height / 2 - ((top + bottom) / 2) * k, k };
}

export function nodeAt(nodes: SimNode[], camera: Camera, px: number, py: number): SimNode | null {
  const x = (px - camera.x) / camera.k;
  const y = (py - camera.y) / camera.k;
  let best: SimNode | null = null;
  let distance = Infinity;
  for (const node of nodes) {
    const away = Math.hypot((node.x ?? 0) - x, (node.y ?? 0) - y);
    if (away <= RADIUS[node.kind] + 4 / camera.k && away < distance) {
      best = node;
      distance = away;
    }
  }
  return best;
}
