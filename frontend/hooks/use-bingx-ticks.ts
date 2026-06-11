"use client";

import * as React from "react";

import { getApiBaseUrl } from "@/lib/api-client";
import type { BingXMicroBar } from "@/lib/bingx-bot-types";

const MAX_BARS = 300;

export function useBingxTicks(symbol: string | null) {
  const [bars, setBars] = React.useState<BingXMicroBar[]>([]);
  const [isConnected, setIsConnected] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [prevSymbol, setPrevSymbol] = React.useState(symbol);

  if (prevSymbol !== symbol) {
    setPrevSymbol(symbol);
    setBars([]);
    setIsConnected(false);
    setError(null);
  }

  React.useEffect(() => {
    if (!symbol) return;
    const apiBase = getApiBaseUrl().replace(/\/$/, "");
    const source = new EventSource(
      `${apiBase}/api/v1/bingx-bot/stream/ticks/${encodeURIComponent(symbol)}`,
    );

    source.onopen = () => {
      setIsConnected(true);
      setError(null);
    };

    source.onmessage = (event) => {
      try {
        const bar = JSON.parse(event.data) as BingXMicroBar;
        setBars((prev) => [...prev, bar].slice(-MAX_BARS));
      } catch {
        setError("Invalid tick payload");
      }
    };

    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) return;
      setIsConnected(false);
      setError("Tick stream disconnected");
    };

    return () => {
      source.close();
    };
  }, [symbol]);

  return { bars, isConnected, error };
}
