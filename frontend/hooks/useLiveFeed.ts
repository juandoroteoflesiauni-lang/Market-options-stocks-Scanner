"use client";

import { useEffect, useRef, useState } from "react";

import { basePrice } from "@/lib/terminal/mock";

export interface LiveQuote {
  price: number;
  prev: number;
  dir: "up" | "down" | "flat";
}

/**
 * Simulates a live websocket price feed for a set of symbols.
 * Updates on a fixed interval with small GBM-style ticks.
 */
export function useLiveFeed(symbols: string[], intervalMs = 1200) {
  const [quotes, setQuotes] = useState<Record<string, LiveQuote>>(() => {
    const init: Record<string, LiveQuote> = {};
    for (const s of symbols) {
      const p = basePrice(s);
      init[s] = { price: p, prev: p, dir: "flat" };
    }
    return init;
  });
  const symbolsRef = useRef(symbols.join(","));

  useEffect(() => {
    const list = symbolsRef.current.split(",").filter(Boolean);
    const id = setInterval(() => {
      setQuotes((prevQuotes) => {
        const next: Record<string, LiveQuote> = { ...prevQuotes };
        for (const s of list) {
          const current = prevQuotes[s] ?? {
            price: basePrice(s),
            prev: basePrice(s),
            dir: "flat" as const,
          };
          const vol = s.includes("USDT") ? 0.0015 : 0.0008;
          const shock = (Math.random() - 0.5) * 2 * vol;
          const price = Number((current.price * (1 + shock)).toFixed(2));
          next[s] = {
            price,
            prev: current.price,
            dir: price > current.price ? "up" : price < current.price ? "down" : "flat",
          };
        }
        return next;
      });
    }, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);

  return quotes;
}
