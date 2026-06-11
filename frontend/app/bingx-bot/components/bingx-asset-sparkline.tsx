"use client";

interface BingxAssetSparklineProps {
  closes: number[];
  height?: number;
  spiking?: boolean;
}

export function BingxAssetSparkline({
  closes,
  height = 38,
  spiking = false,
}: BingxAssetSparklineProps) {
  if (closes.length < 2) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center font-mono text-[10px] uppercase tracking-[0.1em] text-ink-600"
      >
        sin datos
      </div>
    );
  }

  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const range = max - min || 1;
  const width = 280;
  const step = width / (closes.length - 1);

  const points = closes
    .map((close, index) => {
      const x = index * step;
      const y = height - ((close - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");

  const fillPoints = `0,${height} ${points} ${width},${height}`;
  const gradientId = spiking ? "spark-bull" : "spark-neutral";
  const strokeColor = spiking ? "var(--bull)" : "var(--line-strong)";

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      style={{ height, width: "100%" }}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity="0.28" />
          <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={fillPoints} fill={`url(#${gradientId})`} />
      <polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.5"
      />
    </svg>
  );
}
