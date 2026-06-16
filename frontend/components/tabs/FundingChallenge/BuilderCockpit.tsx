"use client";

import { MetricCard } from "@/components/panels/MetricCard";
import type { BuilderMetricsSnapshot } from "@/store/fundingStore";

interface BuilderCockpitProps {
  metrics: BuilderMetricsSnapshot | null;
  accentColor: string;
}

export function BuilderCockpit({ metrics, accentColor }: BuilderCockpitProps) {
  const payoutState = metrics?.payout_eligibility_state ?? "not_applicable";
  const payoutColor =
    payoutState === "eligible"
      ? "#00E676"
      : payoutState === "building_buffer"
        ? "#FFB800"
        : accentColor;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        background: "rgba(15, 23, 42, 0.4)",
        backdropFilter: "blur(12px)",
        border: "1px solid rgba(255, 255, 255, 0.05)",
        borderRadius: "var(--radius-lg)",
        padding: 16,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 14,
            fontWeight: 600,
            color: "#E8EDF5",
          }}
        >
          MFFU Builder Cockpit
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            color: accentColor,
            letterSpacing: "0.08em",
          }}
        >
          {metrics?.phase ?? "EVAL_ACTIVE"}
        </span>
      </div>

      {metrics?.is_floor_drift_warning && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            background: "rgba(255, 184, 0, 0.12)",
            border: "1px solid rgba(255, 184, 0, 0.4)",
            borderRadius: "var(--radius-md)",
            padding: "8px 12px",
            fontSize: 12,
            color: "#FFB800",
          }}
        >
          <span style={{ fontWeight: 600 }}>Floor drift</span>
          <span style={{ color: "#E8EDF5" }}>
            tu nuevo máximo sube el piso EOD ${metrics.floor_drift_usd}. Quedarías a $
            {metrics.distance_to_projected_floor} del piso de mañana.
          </span>
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: 12,
        }}
      >
        <MetricCard
          title="Eval Progress"
          value={`${metrics?.eval_progress_pct ?? "0"}%`}
          delta={parseFloat(metrics?.eval_progress_pct ?? "0")}
          deltaLabel="toward $3,000"
          accentColor={accentColor}
        />
        <MetricCard
          title="Trailing DD Distance"
          value={`$${metrics?.distance_to_trailing_dd ?? "0"}`}
          delta={parseFloat(metrics?.survival_score ?? "0")}
          deltaLabel={`score ${metrics?.survival_status ?? "SAFE"}`}
          accentColor={accentColor}
        />
        <MetricCard
          title="DLL Soft Pause"
          value={`$${metrics?.distance_to_dll_soft_pause ?? "0"}`}
          delta={parseFloat(metrics?.recommended_risk_pct ?? "0")}
          deltaLabel="risk %"
          accentColor={accentColor}
        />
        <MetricCard
          title="Payout Buffer"
          value={`${metrics?.buffer_progress_pct ?? "0"}%`}
          delta={parseFloat(metrics?.withdrawable_amount ?? "0")}
          deltaLabel={`withdrawable $${metrics?.withdrawable_amount ?? "0"}`}
          accentColor={payoutColor}
        />
        <MetricCard
          title="Max Profit Hoy"
          value={`$${metrics?.max_profit_today_usd ?? "0"}`}
          delta={metrics?.is_consistency_at_risk ? -1 : 1}
          deltaLabel={metrics?.is_consistency_at_risk ? "consistency at risk" : "consistency ok"}
          accentColor={metrics?.is_consistency_at_risk ? "#FFB800" : accentColor}
        />
        <MetricCard
          title="Payout ETA"
          value={
            metrics?.estimated_days_to_payout != null
              ? `${metrics.estimated_days_to_payout}d`
              : payoutState === "eligible"
                ? "Ready"
                : "—"
          }
          delta={metrics?.qualified_days_remaining ?? 0}
          deltaLabel={`faltan ${metrics?.qualified_days_remaining ?? 0} días / $${
            metrics?.buffer_remaining ?? "0"
          }`}
          accentColor={payoutColor}
        />
      </div>
    </div>
  );
}
