"use client";
import { MiniSparkline } from "./MiniSparkline";
import clsx from "clsx";

interface MetricCardProps {
  title: string;
  value: string;
  delta?: { value: string; isPositive: boolean };
  sparklineData?: number[];
  className?: string;
}

export function MetricCard({
  title,
  value,
  delta,
  sparklineData,
  className,
}: MetricCardProps) {
  return (
    <div
      className={clsx(
        "glass-card p-3 flex flex-col justify-between min-w-[140px]",
        className,
      )}
    >
      <h4 className="font-mono text-[10px] text-text-secondary uppercase tracking-widest mb-2">
        {title}
      </h4>
      <div className="flex items-end justify-between gap-4">
        <div>
          <div className="font-mono text-xl text-text-primary font-medium">
            {value}
          </div>
          {delta && (
            <div
              className={clsx(
                "font-mono text-[10px] mt-1 flex items-center gap-1",
                delta.isPositive ? "text-signal-bull" : "text-signal-bear",
              )}
            >
              {delta.isPositive ? "▲" : "▼"} {delta.value}
            </div>
          )}
        </div>
        {sparklineData && (
          <div className="shrink-0">
            <MiniSparkline data={sparklineData} width={48} height={20} />
          </div>
        )}
      </div>
    </div>
  );
}
