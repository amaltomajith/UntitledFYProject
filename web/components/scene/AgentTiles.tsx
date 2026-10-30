"use client";

import { createElement, useEffect, useMemo, useRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import {
  BookOpen,
  Broom,
  Bug,
  Code,
  Eye,
  Flag,
  Flask,
  GitBranch,
  MagnifyingGlass,
  PencilLine,
  Power,
  TreeStructure,
  type Icon,
} from "@phosphor-icons/react";
import { AGENTS, ROLE, pad2, type IconName } from "@/lib/agents";
import { range, scene, smooth } from "@/lib/sceneState";

const ICONS: Record<IconName, Icon> = {
  MagnifyingGlass,
  TreeStructure,
  Code,
  Flask,
  Bug,
  Broom,
  Eye,
  PencilLine,
  BookOpen,
  GitBranch,
  Flag,
  Power,
};

const TAU = Math.PI * 2;
const DEPTH = 0.2;
const FACE = 0.84;
const BODY = new THREE.Color("#6a4dff");
const BODY_DIM = new THREE.Color("#120e36");

/* ------------------------------------------------------------------ face texture */

function roundRect(g: CanvasRenderingContext2D, s: number, r: number) {
  g.beginPath();
  g.moveTo(r, 0);
  g.arcTo(s, 0, s, s, r);
  g.arcTo(s, s, 0, s, r);
  g.arcTo(0, s, 0, 0, r);
  g.arcTo(0, 0, s, 0, r);
  g.closePath();
}

function faceTexture(icon: Icon, onReady: () => void) {
  const S = 512;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d")!;

  roundRect(g, S, 46);
  g.save();
  g.clip();
  const grad = g.createLinearGradient(0, 0, S * 0.9, S);
  grad.addColorStop(0, "#ece8ff");
  grad.addColorStop(0.3, "#b3a4ff");
  grad.addColorStop(0.68, "#7c62ff");
  grad.addColorStop(1, "#5237ea");
  g.fillStyle = grad;
  g.fillRect(0, 0, S, S);
  const top = g.createLinearGradient(0, 0, 0, S * 0.3);
  top.addColorStop(0, "rgba(255,255,255,0.5)");
  top.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = top;
  g.fillRect(0, 0, S, S * 0.3);
  g.restore();

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;

  let svg = renderToStaticMarkup(createElement(icon, { size: 236, color: "#ffffff", weight: "regular" }));
  if (!svg.includes("xmlns=")) svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
  const img = new Image();
  img.onload = () => {
    g.save();
    g.shadowColor = "rgba(30, 14, 130, 0.35)";
    g.shadowBlur = 16;
    g.shadowOffsetY = 4;
    g.drawImage(img, (S - 236) / 2, (S - 236) / 2, 236, 236);
    g.restore();
    tex.needsUpdate = true;
    onReady();
  };
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  return tex;
}

/* ------------------------------------------------------------------ layouts */

type Pose = { x: number; y: number; z: number; rx: number; ry: number; rz: number; s: number; dim: number };
const KEYS = ["x", "y", "z", "rx", "ry", "rz", "s", "dim"] as const;
const pose = (): Pose => ({ x: 0, y: 0, z: 0, rx: 0, ry: 0, rz: 0, s: 1, dim: 0 });
function mix(a: Pose, b: Pose, t: number) {
  if (t <= 0) return;
  for (const k of KEYS) a[k] += (b[k] - a[k]) * t;
}
const wrapAngle = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));
const hash = (n: number) => {
  const s = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
};

/** Chapter 00: a slow carousel standing on the grid, upper right. */
function heroPose(i: number, n: number, vw: number, vh: number, t: number, mobile: boolean, o: Pose) {
  const R = mobile ? 3.1 : 5.4;
  const th = wrapAngle((i / n) * TAU + t * 0.1);
  const front = smooth(range(Math.cos(th), -0.2, 0.6));
  o.x = (mobile ? 0 : vw * 0.2) + R * Math.sin(th);
  o.y = (mobile ? vh * 0.2 : vh * 0.1) + Math.sin(th * 2 + i) * 0.12;
  o.z = -R + R * Math.cos(th) - (mobile ? 1.2 : 0);
  o.rx = -0.06;
  o.ry = th * 0.92;
  o.rz = Math.sin(th) * 0.05;
  o.s = (mobile ? 1.15 : 2.0) * front;
  o.dim = 0.2 * (1 - front);
}

