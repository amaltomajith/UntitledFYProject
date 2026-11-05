"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type Simulation } from "d3-force";
import { IDE } from "@/lib/content";
import {
  draw,
  fitted,
  NODE_KINDS,
  nodeAt,
  PULSE_MS,
  RADIUS,
  readColors,
  type Camera,
  type Colors,
  type GraphData,
  type NodeDetail,
  type NodeKind,
  type SimLink,
  type SimNode,
} from "@/lib/graph";
import { IdeError, ideCall, when, type IdeClient } from "@/lib/ide";

const G = IDE.graph;
const DEBOUNCE_MS = 300;
const SETTLE_TICKS = 300;
const CLICK_SLOP = 4;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 4;
const GRAVITY = 0.07;
const LINK_LENGTH = 72;

type Scope = "session" | "project";

type Props = {
  client: IdeClient;
  active: boolean;                 // a hidden folder's graph stops moving
  tick: number;
  touched: { path: string; n: number } | null;
  onOpen: (path: string, line?: number, end?: number) => void;
  onJump: (session: string, turn: number) => Promise<void>;
};

const failure = (e: unknown) => (e instanceof IdeError ? e.message : G.failed);

function Canvas({
  data,
  active,
  selected,
  touched,
  onSelect,
}: {
  data: GraphData;
  active: boolean;
  selected: string | null;
  touched: Props["touched"];
  onSelect: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const sim = useRef<Simulation<SimNode, SimLink> | null>(null);
  const nodes = useRef<SimNode[]>([]);
  const links = useRef<SimLink[]>([]);
  const camera = useRef<Camera>({ x: 0, y: 0, k: 1 });
  const size = useRef({ width: 0, height: 0 });
  const colors = useRef<Colors | null>(null);
  const hover = useRef<string | null>(null);
  const chosen = useRef(selected);
  const pulses = useRef(new Map<string, number>());
  const frame = useRef(0);
  const still = useRef(false);
  const awake = useRef(active);
  const moved = useRef(false);        // the user's own view is never taken away
  const select = useRef(onSelect);
  select.current = onSelect;
  chosen.current = selected;

  const paint = useCallback(() => {
    if (frame.current) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = 0;
      const el = canvas.current;
      const ctx = el?.getContext("2d");
      if (!el || !ctx || !colors.current) return;
      const dpr = window.devicePixelRatio || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const now = performance.now();
      draw(ctx, {
        ...size.current,
        camera: camera.current,
        nodes: nodes.current,
        links: links.current,
        colors: colors.current,
        hover: hover.current,
        selected: chosen.current,
        pulses: pulses.current,
        now,
      });
      for (const [id, started] of pulses.current) {
        if (now - started >= PULSE_MS) pulses.current.delete(id);
      }
      if (pulses.current.size && !still.current) paint();
    });
  }, []);

  const fit = useCallback(() => {
    const { width, height } = size.current;
    if (moved.current || !width || !nodes.current.length) return;
    camera.current = fitted(nodes.current, width, height);
  }, []);

  useEffect(() => {
    still.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    colors.current = readColors(canvas.current as HTMLCanvasElement);
    const simulation = forceSimulation<SimNode, SimLink>()
      .force("charge", forceManyBody<SimNode>().strength(-160).distanceMax(320))
      .force("x", forceX<SimNode>(0).strength(GRAVITY))      // keeps a node with no links near the rest
      .force("y", forceY<SimNode>(0).strength(GRAVITY))
      .force("collide", forceCollide<SimNode>((node) => RADIUS[node.kind] + 12))
      .stop();
    simulation.on("tick", () => {
      fit();
      paint();
    });
    sim.current = simulation;

    const box = host.current as HTMLDivElement;
    const resize = new ResizeObserver(() => {
      const el = canvas.current as HTMLCanvasElement;
      const { width, height } = box.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      size.current = { width, height };
      el.width = Math.round(width * dpr);
      el.height = Math.round(height * dpr);
      fit();
      paint();
    });
    resize.observe(box);
    return () => {
      resize.disconnect();
      simulation.stop();
      cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [paint, fit]);

  useEffect(() => {
    const simulation = sim.current;
    if (!simulation) return;
    const known = new Map(nodes.current.map((node) => [node.id, node]));
    const first = known.size === 0;
    nodes.current = data.nodes.map((node) => Object.assign(known.get(node.id) ?? {}, node) as SimNode);
    const fresh = data.edges.map((edge) => ({ source: edge.src, target: edge.dst, kind: edge.kind }));
    simulation.nodes(nodes.current);
    simulation.force(
      "link",
      forceLink<SimNode, { source: string | SimNode; target: string | SimNode; kind: string }>(fresh)
        .id((node) => node.id)
        .distance(LINK_LENGTH)
        .strength(0.5),
    );
    links.current = fresh as unknown as SimLink[];
    if (still.current) {
      simulation.alpha(1);
      simulation.tick(SETTLE_TICKS);
      fit();
      paint();
    } else {
      simulation.alpha(first ? 1 : 0.4);
      if (awake.current) simulation.restart();
    }
  }, [data, paint, fit]);

  useEffect(() => {
    awake.current = active;
    const simulation = sim.current;
    if (!simulation) return;
    if (!active) simulation.stop();
    else if (!still.current && simulation.alpha() > simulation.alphaMin()) simulation.restart();
  }, [active]);

  useEffect(paint, [selected, paint]);

  useEffect(() => {
    if (!touched) return;
    const match = nodes.current.find((node) => node.kind === "file" && node.file === touched.path);
    if (!match) return;
    pulses.current.set(match.id, performance.now());
    paint();
  }, [touched, paint]);

  useEffect(() => {
    const el = canvas.current as HTMLCanvasElement;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      const { x, y, k } = camera.current;
      const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, k * Math.exp(-e.deltaY * 0.0015)));
      const px = e.clientX - box.left;
      const py = e.clientY - box.top;
      moved.current = true;
      camera.current = { x: px - ((px - x) / k) * next, y: py - ((py - y) / k) * next, k: next };
      paint();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [paint]);

  const drag = useRef<{ node: SimNode | null; x: number; y: number; moved: number } | null>(null);
  const point = (e: React.PointerEvent) => {
    const box = (canvas.current as HTMLCanvasElement).getBoundingClientRect();
    return [e.clientX - box.left, e.clientY - box.top] as const;
  };

  return (
    <div ref={host} className="ide-graph__canvas">
      <canvas
        ref={canvas}
        role="img"
        aria-label={G.canvasLabel(data.nodes.length, data.edges.length)}
        onPointerDown={(e) => {
          const [px, py] = point(e);
          const node = nodeAt(nodes.current, camera.current, px, py);
          drag.current = { node, x: px, y: py, moved: 0 };
          e.currentTarget.setPointerCapture(e.pointerId);
          if (node) {
            node.fx = node.x;
            node.fy = node.y;
            if (!still.current) sim.current?.alphaTarget(0.2).restart();
          }
        }}
        onPointerMove={(e) => {
          const [px, py] = point(e);
          const active = drag.current;
          if (!active) {
            const found = nodeAt(nodes.current, camera.current, px, py)?.id ?? null;
            if (found !== hover.current) {
              hover.current = found;
              e.currentTarget.style.cursor = found ? "pointer" : "grab";
              paint();
            }
            return;
          }
          active.moved += Math.hypot(px - active.x, py - active.y);
          if (active.node) {
            active.node.fx = (px - camera.current.x) / camera.current.k;
            active.node.fy = (py - camera.current.y) / camera.current.k;
            if (still.current) {
              active.node.x = active.node.fx;
              active.node.y = active.node.fy;
            }
          } else {
            moved.current = true;
            camera.current = { ...camera.current, x: camera.current.x + px - active.x, y: camera.current.y + py - active.y };
          }
          active.x = px;
          active.y = py;
          paint();
        }}
        onPointerUp={() => {
          const active = drag.current;
          drag.current = null;
          if (!active) return;
          if (active.node) {
            active.node.fx = null;
            active.node.fy = null;
            sim.current?.alphaTarget(0);
            if (active.moved < CLICK_SLOP) select.current(active.node.id);
          }
        }}
        onPointerLeave={() => {
          hover.current = null;
          paint();
        }}
      />
    </div>
  );
}

