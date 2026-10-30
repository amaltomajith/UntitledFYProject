"use client";

import { useCallback, useEffect, useState } from "react";
import { getHealth, type Health } from "./api";

export type ServerState = { status: "checking" } | { status: "offline" } | { status: "live"; health: Health };

export function useServer() {
  const [state, setState] = useState<ServerState>({ status: "checking" });

  const refresh = useCallback(async () => {
    const health = await getHealth();
    setState(health ? { status: "live", health } : { status: "offline" });
  }, []);

  useEffect(() => {
    let live = true;
    getHealth().then((health) => {
      if (live) setState(health ? { status: "live", health } : { status: "offline" });
    });
    return () => {
      live = false;
    };
  }, []);

  return { server: state, refresh };
}