/** Chapter 01: an ellipse framing the centred text, slowly orbiting with scroll. */
function ringPose(i: number, n: number, vw: number, vh: number, p: number, t: number, mobile: boolean, o: Pose) {
  const ph = (i / n) * TAU + 0.35 + p * 0.9 + t * 0.012;
  o.x = Math.cos(ph) * vw * (mobile ? 0.5 : 0.45);
  o.y = Math.sin(ph) * vh * (mobile ? 0.46 : 0.42);
  o.z = -0.6 + Math.sin(ph * 3 + 1) * 0.9;
  o.rx = Math.sin(ph) * 0.42;
  o.ry = -Math.cos(ph) * 0.55;
  o.rz = Math.cos(ph * 2) * 0.06;
  o.s = mobile ? 0.5 : 1.1;
  o.dim = 0;
}

/** Chapter 02: every agent collapsed into one stack, the single MCP tool. */
function mcpPose(i: number, n: number, vw: number, vh: number, t: number, o: Pose) {
  const S = scene.mcpS * vh * 0.56;
  // Four visible layers with clear gaps; the other tiles tuck in behind the last one.
  const layer = Math.min(i, 3);
  const d = layer / 3;
  const gap = DEPTH * S * 1.5;
  o.x = scene.mcpX * vw + (d - 0.5) * S * 0.34;
  o.y = scene.mcpY * vh + (d - 0.5) * S * 0.14;
  o.z = 0.6 - layer * gap;
  o.rx = 0.2;
  o.ry = -0.5 + Math.sin(t * 0.4) * 0.09;
  o.rz = 0;
  o.s = S * (i > 3 ? 0.96 : 1);
  o.dim = d * 0.72;
}

type Mod = { dy: number; dz: number; ds: number; dim: number; dry: number };
const mod = (): Mod => ({ dy: 0, dz: 0, ds: 1, dim: 0, dry: 0 });

/** Per-step state of one tile in the episode (states S0..S5; step k animates S_k -> S_k+1). */
function stepMod(k: number, i: number, cell: number, o: Mod) {
  o.dy = 0;
  o.dz = 0;
  o.ds = 1;
  o.dim = 0;
  o.dry = 0;
  const injected = (ROLE.injected as readonly number[]).includes(i);
  const shielded = (ROLE.shielded as readonly number[]).includes(i);
  const coder = i === ROLE.coder;
  const runner = i === ROLE.testRunner;
  if (k === 0 && injected) {
    o.dy = cell * 2.4;
    o.ds = 0.45;
    o.dim = 0.85;
  }
  if (k >= 2 && shielded) {
    o.dz = -1.4;
    o.ds = 0.8;
    o.dim = 0.78;
  }
  if (k === 3) {
    if (coder) {
      o.dz = 0.9;
      o.ds = 1.3;
    } else if (!shielded) o.dim = 0.4;
  }
  if (k === 4) {
    if (coder) {
      o.dz = 1.3;
      o.ds = 1.42;
      o.dry = 0.32;
    } else if (!shielded) o.dim = 0.55;
  }
  if (k === 5) {
    if (coder) {
      o.dz = 0.3;
      o.ds = 1.08;
    } else if (runner) {
      o.dz = 0.9;
      o.ds = 1.32;
    } else if (!shielded) o.dim = 0.45;
  }
}

/** Chapter 02: the agent pool as a 4 x 3 grid beside the step list. */
function howPose(i: number, vw: number, vh: number, stepF: number, mobile: boolean, o: Pose, a: Mod, b: Mod) {
  const cols = 4;
  const rows = 3;
  const areaW = mobile ? vw * 0.7 : vw * 0.4;
  const areaH = mobile ? vh * 0.26 : vh * 0.64;
  const cell = Math.min(areaW / cols, areaH / rows);
  const col = i % cols;
  const row = Math.floor(i / cols);
  const k = Math.min(4, Math.max(0, Math.floor(stepF)));
  const f = smooth(range(stepF - k, 0.12, 0.72));
  stepMod(k, i, cell, a);
  stepMod(k + 1, i, cell, b);
  const m = (key: keyof Mod) => a[key] + (b[key] - a[key]) * f;
  o.x = (mobile ? 0 : vw * 0.215) + (col - (cols - 1) / 2) * cell;
  o.y = (mobile ? vh * 0.29 : 0) - (row - (rows - 1) / 2) * cell + m("dy");
  // Phones have little headroom above the grid, so the "pick" pop is halved there.
  const pop = mobile ? 0.5 : 1;
  o.z = m("dz") * pop;
  o.rx = 0.08;
  o.ry = (mobile ? 0 : -0.22) + m("dry");
  o.rz = 0;
  o.s = cell * 0.74 * (1 + (m("ds") - 1) * pop);
  o.dim = m("dim");
}

