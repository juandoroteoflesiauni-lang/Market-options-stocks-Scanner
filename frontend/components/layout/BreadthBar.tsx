"use client";
import { useCallback, useEffect, useState } from "react";
import { fetchJson } from "@/lib/api-client";

interface BreadthData {
  bullish_pct: number;
  bearish_pct: number;
  total_scanned: number;
}

const POLL_INTERVAL_MS = 30_000;

export function BreadthBar() {
  const [data, setData] = useState<BreadthData | null>(null);

  const fetch = useCallback(async () => {
    try {
      const res = await fetchJson<{ market_breadth: BreadthData | null }>(
        "/api/funnel/overview",
      );
      if (res.market_breadth) {
        setData(res.market_breadth);
      }
    } catch {
      // Silently ignore — will retry on next poll
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetch();
    const id = setInterval(fetch, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetch]);

  if (!data || data.total_scanned === 0) return null;

  return (
    <div className="flex items-center gap-2 shrink-0">
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 10,
          color: "rgba(255,255,255,0.35)",
          letterSpacing: "0.08em",
        }}
      >
        B
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "#2FB67C",
          letterSpacing: "0.05em",
        }}
      >
        ↑{data.bullish_pct.toFixed(0)}%
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "#E04E5C",
          letterSpacing: "0.05em",
        }}
      >
        ↓{data.bearish_pct.toFixed(0)}%
      </span>
    </div>
  );
}
