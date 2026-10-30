"use client";

import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { scene, smooth } from "@/lib/sceneState";

// A large plane bent onto a sphere, drawn as faint grid lines with bright "+" marks
// at the intersections. The sphere bulges toward the camera, so the grid curves away.

const vertex = /* glsl */ `
  uniform float uRadius;
  varying vec2 vArc;
  varying float vDepth;
  void main() {
    vArc = position.xy;
    float th = position.x / uRadius;
    float ph = position.y / uRadius;
    vec3 p = vec3(
      uRadius * sin(th) * cos(ph),
      uRadius * sin(ph),
      uRadius * cos(th) * cos(ph) - uRadius
    );
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    vDepth = -mv.z;
    gl_Position = projectionMatrix * mv;
  }
`;

const fragment = /* glsl */ `
  uniform float uCell;
  uniform float uOpacity;
  uniform float uTime;
  uniform vec2 uHalf;
  uniform float uResX;
  varying vec2 vArc;
  varying float vDepth;

  void main() {
    vec2 p = vArc / uCell + vec2(uTime * 0.02, 0.0);
    vec2 q = abs(p - floor(p + 0.5));
    vec2 w = fwidth(p);

    float lx = 1.0 - smoothstep(0.0, w.x * 1.1, q.x);
    float ly = 1.0 - smoothstep(0.0, w.y * 1.1, q.y);
    float lines = max(lx, ly) * 0.14;

    float arm = 0.115;
    float th = 0.012;
    float hBar = (1.0 - smoothstep(th, th + w.y, q.y)) * (1.0 - smoothstep(arm, arm + w.x, q.x));
    float vBar = (1.0 - smoothstep(th, th + w.x, q.x)) * (1.0 - smoothstep(arm, arm + w.y, q.y));
    float plus = max(hBar, vBar) * 0.62;

    vec2 e = abs(vArc) / uHalf;
    float edge = (1.0 - smoothstep(0.55, 1.0, e.x)) * (1.0 - smoothstep(0.55, 1.0, e.y));
    float depth = 1.0 - smoothstep(18.0, 46.0, vDepth);
    // Keep the left of the screen (where the headline sits) quieter than the lit right side.
    float side = mix(0.3, 1.0, smoothstep(0.08, 0.62, gl_FragCoord.x / uResX));

    float a = max(lines, plus) * edge * depth * side * uOpacity;
    gl_FragColor = vec4(vec3(0.88, 0.86, 1.0), a);
  }
`;

const W = 72;
const H = 52;

export function CurvedGrid() {
  const mat = useRef<THREE.ShaderMaterial>(null);
  const mesh = useRef<THREE.Mesh>(null);

  const uniforms = useMemo(
    () => ({
      uRadius: { value: 24 },
      uCell: { value: 1.75 },
      uOpacity: { value: 1 },
      uTime: { value: 0 },
      uHalf: { value: new THREE.Vector2(W / 2, H / 2) },
      uResX: { value: 1 },
    }),
    [],
  );

  useFrame((state, dt) => {
    if (!mat.current || !mesh.current) return;
    const u = mat.current.uniforms;
    u.uResX.value = state.size.width * state.viewport.dpr;
    if (!scene.reduced) u.uTime.value += dt;
    // Full grid in the hero, dims behind the About ring, nearly gone behind the content sections.
    const ring = smooth(scene.heroOut);
    const away = Math.max(smooth(scene.mcpIn), smooth(scene.howIn), smooth(scene.capIn));
    const target = (1 - 0.72 * ring) * (1 - 0.7 * away);
    u.uOpacity.value = THREE.MathUtils.damp(u.uOpacity.value, target, 5, dt);
    // A slow tilt with scroll keeps the floor alive as the page moves.
    mesh.current.rotation.x = -0.62 + ring * 0.12;
    mesh.current.position.y = -1.6 - ring * 0.8;
    const mobile = state.size.width < 768;
    mesh.current.position.x = mobile ? 0 : 3.2;
  });

  return (
    <mesh ref={mesh} position={[3.2, -1.6, -4]} rotation={[-0.62, -0.42, 0.16]} frustumCulled={false}>
      <planeGeometry args={[W, H, 144, 104]} />
      <shaderMaterial
        ref={mat}
        vertexShader={vertex}
        fragmentShader={fragment}
        uniforms={uniforms}
        transparent
        depthWrite={false}
      />
    </mesh>
  );
}
