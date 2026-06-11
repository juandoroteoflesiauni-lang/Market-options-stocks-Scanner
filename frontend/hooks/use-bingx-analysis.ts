"use client";

import * as React from "react";

import { fetchJson } from "@/lib/api-client";
import type { BingXAnalysisResponse } from "@/lib/bingx-bot-types";

const POLL_INTERVAL_MS = 60_000;

export function useBingxAnalysis(
  symbol: string | null,
  interval: string = "5m",
) {
  const [analysis, setAnalysis] = React.useState<BingXAnalysisResponse | null>(
    null,
  );
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const requestIdRef = React.useRef(0);

  const fetch = React.useCallback(
    async (sym: string, ivl: string, requestId: number) => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await fetchJson<BingXAnalysisResponse>(
          `/api/v1/bingx-bot/analysis/${encodeURIComponent(sym)}?interval=${encodeURIComponent(ivl)}`,
        );
        if (requestIdRef.current === requestId) setAnalysis(data);
      } catch (cause) {
        if (requestIdRef.current !== requestId) return;
        setError(
          cause instanceof Error ? cause.message : "Analysis unavailable",
        );
      } finally {
        if (requestIdRef.current === requestId) setIsLoading(false);
      }
    },
    [],
  );

  React.useEffect(() => {
    if (!symbol) {
      requestIdRef.current += 1;
      setAnalysis(null);
      setIsLoading(false);
      return;
    }
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setAnalysis(null);
    void fetch(symbol, interval, requestId);
    const id = setInterval(
      () => void fetch(symbol, interval, requestId),
      POLL_INTERVAL_MS,
    );
    return () => {
      requestIdRef.current += 1;
      clearInterval(id);
    };
  }, [symbol, interval, fetch]);

  return { analysis, isLoading, error };
}
