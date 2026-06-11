"use client";
import { useMemo, useId } from "react";

interface Props {
  data: number[];
  width?: number;
  height?: number;
  id?: string;
}

export function MiniSparkline({ data, width = 80, height = 24, id }: Props) {
  const generatedId = useId();
  // Safe unique ID for SVG linearGradient
  const gradId = id ?? `sg-${generatedId.replace(/:/g, "")}`; // # [PD-3][TH][IM]

  const { points, fillPath, isUp } = useMemo(() => {
    if (!data || data.length < 2)
      return { points: "", fillPath: "", isUp: true };

    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const xStep = width / (data.length - 1);

    const coords = data.map((v, i) => ({
      x: i * xStep,
      y: height - ((v - min) / range) * (height - 2) - 1,
    }));

    const linePoints = coords
      .map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(" ");
    const fillPath = [
      `M${coords[0].x.toFixed(1)},${height}`,
      ...coords.map((p) => `L${p.x.toFixed(1)},${p.y.toFixed(1)}`),
      `L${coords[coords.length - 1].x.toFixed(1)},${height}`,
      "Z",
    ].join(" ");

    return {
      points: linePoints,
      fillPath,
      isUp: data[data.length - 1] >= data[0],
    };
  }, [data, width, height]);

  const color = isUp ? "#00E676" : "#FF3D5A";

  return (
    <svg
      width={width}
      height={height}
      style={{ overflow: "visible", display: "block" }}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.35} />
          <stop offset="100%" stopColor={color} stopOpacity={0.01} />
        </linearGradient>
      </defs>
      {fillPath && <path d={fillPath} fill={`url(#${gradId})`} />}
      {points && (
        <polyline
          points={points}
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}
