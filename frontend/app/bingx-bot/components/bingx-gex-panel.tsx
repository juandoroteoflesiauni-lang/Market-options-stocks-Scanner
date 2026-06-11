"use client";

import { Layers3 } from "lucide-react";

import { fmtPrice, type BingXOptionsMetrics } from "@/lib/bingx-bot-types";
import { cn } from "@/lib/utils";

interface BingxGexPanelProps {
  options: BingXOptionsMetrics | null;
  loading?: boolean;
}

export function BingxGexPanel({
  options,
  loading = false,
}: BingxGexPanelProps) {
  return (
    <section className="border border-line bg-base">
      <header className="flex items-center justify-between border-b border-line px-3 py-2">
        <p className="inline-flex items-center gap-2 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-brass">
          <Layers3 className="h-3.5 w-3.5" />
          Capa Options / GEX
        </p>
        <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-600">
          Layer 3
        </span>
      </header>

      {loading ? (
        <div className="m-3 h-28 animate-pulse bg-line/40" />
      ) : !options ? (
        <div className="grid grid-cols-2 gap-3 p-3">
          {["GEX Wall", "IV Percentile", "Put/Call", "Delta MM"].map(
            (label) => (
              <GexMetric key={label} label={label} value="N/A" />
            ),
          )}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 p-3">
          <GexMetric
            label="GEX Wall"
            value={fmtPrice(options.gex_wall_price)}
            valueClassName="text-ink-100"
            detail={
              options.gex_wall_distance_pct == null
                ? "--"
                : `${options.gex_wall_direction === "above" ? "arriba" : "abajo"} / ${options.gex_wall_distance_pct.toFixed(1)}%`
            }
            detailClassName={
              (options.gex_wall_distance_pct ?? 99) < 2
                ? "text-warn"
                : "text-ink-500"
            }
          />

          <GexMetric
            label="IV Percentile"
            value={
              options.iv_percentile == null
                ? "--"
                : `${options.iv_percentile.toFixed(0)}%`
            }
            valueClassName="text-info"
            detail={ivLabel(options.iv_percentile)}
          >
            <Meter
              value={Math.min(Math.max(options.iv_percentile ?? 0, 0), 100)}
              fillClassName="bg-info"
            />
          </GexMetric>

          <GexMetric
            label="Put/Call"
            value={
              options.put_call_ratio == null
                ? "--"
                : options.put_call_ratio.toFixed(2)
            }
            valueClassName={
              options.put_call_ratio == null
                ? "text-ink-500"
                : options.put_call_ratio < 0.7
                  ? "text-bull"
                  : options.put_call_ratio > 1.2
                    ? "text-bear"
                    : "text-ink-300"
            }
            detail={putCallLabel(options.put_call_ratio)}
          >
            <Meter
              value={Math.min(((options.put_call_ratio ?? 0) / 2) * 100, 100)}
              fillClassName={
                (options.put_call_ratio ?? 0) < 0.7 ? "bg-bull" : "bg-bear"
              }
            />
          </GexMetric>

          <GexMetric
            label="Delta Exposure MM"
            value={
              options.delta_exposure_usd == null
                ? "--"
                : `${options.delta_exposure_usd >= 0 ? "+" : ""}${(options.delta_exposure_usd / 1_000_000).toFixed(1)}M`
            }
            valueClassName={
              (options.delta_exposure_usd ?? 0) >= 0 ? "text-bull" : "text-bear"
            }
            detail={
              (options.delta_exposure_usd ?? 0) >= 0
                ? "MM net long"
                : "MM net short"
            }
          />
        </div>
      )}
    </section>
  );
}

function GexMetric({
  label,
  value,
  valueClassName,
  detail,
  detailClassName,
  children,
}: {
  label: string;
  value: string;
  valueClassName?: string;
  detail?: string;
  detailClassName?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="min-w-0 border border-line bg-elevated p-2">
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink-500">
          {label}
        </span>
        <span
          className={cn(
            "font-mono text-sm font-black tabular-nums",
            valueClassName ?? "text-ink-500",
          )}
        >
          {value}
        </span>
      </div>
      {children ? <div className="mt-2">{children}</div> : null}
      {detail ? (
        <p
          className={cn(
            "mt-1 font-mono text-[8px] uppercase tracking-[0.08em] text-ink-600",
            detailClassName,
          )}
        >
          {detail}
        </p>
      ) : null}
    </div>
  );
}

function Meter({
  value,
  fillClassName,
}: {
  value: number;
  fillClassName: string;
}) {
  return (
    <div className="h-1.5 overflow-hidden bg-line">
      <div
        className={cn("h-full transition-all", fillClassName)}
        style={{ width: `${value}%` }}
      />
    </div>
  );
}

function ivLabel(value: number | null) {
  if (value == null) return "--";
  if (value > 70) return "IV alta";
  if (value < 30) return "IV comprimida";
  return "IV moderada";
}

function putCallLabel(value: number | null) {
  if (value == null) return "--";
  if (value < 0.7) return "Skew alcista";
  if (value > 1.2) return "Skew bajista";
  return "Skew neutral";
}
