"use client";

import { useMemo } from "react";

interface MiniSparklineProps {
  data: number[];
  width?: number;
  height?: number;
}

export function MiniSparkline({
  data,
  width = 80,
  height = 24,
}: MiniSparklineProps) {
  const pathData = useMemo(() => {
    if (!data || data.length === 0) return "";

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;

    return data
      .map((val, i) => {
        const x = (i / (data.length - 1)) * width;
        const y = height - ((val - min) / range) * height;
        return `${i === 0 ? "M" : "L"} ${x} ${y}`;
      })
      .join(" ");
  }, [data, width, height]);

  if (!data || data.length === 0)
    return <div style={{ width, height }} className="bg-bg-panel rounded" />;

  const isPositive = data[data.length - 1] >= data[0];
  const strokeColor = isPositive
    ? "var(--color-signal-bull)"
    : "var(--color-signal-bear)";
  const fillId = isPositive ? "sparkFillBull" : "sparkFillBear";

  return (
    <svg width={width} height={height} className="overflow-visible">
      <defs>
        <linearGradient id="sparkFillBull" x1="0" x2="0" y1="0" y2="1">
          <stop
            offset="0%"
            stopColor="var(--color-signal-bull)"
            stopOpacity="0.2"
          />
          <stop
            offset="100%"
            stopColor="var(--color-bg-base)"
            stopOpacity="0"
          />
        </linearGradient>
        <linearGradient id="sparkFillBear" x1="0" x2="0" y1="0" y2="1">
          <stop
            offset="0%"
            stopColor="var(--color-signal-bear)"
            stopOpacity="0.2"
          />
          <stop
            offset="100%"
            stopColor="var(--color-bg-base)"
            stopOpacity="0"
          />
        </linearGradient>
      </defs>

      {/* Fill under the line */}
      <path
        d={`${pathData} L ${width} ${height} L 0 ${height} Z`}
        fill={`url(#${fillId})`}
      />

      {/* The sparkline itself */}
      <path
        d={pathData}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
