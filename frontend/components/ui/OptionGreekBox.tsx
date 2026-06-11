"use client";
import type { Greeks } from "@/store/types";

export function OptionGreekBox({ greeks }: { greeks?: Greeks }) {
  if (!greeks) {
    return (
      <div className="glass-panel p-2 rounded-md text-text-muted text-xs">
        No Greeks Data
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-px bg-border-subtle rounded-md overflow-hidden border border-border-subtle">
      <div className="bg-bg-panel p-2 flex flex-col">
        <span className="font-mono text-[9px] text-text-secondary">
          Δ Delta
        </span>
        <span className="font-mono text-xs text-text-accent mt-0.5">
          {greeks.delta}
        </span>
      </div>
      <div className="bg-bg-panel p-2 flex flex-col">
        <span className="font-mono text-[9px] text-text-secondary">
          Γ Gamma
        </span>
        <span className="font-mono text-xs text-text-accent mt-0.5">
          {greeks.gamma}
        </span>
      </div>
      <div className="bg-bg-panel p-2 flex flex-col">
        <span className="font-mono text-[9px] text-text-secondary">
          Θ Theta
        </span>
        <span className="font-mono text-xs text-text-accent mt-0.5">
          {greeks.theta}
        </span>
      </div>
      <div className="bg-bg-panel p-2 flex flex-col">
        <span className="font-mono text-[9px] text-text-secondary">V Vega</span>
        <span className="font-mono text-xs text-text-accent mt-0.5">
          {greeks.vega}
        </span>
      </div>
    </div>
  );
}
