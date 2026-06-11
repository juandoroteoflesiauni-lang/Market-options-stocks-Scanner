"use client";

import * as React from "react";
import { Activity, Wifi, X } from "lucide-react";

import { StatusBadge } from "@/components/ui/terminal";
import { useBingxAnalysis } from "@/hooks/use-bingx-analysis";
import {
  deriveL2Status,
  fmtPrice,
  vsaLevel,
  type BingXSnapshotSummary,
} from "@/lib/bingx-bot-types";
import { cn } from "@/lib/utils";

import { BingxChart } from "./bingx-chart";
import { BingxGexPanel } from "./bingx-gex-panel";
import { BingxProbabilisticPanel } from "./bingx-probabilistic-panel";
import { BingxTaTabs } from "./bingx-underlying-ta-panel";

const INTERVALS = ["1m", "5m", "15m", "1h"] as const;
type Interval = (typeof INTERVALS)[number];

interface BingxAnalysisDrawerProps {
  symbol: string | null;
  snapshot: BingXSnapshotSummary | null;
  onClose: () => void;
}

export function BingxAnalysisDrawer({
  symbol,
  snapshot,
  onClose,
}: BingxAnalysisDrawerProps) {
  const [interval, setInterval] = React.useState<Interval>("5m");
  const { analysis, isLoading } = useBingxAnalysis(symbol, interval);

  const open = symbol != null;
  const level = vsaLevel(snapshot?.volume_z_score);
  const levelTone =
    level === "spike" ? "bull" : level === "watch" ? "warn" : "neutral";

  return (
    <div
      className={cn(
        "overflow-hidden transition-[max-height,opacity] duration-300 ease-in-out",
        open ? "max-h-[1400px] opacity-100" : "max-h-0 opacity-0",
      )}
    >
      <section className="border border-info/35 bg-elevated">
        <header className="flex flex-wrap items-center gap-3 border-b border-line bg-base px-4 py-3">
          <div className="flex min-w-[180px] items-center gap-3">
            <span
              className={cn(
                "h-2 w-2",
                level === "spike" ? "animate-pulse bg-bull" : "bg-line-strong",
              )}
            />
            <div>
              <div className="q-eyebrow">Analisis simbolo</div>
              <div className="font-mono text-sm font-black uppercase text-ink-100">
                {symbol ?? "--"}
              </div>
            </div>
          </div>

          <StatusBadge tone={levelTone}>
            VSA{" "}
            {snapshot?.volume_z_score != null
              ? `${snapshot.volume_z_score >= 0 ? "+" : ""}${snapshot.volume_z_score.toFixed(1)}s`
              : "--"}
          </StatusBadge>

          <div className="grid grid-cols-2 gap-2 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-500 sm:flex sm:items-center">
            <span>
              Ultimo{" "}
              <strong className="text-ink-100">
                {fmtPrice(snapshot?.latest_close)}
              </strong>
            </span>
            <span>
              Barras{" "}
              <strong className="text-ink-100">{snapshot?.bars ?? 0}</strong>
            </span>
          </div>

          <div className="flex-1" />

          <div className="flex border border-line bg-base font-mono text-[10px] font-bold uppercase">
            {INTERVALS.map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setInterval(value)}
                className={cn(
                  "h-8 px-3 transition-colors",
                  interval === value
                    ? "bg-info text-void"
                    : "text-ink-500 hover:bg-hover hover:text-ink-100",
                )}
              >
                {value}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={onClose}
            className="grid h-8 w-8 place-items-center border border-line bg-elevated text-ink-500 hover:border-line-strong hover:bg-hover hover:text-ink-100"
            aria-label="Cerrar analisis"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="border-b border-line bg-base px-4 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-500">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="inline-flex items-center gap-2">
              <Activity
                className={cn(
                  "h-3.5 w-3.5",
                  isLoading ? "animate-pulse text-info" : "text-brass",
                )}
              />
              {isLoading
                ? "Actualizando stack tecnico"
                : "Stack TA / GEX listo"}
            </span>
            <L2StatusChip
              status={deriveL2Status(
                analysis?.lob_status,
                analysis?.data_sources,
              )}
              qualityScore={analysis?.lob_quality_score ?? null}
            />
          </div>
          {analysis?.data_sources?.length ? (
            <div className="mt-1 flex flex-wrap gap-1">
              {analysis.data_sources.map((src) => (
                <span
                  key={src}
                  className="border border-line bg-elevated px-1.5 py-0.5 font-mono text-[8px] text-ink-600"
                >
                  {src}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="p-3">
          <BingxChart
            klines={analysis?.klines ?? []}
            ema9={analysis?.ta.ema_9 ?? null}
            ema21={analysis?.ta.ema_21 ?? null}
            vwap={analysis?.ta.vwap ?? null}
            vwapUpper={analysis?.ta.vwap_upper_1 ?? null}
            vwapLower={analysis?.ta.vwap_lower_1 ?? null}
            height={180}
          />
        </div>

        <div className="grid grid-cols-1 gap-3 border-t border-line p-3 lg:grid-cols-2">
          <BingxTaTabs
            venueTa={
              analysis?.ta ?? {
                rsi_14: null,
                ema_9: null,
                ema_21: null,
                ema_50: null,
                vwap: null,
                vwap_upper_1: null,
                vwap_lower_1: null,
                vsa_delta: null,
                vsa_z_score: null,
                trend: "neutral",
              }
            }
            underlyingTa={analysis?.underlying_ta ?? null}
            errorReason={analysis?.errors?.underlying_ta}
            underlyingSymbol={analysis?.underlying_symbol}
            loading={isLoading && !analysis}
          />
          <BingxGexPanel
            options={analysis?.options ?? null}
            loading={isLoading && !analysis}
          />
        </div>

        <div className="grid grid-cols-1 gap-3 border-t border-line p-3">
          <BingxProbabilisticPanel
            probabilistic={analysis?.probabilistic ?? null}
            errorReason={analysis?.errors?.probabilistic}
            loading={isLoading && !analysis}
          />
        </div>
      </section>
    </div>
  );
}

function L2StatusChip({
  status,
  qualityScore,
}: {
  status: "active" | "pending" | "unavailable";
  qualityScore?: number | null;
}) {
  const qualityLabel =
    qualityScore != null && Number.isFinite(qualityScore)
      ? ` ${(qualityScore * 100).toFixed(0)}%`
      : "";
  if (status === "active") {
    return (
      <span
        data-testid="l2-status-chip"
        data-status="active"
        className="inline-flex items-center gap-1 border border-bull/45 bg-bull/10 px-1.5 py-0.5 font-mono text-[8px] font-bold uppercase text-bull"
      >
        <Wifi className="h-2.5 w-2.5" />
        L2 Activo{qualityLabel}
      </span>
    );
  }
  if (status === "unavailable") {
    return (
      <span
        data-testid="l2-status-chip"
        data-status="unavailable"
        className="inline-flex items-center gap-1 border border-bear/45 bg-bear/10 px-1.5 py-0.5 font-mono text-[8px] font-bold uppercase text-bear"
      >
        <Wifi className="h-2.5 w-2.5" />
        L2 N/D
      </span>
    );
  }
  return (
    <span
      data-testid="l2-status-chip"
      data-status="pending"
      className="inline-flex items-center gap-1 border border-line bg-elevated px-1.5 py-0.5 font-mono text-[8px] uppercase text-ink-500"
    >
      <Wifi className="h-2.5 w-2.5" />
      L2 Pendiente
    </span>
  );
}
