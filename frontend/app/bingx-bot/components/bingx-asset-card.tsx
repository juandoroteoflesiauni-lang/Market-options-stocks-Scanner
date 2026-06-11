"use client";

import { ArrowDownRight, ArrowUpRight, Ban, Radio } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  assetTypeLabel,
  fmtPrice,
  fmtVol,
  type BingXInstrument,
  type BingXSnapshotSummary,
  vsaLevel,
} from "@/lib/bingx-bot-types";

import { BingxAssetSparkline } from "./bingx-asset-sparkline";

interface BingxAssetCardProps {
  snapshot: BingXSnapshotSummary;
  instrument?: BingXInstrument;
  selected: boolean;
  compact?: boolean;
  onClick: () => void;
}

export function BingxAssetCard({
  snapshot,
  instrument,
  selected,
  compact = false,
  onClick,
}: BingxAssetCardProps) {
  const level = vsaLevel(snapshot.volume_z_score);
  const spiking = level === "spike";
  const watching = level === "watch";
  const assetLabel = instrument
    ? assetTypeLabel(instrument)
    : assetTypeLabel(snapshot.symbol);
  const lastPrice = instrument?.last_price ?? snapshot.latest_close;
  const closes = snapshot.closes_recent;
  const tapeUp =
    closes.length > 1 ? closes[closes.length - 1] >= closes[0] : true;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group relative min-h-[132px] w-full border bg-base p-3 text-left transition-colors",
        selected
          ? "border-info bg-info/[0.055] shadow-[inset_3px_0_0_var(--info)]"
          : spiking
            ? "border-bull/45 bg-bull/[0.035] hover:border-bull"
            : "border-line hover:border-line-strong hover:bg-hover",
        !instrument?.execution_allowed && instrument && "border-warn/40",
        compact && "min-h-[104px]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "h-1.5 w-1.5",
                spiking ? "animate-pulse bg-bull" : "bg-line-strong",
              )}
            />
            <span className="truncate font-mono text-sm font-black uppercase text-ink-100">
              {snapshot.symbol}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className="border border-line bg-elevated px-1.5 py-0.5 font-mono text-[9px] uppercase text-ink-500">
              {assetLabel}
            </span>
            {instrument && !instrument.execution_allowed ? (
              <span className="inline-flex items-center gap-1 border border-warn/45 bg-warn/10 px-1.5 py-0.5 font-mono text-[9px] uppercase text-warn">
                <Ban className="h-2.5 w-2.5" />
                bloqueado
              </span>
            ) : null}
            {instrument?.underlying_symbol &&
            instrument.underlying_symbol !== instrument.venue_symbol ? (
              <span className="border border-line bg-elevated px-1.5 py-0.5 font-mono text-[9px] uppercase text-ink-400">
                {instrument.underlying_symbol}
              </span>
            ) : null}
          </div>
        </div>
        <VsaBadge level={level} z={snapshot.volume_z_score} />
      </div>

      {!compact ? (
        <div className="my-2 border-y border-line/70 py-2">
          <BingxAssetSparkline
            closes={snapshot.closes_recent}
            spiking={spiking || tapeUp}
          />
        </div>
      ) : null}

      <div className="grid grid-cols-3 gap-2">
        <Metric
          label="Ultimo"
          value={fmtPrice(lastPrice)}
          tone="text-ink-100"
        />
        <Metric
          label={instrument ? "24h Vol" : "Vol Z"}
          value={
            instrument
              ? fmtVol(instrument.volume_24h_usdt)
              : snapshot.volume_z_score != null
                ? `${snapshot.volume_z_score >= 0 ? "+" : ""}${snapshot.volume_z_score.toFixed(1)}s`
                : "--"
          }
          tone={spiking ? "text-bull" : watching ? "text-warn" : "text-ink-400"}
        />
        <Metric
          label="OI / Lev"
          value={
            instrument
              ? `${fmtVol(instrument.open_interest)} / ${instrument.max_leverage}x`
              : `${snapshot.bars} barras`
          }
          tone="text-ink-300"
        />
      </div>

      <div className="mt-2 flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.08em] text-ink-600">
        <span className="inline-flex items-center gap-1">
          <Radio className="h-3 w-3" />
          {snapshot.interval}
        </span>
        <span
          className={cn(
            "inline-flex items-center gap-1",
            tapeUp ? "text-bull" : "text-bear",
          )}
        >
          {tapeUp ? (
            <ArrowUpRight className="h-3 w-3" />
          ) : (
            <ArrowDownRight className="h-3 w-3" />
          )}
          tape
        </span>
      </div>
    </button>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: string;
}) {
  return (
    <div className="min-w-0 border border-line bg-elevated px-2 py-1.5">
      <div className="text-[9px] font-bold uppercase tracking-[0.08em] text-ink-600">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 truncate font-mono text-xs font-bold tabular-nums",
          tone,
        )}
      >
        {value}
      </div>
    </div>
  );
}

function VsaBadge({
  level,
  z,
}: {
  level: string;
  z: number | null | undefined;
}) {
  const text = z != null ? `${z >= 0 ? "+" : ""}${z.toFixed(1)}s` : "--";
  if (level === "spike") {
    return (
      <span className="shrink-0 border border-bull/45 bg-bull/10 px-2 py-1 font-mono text-[10px] font-bold uppercase text-bull">
        VSA {text}
      </span>
    );
  }
  if (level === "watch") {
    return (
      <span className="shrink-0 border border-warn/45 bg-warn/10 px-2 py-1 font-mono text-[10px] font-bold uppercase text-warn">
        Alerta {text}
      </span>
    );
  }
  return (
    <span className="shrink-0 border border-line bg-elevated px-2 py-1 font-mono text-[10px] uppercase text-ink-500">
      Plano
    </span>
  );
}
