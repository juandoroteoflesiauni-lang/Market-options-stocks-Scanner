import type { EngineCategory } from "@/types";

// Hex mirrors of the CSS tokens defined in src/styles/theme.css.
// Kept as hex so the `${col}18` opacity-suffix pattern keeps working.
// If you change a token in theme.css, mirror it here.
const BULL = "#2FB67C";
const BEAR = "#E04E5C";
const WARN = "#FFB020";
const INFO = "#6FA8DC";
const NEUTRAL = "#6B6B6B";

const PHASE_A = "#6D4AA8";
const PHASE_B = "#3B6FB8";
const PHASE_C = "#B86E1F";
const PHASE_D = "#A8333B";

export function phaseColor(phase: "A" | "B" | "C" | "D"): string {
  const map = { A: PHASE_A, B: PHASE_B, C: PHASE_C, D: PHASE_D };
  return map[phase];
}

export function phaseLabel(phase: "A" | "B" | "C" | "D"): string {
  const map = {
    A: "ACUMULACIÓN",
    B: "MARKUP",
    C: "DISTRIBUCIÓN",
    D: "MARKDOWN",
  };
  return map[phase];
}

export function signalColor(signal: "BULL" | "BEAR" | "NEUTRAL"): string {
  if (signal === "BULL") return BULL;
  if (signal === "BEAR") return BEAR;
  return NEUTRAL;
}

export function signalClass(signal: "BULL" | "BEAR" | "NEUTRAL"): string {
  if (signal === "BULL") return `text-[${BULL}]`;
  if (signal === "BEAR") return `text-[${BEAR}]`;
  return `text-[${NEUTRAL}]`;
}

export function confidenceColor(confidence: number): string {
  if (confidence >= 70) return BULL;
  if (confidence >= 50) return WARN;
  return BEAR;
}

export function pnlClass(value: number): string {
  if (value > 0) return `text-[${BULL}]`;
  if (value < 0) return `text-[${BEAR}]`;
  return `text-[${NEUTRAL}]`;
}

export function categoryColor(cat: EngineCategory): string {
  const map: Record<EngineCategory, string> = {
    ML: INFO,
    STATISTICAL: BULL,
    TECHNICAL: WARN,
    OPTIONS: PHASE_C,
    MACRO: BEAR,
    HYBRID: PHASE_A,
  };
  return map[cat];
}

export function riskBarColor(value: number, warn = 0.6, danger = 0.8): string {
  if (value >= danger) return BEAR;
  if (value >= warn) return WARN;
  return BULL;
}