/** Chapters 03-04: drift deep in the background, out of the way of the content. */
function scatterPose(i: number, vw: number, vh: number, t: number, o: Pose) {
  const r1 = hash(i * 3.1);
  const r2 = hash(i * 7.7 + 1);
  const r3 = hash(i * 1.3 + 2);
  o.x = (r1 - 0.5) * vw * 2.4;
  o.y = (r2 - 0.5) * vh * 2.2;
  o.z = -26 - r3 * 12;
  o.rx = r2 + t * 0.05 * (r1 - 0.5);
  o.ry = r1 * 2 + t * 0.07 * (r2 - 0.5);
  o.rz = r3;
  o.s = 1.1;
  o.dim = 1;
}

/* ------------------------------------------------------------------ component */

export function AgentTiles() {
  const n = AGENTS.length;
  const invalidate = useThree((s) => s.invalidate);
  const groups = useRef<(THREE.Group | null)[]>([]);
  const hoverAmt = useRef<number[]>(new Array(n).fill(0));
  const tilt = useRef(Array.from({ length: n }, () => ({ x: 0, y: 0 })));
  const chipIdx = useRef(-1);
  const first = useRef(true);
  const clock = useRef(0);

  const work = useMemo(
    () => ({ a: pose(), b: pose(), c: pose(), d: pose(), m1: mod(), m2: mod(), v: new THREE.Vector3() }),
    [],
  );

  const geometry = useMemo(() => new RoundedBoxGeometry(1, 1, DEPTH, 5, 0.09), []);
  const faceGeometry = useMemo(() => new THREE.PlaneGeometry(FACE, FACE), []);

  const materials = useMemo(
    () =>
      AGENTS.map((agent) => {
        const map = faceTexture(ICONS[agent.icon], () => invalidate());
        return {
          body: new THREE.MeshPhysicalMaterial({
            color: BODY.clone(),
            roughness: 0.24,
            metalness: 0,
            clearcoat: 1,
            clearcoatRoughness: 0.12,
            envMapIntensity: 1.25,
          }),
          face: new THREE.MeshPhysicalMaterial({
            map,
            emissiveMap: map,
            emissive: new THREE.Color("#ffffff"),
            emissiveIntensity: 0.32,
            transparent: true,
            roughness: 0.3,
            clearcoat: 1,
            clearcoatRoughness: 0.1,
            envMapIntensity: 0.5,
          }),
        };
      }),
    [invalidate],
  );

  useEffect(
    () => () => {
      geometry.dispose();
      faceGeometry.dispose();
      materials.forEach((m) => {
        m.face.map?.dispose();
        m.body.dispose();
        m.face.dispose();
      });
    },
    [geometry, faceGeometry, materials],
  );

  useFrame((state, delta) => {
    const dt = Math.min(delta, 1 / 20);
    if (!scene.reduced) clock.current += dt;
    const t = clock.current;
    const vp = state.viewport.getCurrentViewport(state.camera, [0, 0, 0]);
    const vw = vp.width;
    const vh = vp.height;
    const mobile = state.size.width < 768;

    const eRing = smooth(scene.heroOut);
    const eMcp = smooth(scene.mcpIn);
    const eHow = smooth(scene.howIn);
    const eScat = smooth(scene.capIn);
    const swoop = Math.sin(eRing * Math.PI);
    const stepF = scene.how * 5;
    const hoverOn = eScat < 0.5;
    const snap = first.current || scene.reduced;
    const { a, b, c, d, m1, m2, v } = work;

    for (let i = 0; i < n; i++) {
      const g = groups.current[i];
      if (!g) continue;

      heroPose(i, n, vw, vh, t, mobile, a);
      if (eRing > 0) {
        ringPose(i, n, vw, vh, scene.about, t, mobile, b);
        mix(a, b, eRing);
        a.z += swoop * (1.6 + hash(i) * 1.6);
        a.rx += swoop * 0.7;
      }
      if (eMcp > 0) {
        mcpPose(i, n, vw, vh, t, c);
        mix(a, c, eMcp);
      }
      if (eHow > 0) {
        howPose(i, vw, vh, stepF, mobile, c, m1, m2);
        mix(a, c, eHow);
      }
      if (eScat > 0) {
        scatterPose(i, vw, vh, t, d);
        mix(a, d, eScat);
      }

      // Hover: lift, grow and tilt toward the cursor.
      const target = hoverOn && scene.hovered === i ? 1 : 0;
      hoverAmt.current[i] = THREE.MathUtils.damp(hoverAmt.current[i], target, 9, dt);
      const h = hoverAmt.current[i];
      const tl = tilt.current[i];
      a.z += h * 0.6;
      a.s *= 1 + h * 0.08;
      a.rx += -tl.y * 0.38 * h;
      a.ry += tl.x * 0.38 * h;
      a.dim *= 1 - h;

      const L = 7;
      if (snap) {
        g.position.set(a.x, a.y, a.z);
        g.rotation.set(a.rx, a.ry, a.rz);
        g.scale.setScalar(Math.max(a.s, 0.0001));
      } else {
        g.position.set(
          THREE.MathUtils.damp(g.position.x, a.x, L, dt),
          THREE.MathUtils.damp(g.position.y, a.y, L, dt),
          THREE.MathUtils.damp(g.position.z, a.z, L, dt),
        );
        g.rotation.set(
          THREE.MathUtils.damp(g.rotation.x, a.rx, L, dt),
          THREE.MathUtils.damp(g.rotation.y, a.ry, L, dt),
          THREE.MathUtils.damp(g.rotation.z, a.rz, L, dt),
        );
        g.scale.setScalar(Math.max(THREE.MathUtils.damp(g.scale.x, a.s, L, dt), 0.0001));
      }
      g.visible = g.scale.x > 0.01;

      const mat = materials[i];
      mat.body.color.lerpColors(BODY, BODY_DIM, a.dim);
      mat.face.color.setScalar(1 - 0.9 * a.dim);
      mat.face.emissiveIntensity = (0.32 + 0.4 * h) * (1 - a.dim);
    }
    first.current = false;

    // The hover chip ("03 CODER") follows the tile's top-right corner in screen space.
    const chip = scene.chipEl;
    if (chip) {
      if (scene.hovered >= 0 && hoverOn && scene.hovered !== chipIdx.current) {
        chipIdx.current = scene.hovered;
        const idx = chip.querySelector("[data-chip-i]");
        const name = chip.querySelector("[data-chip-n]");
        if (idx) idx.textContent = pad2(chipIdx.current + 1);
        if (name) name.textContent = AGENTS[chipIdx.current].name.toUpperCase();
      }
      const ci = chipIdx.current;
      const g = ci >= 0 ? groups.current[ci] : null;
      const h = ci >= 0 ? hoverAmt.current[ci] : 0;
      if (g && h > 0.3) {
        v.set(0.32, 0.36, DEPTH / 2 + 0.02).applyMatrix4(g.matrixWorld).project(state.camera);
        const x = (v.x * 0.5 + 0.5) * state.size.width;
        const y = (-v.y * 0.5 + 0.5) * state.size.height;
        chip.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-50%, -50%)`;
        chip.style.opacity = h.toFixed(3);
      } else if (chip.style.opacity !== "0") {
        chip.style.opacity = "0";
      }
    }
  });

  const onOver = (i: number) => (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    scene.hovered = i;
  };
  const onOut = (i: number) => () => {
    if (scene.hovered === i) scene.hovered = -1;
  };
  const onMove = (i: number) => (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    const g = groups.current[i];
    if (!g) return;
    const local = g.worldToLocal(e.point.clone());
    tilt.current[i].x = THREE.MathUtils.clamp(local.x * 2, -1, 1);
    tilt.current[i].y = THREE.MathUtils.clamp(local.y * 2, -1, 1);
  };

  return (
    <group>
      {AGENTS.map((agent, i) => (
        <group
          key={agent.name}
          ref={(el) => {
            groups.current[i] = el;
          }}
          onPointerOver={onOver(i)}
          onPointerOut={onOut(i)}
          onPointerMove={onMove(i)}
        >
          <mesh geometry={geometry} material={materials[i].body} />
          <mesh geometry={faceGeometry} material={materials[i].face} position={[0, 0, DEPTH / 2 + 0.003]} />
        </group>
      ))}
    </group>
  );
}
