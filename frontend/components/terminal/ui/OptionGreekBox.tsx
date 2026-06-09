"use client";

import { cn } from "@/lib/terminal/format";
import type { Greeks } from "@/lib/terminal/types";

export function OptionGreekBox({ greeks, className }: { greeks: Greeks; className?: string }) {
  const cells: { label: string; symbol: string; value: number }[] = [
    { label: "Delta", symbol: "Δ", value: greeks.delta },
    { label: "Gamma", symbol: "Γ", value: greeks.gamma },
    { label: "Theta", symbol: "Θ", value: greeks.theta },
    { label: "Vega", symbol: "V", value: greeks.vega },
  ];
  return (
    <div className={cn("grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border-subtle bg-border-subtle", className)}>
      {cells.map((c) => (
        <div key={c.label} className="flex items-center justify-between gap-1 bg-bg-panel px-2 py-1">
          <span className="font-mono text-[10px] text-text-muted">
            <span className="text-text-secondary">{c.symbol}</span> {c.label}
          </span>
          <span className="font-mono text-[11px] tabular-nums text-text-accent">
            {c.value.toFixed(c.symbol === "Γ" ? 3 : 2)}
          </span>
        </div>
      ))}
    </div>
  );
}
