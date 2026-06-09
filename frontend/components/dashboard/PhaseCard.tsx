import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { ProgressBar } from "@/components/ui/ProgressBar";

import type { PhaseMetrics, PhaseStatus } from "@/store/types";

interface PhaseCardProps {
  phase: PhaseMetrics;
}

const phaseLabels: Record<string, string> = {
  A: "Scanner",
  B: "Microstructure",
  C: "Derivatives",
  D: "Real-Time Monitor",
};

const phaseDescriptions: Record<string, string> = {
  A: "REST polling → volume & volatility filters",
  B: "VPIN + OFI matrix processing (local)",
  C: "Options chain analysis → top contracts",
  D: "WebSocket tick-by-tick monitoring",
};

const statusVariant: Record<
  PhaseStatus,
  "buy" | "sell" | "neutral" | "warning"
> = {
  ACTIVE: "buy",
  IDLE: "neutral",
  ERROR: "sell",
  DISABLED: "warning",
};

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return date.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function PhaseCard({ phase }: PhaseCardProps) {
  const reductionRatio =
    phase.input_count > 0
      ? ((phase.input_count - phase.output_count) / phase.input_count) * 100
      : 0;

  return (
    <Card>
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-xs font-mono font-semibold tracking-widest text-text-muted">
            PHASE {phase.phase_id}
          </span>
          <h3 className="text-base font-medium text-text-primary">
            {phaseLabels[phase.phase_id] ?? phase.label}
          </h3>
        </div>
        <Badge variant={statusVariant[phase.status]}>{phase.status}</Badge>
      </div>

      <p className="text-xs text-text-muted mb-4">
        {phaseDescriptions[phase.phase_id]}
      </p>

      {/* Funnel metrics */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="font-mono tabular-nums text-sm text-text-primary">
            {phase.input_count.toLocaleString()}
          </span>
          <span className="text-text-muted text-xs">→</span>
          <span className="font-mono tabular-nums text-sm font-semibold text-signal-buy">
            {phase.output_count.toLocaleString()}
          </span>
        </div>
        <span className="text-xs text-text-secondary">
          {formatTimestamp(phase.last_processed_at)}
        </span>
      </div>

      <ProgressBar
        value={phase.output_count}
        max={phase.input_count}
        variant={phase.status === "ACTIVE" ? "buy" : "default"}
        showLabel
      />

      {/* Processing time */}
      {phase.processing_time_ms !== null && (
        <div className="mt-2 text-xs text-text-muted">
          <span className="font-mono tabular-nums">
            {phase.processing_time_ms}ms
          </span>
          {" processing · "}
          <span className="font-mono tabular-nums">
            {reductionRatio.toFixed(1)}%
          </span>
          {" filtered"}
        </div>
      )}
    </Card>
  );
}
