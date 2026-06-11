"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  ColorType,
  ISeriesApi,
  Time,
} from "lightweight-charts";
import type { OHLCV } from "@/types";

interface Props {
  data: OHLCV[];
  width?: number;
  height?: number;
}

export function LightweightChart({ data, width = 120, height = 36 }: Props) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Create chart
    const chart = createChart(chartContainerRef.current, {
      width,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "transparent",
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      timeScale: {
        visible: false,
        borderVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      rightPriceScale: {
        visible: false,
        borderVisible: false,
      },
      handleScroll: false,
      handleScale: false,
      crosshair: {
        mode: 0, // Normal
        vertLine: { visible: false },
        horzLine: { visible: false },
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#00E676",
      downColor: "#FF3D5A",
      borderVisible: false,
      wickUpColor: "#00E676",
      wickDownColor: "#FF3D5A",
    });

    seriesRef.current = series;

    // Transform initial data
    const chartData = data.map((d) => ({
      time: d.time as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    series.setData(chartData);

    return () => {
      chart.remove();
    };
  }, [width, height, data]);

  const lastFirstTimeRef = useRef<number | null>(null);

  // Update logic when data changes
  useEffect(() => {
    if (!seriesRef.current || data.length === 0) return;

    const currentFirstTime = data[0].time as number;

    // If the beginning of our dataset changed, it's a full replacement (e.g. from Mock -> FMP)
    if (lastFirstTimeRef.current !== currentFirstTime) {
      seriesRef.current.setData(
        data.map((d) => ({
          time: d.time as Time,
          open: d.open,
          high: d.high,
          low: d.low,
          close: d.close,
        })),
      );
      lastFirstTimeRef.current = currentFirstTime;
    } else {
      // Otherwise, it's just a tick update at the end
      const lastCandle = data[data.length - 1];
      seriesRef.current.update({
        time: lastCandle.time as Time,
        open: lastCandle.open,
        high: lastCandle.high,
        low: lastCandle.low,
        close: lastCandle.close,
      });
    }
  }, [data]);

  return (
    <div
      ref={chartContainerRef}
      style={{ width, height, overflow: "hidden" }}
    />
  );
}
