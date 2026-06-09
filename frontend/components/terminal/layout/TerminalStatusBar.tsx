"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/terminal/format";
import { useLiveFeed } from "@/hooks/useLiveFeed";

const APIS = [
  { name: "BingX", status: "HEALTHY" },
  { name: "Alpaca", status: "HEALTHY" },
  { name: "Binance", status: "HEALTHY" },
  { name: "Options Feed", status: "DEGRADED" },
] as const;

const dotColor: Record<string, string> = {
  HEALTHY: "bg-signal-bull shadow-[0_0_6px_rgba(0,230,118,0.5)]",
  DEGRADED: "bg-signal-warn shadow-[0_0_6px_rgba(255,184,0,0.5)]",
  DOWN: "bg-signal-bear shadow-[0_0_6px_rgba(255,61,90,0.5)]",
};

const TAPE = ["AAPL", "TSLA", "NVDA", "BTC-USDT", "ETH-USDT", "SPY", "META", "MSFT"];

export function TerminalStatusBar() {
  const quotes = useLiveFeed(TAPE, 1600);
  const [refreshed, setRefreshed] = useState<string>("--:--:--");

  useEffect(() => {
    setRefreshed(new Date().toLocaleTimeString("en-US", { hour12: false }));
    const id = setInterval(
      () => setRefreshed(new Date().toLocaleTimeString("en-US", { hour12: false })),
      1000,
    );
    return () => clearInterval(id);
  }, []);

  const tape = [...TAPE, ...TAPE];

  return (
    <footer
      className="sticky bottom-0 z-40 flex h-7 w-full items-center justify-between gap-4 border-t border-border-subtle bg-bg-base/90 px-4 font-mono text-[10px] backdrop-blur-xl"
      role="contentinfo"
      aria-label="System status"
    >
      <div className="flex items-center gap-3">
        {APIS.map((api) => (
          <div key={api.name} className="flex items-center gap-1.5">
            <span className={cn("h-1.5 w-1.5 rounded-full", dotColor[api.status])} />
            <span className="text-text-secondary">{api.name}</span>
          </div>
        ))}
      </div>

      {/* Scrolling ticker tape */}
      <div className="relative hidden flex-1 overflow-hidden md:block">
        <div className="ticker-scroll flex w-max gap-6 whitespace-nowrap">
          {tape.map((s, i) => {
            const q = quotes[s];
            const up = q?.dir === "up";
            return (
              <span key={`${s}-${i}`} className="flex items-center gap-1.5">
                <span className="text-text-secondary">{s}</span>
                <span className="tabular-nums text-text-primary">
                  {q ? q.price.toLocaleString("en-US", { maximumFractionDigits: 2 }) : "--"}
                </span>
                <span className={up ? "text-signal-bull" : "text-signal-bear"}>
                  {up ? "▲" : "▼"}
                </span>
              </span>
            );
          })}
        </div>
      </div>

      <div className="flex items-center gap-3 text-text-muted">
        <span>
          REFRESH <span className="tabular-nums text-text-secondary">{refreshed}</span>
        </span>
        <span className="text-text-accent">v2.0.0</span>
      </div>
    </footer>
  );
}
