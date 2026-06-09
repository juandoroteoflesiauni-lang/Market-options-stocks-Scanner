"use client";

import { memo } from "react";

import { cn, signColor } from "@/lib/terminal/format";

export const MiniSparkline = memo(function MiniSparkline({
  data,
  width = 80,
  height = 24,
  className,
}: {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
}) {
  if (!data || data.length < 2) {
    return <svg width={width} height={height} className={className} />;
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const up = data[data.length - 1] >= data[0];
  const stroke = up ? "#00e676" : "#ff3d5a";
  const id = `spark-${Math.round(min)}-${Math.round(max)}-${data.length}`;

  const pts = data.map((d, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((d - min) / range) * (height - 2) - 1;
    return [x, y] as const;
  });
  const line = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;

  return (
    <svg width={width} height={height} className={className} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.3" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
});

export const MetricCard = memo(function MetricCard({
  title,
  value,
  delta,
  deltaPct,
  sparkline,
  className,
}: {
  title: string;
  value: string;
  delta?: number;
  deltaPct?: number;
  sparkline?: number[];
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-[10px] border border-border-subtle bg-bg-panel/70 p-3",
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-widest text-text-muted">
          {title}
        </span>
        {sparkline && <MiniSparkline data={sparkline} width={48} height={18} />}
      </div>
      <div className="mt-1 font-mono text-xl font-semibold tabular-nums text-text-primary">
        {value}
      </div>
      {(delta !== undefined || deltaPct !== undefined) && (
        <div className={cn("mt-0.5 font-mono text-xs tabular-nums", signColor(deltaPct ?? delta ?? 0))}>
          {(deltaPct ?? delta ?? 0) >= 0 ? "▲" : "▼"}{" "}
          {delta !== undefined && `${delta >= 0 ? "+" : ""}${delta.toFixed(2)}`}
          {deltaPct !== undefined && ` (${deltaPct >= 0 ? "+" : ""}${deltaPct.toFixed(2)}%)`}
        </div>
      )}
    </div>
  );
});