function Inspector({ client, id, tick, onOpen, onJump }: { id: string } & Pick<Props, "client" | "tick" | "onOpen" | "onJump">) {
  const [node, setNode] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    setError(null);
    ideCall<NodeDetail>(client, `graph/node/${encodeURIComponent(id)}`).then(
      (found) => {
        if (!stale) setNode(found);
      },
      (e) => {
        if (stale) return;
        setNode(null);
        setError(failure(e));
      },
    );
    return () => {
      stale = true;
    };
  }, [client, id, tick]);

  if (error) {
    return (
      <p className="ide-graph__note" role="alert">
        {error}
      </p>
    );
  }
  if (!node) return <p className="ide-graph__note">{G.loading}</p>;
  const lines = node.line ? (node.end && node.end !== node.line ? `${node.line}-${node.end}` : `${node.line}`) : "";
  return (
    <div className="ide-graph__detail">
      <p className="ide-graph__kind">{node.sub && node.kind !== "episode" ? `${G.kinds[node.kind]}, ${node.sub}` : G.kinds[node.kind]}</p>
      <h3>{node.kind === "episode" ? G.turn(node.turn ?? 0) : node.label}</h3>
      {node.signature ? <code>{node.signature}</code> : null}
      {node.doc ? <p>{node.doc}</p> : null}
      {node.partial ? <p className="ide-graph__note">{G.partial}</p> : null}
      {node.kind === "episode" ? (
        <>
          <p>{node.prompt}</p>
          <dl>
            <dt>{G.result}</dt>
            <dd>{IDE.chat.status[node.sub ?? ""] ?? node.sub}</dd>
            {node.check ? (
              <>
                <dt>{G.check}</dt>
                <dd>{node.check}</dd>
              </>
            ) : null}
            <dt>{G.agents}</dt>
            <dd>{node.agents?.length ? Array.from(new Set(node.agents)).join(", ") : G.none}</dd>
            <dt>{G.tokens}</dt>
            <dd>{(node.tokens ?? 0).toLocaleString("en-US")}</dd>
            {node.at ? (
              <>
                <dt>{G.when}</dt>
                <dd>{when(node.at)}</dd>
              </>
            ) : null}
          </dl>
          {node.files?.length ? (
            <ul aria-label={G.changed}>
              {node.files.map((path) => (
                <li key={path}>
                  <button type="button" onClick={() => onOpen(path)}>
                    {path}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => void onJump(node.session as string, node.turn ?? 1).catch((e) => setError(failure(e)))}
          >
            {G.jump}
          </button>
        </>
      ) : null}
      {node.file && node.kind !== "episode" ? (
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => onOpen(node.file as string, node.line ?? undefined, node.end ?? undefined)}>
          {lines ? G.openAt(lines) : G.openFile}
        </button>
      ) : null}
    </div>
  );
}

export function MemoryGraph({ client, active, tick, touched, onOpen, onJump }: Props) {
  const [scope, setScope] = useState<Scope>("session");
  const [text, setText] = useState("");
  const searchId = useId();
  const [query, setQuery] = useState("");
  const [hidden, setHidden] = useState<NodeKind[]>([]);
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(text.trim()), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  useEffect(() => {
    let stale = false;
    const shown = NODE_KINDS.filter((kind) => !hidden.includes(kind));
    const params = new URLSearchParams({ scope, q: query, types: shown.join(",") });
    const timer = window.setTimeout(async () => {
      try {
        const found = await ideCall<GraphData>(client, `graph?${params}`);
        if (stale) return;
        setData(found);
        setError(null);
        setSelected((id) => (found.nodes.some((node) => node.id === id) ? id : null));   // Undo can remove it
      } catch (e) {
        if (!stale) setError(failure(e));
      }
    }, DEBOUNCE_MS);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [client, scope, query, hidden, tick]);

  const toggleKind = (kind: NodeKind) =>
    setHidden((all) => (all.includes(kind) ? all.filter((k) => k !== kind) : [...all, kind]));

  return (
    <section className="ide-graph" aria-label={G.title}>
      <div className="ide-graph__bar">
        <div className="ide-graph__scope" role="group" aria-label={G.scopeLabel}>
          {(["session", "project"] as const).map((name) => (
            <button key={name} type="button" aria-pressed={scope === name} onClick={() => setScope(name)}>
              {G.scopes[name]}
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor={searchId}>
          {G.search}
        </label>
        <input
          id={searchId}
          type="search"
          value={text}
          placeholder={G.search}
          spellCheck={false}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="ide-graph__kinds" role="group" aria-label={G.kindsLabel}>
          {NODE_KINDS.map((kind) => (
            <button key={kind} type="button" aria-pressed={!hidden.includes(kind)} onClick={() => toggleKind(kind)}>
              <span className="ide-graph__mark" data-kind={kind} aria-hidden="true" />
              {G.kinds[kind]}
            </button>
          ))}
        </div>
        <span className="ide__spacer" />
        {data ? (
          <>
            <span className="ide-graph__meta" title={G.savedHint}>
              {G.saved(data.saved)}
            </span>
            <span className="ide-graph__meta">{G.shown(data.nodes.length, data.total)}</span>
          </>
        ) : null}
      </div>
      {error ? (
        <p className="ide-graph__note" role="alert">
          {error}
        </p>
      ) : null}
      {!data && !error ? <p className="ide-graph__note">{G.loading}</p> : null}
      {data ? (
        <div className="ide-graph__body">
          {data.nodes.length ? (
            <Canvas data={data} active={active} selected={selected} touched={touched} onSelect={setSelected} />
          ) : (
            <p className="ide-graph__empty">{query || hidden.length ? G.noMatch : G.empty}</p>
          )}
          <aside className="ide-graph__side" aria-label={G.details}>
            {selected ? (
              <Inspector client={client} id={selected} tick={tick} onOpen={onOpen} onJump={onJump} />
            ) : (
              <p className="ide-graph__note">{G.pick}</p>
            )}
            <p className="ide-graph__note">{data.scripts ? G.languages : G.noScripts}</p>
            <ul className="ide-graph__list" aria-label={G.list}>
              {data.nodes.map((node) => (
                <li key={node.id}>
                  <button type="button" aria-current={node.id === selected ? "true" : undefined} onClick={() => setSelected(node.id)}>
                    <span className="ide-graph__mark" data-kind={node.kind} aria-hidden="true" />
                    <span className="sr-only">{G.kinds[node.kind]}: </span>
                    {node.kind === "episode" ? `${G.turn(node.turn ?? 0)}: ${node.label}` : node.label}
                  </button>
                </li>
              ))}
            </ul>
          </aside>
        </div>
      ) : null}
    </section>
  );
}
