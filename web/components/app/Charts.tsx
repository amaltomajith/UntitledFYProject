"use client";

// Small hand-written charts. One measure each, one axis, values stay in text colours.
// Radius 0 follows DESIGN.md: every shape in the UI is sharp.

import { useId, useState } from "react";

export type Bar = { label: string; value: number; note?: string; emphasis?: boolean };

export function BarList({ title, bars, format }: { title: string; bars: Bar[]; format: (n: number) => string }) {
  const max = Math.max(...bars.map((b) => b.value), 1);
  return (
    <figure className="chart">
      <figcaption className="chart__title">{title}</figcaption>
      <ul className="bars">
        {bars.map((b) => (
          <li key={b.label} className="bar" data-emphasis={b.emphasis || undefined}>
            <span className="bar__label">
              {b.label}
              {b.note ? <span className="bar__note">{b.note}</span> : null}
            </span>
            <span className="bar__track" aria-hidden="true">
              <span className="bar__fill" style={{ width: `${Math.max((b.value / max) * 100, 0.5)}%` }} />
            </span>
            <span className="bar__value">{format(b.value)}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

export type Point = { x: number; y: number; label: string };

function Hover({ point, left }: { point: Point; left: number }) {
  return (
    <span className="chart__tip" style={{ left: `${left}%` }} data-flip={left > 70 || undefined}>
      {point.label}
    </span>
  );
}

function Axis({ top, bottom, first, last }: { top: string; bottom: string; first: string; last: string }) {
  return (
    <>
      <span className="chart__y chart__y--top">{top}</span>
      <span className="chart__y chart__y--bottom">{bottom}</span>
      <span className="chart__x">
        <span>{first}</span>
        <span>{last}</span>
      </span>
    </>
  );
}

export function Columns({
  title,
  points,
  format,
  xName,
  compact,
}: {
  title?: string;
  points: Point[];
  format: (n: number) => string;
  xName: string;
  compact?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...points.map((p) => p.y), 1);
  return (
    <figure className="chart" data-compact={compact || undefined}>
      {title ? <figcaption className="chart__title">{title}</figcaption> : null}
      <div className="plot" onPointerLeave={() => setHover(null)}>
        <div className="cols" role="img" aria-label={`${title ?? xName}: ${points.length} values, highest ${format(max)}`}>
          {points.map((p, i) => (
            <span
              key={p.x}
              className="col"
              data-active={hover === i || undefined}
              onPointerEnter={() => setHover(i)}
            >
              <span className="col__fill" style={{ height: `${Math.max((p.y / max) * 100, 1)}%` }} />
            </span>
          ))}
        </div>
        {hover !== null ? <Hover point={points[hover]} left={((hover + 0.5) / points.length) * 100} /> : null}
        <Axis
          top={format(max)}
          bottom="0"
          first={`${xName} ${points[0]?.x ?? ""}`}
          last={String(points.at(-1)?.x ?? "")}
        />
      </div>
    </figure>
  );
}

export function Line({ title, points, xName }: { title: string; points: Point[]; xName: string }) {
  const id = useId();
  const [hover, setHover] = useState<number | null>(null);
  const ys = points.map((p) => p.y);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || 1;
  const px = (i: number) => (points.length > 1 ? (i / (points.length - 1)) * 100 : 50);
  const py = (y: number) => 4 + (1 - (y - lo) / span) * 92;
  const path = points.map((p, i) => `${i ? "L" : "M"}${px(i)} ${py(p.y)}`).join(" ");

  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - box.left) / box.width;
    setHover(Math.min(points.length - 1, Math.max(0, Math.round(ratio * (points.length - 1)))));
  };

  return (
    <figure className="chart">
      <figcaption className="chart__title" id={id}>
        {title}
      </figcaption>
      <div className="plot" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        <svg className="chart__line" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-labelledby={id}>
          <path d={path} fill="none" stroke="var(--accent-hi)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
        {hover !== null ? (
          <>
            <span className="chart__cross" style={{ left: `${px(hover)}%` }} />
            <span className="chart__dot" style={{ left: `${px(hover)}%`, top: `${py(points[hover].y)}%` }} />
            <Hover point={points[hover]} left={px(hover)} />
          </>
        ) : null}
        <Axis
          top={hi.toFixed(2)}
          bottom={lo.toFixed(2)}
          first={`${xName} ${points[0]?.x ?? ""}`}
          last={String(points.at(-1)?.x ?? "")}
        />
      </div>
    </figure>
  );
}
