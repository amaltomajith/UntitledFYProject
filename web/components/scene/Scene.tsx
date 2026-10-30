"use client";

import { Canvas, useFrame } from "@react-three/fiber";
import { Environment, Lightformer } from "@react-three/drei";
import * as THREE from "three";
import { AgentTiles } from "./AgentTiles";
import { CurvedGrid } from "./CurvedGrid";
import { Stars } from "./Stars";
import { scene } from "@/lib/sceneState";

function CameraRig() {
  useFrame((state, dt) => {
    const cam = state.camera;
    const px = scene.reduced ? 0 : state.pointer.x;
    const py = scene.reduced ? 0 : state.pointer.y;
    cam.position.x = THREE.MathUtils.damp(cam.position.x, px * 0.35, 2.5, dt);
    cam.position.y = THREE.MathUtils.damp(cam.position.y, py * 0.22, 2.5, dt);
    cam.lookAt(0, 0, 0);
  });
  return null;
}

/** Fixed, full-viewport WebGL layer. Loaded client-only (see SceneMount). */
export default function Scene() {
  const reduced = scene.reduced;
  const mobile = window.innerWidth < 768;

  return (
    <div className={reduced ? "scene-layer scene-layer--static" : "scene-layer"} aria-hidden="true">
      <Canvas
        eventSource={document.body}
        eventPrefix="client"
        frameloop={reduced ? "demand" : "always"}
        dpr={[1, mobile ? 1.5 : 1.75]}
        camera={{ fov: 35, position: [0, 0, 12], near: 0.1, far: 120 }}
        gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}
        fallback={null}
      >
        <Environment resolution={256} frames={1}>
          <Lightformer form="rect" intensity={2.4} color="#ffffff" position={[-6, 6, 6]} scale={[12, 4, 1]} />
          <Lightformer form="rect" intensity={2.2} color="#5b8cff" position={[8, 0, 4]} scale={[4, 12, 1]} />
          <Lightformer form="ring" intensity={1.4} color="#a9c1ff" position={[0, -5, 6]} scale={5} />
        </Environment>
        <ambientLight intensity={0.35} />
        <directionalLight position={[-4, 6, 8]} intensity={1.3} />
        <Stars count={mobile ? 320 : 650} />
        <CurvedGrid />
        <AgentTiles />
        <CameraRig />
      </Canvas>
    </div>
  );
}
