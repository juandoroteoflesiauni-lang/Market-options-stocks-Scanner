"use client";

import { Brain } from "lucide-react";

import { StatusBadge, type TerminalTone } from "@/components/ui/terminal";
import type {
  BingXPredictiveSignal,
  EquityProbabilistic,
  EquityProbabilisticFeatures,
} from "@/lib/bingx-bot-types";
import { cn } from "@/lib/utils";

interface BingxProbabilisticPanelProps {
  probabilistic: EquityProbabilistic | null | undefined;
  signal?: BingXPredictiveSignal | null | undefined;
  errorReason?: string;
  loading?: boolean;
}

export function BingxProbabilisticPanel({
  probabilistic,
  signal,
  errorReason,
  loading = false,
}: BingxProbabilisticPanelProps) {
  // Prefer the bridge signal's source/quality for the header badge when
  // present — that is the institutional output Risk-Desk consumes. The
  // legacy ``probabilistic.source`` is shown only when no bridge signal is
  // available (backward-compatible fallback path).
  const headerSource = signal?.source ?? probabilistic?.source;
  const headerOk = signal != null ? true : (probabilistic?.ok ?? false);

  return (
    <section className="border border-line bg-base">
      <header className="flex items-center justify-between border-b border-line px-3 py-2">
        <p className="inline-flex items-center gap-2 font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-info">
          <Brain className="h-3.5 w-3.5" />
          Senales probabilisticas
        </p>
        <SourceBadge source={headerSource} ok={headerOk} />
      </header>

      {loading ? (
        <div className="m-3 h-28 animate-pulse bg-line/40" />
      ) : signal ? (
        <SignalSummary signal={signal} probabilistic={probabilistic} />
      ) : !probabilistic || !probabilistic.ok ? (
        <UnavailableBlock reason={errorReason ?? probabilistic?.reason} />
      ) : (
        <div className="space-y-3 p-3">
          <ProbabilityBar
            bull={probabilistic.bull_probability}
            bear={probabilistic.bear_probability}
            neutral={probabilistic.neutral_probability}
          />

          <div className="grid grid-cols-2 gap-2">
            <ScoreCell label="Confidence" value={probabilistic.confidence} />
            <ScoreCell
              label="Coverage"
              value={probabilistic.features?.coverage}
            />
          </div>

          {probabilistic.features ? (
            <FeaturesTable features={probabilistic.features} />
          ) : null}
        </div>
      )}
    </section>
  );
}

function SignalSummary({
  signal,
  probabilistic,
}: {
  signal: BingXPredictiveSignal;
  probabilistic?: EquityProbabilistic | null;
}) {
  return (
    <div className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <BiasChip bias={signal.directional_bias} />
        <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-500">
          Horizonte <span className="text-ink-100">{signal.horizon}</span>
        </span>
        {signal.quality_score != null ? (
          <span className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-500">
            Quality{" "}
            <span className="text-ink-100">
              {(signal.quality_score * 100).toFixed(0)}%
            </span>
          </span>
        ) : null}
      </div>

      <ProbabilityBar
        bull={signal.probability_long ?? probabilistic?.bull_probability}
        bear={signal.probability_short ?? probabilistic?.bear_probability}
        neutral={
          signal.probability_long != null && signal.probability_short != null
            ? Math.max(
                0,
                1 -
                  (signal.probability_long ?? 0) -
                  (signal.probability_short ?? 0),
              )
            : probabilistic?.neutral_probability
        }
      />

      <div className="grid grid-cols-2 gap-2">
        <ScoreCell label="Confidence" value={signal.confidence} />
        <ScoreCell label="Quality" value={signal.quality_score} />
      </div>

      {signal.reason_codes.length > 0 ? (
        <ReasonCodes codes={signal.reason_codes} />
      ) : null}
    </div>
  );
}

