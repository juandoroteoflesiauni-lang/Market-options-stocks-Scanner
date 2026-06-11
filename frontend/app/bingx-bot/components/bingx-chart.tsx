"use client";

import * as React from "react";
import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";

import type { BingXKlinePoint } from "@/lib/bingx-bot-types";

interface BingxChartProps {
  klines: BingXKlinePoint[];
  ema9: number | null;
  ema21: number | null;
  vwap: number | null;
  vwapUpper: number | null;
  vwapLower: number | null;
  height?: number;
}

const chartPalette = {
  bg: "#070808",
  text: "rgba(194,204,196,0.72)",
  grid: "rgba(215,168,79,0.07)",
  bull: "#32d074",
  bear: "#f05a4f",
  info: "#35d4ff",
  brass: "#d7a84f",
};

export function BingxChart({
  klines,
  ema9: _ema9,
  ema21: _ema21,
  vwap: _vwap,
  vwapUpper: _vwapUpper,
  vwapLower: _vwapLower,
  height = 160,
}: BingxChartProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartRef = React.useRef<IChartApi | null>(null);
  const candleRef = React.useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = React.useRef<ISeriesApi<"Histogram"> | null>(null);
  const heightRef = React.useRef(height);

  React.useEffect(() => {
    const container = containerRef.current;
    if (!container || chartRef.current) return;
    let mounted = true;
    let resizeObs: ResizeObserver | null = null;

    void import("lightweight-charts").then((mod) => {
      if (!mounted || !containerRef.current) return;

      const chart = mod.createChart(containerRef.current, {
        width: containerRef.current.clientWidth || 400,
        height: heightRef.current,
        layout: {
          background: { color: chartPalette.bg },
          textColor: chartPalette.text,
          fontFamily: "var(--font-geist-mono), monospace",
          fontSize: 10,
        },
        grid: {
          vertLines: { color: chartPalette.grid },
          horzLines: { color: chartPalette.grid },
        },
        rightPriceScale: {
          borderVisible: false,
          scaleMargins: { top: 0.08, bottom: 0.25 },
        },
        timeScale: {
          borderVisible: false,
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 2,
        },
        crosshair: { mode: mod.CrosshairMode.Normal },
        handleScroll: true,
        handleScale: true,
      });

      const candle = chart.addSeries(mod.CandlestickSeries, {
        upColor: chartPalette.bull,
        downColor: chartPalette.bear,
        wickUpColor: chartPalette.bull,
        wickDownColor: chartPalette.bear,
        borderVisible: false,
      });

      const vol = chart.addSeries(mod.HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "vol",
        color: "rgba(50,208,116,0.28)",
      });
      chart.priceScale("vol").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });

      chartRef.current = chart;
      candleRef.current = candle;
      volRef.current = vol;

      resizeObs = new ResizeObserver((entries) => {
        const width = Math.max(
          200,
          Math.floor(entries[0]?.contentRect.width ?? 0),
        );
        chart.applyOptions({ width, height: heightRef.current });
        chart.timeScale().fitContent();
      });
      resizeObs.observe(containerRef.current);
    });

    return () => {
      mounted = false;
      resizeObs?.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
      candleRef.current = null;
      volRef.current = null;
    };
  }, []);

  React.useEffect(() => {
    heightRef.current = height;
    chartRef.current?.applyOptions({ height });
  }, [height]);

  React.useEffect(() => {
    if (!candleRef.current || !volRef.current || !klines.length) return;

    const sorted = [...klines].sort((a, b) => a.time - b.time);
    const uniq: typeof klines = [];
    for (const item of sorted) {
      const prev = uniq[uniq.length - 1];
      if (prev && prev.time === item.time) {
        uniq[uniq.length - 1] = item;
      } else {
        uniq.push(item);
      }
    }

    candleRef.current.setData(
      uniq.map((bar) => ({
        time: bar.time as Time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );
    volRef.current.setData(
      uniq.map((bar) => ({
        time: bar.time as Time,
        value: bar.volume,
        color:
          bar.close >= bar.open
            ? "rgba(50,208,116,0.22)"
            : "rgba(240,90,79,0.22)",
      })),
    );
    chartRef.current?.timeScale().fitContent();
  }, [klines]);

  return (
    <div
      className="relative overflow-hidden border border-line bg-base"
      style={{ height }}
    >
      <div ref={containerRef} className="h-full w-full" />
      {!klines.length && (
        <div className="absolute inset-0 flex items-center justify-center bg-base font-mono text-[10px] uppercase tracking-[0.12em] text-ink-600">
          Esperando velas
        </div>
      )}
      <div className="absolute left-2 top-2 flex flex-wrap gap-1.5">
        <Legend colorClassName="text-info" label="EMA 9" />
        <Legend colorClassName="text-bull" label="EMA 21" />
        <Legend colorClassName="text-brass" label="VWAP" />
      </div>
    </div>
  );
}

function Legend({
  colorClassName,
  label,
}: {
  colorClassName: string;
  label: string;
}) {
  return (
    <span
      className={`border border-line bg-elevated px-1.5 py-0.5 font-mono text-[9px] uppercase ${colorClassName}`}
    >
      {label}
    </span>
  );
}
