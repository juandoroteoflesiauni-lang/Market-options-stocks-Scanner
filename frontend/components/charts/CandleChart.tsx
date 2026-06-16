"use client";
import { useRef, useState, useEffect, useCallback, useMemo } from "react";
import type { OHLCV } from "@/types";
import { generateGBM } from "@/services/mock/gbm";
import { formatPrice } from "@/utils/format";

type Timeframe = "1m" | "5m" | "15m" | "1H" | "4H" | "1D";

interface HLine {
  price: number;
  label: string;
  color: string;
  dashed?: boolean;
}

interface Props {
  ticker?: string;
  initialPrice?: number;
  entryPrice?: number;
  takeProfit?: number;
  stopLoss?: number;
  height?: number;
}

const TF_BARS: Record<Timeframe, number> = {
  "1m": 80,
  "5m": 80,
  "15m": 60,
  "1H": 60,
  "4H": 50,
  "1D": 40,
};

const MARGIN = { top: 12, right: 64, bottom: 24, left: 12 };
const VOL_RATIO = 0.22;

interface TooltipData {
  x: number;
  y: number;
  candle: OHLCV;
  dateStr: string;
}

export function CandleChart({
  ticker = "AAPL",
  initialPrice = 194,
  entryPrice,
  takeProfit,
  stopLoss,
  height = 340,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [tf, setTf] = useState<Timeframe>("1H");
  const [data, setData] = useState<OHLCV[]>([]);
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const [crossX, setCrossX] = useState<number | null>(null);

  // Responsive width
  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver((e) => setWidth(e[0].contentRect.width));
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  // Regenerate data on ticker/tf/initialPrice change
  useEffect(() => {
    const sigma =
      ticker.includes("TSLA") || ticker.includes("COIN") ? 0.025 : 0.015;
    const id = setTimeout(
      () => setData(generateGBM(initialPrice, TF_BARS[tf], 0.0001, sigma)),
      0,
    );
    return () => clearTimeout(id);
  }, [ticker, tf, initialPrice, data]);

  const chartH = height - MARGIN.top - MARGIN.bottom;
  const volH = chartH * VOL_RATIO;
  const priceH = chartH - volH - 6;
  const plotW = width - MARGIN.left - MARGIN.right;

  const { priceMin, priceMax, volMax } = useMemo(() => {
    if (!data.length) return { priceMin: 0, priceMax: 1, volMax: 1 };
    const lows = data.map((d) => d.low);
    const highs = data.map((d) => d.high);
    // include h-lines in domain
    const extras = [entryPrice, takeProfit, stopLoss].filter(
      Boolean,
    ) as number[];
    let pMin = Math.min(...lows, ...extras) * 0.999;
    let pMax = Math.max(...highs, ...extras) * 1.001;
    if (pMax <= pMin) {
      pMin -= 1;
      pMax += 1;
    }
    const vMax = Math.max(...data.map((d) => d.volume));
    return {
      priceMin: pMin,
      priceMax: pMax,
      volMax: vMax > 0 ? vMax : 1,
    };
  }, [data, entryPrice, takeProfit, stopLoss]);

  const py = useCallback(
    (v: number) =>
      MARGIN.top + priceH - ((v - priceMin) / (priceMax - priceMin)) * priceH,
    [priceMin, priceMax, priceH],
  );

  const vy = useCallback(
    (v: number) => MARGIN.top + chartH - (v / volMax) * volH,
    [chartH, volH, volMax],
  );

  const barW = Math.max(2, plotW / (data.length || 1) - 1);
  const cx = useCallback(
    (i: number) =>
      MARGIN.left + (i + 0.5) * (plotW / (data.length || 1)),
    [plotW, data.length],
  );

  // Price axis labels
  const priceLabels = useMemo(() => {
    if (!data.length) return [];
    const count = 5;
    return Array.from({ length: count + 1 }, (_, i) => {
      const v = priceMin + (priceMax - priceMin) * (i / count);
      return { v, y: py(v) };
    });
  }, [priceMin, priceMax, py, data]);

  // Time labels (every N candles)
  const timeLabels = useMemo(() => {
    if (!data.length) return [];
    const step = Math.ceil(data.length / 6);
    return data
      .filter((_, i) => i % step === 0)
      .map((d, i) => {
        const idx = i * step;
        const date = new Date(d.time);
        const label =
          tf === "1D"
            ? date.toLocaleDateString("en-US", {
                month: "short",
                day: "numeric",
              })
            : date.toLocaleTimeString("en-US", {
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
              });
        return { label, x: cx(idx) };
      });
  }, [data, tf, cx]);

  const hLines: HLine[] = [
    entryPrice && {
      price: entryPrice,
      label: `ENTRY $${formatPrice(entryPrice)}`,
      color: "#00C3FF",
      dashed: false,
    },
    takeProfit && {
      price: takeProfit,
      label: `TP $${formatPrice(takeProfit)}`,
      color: "#00E676",
      dashed: true,
    },
    stopLoss && {
      price: stopLoss,
      label: `SL $${formatPrice(stopLoss)}`,
      color: "#FF3D5A",
      dashed: true,
    },
  ].filter(Boolean) as HLine[];

  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const mx = e.clientX - rect.left - MARGIN.left;
    const idx = Math.round(mx / (plotW / data.length) - 0.5);
    if (idx < 0 || idx >= data.length) {
      setTooltip(null);
      setCrossX(null);
      return;
    }
    const d = data[idx];
    const date = new Date(d.time);
    const dateStr = `${date.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${date.toLocaleTimeString(
      "en-US",
      { hour: "2-digit", minute: "2-digit", hour12: false },
    )}`;
    setTooltip({ x: cx(idx), y: py(d.close), candle: d, dateStr });
    setCrossX(cx(idx));
  }

  const TF_LIST: Timeframe[] = ["1m", "5m", "15m", "1H", "4H", "1D"];

  return (
    <div ref={containerRef} style={{ width: "100%", userSelect: "none" }}>
      {/* Timeframe selector */}
      <div
        style={{
          display: "flex",
          gap: 4,
          marginBottom: 8,
          alignItems: "center",
        }}
      >
        {TF_LIST.map((t) => (
          <button
            key={t}
            onClick={() => setTf(t)}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              padding: "3px 8px",
              background: tf === t ? "rgba(0,195,255,0.15)" : "transparent",
              border: `1px solid ${tf === t ? "rgba(0,195,255,0.4)" : "rgba(255,255,255,0.08)"}`,
              borderRadius: 4,
              color: tf === t ? "#00C3FF" : "#4A5568",
              cursor: "pointer",
              transition: "all 0.15s",
              letterSpacing: "0.06em",
            }}
          >
            {t}
          </button>
        ))}
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "#4A5568",
            marginLeft: 8,
          }}
        >
          {ticker}
        </span>
      </div>

      <svg
        width={width}
        height={height}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => {
          setTooltip(null);
          setCrossX(null);
        }}
        style={{ display: "block", cursor: "crosshair" }}
      >
        {/* Background */}
        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={plotW}
          height={chartH}
          fill="rgba(8,12,20,0.5)"
          rx={4}
        />

        {/* Grid lines */}
        {priceLabels.map((l, i) => (
          <line
            key={i}
            x1={MARGIN.left}
            x2={MARGIN.left + plotW}
            y1={l.y}
            y2={l.y}
            stroke="rgba(255,255,255,0.05)"
            strokeWidth={1}
          />
        ))}

        {/* Price labels */}
        {priceLabels.map((l, i) => (
          <text
            key={i}
            x={MARGIN.left + plotW + 6}
            y={l.y + 4}
            fill="#4A5568"
            fontSize={9}
            fontFamily="var(--font-mono)"
          >
            {formatPrice(l.v)}
          </text>
        ))}

        {/* Time labels */}
        {timeLabels.map((l, i) => (
          <text
            key={i}
            x={l.x}
            y={MARGIN.top + chartH + 14}
            fill="#4A5568"
            fontSize={9}
            fontFamily="var(--font-mono)"
            textAnchor="middle"
          >
            {l.label}
          </text>
        ))}

        {/* H-Lines (entry / TP / SL) */}
        {hLines.map((hl, i) => {
          const y = py(hl.price);
          return (
            <g key={i}>
              <line
                x1={MARGIN.left}
                x2={MARGIN.left + plotW}
                y1={y}
                y2={y}
                stroke={hl.color}
                strokeWidth={1}
                strokeDasharray={hl.dashed ? "4 3" : undefined}
                opacity={0.7}
              />
              <rect
                x={MARGIN.left + plotW + 2}
                y={y - 9}
                width={60}
                height={14}
                fill={`${hl.color}22`}
                rx={2}
              />
              <text
                x={MARGIN.left + plotW + 5}
                y={y + 2}
                fill={hl.color}
                fontSize={8}
                fontFamily="var(--font-mono)"
              >
                {hl.label}
              </text>
            </g>
          );
        })}

        {/* Candles */}
        {data.map((d, i) => {
          const bull = d.close >= d.open;
          const color = bull ? "#00E676" : "#FF3D5A";
          const bTop = py(Math.max(d.open, d.close));
          const bBot = py(Math.min(d.open, d.close));
          const wTop = py(d.high);
          const wBot = py(d.low);
          const x = cx(i);
          const bh = Math.max(1, bBot - bTop);

          return (
            <g key={i}>
              {/* Wick */}
              <line
                x1={x}
                x2={x}
                y1={wTop}
                y2={bTop}
                stroke={color}
                strokeWidth={1}
                opacity={0.8}
              />
              <line
                x1={x}
                x2={x}
                y1={bBot}
                y2={wBot}
                stroke={color}
                strokeWidth={1}
                opacity={0.8}
              />
              {/* Body */}
              <rect
                x={x - barW / 2}
                y={bTop}
                width={barW}
                height={bh}
                fill={color}
                fillOpacity={bull ? 0.85 : 0.9}
                rx={barW > 4 ? 1 : 0}
              />
            </g>
          );
        })}

        {/* Volume histogram */}
        {data.map((d, i) => {
          const bull = d.close >= d.open;
          const color = bull ? "rgba(0,230,118,0.35)" : "rgba(255,61,90,0.35)";
          const barH = (d.volume / volMax) * volH;
          const y = MARGIN.top + chartH - barH;
          return (
            <rect
              key={i}
              x={cx(i) - barW / 2}
              y={y}
              width={barW}
              height={barH}
              fill={color}
              rx={barW > 4 ? 1 : 0}
            />
          );
        })}

        {/* Crosshair */}
        {crossX !== null && (
          <>
            <line
              x1={crossX}
              x2={crossX}
              y1={MARGIN.top}
              y2={MARGIN.top + chartH}
              stroke="rgba(0,195,255,0.3)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            {tooltip && (
              <line
                x1={MARGIN.left}
                x2={MARGIN.left + plotW}
                y1={tooltip.y}
                y2={tooltip.y}
                stroke="rgba(0,195,255,0.2)"
                strokeWidth={1}
                strokeDasharray="3 3"
              />
            )}
          </>
        )}

        {/* Tooltip */}
        {tooltip &&
          (() => {
            const d = tooltip.candle;
            const bull = d.close >= d.open;
            const tx = Math.min(tooltip.x + 10, width - 130);
            const ty = Math.max(MARGIN.top + 4, tooltip.y - 70);
            return (
              <g>
                <rect
                  x={tx}
                  y={ty}
                  width={120}
                  height={80}
                  rx={4}
                  fill="rgba(17,24,39,0.95)"
                  stroke="rgba(0,195,255,0.3)"
                  strokeWidth={1}
                />
                <text
                  x={tx + 8}
                  y={ty + 14}
                  fill="#8B9AAF"
                  fontSize={9}
                  fontFamily="var(--font-mono)"
                >
                  {tooltip.dateStr}
                </text>
                {[
                  { label: "O", value: d.open, color: "#E8EDF5" },
                  { label: "H", value: d.high, color: "#00E676" },
                  { label: "L", value: d.low, color: "#FF3D5A" },
                  {
                    label: "C",
                    value: d.close,
                    color: bull ? "#00E676" : "#FF3D5A",
                  },
                ].map((row, i) => (
                  <g key={row.label}>
                    <text
                      x={tx + 8}
                      y={ty + 28 + i * 13}
                      fill="#4A5568"
                      fontSize={9}
                      fontFamily="var(--font-mono)"
                    >
                      {row.label}
                    </text>
                    <text
                      x={tx + 24}
                      y={ty + 28 + i * 13}
                      fill={row.color}
                      fontSize={9}
                      fontFamily="var(--font-mono)"
                    >
                      {formatPrice(row.value)}
                    </text>
                  </g>
                ))}
              </g>
            );
          })()}
      </svg>
    </div>
  );
}
