"use client";

import clsx from "clsx";
import type { WyckoffPhase } from "@/store/types";

interface DataPanelProps {
  title: string;
  phase?: WyckoffPhase | "a" | "b" | "c" | "d";
  active?: boolean;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

const phaseBorderColors: Record<string, string> = {
  a: "bg-phase-a",
  b: "bg-phase-b",
  c: "bg-phase-c",
  d: "bg-phase-d",
  A: "bg-phase-a",
  B: "bg-phase-b",
  C: "bg-phase-c",
  D: "bg-phase-d",
};

export function DataPanel({
  title,
  phase,
  active = false,
  actions,
  children,
  className,
}: DataPanelProps) {
  const phaseColorClass = phase ? phaseBorderColors[phase] : undefined;

  return (
    <section
      className={clsx(
        "bg-bg-surface/60 backdrop-blur-md border border-white/5 flex flex-col overflow-hidden h-full rounded-lg shadow-[0_4px_24px_rgba(0,0,0,0.3)]",
        active ? "border-border-accent/40" : "border-border-default/60",
        className,
      )}
    >
      {/* Panel Header */}
      <div className="h-8 bg-bg-elevated/45 border-b border-border-subtle flex items-center px-3 gap-2 shrink-0 select-none rounded-t-lg">
        {/* Phase Color Accent Bar */}
        {phase && phaseColorClass && (
          <span
            className={clsx(
              "w-[3px] h-3.5 rounded-xs shrink-0",
              phaseColorClass,
            )}
          />
        )}

        {/* Panel Title */}
        <span className="font-ui text-[10px] font-bold tracking-caps uppercase text-text-secondary">
          {title}
        </span>

        {/* Actions Spacer */}
        <div className="flex-1" />

        {/* Custom Actions (e.g. Buttons/Selects) */}
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>

      {/* Panel Scrollable Content */}
      <div className="flex-1 overflow-auto min-h-0 relative rounded-b-lg">
        {children}
      </div>
    </section>
  );
}
