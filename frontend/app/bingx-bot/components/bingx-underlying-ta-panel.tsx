"use client";

import * as React from "react";
import { LineChart } from "lucide-react";

import { StatusBadge } from "@/components/ui/terminal";
import {
  fmtPrice,
  type BingXTAMetrics,
  type UnderlyingTA,
} from "@/lib/bingx-bot-types";
import { cn } from "@/lib/utils";

import { BingxTaPanel } from "./bingx-ta-panel";

interface BingxUnderlyingTaPanelProps {
  underlyingTa: UnderlyingTA | null | undefined;
  errorReason?: string;
  underlyingSymbol?: string;
  loading?: boolean;
}

export function BingxUnderlyingTaPanel({
  underlyingTa,
  errorReason,
  underlyingSymbol,
  loading = false,
}: BingxUnderlyingTaPanelProps) {
  const ok = underlyingTa?.ok ?? false;

  return (
    <section className="border border-line bg-base">
      <header className="flex items-center justify-between border-b border-line px-3 py-2">
        <p className="inline-flex items-center gap-2 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-brass">
          <LineChart className="h-3.5 w-3.5" />
          Underlying TA
          {underlyingSymbol ? (
            <span className="ml-1 font-mono text-[9px] tracking-[0.08em] text-ink-500">
              [{underlyingSymbol}]
            </span>
          ) : null}
        </p>
        <TrendBadge trend={underlyingTa?.trend_direction} ok={ok} />
      </header>

      {loading ? (
        <div className="m-3 h-28 animate-pulse bg-line/40" />
      ) : !ok ? (
        <UnavailableBlock reason={errorReason ?? underlyingTa?.reason} />
      ) : (
        <div className="space-y-3 p-3">
          <RsiGauge rsi={underlyingTa?.rsi_14} />
          <EmaStack
            fast={underlyingTa?.ema_fast}
            slow={underlyingTa?.ema_slow}
            trend={underlyingTa?.trend_direction}
          />
          <SourceFooter
            source={underlyingTa?.source}
            bars={underlyingTa?.bars_used}
          />
        </div>
      )}
    </section>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────

function TrendBadge({
  trend,
  ok,
}: {
  trend?: UnderlyingTA["trend_direction"];
  ok: boolean;
}) {
  if (!ok) {
    return (
      <StatusBadge tone="neutral" className="!min-h-6 !px-1.5 text-[9px]">
        N/A
      </StatusBadge>
    );
  }
  if (trend === "bullish") {
    return (
      <StatusBadge tone="bull" className="!min-h-6 !px-1.5 text-[9px]">
        Alcista
      </StatusBadge>
    );
  }
  if (trend === "bearish") {
    return (
      <StatusBadge tone="bear" className="!min-h-6 !px-1.5 text-[9px]">
        Bajista
      </StatusBadge>
    );
  }
  return (
    <StatusBadge tone="neutral" className="!min-h-6 !px-1.5 text-[9px]">
      Neutral
    </StatusBadge>
  );
}

function RsiGauge({ rsi }: { rsi: number | null | undefined }) {
  const value = rsi == null ? null : Math.min(Math.max(rsi, 0), 100);
  const color =
    value == null
      ? "text-ink-500"
      : value > 70
        ? "text-bear"
        : value < 30
          ? "text-info"
          : "text-bull";
  const fill =
    value == null
      ? "bg-line-strong"
      : value > 70
        ? "bg-bear"
        : value < 30
          ? "bg-info"
          : "bg-bull";

  return (
    <div className="border border-line bg-elevated p-2">
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink-500">
          RSI 14
        </span>
        <span
          className={cn("font-mono text-sm font-black tabular-nums", color)}
        >
          {value == null ? "--" : value.toFixed(1)}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden bg-line">
        <div
          className={cn("h-full transition-all", fill)}
          style={{ width: `${value ?? 50}%` }}
        />
      </div>
      <div className="mt-1 flex justify-between font-mono text-[8px] uppercase text-ink-600">
        <span>OS 30</span>
        <span>OB 70</span>
      </div>
    </div>
  );
}

function EmaStack({
  fast,
  slow,
  trend,
}: {
  fast: number | null | undefined;
  slow: number | null | undefined;
  trend?: UnderlyingTA["trend_direction"];
}) {
  const crossLabel =
    fast == null || slow == null
      ? "--"
      : fast > slow
        ? "Fast > Slow"
        : fast < slow
          ? "Fast < Slow"
          : "Fast = Slow";
  const crossClass =
    trend === "bullish"
      ? "text-bull"
      : trend === "bearish"
        ? "text-bear"
        : "text-ink-400";

  return (
    <div className="border border-line bg-elevated p-2">
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink-500">
          EMA Stack
        </span>
        <span
          className={cn(
            "font-mono text-[10px] font-bold uppercase tabular-nums",
            crossClass,
          )}
        >
          {crossLabel}
        </span>
      </div>
      <div className="mt-2 space-y-1">
        <PriceRow
          label="EMA Fast"
          value={fmtPrice(fast)}
          labelClassName="text-info"
        />
        <PriceRow
          label="EMA Slow"
          value={fmtPrice(slow)}
          labelClassName="text-brass"
        />
      </div>
    </div>
  );
}

function PriceRow({
  label,
  value,
  labelClassName,
}: {
  label: string;
  value: string;
  labelClassName?: string;
}) {
  return (
    <div className="flex justify-between gap-2 font-mono text-[10px]">
      <span className={cn("text-ink-600", labelClassName)}>{label}</span>
      <span className="tabular-nums text-ink-300">{value}</span>
    </div>
  );
}

function SourceFooter({
  source,
  bars,
}: {
  source: string | null | undefined;
  bars: number | null | undefined;
}) {
  return (
    <div className="flex items-center justify-between font-mono text-[9px] uppercase tracking-[0.1em] text-ink-600">
      <span>Source {source ?? "--"}</span>
      <span>Bars {bars ?? "--"}</span>
    </div>
  );
}

function UnavailableBlock({ reason }: { reason?: string }) {
  return (
    <div className="m-3 border border-dashed border-line bg-elevated p-3">
      <div className="font-mono text-[10px] font-bold uppercase tracking-[0.1em] text-ink-300">
        Underlying TA no disponible
      </div>
      <p className="mt-1 font-mono text-[10px] leading-relaxed text-ink-500">
        {reason ?? "engine_not_wired"}
      </p>
    </div>
  );
}

// ── Tab container variant ──────────────────────────────────────────────────
// Convenience wrapper: renders Venue TA + Underlying TA inside a tabbed
// section when both are available. Falls back to a single view when only
// one side has data. Kept in the same file because both views share styling
// and the wrapper is consumed only from the analysis drawer.

type TaTab = "venue" | "underlying";

interface BingxTaTabsProps {
  venueTa: BingXTAMetrics;
  underlyingTa: UnderlyingTA | null | undefined;
  errorReason?: string;
  underlyingSymbol?: string;
  loading?: boolean;
}

export function BingxTaTabs({
  venueTa,
  underlyingTa,
  errorReason,
  underlyingSymbol,
  loading = false,
}: BingxTaTabsProps) {
  const hasUnderlying = underlyingTa != null || errorReason != null;
  const [tab, setTab] = React.useState<TaTab>("venue");

  if (!hasUnderlying) {
    // Crypto path or routing not resolved — keep the legacy single-panel UI.
    return <BingxTaPanel ta={venueTa} loading={loading} />;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex border border-line bg-base font-mono text-[10px] font-bold uppercase">
        <TabButton active={tab === "venue"} onClick={() => setTab("venue")}>
          Venue TA
        </TabButton>
        <TabButton
          active={tab === "underlying"}
          onClick={() => setTab("underlying")}
        >
          Underlying TA
        </TabButton>
      </div>
      {tab === "venue" ? (
        <BingxTaPanel ta={venueTa} loading={loading} />
      ) : (
        <BingxUnderlyingTaPanel
          underlyingTa={underlyingTa}
          errorReason={errorReason}
          underlyingSymbol={underlyingSymbol}
          loading={loading}
        />
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "h-8 px-3 transition-colors",
        active
          ? "bg-brass text-void"
          : "text-ink-500 hover:bg-hover hover:text-ink-100",
      )}
    >
      {children}
    </button>
  );
}
