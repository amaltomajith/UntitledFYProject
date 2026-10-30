"""
puppeteer_code/report.py
========================
`pcode report`: one self-contained HTML file (inline SVG, no external
assets) summarising a benchmark run and/or a training curve:

  * results table (mean ± std over seeds) and success / token bars per method
  * context size per LLM call: growing-transcript ReAct vs bounded snapshots
  * training learning curve (rolling success rate, tokens per episode)
  * agent usage early vs late in training (what the policy learned to skip)

Runs made with the simulated provider are bannered as mechanism checks.
"""

from __future__ import annotations

import html
import json
import statistics
from pathlib import Path
from typing import Optional

W = 820           # chart viewBox width (wide enough that SVG text renders near body size)
PAD_L, PAD_R, PAD_T, PAD_B = 56, 96, 16, 36


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    import math
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def _fmt(v: float) -> str:
    if v >= 10000:
        return f"{v / 1000:.0f}K"
    if v >= 100 or float(v).is_integer():
        return f"{v:,.0f}"
    return f"{v:.2f}"


def hbar_chart(items: list[tuple[str, float, str]], series: str = "--series-1", label_w: int = 120,
               xmax: Optional[float] = None) -> str:
    """Horizontal bars: (label, value, display text).  One series, value at the tip."""
    row, bar = 32, 18
    h = len(items) * row + 8
    xmax = xmax or _nice_max(max((v for _, v, _ in items), default=1))
    plot_w = W - label_w - 70
    out = [f'<svg viewBox="0 0 {W} {h}" role="img" class="chart">']
    out.append(f'<line x1="{label_w}" y1="0" x2="{label_w}" y2="{h - 4}" class="axis"/>')
    for i, (label, value, text) in enumerate(items):
        y = i * row + (row - bar) / 2 + 4
        x1 = label_w + max(0.0, value) / xmax * plot_w
        r = min(4, max(0.0, x1 - label_w))
        path = (f"M{label_w},{y} H{x1 - r:.1f} Q{x1:.1f},{y} {x1:.1f},{y + r} V{y + bar - r} "
                f"Q{x1:.1f},{y + bar} {x1 - r:.1f},{y + bar} H{label_w} Z")
        tip = f"{label}: {text}"
        out.append(f'<g class="hit" tabindex="0" data-tip="{esc(tip)}">'
                   f'<rect x="0" y="{i * row}" width="{W}" height="{row}" fill="transparent"/>'
                   f'<path d="{path}" style="fill:var({series})"/>'
                   f'<text x="{label_w - 8}" y="{y + bar / 2 + 4}" text-anchor="end" class="lbl">{esc(label)}</text>'
                   f'<text x="{x1 + 6:.1f}" y="{y + bar / 2 + 4}" class="val">{esc(text)}</text></g>')
    out.append("</svg>")
    return "".join(out)


