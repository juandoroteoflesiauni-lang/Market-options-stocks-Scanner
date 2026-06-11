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
  const [requestId, setRequestId] = React.useState(0);
  const [lastSymbol, setLastSymbol] = React.useState(symbol);
  const [lastInterval, setLastInterval] = React.useState(interval);

  if (lastSymbol !== symbol || lastInterval !== interval) {
    setLastSymbol(symbol);
    setLastInterval(interval);
    setRequestId((id) => id + 1);
    setAnalysis(null);
    setIsLoading(false);
    setError(null);
  }

  const fetch = React.useCallback(
    async (sym: string, ivl: string, currentId: number) => {
      setIsLoading(true);
      setError(null);
      try {
        const data = await fetchJson<BingXAnalysisResponse>(
          `/api/v1/bingx-bot/analysis/${encodeURIComponent(sym)}?interval=${encodeURIComponent(ivl)}`,
        );
        if (currentId === requestId) setAnalysis(data);
      } catch (cause) {
        if (currentId !== requestId) return;
        setError(
          cause instanceof Error ? cause.message : "Analysis unavailable",
        );
      } finally {
        if (currentId === requestId) setIsLoading(false);
      }
    },
    [requestId],
  );

  React.useEffect(() => {
    if (!symbol) return;
    const initialId = setTimeout(
      () => void fetch(symbol, interval, requestId),
      0,
    );
    const id = setInterval(
      () => void fetch(symbol, interval, requestId),
      POLL_INTERVAL_MS,
    );
    return () => {
      clearTimeout(initialId);
      clearInterval(id);
    };
  }, [symbol, interval, fetch, requestId]);

  return { analysis, isLoading, error };
}
