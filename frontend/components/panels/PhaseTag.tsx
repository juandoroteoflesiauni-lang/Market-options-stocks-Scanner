"use client";
import { phaseColor, phaseLabel } from "@/utils/colors";

interface Props {
  phase: "A" | "B" | "C" | "D";
  compact?: boolean;
}

export function PhaseTag({ phase, compact = false }: Props) {
  const color = phaseColor(phase);
  return (
    <span
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: compact ? 9 : 10,
        color,
        background: `${color}18`,
        border: `1px solid ${color}44`,
        borderRadius: "var(--radius-pill)",
        padding: compact ? "1px 6px" : "2px 8px",
        letterSpacing: "0.08em",
        whiteSpace: "nowrap",
        display: "inline-block",
      }}
    >
      {phase}
      {!compact && ` ${phaseLabel(phase)}`}
    </span>
  );
}