def line_chart(series: list[tuple[str, list[float], list[float], str]], y_label: str,
               x_label: str, ymax: Optional[float] = None, y_pct: bool = False, h: int = 260) -> str:
    """Lines with a crosshair tooltip.  series: (name, xs, ys, css var)."""
    xs_all = [x for _, xs, _, _ in series for x in xs]
    ys_all = [y for _, _, ys, _ in series for y in ys]
    if not xs_all:
        return "<p class='muted'>no data</p>"
    x0, x1 = min(xs_all), max(xs_all)
    x1 = x1 if x1 > x0 else x0 + 1
    ymax = ymax or _nice_max(max(ys_all))
    pw, ph = W - PAD_L - PAD_R, h - PAD_T - PAD_B

    def X(x):
        return PAD_L + (x - x0) / (x1 - x0) * pw

    def Y(y):
        return PAD_T + ph - y / ymax * ph

    out = [f'<svg viewBox="0 0 {W} {h}" role="img" class="chart line" '
           f'data-series="{esc(json.dumps([[n, xs, ys, c] for n, xs, ys, c in series]))}" '
           f'data-geom="{esc(json.dumps([PAD_L, PAD_T, pw, ph, x0, x1, ymax, int(y_pct)]))}">']
    for k in range(5):
        v = ymax * k / 4
        y = Y(v)
        cls = "axis" if k == 0 else "grid"
        out.append(f'<line x1="{PAD_L}" x2="{PAD_L + pw}" y1="{y:.1f}" y2="{y:.1f}" class="{cls}"/>')
        txt = f"{v * 100:.0f}%" if y_pct else _fmt(v)
        out.append(f'<text x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{txt}</text>')
    for k in range(5):
        xv = x0 + (x1 - x0) * k / 4
        out.append(f'<text x="{X(xv):.1f}" y="{h - 14}" text-anchor="middle" class="tick">{_fmt(round(xv))}</text>')
    out.append(f'<text x="{PAD_L + pw / 2}" y="{h - 1}" text-anchor="middle" class="tick">{esc(x_label)}</text>')
    out.append(f'<text x="12" y="{PAD_T + ph / 2}" text-anchor="middle" class="tick" '
               f'transform="rotate(-90 12 {PAD_T + ph / 2})">{esc(y_label)}</text>')
    for name, xs, ys, color in series:
        pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(xs, ys))
        out.append(f'<polyline points="{pts}" class="ln" style="stroke:var({color})"/>')
        ex, ey = X(xs[-1]), Y(ys[-1])
        out.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" class="dot" style="fill:var({color})"/>')
        end = f"{ys[-1] * 100:.0f}%" if y_pct else _fmt(ys[-1])
        out.append(f'<text x="{ex + 8:.1f}" y="{ey + 4:.1f}" class="val">{esc(name)} {end}</text>')
    out.append(f'<line class="xhair" x1="0" x2="0" y1="{PAD_T}" y2="{PAD_T + ph}" visibility="hidden"/>')
    out.append(f'<rect class="hitarea" x="{PAD_L}" y="{PAD_T}" width="{pw}" height="{ph}" fill="transparent"/>')
    out.append("</svg>")
    return "".join(out)


def legend(items: list[tuple[str, str]], kind: str = "line") -> str:
    keys = []
    for name, color in items:
        mark = (f'<span class="key-line" style="background:var({color})"></span>' if kind == "line"
                else f'<span class="key-box" style="background:var({color})"></span>')
        keys.append(f"<span class='key'>{mark}{esc(name)}</span>")
    return f"<div class='legend'>{''.join(keys)}</div>"


