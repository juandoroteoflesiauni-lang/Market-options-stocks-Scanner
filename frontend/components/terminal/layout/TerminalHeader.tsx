"use client";

import { useEffect, useState } from "react";
import { Settings } from "lucide-react";

import { useTerminalStore } from "@/store/terminalStore";
import { cn } from "@/lib/terminal/format";
import type { MarketSession } from "@/lib/terminal/types";

function getSession(d: Date): { session: MarketSession; color: string } {
  const h = d.getUTCHours() - 5; // approx ET
  const hour = (h + 24) % 24;
  if (hour >= 4 && hour < 9.5) return { session: "PRE-MKT", color: "text-signal-warn" };
  if (hour >= 9.5 && hour < 16) return { session: "OPEN", color: "text-signal-bull" };
  if (hour >= 16 && hour < 20) return { session: "AFTER", color: "text-signal-info" };
  return { session: "CLOSED", color: "text-text-muted" };
}

export function TerminalHeader() {
  const connected = useTerminalStore((s) => s.connected);
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const session = now ? getSession(now) : { session: "CLOSED" as const, color: "text-text-muted" };

  return (
    <header
      className="sticky top-0 z-50 flex h-[52px] w-full items-center justify-between gap-4 border-b border-border-subtle bg-bg-base/85 px-4 backdrop-blur-xl"
      role="banner"
    >
      {/* Left — wordmark + live */}
      <div className="flex items-center gap-3">
        <div className="flex items-center font-display text-lg font-bold tracking-tight">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-text-accent font-mono text-text-inverse">
            Q
          </span>
          <span className="ml-1 text-text-primary">UANT</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              "h-2 w-2 rounded-full",
              connected ? "bg-signal-bull pulse-dot" : "bg-signal-bear",
            )}
          />
          <span className="font-mono text-[10px] tracking-widest text-text-secondary">
            {connected ? "LIVE" : "DOWN"}
          </span>
        </div>
      </div>

      {/* Center — tagline */}
      <div className="hidden flex-1 items-center justify-center lg:flex">
        <span className="font-mono text-[10px] uppercase tracking-[0.3em] text-text-muted">
          Multi-Strategy Quant Terminal
        </span>
      </div>

      {/* Right — clock, session, settings */}
      <div className="flex items-center gap-3">
        <span className="hidden font-mono text-xs tabular-nums text-text-secondary sm:inline">
          {now ? now.toLocaleTimeString("en-US", { hour12: false }) : "--:--:--"}
        </span>
        <span
          className={cn(
            "rounded border border-border-subtle bg-bg-panel px-2 py-0.5 font-mono text-[10px] tracking-widest",
            session.color,
          )}
        >
          {session.session}
        </span>
        <button
          type="button"
          className="text-text-muted transition-colors hover:text-text-primary"
          aria-label="Settings"
        >
          <Settings size={16} />
        </button>
      </div>
    </header>
  );
}
