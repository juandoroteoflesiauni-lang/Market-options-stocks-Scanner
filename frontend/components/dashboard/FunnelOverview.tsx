"use client";

import { PhaseCard } from "./PhaseCard";
import { useFunnelOverview } from "@/hooks/useFunnelOverview";

export function FunnelOverview() {
  const { overview, isLoading, error } = useFunnelOverview();

  if (isLoading && !overview) {
    return (
      <section className="space-y-4" aria-label="Funnel Overview">
        <h2 className="text-2xl font-semibold tracking-tight text-text-primary">
          Quantitative Funnel
        </h2>
        <div className="grid gap-4 md:grid-cols-2">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="glass-card p-6 h-40 animate-pulse bg-bg-elevated/50"
            />
          ))}
        </div>
      </section>
    );
  }

  if (error && !overview) {
    return (
      <section className="space-y-4" aria-label="Funnel Overview">
        <h2 className="text-2xl font-semibold tracking-tight text-text-primary">
          Quantitative Funnel
        </h2>
        <div className="glass-card p-6 text-center">
          <p className="text-signal-sell text-sm">{error}</p>
          <p className="text-text-muted text-xs mt-2">
            Backend not connected. Start the API server to see live data.
          </p>
        </div>
      </section>
    );
  }

  // Fallback phases when backend is not yet connected
  const phases = overview?.phases ?? [
    {
      phase_id: "A" as const,
      label: "Scanner",
      status: "IDLE" as const,
      input_count: 5000,
      output_count: 0,
      last_processed_at: null,
      processing_time_ms: null,
    },
    {
      phase_id: "B" as const,
      label: "Microstructure",
      status: "IDLE" as const,
      input_count: 300,
      output_count: 0,
      last_processed_at: null,
      processing_time_ms: null,
    },
    {
      phase_id: "C" as const,
      label: "Derivatives",
      status: "IDLE" as const,
      input_count: 20,
      output_count: 0,
      last_processed_at: null,
      processing_time_ms: null,
    },
    {
      phase_id: "D" as const,
      label: "Monitor",
      status: "DISABLED" as const,
      input_count: 5,
      output_count: 0,
      last_processed_at: null,
      processing_time_ms: null,
    },
  ];

  return (
    <section className="space-y-4" aria-label="Funnel Overview">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight text-text-primary">
          Quantitative Funnel
        </h2>
        {overview && (
          <div className="flex items-center gap-4 text-xs text-text-muted">
            <span>
              Signals:{" "}
              <span className="font-mono tabular-nums text-signal-buy">
                {overview.total_signals_emitted}
              </span>
            </span>
          </div>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {phases.map((phase) => (
          <PhaseCard key={phase.phase_id} phase={phase} />
        ))}
      </div>
    </section>
  );
}