def table(head: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='tablewrap'><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>"


def _ms(m: dict, pct=False, digits=0) -> str:
    if not m or m.get("mean") is None:
        return "n/a"
    if pct:
        return f"{100 * m['mean']:.0f}% ± {100 * m['std']:.0f}"
    return f"{m['mean']:,.{digits}f} ± {m['std']:,.{digits}f}"


def _rolling(xs: list[float], w: int) -> list[float]:
    out = []
    for i in range(len(xs)):
        win = xs[max(0, i - w + 1): i + 1]
        out.append(sum(win) / len(win))
    return out


# ═════════════════════════════════════════════════════════════════════════════

def bench_section(data: dict) -> str:
    data = dict(data, rows=[r for r in data["rows"] if not r.get("provider_error")])
    s = data["summary"]
    methods = list(s)
    parts = ["<h2>Benchmark</h2>",
             f"<p class='muted'>{len(data['tasks'])} tasks × {len(methods)} methods × "
             f"{len(data['seeds'])} seed(s); provider <b>{esc(data['provider'])}</b>. Success = all visible "
             f"tests green, no regressions, no integrity violation, and every hidden test passes.</p>"]
    rows = [[m, _ms(v["success"], True), _ms(v["holdout_pass_rate"], True), _ms(v["tokens"]),
             f"{v['tokens_per_success']:,.0f}", _ms(v["llm_calls"], digits=1), _ms(v["steps"], digits=1),
             _ms(v["max_prompt_chars"]), v["regressions"], v["integrity_violations"]] for m, v in s.items()]
    parts.append(table(["method", "success", "hidden-test pass", "tokens / task", "tokens / success",
                        "LLM calls", "steps", "largest prompt (chars)", "regressions", "integrity"], rows))

    succ = sorted(((m, v["success"]["mean"] or 0) for m, v in s.items()), key=lambda t: -t[1])
    parts.append("<div class='card'><h3>Success rate by method</h3>"
                 + hbar_chart([(m, v, f"{v * 100:.0f}%") for m, v in succ], xmax=1.0) + "</div>")
    toks = sorted(((m, v["tokens_per_success"]) for m, v in s.items()), key=lambda t: t[1])
    parts.append("<div class='card'><h3>Tokens spent per solved task</h3>"
                 "<p class='muted'>All tokens across every attempt, divided by the number solved. Lower is better.</p>"
                 + hbar_chart([(m, v, f"{v:,.0f}") for m, v in toks]) + "</div>")

    # context growth: mean prompt chars by LLM-call index
    growth = []
    colors = ["--series-1", "--series-2", "--series-3"]
    pick = [m for m in ("react", "heuristic", "linear") if m in s][:3]
    for i, m in enumerate(pick):
        per_call: dict[int, list[int]] = {}
        for r in data["rows"]:
            if r["method"] != m:
                continue
            calls = [c for c in r["prompt_chars"] if c]
            for j, c in enumerate(calls):
                per_call.setdefault(j + 1, []).append(c)
        if per_call:
            xs = sorted(k for k, v in per_call.items() if len(v) >= 2) or sorted(per_call)
            growth.append((m, xs, [statistics.fmean(per_call[x]) for x in xs], colors[i]))
    if growth:
        parts.append("<div class='card'><h3>Prompt size per LLM call</h3>"
                     "<p class='muted'>ReAct re-sends its whole transcript each turn; Puppeteer agents get a "
                     "fresh, budgeted snapshot, so call 10 costs no more context than call 2.</p>"
                     + legend([(n, c) for n, _, _, c in growth])
                     + line_chart(growth, "characters", "LLM call #") + "</div>")

    by_diff = sorted({d for v in s.values() for d in v["by_difficulty"]})
    parts.append("<details><summary>Success by difficulty</summary>"
                 + table(["method", *by_diff], [[m, *[f"{100 * v['by_difficulty'].get(d, 0):.0f}%" for d in by_diff]]
                                                for m, v in s.items()]) + "</details>")
    parts.append("<details><summary>Every run</summary>" + table(
        ["method", "seed", "task", "difficulty", "success", "pass", "hidden", "tokens", "steps", "agents"],
        [[r["method"], r["seed"], r["task"], r["difficulty"], "yes" if r["success"] else "no",
          f"{r['pass_rate']:.2f}", "-" if r["holdout_pass_rate"] is None else f"{r['holdout_pass_rate']:.2f}",
          r["tokens"], r["steps"], " → ".join(a.replace("Agent", "") for a in r["agents"])]
         for r in data["rows"]]) + "</details>")
    return "".join(parts)


def curve_section(data: dict) -> str:
    eps = data["episodes"]
    if not eps:
        return ""
    w = max(5, len(eps) // 10)
    xs = [e["episode"] + 1 for e in eps]
    succ = _rolling([float(e["success"]) for e in eps], w)
    toks = _rolling([float(e["tokens"]) for e in eps], w)
    steps = _rolling([float(e["steps"]) for e in eps], w)
    parts = ["<h2>Training (REINFORCE, LinearPolicy)</h2>",
             f"<p class='muted'>{len(eps)} episodes on the train split, provider <b>{esc(data['provider'])}</b>, "
             f"lr={data.get('lr')}. Curves are rolling means over {w} episodes.</p>",
             "<div class='card'><h3>Success rate</h3>" + line_chart(
                 [("success", xs, succ, "--series-1")], "rolling success", "episode", ymax=1.0, y_pct=True) + "</div>",
             "<div class='card'><h3>Tokens per episode</h3>" + line_chart(
                 [("tokens", xs, toks, "--series-1")], "tokens", "episode") + "</div>",
             "<div class='card'><h3>Steps per episode</h3>" + line_chart(
                 [("steps", xs, steps, "--series-1")], "steps", "episode") + "</div>"]
    n = max(1, len(eps) // 4)
    early, late = eps[:n], eps[-n:]
    agents = sorted({a for e in eps for a in e["agents"]})

    def avg(group, a):
        return sum(e["agents"].count(a) for e in group) / len(group)

    rows = sorted(((a, avg(early, a), avg(late, a)) for a in agents), key=lambda t: -(t[1] + t[2]))
    rowh, bar = 40, 12
    h = len(rows) * rowh + 8
    vmax = _nice_max(max(max(e, l) for _, e, l in rows))
    label_w, plot_w = 130, W - 130 - 70
    svg = [f'<svg viewBox="0 0 {W} {h}" class="chart" role="img">',
           f'<line x1="{label_w}" y1="0" x2="{label_w}" y2="{h - 4}" class="axis"/>']
    for i, (a, e, l) in enumerate(rows):
        name = a.replace("Agent", "")
        for j, (v, color, tag) in enumerate(((e, "--series-1", f"first {n}"), (l, "--series-2", f"last {n}"))):
            y = i * rowh + 6 + j * (bar + 2)
            x1 = label_w + v / vmax * plot_w
            r = min(4, x1 - label_w)
            svg.append(f'<g class="hit" tabindex="0" data-tip="{esc(f"{name}, {tag} episodes: {v:.2f} per episode")}">'
                       f'<rect x="0" y="{y - 1}" width="{W}" height="{bar + 2}" fill="transparent"/>'
                       f'<path d="M{label_w},{y} H{x1 - r:.1f} Q{x1:.1f},{y} {x1:.1f},{y + r} V{y + bar - r} '
                       f'Q{x1:.1f},{y + bar} {x1 - r:.1f},{y + bar} H{label_w} Z" style="fill:var({color})"/>'
                       f'<text x="{x1 + 6:.1f}" y="{y + bar - 2}" class="val">{v:.2f}</text></g>')
        svg.append(f'<text x="{label_w - 8}" y="{i * rowh + 22}" text-anchor="end" class="lbl">{esc(name)}</text>')
    svg.append("</svg>")
    parts.append("<div class='card'><h3>Agent activations per episode, early vs late training</h3>"
                 + legend([(f"first {n} episodes", "--series-1"), (f"last {n} episodes", "--series-2")], "box")
                 + "".join(svg) + "</div>")
    tw = data.get("top_weights") or {}
    if tw:
        parts.append("<details><summary>Largest learned weights per agent</summary>" + table(
            ["agent", "features (weight)"],
            [[a, ", ".join(f"{f} ({w:+.2f})" for f, w in ws)] for a, ws in tw.items()]) + "</details>")
    return "".join(parts)


CSS = """
:root{--surface-1:#fcfcfb;--page:#f9f9f7;--text-primary:#0b0b0b;--text-secondary:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;
--warn-bg:#fff4e0;color-scheme:light}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){--surface-1:#1a1a19;--page:#0d0d0d;
--text-primary:#f0efec;--text-secondary:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--warn-bg:#3a2a10;color-scheme:dark}}
:root[data-theme="dark"]{--surface-1:#1a1a19;--page:#0d0d0d;--text-primary:#f0efec;--text-secondary:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--series-1:#3987e5;--series-2:#d95926;
--series-3:#199e70;--warn-bg:#3a2a10;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--text-primary);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:36px 0 8px}h3{font-size:15px;margin:0 0 8px;font-weight:600}
.muted{color:var(--text-secondary)}.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;
padding:16px;margin:14px 0}.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:0 14px}
.banner{background:var(--warn-bg);border:1px solid var(--border);border-radius:10px;padding:10px 14px;margin:14px 0}
.chart{width:100%;height:auto;display:block;overflow:visible}.grid{stroke:var(--grid);stroke-width:1}
.axis{stroke:var(--axis);stroke-width:1}.tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.lbl{fill:var(--text-secondary);font-size:12px}.val{fill:var(--text-primary);font-size:12px}
.ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}.dot{stroke:var(--surface-1);stroke-width:2}
.xhair{stroke:var(--axis);stroke-width:1}.hit:hover path,.hit:focus path{opacity:.8}.hit:focus{outline:none}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);margin:0 0 6px}
.key{display:inline-flex;align-items:center;gap:6px}.key-line{width:14px;height:2px;border-radius:1px}
.key-box{width:10px;height:10px;border-radius:2px}
.tablewrap{overflow-x:auto;background:var(--surface-1);border:1px solid var(--border);border-radius:10px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:6px 10px;text-align:left;white-space:nowrap;
border-bottom:1px solid var(--grid)}td{font-variant-numeric:tabular-nums}th{color:var(--text-secondary);font-weight:600}
details{margin:10px 0}summary{cursor:pointer;color:var(--text-secondary)}
#tip{position:fixed;pointer-events:none;background:var(--surface-1);color:var(--text-primary);border:1px solid var(--border);
border-radius:8px;padding:6px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none;z-index:9}
#tip b{font-size:13px}#tip .row{display:flex;align-items:center;gap:6px}
"""

JS = r"""
const tip=document.getElementById('tip');
function show(e,build){tip.replaceChildren();build(tip);tip.style.display='block';
 const x=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8),y=Math.min(e.clientY+14,innerHeight-tip.offsetHeight-8);
 tip.style.left=x+'px';tip.style.top=y+'px';}
function hide(){tip.style.display='none';}
document.querySelectorAll('.hit').forEach(g=>{
 const f=e=>show(e.clientX!==undefined?e:{clientX:g.getBoundingClientRect().right,clientY:g.getBoundingClientRect().top},
  t=>{t.textContent=g.dataset.tip;});
 g.addEventListener('pointermove',f);g.addEventListener('focus',f);g.addEventListener('pointerleave',hide);g.addEventListener('blur',hide);});
document.querySelectorAll('svg.line').forEach(svg=>{
 const S=JSON.parse(svg.dataset.series),[L,T,PW,PH,X0,X1,YM,PCT]=JSON.parse(svg.dataset.geom);
 const xh=svg.querySelector('.xhair'),hit=svg.querySelector('.hitarea');
 hit.addEventListener('pointermove',e=>{
  const pt=svg.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;const p=pt.matrixTransform(svg.getScreenCTM().inverse());
  const xv=X0+(p.x-L)/PW*(X1-X0);let best=null;
  S.forEach(([n,xs,ys,c])=>{let i=0,d=1e18;xs.forEach((x,j)=>{const dd=Math.abs(x-xv);if(dd<d){d=dd;i=j;}});
   if(!best||d<best.d)best={d,x:xs[i]};});
  const sx=L+(best.x-X0)/(X1-X0)*PW;xh.setAttribute('x1',sx);xh.setAttribute('x2',sx);xh.setAttribute('visibility','visible');
  show(e,t=>{const h=document.createElement('div');h.className='muted';h.textContent='x = '+best.x;t.appendChild(h);
   S.forEach(([n,xs,ys,c])=>{const j=xs.indexOf(best.x);if(j<0)return;const r=document.createElement('div');r.className='row';
    const k=document.createElement('span');k.className='key-line';k.style.background='var('+c+')';
    const v=document.createElement('b');v.textContent=PCT?Math.round(ys[j]*100)+'%':(Math.round(ys[j]*100)/100).toLocaleString();
    const l=document.createElement('span');l.className='muted';l.textContent=n;r.append(k,v,l);t.appendChild(r);});});});
 hit.addEventListener('pointerleave',()=>{hide();xh.setAttribute('visibility','hidden');});});
"""


def write_report(out: Path, bench_path: Optional[str] = None, curve_path: Optional[str] = None) -> Path:
    bench = json.loads(Path(bench_path).read_text(encoding="utf-8")) if bench_path else None
    curve = json.loads(Path(curve_path).read_text(encoding="utf-8")) if curve_path else None
    providers = {d["provider"] for d in (bench, curve) if d}
    body = ["<h1>Puppeteer-Code report</h1>",
            "<p class='muted'>RL-orchestrated coding agents on PCodeBench-Mini. Every edit runs in a git "
            "sandbox; only test-verified changes survive.</p>"]
    if providers & {"sim", "oracle", "mock"}:
        body.append("<div class='banner'><b>Simulated models.</b> Some runs below use the offline simulated "
                    "provider. They check that the machinery works (routing, rollback, credit assignment, "
                    "learning); they are not measurements of real model quality.</div>")
    if bench:
        body.append(bench_section(bench))
    if curve:
        body.append(curve_section(curve))
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Puppeteer-Code report</title><style>{CSS}</style></head><body><main>"
            f"{''.join(body)}</main><div id='tip' role='tooltip'></div><script>{JS}</script></body></html>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