function BiasChip({
  bias,
}: {
  bias: BingXPredictiveSignal["directional_bias"];
}) {
  if (bias === "LONG") {
    return (
      <span
        data-testid="bias-chip"
        data-bias="LONG"
        className="inline-flex items-center gap-1 border border-bull/45 bg-bull/10 px-1.5 py-0.5 font-mono text-[10px] font-black uppercase text-bull"
      >
        LONG
      </span>
    );
  }
  if (bias === "SHORT") {
    return (
      <span
        data-testid="bias-chip"
        data-bias="SHORT"
        className="inline-flex items-center gap-1 border border-bear/45 bg-bear/10 px-1.5 py-0.5 font-mono text-[10px] font-black uppercase text-bear"
      >
        SHORT
      </span>
    );
  }
  return (
    <span
      data-testid="bias-chip"
      data-bias="NEUTRAL"
      className="inline-flex items-center gap-1 border border-line bg-elevated px-1.5 py-0.5 font-mono text-[10px] font-black uppercase text-ink-300"
    >
      NEUTRAL
    </span>
  );
}

function ReasonCodes({ codes }: { codes: string[] }) {
  return (
    <div
      data-testid="reason-codes"
      className="border border-line bg-elevated p-2"
    >
      <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-ink-500">
        Reason codes
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {codes.map((code) => (
          <span
            key={code}
            className="border border-line bg-base px-1.5 py-0.5 font-mono text-[8px] text-ink-400"
          >
            {code}
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────

function SourceBadge({ source, ok }: { source?: string; ok: boolean }) {
  if (!source || !ok) {
    return (
      <StatusBadge tone="neutral" className="!min-h-6 !px-1.5 text-[9px]">
        No disponible
      </StatusBadge>
    );
  }
  // Bridge sources — ranked highest-to-lowest authority. Tone differentiates
  // institutional sources from the heuristic fallback so the operator never
  // confuses "meta-signal said LONG" with "RSI heuristic said LONG".
  if (source === "meta_signal") {
    return (
      <StatusBadge tone="info" className="!min-h-6 !px-1.5 text-[9px]">
        Meta-Signal
      </StatusBadge>
    );
  }
  if (source === "predictive_options_2") {
    return (
      <StatusBadge tone="info" className="!min-h-6 !px-1.5 text-[9px]">
        Predictive Options 2
      </StatusBadge>
    );
  }
  if (source === "thesis") {
    return (
      <StatusBadge tone="info" className="!min-h-6 !px-1.5 text-[9px]">
        Thesis AI
      </StatusBadge>
    );
  }
  if (source === "crypto_predictive") {
    return (
      <StatusBadge tone="info" className="!min-h-6 !px-1.5 text-[9px]">
        Crypto Engine
      </StatusBadge>
    );
  }
  if (source === "meta_learner") {
    return (
      <StatusBadge tone="info" className="!min-h-6 !px-1.5 text-[9px]">
        Meta-Learner
      </StatusBadge>
    );
  }
  if (source === "equity_heuristic") {
    return (
      <StatusBadge tone="warn" className="!min-h-6 !px-1.5 text-[9px]">
        Heuristico
      </StatusBadge>
    );
  }
  return (
    <StatusBadge tone="info" className="!min-h-6 !px-1.5 text-[9px]">
      {source}
    </StatusBadge>
  );
}

function ProbabilityBar({
  bull,
  bear,
  neutral,
}: {
  bull: number | undefined;
  bear: number | undefined;
  neutral: number | undefined;
}) {
  const b = clamp01(bull);
  const x = clamp01(bear);
  const n = clamp01(neutral);
  const total = b + x + n;
  const bullPct = total > 0 ? (b / total) * 100 : 0;
  const bearPct = total > 0 ? (x / total) * 100 : 0;
  const neutralPct = total > 0 ? (n / total) * 100 : 0;

  return (
    <div>
      <div className="flex items-center justify-between font-mono text-[9px] uppercase tracking-[0.1em] text-ink-500">
        <span>Probabilidades</span>
        <span className="text-ink-600">Bull / Neutral / Bear</span>
      </div>
      <div
        className="mt-1.5 flex h-4 w-full overflow-hidden border border-line bg-base"
        role="img"
        aria-label={`Bull ${bullPct.toFixed(0)}% Neutral ${neutralPct.toFixed(0)}% Bear ${bearPct.toFixed(0)}%`}
      >
        <ProbSegment
          pct={bullPct}
          className="bg-bull text-void"
          label={`${bullPct.toFixed(0)}%`}
        />
        <ProbSegment
          pct={neutralPct}
          className="bg-line-strong text-ink-100"
          label={`${neutralPct.toFixed(0)}%`}
        />
        <ProbSegment
          pct={bearPct}
          className="bg-bear text-void"
          label={`${bearPct.toFixed(0)}%`}
        />
      </div>
      <div className="mt-1 flex justify-between font-mono text-[9px] uppercase tracking-[0.08em]">
        <span className="text-bull">Bull {fmtProb(bull)}</span>
        <span className="text-ink-400">Neu {fmtProb(neutral)}</span>
        <span className="text-bear">Bear {fmtProb(bear)}</span>
      </div>
    </div>
  );
}

function ProbSegment({
  pct,
  className,
  label,
}: {
  pct: number;
  className: string;
  label: string;
}) {
  if (pct <= 0) return null;
  return (
    <div
      className={cn(
        "flex items-center justify-center font-mono text-[9px] font-black tabular-nums tracking-tight",
        className,
      )}
      style={{ width: `${pct}%` }}
    >
      {pct >= 10 ? label : null}
    </div>
  );
}

function ScoreCell({
  label,
  value,
}: {
  label: string;
  value: number | null | undefined;
}) {
  const tone = semaphoreTone(value);
  const colorClass =
    tone === "bull"
      ? "text-bull"
      : tone === "warn"
        ? "text-warn"
        : tone === "bear"
          ? "text-bear"
          : "text-ink-500";
  const fillClass =
    tone === "bull"
      ? "bg-bull"
      : tone === "warn"
        ? "bg-warn"
        : tone === "bear"
          ? "bg-bear"
          : "bg-line-strong";
  const pct =
    value == null ? 0 : Math.round(Math.min(Math.max(value, 0), 1) * 100);

  return (
    <div className="border border-line bg-elevated p-2">
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-ink-500">
          {label}
        </span>
        <span
          className={cn(
            "font-mono text-sm font-black tabular-nums",
            colorClass,
          )}
        >
          {value == null ? "--" : `${pct}%`}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden bg-line">
        <div
          className={cn("h-full transition-all", fillClass)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function FeaturesTable({
  features,
}: {
  features: EquityProbabilisticFeatures;
}) {
  const rows: Array<
    [string, number | null | undefined, (v: number) => string]
  > = [
    ["RSI 14", features.rsi_14, (v) => v.toFixed(1)],
    [
      "Momentum 10",
      features.momentum_10,
      (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`,
    ],
    [
      "Z-Score 20",
      features.return_zscore_20,
      (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`,
    ],
    ["ATR norm 14", features.atr_norm_14, (v) => v.toFixed(2)],
  ];
  return (
    <div className="border border-line bg-elevated">
      <div className="border-b border-line px-2 py-1 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-500">
        Features
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 p-2 font-mono text-[10px]">
        {rows.map(([label, value, fmt]) => (
          <div key={label} className="flex justify-between gap-2">
            <span className="text-ink-600">{label}</span>
            <span className="tabular-nums text-ink-300">
              {value == null ? "--" : fmt(value)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function UnavailableBlock({ reason }: { reason?: string }) {
  return (
    <div className="m-3 border border-dashed border-line bg-elevated p-3">
      <div className="font-mono text-[10px] font-bold uppercase tracking-[0.1em] text-ink-300">
        No disponible
      </div>
      <p className="mt-1 font-mono text-[10px] leading-relaxed text-ink-500">
        {reason ?? "insufficient_data"}
      </p>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function clamp01(v: number | null | undefined): number {
  if (v == null || Number.isNaN(v)) return 0;
  if (v < 0) return 0;
  if (v > 1) return 1;
  return v;
}

function fmtProb(v: number | null | undefined): string {
  if (v == null) return "--";
  return `${(clamp01(v) * 100).toFixed(0)}%`;
}

function semaphoreTone(v: number | null | undefined): TerminalTone | "neutral" {
  if (v == null) return "neutral";
  if (v >= 0.7) return "bull";
  if (v >= 0.4) return "warn";
  return "bear";
}
