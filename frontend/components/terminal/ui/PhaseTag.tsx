"use client";

import { cn, phaseMeta } from "@/lib/terminal/format";
import type { Phase } from "@/lib/terminal/types";

export function PhaseTag({
  phase,
  short,
  className,
}: {
  phase: Phase;
  short?: boolean;
  className?: string;
}) {
  const meta = phaseMeta[phase];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-widest",
        meta.bg,
        meta.color,
        meta.border,
        className,
      )}
    >
      <span className="font-bold">{phase}</span>
      {!short && <span>{meta.label}</span>}
    </span>
  );
}
