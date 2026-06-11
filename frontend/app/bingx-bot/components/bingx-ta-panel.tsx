"use client";

import { BarChart3 } from "lucide-react";

import { cn } from "@/lib/utils";
import { fmtPrice, fmtVol, type BingXTAMetrics } from "@/lib/bingx-bot-types";

interface BingxTaPanelProps {
  ta: BingXTAMetrics;
  loading?: boolean;
}

export function BingxTaPanel({ ta, loading = false }: BingxTaPanelProps) {
  const rsi = ta.rsi_14;
  const rsiColor =
    rsi == null
      ? "text-ink-500"
      : rsi > 70
        ? "text-bear"
        : rsi < 30
          ? "text-info"
          : "text-bull";
  const rsiFill =
    rsi == null
      ? "bg-line-strong"
      : rsi > 70
        ? "bg-bear"
        : rsi < 30
          ? "bg-info"
          : "bg-bull";
  const vsaPositive = (ta.vsa_delta ?? 0) >= 0;

  return (
    <section className="border border-line bg-base">
      <header className="flex items-center justify-between border-b border-line px-3 py-2">
        <p className="inline-flex items-center gap-2 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-info">
          <BarChart3 className="h-3.5 w-3.5" />
          Capa tecnica
        </p>
        <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-600">
          Layer 2
        </span>
      </header>

      {loading ? (
        <div className="m-3 h-28 animate-pulse bg-line/40" />
      ) : (
        <div className="grid grid-cols-2 gap-3 p-3">
          <PanelMetric
            label="RSI 14"
            value={rsi != null ? rsi.toFixed(1) : "--"}
            valueClassName={rsiColor}
          >
            <Meter
              value={Math.min(Math.max(rsi ?? 50, 0), 100)}
              fillClassName={rsiFill}
            />
            <div className="flex justify-between font-mono text-[8px] uppercase text-ink-600">
              <span>OS 30</span>
              <span>OB 70</span>
            </div>
          </PanelMetric>

          <PanelMetric
            label="VSA Delta"
            value={
              ta.vsa_delta != null
                ? `${ta.vsa_delta >= 0 ? "+" : ""}${fmtVol(ta.vsa_delta)}`
                : "--"
            }
            valueClassName={vsaPositive ? "text-bull" : "text-bear"}
          >
            <DivergingMeter
              value={Math.min(Math.abs((ta.vsa_z_score ?? 0) / 4) * 50, 50)}
              positive={vsaPositive}
            />
            <p className="font-mono text-[8px] uppercase text-ink-600">
              Z {ta.vsa_z_score != null ? ta.vsa_z_score.toFixed(2) : "--"}
            </p>
          </PanelMetric>

          <PanelMetric
            label="VWAP / Bandas"
            value={fmtPrice(ta.vwap)}
            valueClassName="text-brass"
          >
            <PriceRow label="+1s" value={fmtPrice(ta.vwap_upper_1)} />
            <PriceRow label="VWAP" value={fmtPrice(ta.vwap)} strong />
            <PriceRow label="-1s" value={fmtPrice(ta.vwap_lower_1)} />
          </PanelMetric>

          <PanelMetric
            label="EMA Stack"
            value={trendLabel(ta.trend)}
            valueClassName={
              ta.trend === "bullish"
                ? "text-bull"
                : ta.trend === "bearish"
                  ? "text-bear"
                  : "text-ink-400"
            }
          >
            <PriceRow
              label="EMA 9"
              value={fmtPrice(ta.ema_9)}
              labelClassName="text-info"
            />
            <PriceRow
              label="EMA 21"
              value={fmtPrice(ta.ema_21)}
              labelClassName="text-bull"
            />
            <PriceRow label="EMA 50" value={fmtPrice(ta.ema_50)} />
          </PanelMetric>
        </div>
      )}
    </section>
  );
}

function PanelMetric({
  label,
  value,
  valueClassName,
  children,
}: {
  label: string;
  value: string;
  valueClassName?: string;
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
            valueClassName,
          )}
        >
          {value}
        </span>
      </div>
      {children ? <div className="mt-2 space-y-1">{children}</div> : null}
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

function DivergingMeter({
  value,
  positive,
}: {
  value: number;
  positive: boolean;
}) {
  return (
    <div className="relative h-1.5 overflow-hidden bg-line">
      <div
        className={cn(
          "absolute top-0 h-full",
          positive ? "left-1/2 bg-bull" : "right-1/2 bg-bear",
        )}
        style={{ width: `${value}%` }}
      />
      <span className="absolute left-1/2 top-0 h-full w-px bg-line-strong" />
    </div>
  );
}

function PriceRow({
  label,
  value,
  strong,
  labelClassName,
}: {
  label: string;
  value: string;
  strong?: boolean;
  labelClassName?: string;
}) {
  return (
    <div className="flex justify-between gap-2 font-mono text-[10px]">
      <span
        className={cn("text-ink-600", strong && "text-brass", labelClassName)}
      >
        {label}
      </span>
      <span
        className={cn(
          "tabular-nums text-ink-400",
          strong && "font-bold text-brass",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function trendLabel(trend: BingXTAMetrics["trend"]) {
  if (trend === "bullish") return "Alcista";
  if (trend === "bearish") return "Bajista";
  return "Neutral";
}
