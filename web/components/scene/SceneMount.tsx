"use client";

import dynamic from "next/dynamic";
import { useEffect } from "react";
import { scene } from "@/lib/sceneState";

const Scene = dynamic(() => import("./Scene"), { ssr: false });

/** Client-only mount for the WebGL layer, plus the DOM chip that labels a hovered tile. */
export function SceneMount() {
  return (
    <>
      <Scene />
      <TileChip />
    </>
  );
}

function TileChip() {
  useEffect(() => () => void (scene.chipEl = null), []);
  return (
    <div
      ref={(el) => {
        scene.chipEl = el;
      }}
      className="tile-chip"
      style={{ opacity: 0 }}
      aria-hidden="true"
    >
      <span data-chip-i className="tile-chip__i">
        01
      </span>
      <span data-chip-n className="tile-chip__n">
        REPOSCOUT
      </span>
    </div>
  );
}
