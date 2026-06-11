"use client";

import { Ban, CheckCircle2, CircleHelp, Gauge, Wifi } from "lucide-react";

import { cn } from "@/lib/utils";
import type {
  BingXDecisionSummary,
  BingXSuitability,
} from "@/lib/bingx-bot-types";

const EVENT_CONFIG: Record<
  BingXSuitability,
  {
    label: string;
    tone: string;
    score: string;
    icon: typeof CheckCircle2;
  }
> = {
  ALLOW: {
    label: "ALLOW",
    tone: "border-bull/45 bg-bull/10 text-bull",
    score: "text-bull",
    icon: CheckCircle2,
  },
  BLOCK: {
    label: "BLOCK",
    tone: "border-bear/45 bg-bear/10 text-bear",
    score: "text-bear",
    icon: Ban,
  },
  SIZE_DOWN: {
    label: "SIZE DOWN",
    tone: "border-warn/45 bg-warn/10 text-warn",
    score: "text-warn",
    icon: Gauge,
  },
  INSUFFICIENT_DATA: {
    label: "NO DATA",
    tone: "border-info/40 bg-info/10 text-info",
    score: "text-info",
    icon: CircleHelp,
  },
};

const L2_REASON_CODES = new Set([
  "l2_unavailable",
  "l2_spread_too_wide",
  "l2_depth_too_thin",
  "l2_imbalance_extreme",
  "low_l2_quality",
]);

interface BingxBrainEventCardProps {
  decision: BingXDecisionSummary;
}

export function BingxBrainEventCard({ decision }: BingxBrainEventCardProps) {
  const config =
    EVENT_CONFIG[decision.suitability] ?? EVENT_CONFIG.INSUFFICIENT_DATA;
  const Icon = config.icon;
  const timeStr = decision.timestamp
    ? new Date(decision.timestamp).toISOString().slice(11, 19)
    : "--";
  const l2Reasons = decision.reason_codes.filter((r) => L2_REASON_CODES.has(r));
  const coreReasons = decision.reason_codes.filter(
    (r) => !L2_REASON_CODES.has(r),
  );

  return (
    <article className="grid grid-cols-[32px_1fr_auto] gap-2 border border-line bg-base p-2 hover:border-line-strong">
      <div
        className={cn(
          "grid h-8 w-8 shrink-0 place-items-center border",
          config.tone,
        )}
      >
        <Icon className="h-4 w-4" />
      </div>

      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <p className="truncate font-mono text-[11px] font-black uppercase text-ink-100">
            {decision.symbol}
          </p>
          <span
            className={cn(
              "shrink-0 border px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase",
              config.tone,
            )}
          >
            {config.label}
          </span>
        </div>
        {coreReasons.length > 0 || l2Reasons.length === 0 ? (
          <p className="mt-1 truncate font-mono text-[10px] uppercase tracking-[0.05em] text-ink-500">
            {coreReasons.length ? coreReasons.join(" / ") : "limpio"}
          </p>
        ) : null}
        {l2Reasons.length > 0 ? (
          <p className="mt-0.5 inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-[0.05em] text-info">
            <Wifi className="h-2.5 w-2.5 shrink-0" />
            {l2Reasons.join(" / ")}
          </p>
        ) : null}
      </div>

      <div className="flex flex-col items-end justify-between">
        <span
          className={cn(
            "font-mono text-[13px] font-black tabular-nums",
            config.score,
          )}
        >
          {decision.probability != null
            ? `${(decision.probability * 100).toFixed(0)}%`
            : "--"}
        </span>
        <span className="font-mono text-[9px] uppercase text-ink-600">
          {timeStr}
        </span>
      </div>
    </article>
  );
}
