"use client";

import * as React from "react";
import { fetchJson } from "@/lib/api-client";

export interface ProviderStats {
  total_calls: number;
  success_calls: number;
  error_calls: number;
  error_rate: number;
  rate_limited: number;
  circuit_open: number;
  cache_hit_rate: number;
  cache_hits: number;
  cache_misses: number;
  total_cost_usd: number;
  projected_monthly_cost_usd: number;
  avg_duration_ms: number;
  latency_p50_ms: number;
  latency_p99_ms: number;
  top_endpoints: Array<[string, number]>;
  top_api_keys: Array<[string, number]>;
}

export interface ConsumptionDashboard {
  period_start: string;
  period_end: string;
  elapsed_hours: number;
  total_calls: number;
  total_cost_usd: number;
  projected_monthly_cost_usd: number;
  total_errors: number;
  total_rate_limited: number;
  total_cache_hits: number;
  total_cache_misses: number;
  overall_cache_hit_rate: number;
  providers: Record<string, ProviderStats>;
}

export interface RateLimiterBucket {
  rate: number;
  burst: number;
  tokens: number;
  last_request: number;
}

export interface RateLimiterStatus {
  buckets: Record<string, RateLimiterBucket>;
}

const POLL_INTERVAL_MS = 15_000;

export function useApiConsumption() {
  const [dashboard, setDashboard] = React.useState<ConsumptionDashboard | null>(
    null,
  );
  const [rateLimiter, setRateLimiter] =
    React.useState<RateLimiterStatus | null>(null);
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [dashData, rlData] = await Promise.all([
        fetchJson<ConsumptionDashboard>("/api/v1/consumption/dashboard"),
        fetchJson<RateLimiterStatus>("/api/v1/consumption/rate-limiter"),
      ]);
      setDashboard(dashData);
      setRateLimiter(rlData);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "API consumption monitor unavailable",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  const resetStats = React.useCallback(async () => {
    setIsLoading(true);
    try {
      await fetchJson("/api/v1/consumption/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      });
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Failed to reset API consumption stats",
      );
    } finally {
      setIsLoading(false);
    }
  }, [refresh]);

  React.useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  return { dashboard, rateLimiter, isLoading, error, refresh, resetStats };
}
