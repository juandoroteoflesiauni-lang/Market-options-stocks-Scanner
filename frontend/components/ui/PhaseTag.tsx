"use client";
import clsx from "clsx";
import type { WyckoffPhase } from "@/store/types";

interface PhaseTagProps {
  phase: WyckoffPhase;
}

export function PhaseTag({ phase }: PhaseTagProps) {
  const phaseColors: Record<
    WyckoffPhase,
    { bg: string; text: string; label: string }
  > = {
    A: {
      bg: "bg-[rgba(124,58,237,0.15)]",
      text: "text-phase-a",
      label: "ACUMULACIÓN",
    },
    B: {
      bg: "bg-[rgba(37,99,235,0.15)]",
      text: "text-phase-b",
      label: "MARKUP",
    },
    C: {
      bg: "bg-[rgba(217,119,6,0.15)]",
      text: "text-phase-c",
      label: "DISTRIBUCIÓN",
    },
    D: {
      bg: "bg-[rgba(220,38,38,0.15)]",
      text: "text-phase-d",
      label: "MARKDOWN",
    },
  };

  const config = phaseColors[phase];

  return (
    <div
      className={clsx(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-pill border",
        config.bg,
        `border-${config.text.replace("text-", "")}/30`,
      )}
    >
      <div
        className={clsx(
          "w-1.5 h-1.5 rounded-full",
          config.text.replace("text-", "bg-"),
        )}
      />
      <span
        className={clsx("font-mono text-[10px] tracking-wide", config.text)}
      >
        {phase} · {config.label}
      </span>
    </div>
  );
}
