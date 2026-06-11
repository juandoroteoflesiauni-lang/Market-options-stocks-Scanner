"use client";

import * as React from "react";
import { fetchJson } from "@/lib/api-client";

// ── Types ────────────────────────────────────────────────────────────────────

export interface AuditHealth {
  db_path: string;
  persistent: boolean;
  tables: Record<string, number>;
}

export interface ModuleApiStats {
  module: string;
  total_calls: number;
  success_calls: number;
  error_calls: number;
  rate_limited: number;
  total_cost_usd: number;
  cache_hits: number;
  avg_duration_ms: number;
  error_rate_pct: number;
  cache_hit_rate_pct: number;
  first_call: string;
  last_call: string;
}

export interface ModuleErrorStats {
  module: string;
  total: number;
  critical: number;
  errors: number;
  warnings: number;
  resolved: number;
  unresolved: number;
  resolution_rate_pct: number;
  first_error: string;
  last_error: string;
}

export interface ModuleSummary {
  api_calls: number;
  api_cost_usd: number;
  api_error_rate_pct: number;
  errors_total: number;
  errors_critical: number;
  errors_unresolved: number;
}

export interface AuditDashboard {
  health: AuditHealth;
  module_summary: Record<string, ModuleSummary>;
  api_call_stats: Record<string, ModuleApiStats>;
  error_stats: Record<string, ModuleErrorStats>;
  log_stats: {
    by_level: Record<string, number>;
    by_module: Record<string, { total: number; errors: number }>;
    total_logs: number;
  };
}

export interface ApiCall {
  call_id: string;
  timestamp: string;
  module: string;
  provider: string;
  endpoint: string;
  api_key_label: string;
  status: string;
  duration_ms: number;
  estimated_cost: number;
  cache_hit: boolean;
  bytes_received: number;
  retry_count: number;
  error_message: string;
  error_stack: string;
  request_context: Record<string, unknown>;
  correlation_id: string;
}

export interface ApiConsumptionByModule {
  modules: Record<string, ModuleApiStats>;
  provider_breakdown: Record<
    string,
    Record<
      string,
      {
        provider: string;
        calls: number;
        cost_usd: number;
        errors: number;
        avg_duration_ms: number;
        cache_hit_rate_pct: number;
      }
    >
  >;
}

export interface ProcessSnapshot {
  snapshot_id: string;
  timestamp: string;
  module: string;
  symbol: string;
  operation_id: string;
  indicators: Record<string, unknown>;
  orderbook: Record<string, unknown>;
  market_data: Record<string, unknown>;
  signals: Record<string, unknown>;
  decisions: Record<string, unknown>;
  risk_metrics: Record<string, unknown>;
  engine_state: Record<string, unknown>;
  context: Record<string, unknown>;
}

export interface AuditError {
  error_id: string;
  timestamp: string;
  module: string;
  severity: string;
  error_type: string;
  message: string;
  stack_trace: string;
  context: Record<string, unknown>;
  correlation_id: string;
  resolved: boolean;
  resolved_at: string;
  resolved_by: string;
  notes: string;
}

export interface AuditLogEntry {
  log_id: string;
  timestamp: string;
  level: string;
  module: string;
  logger_name: string;
  message: string;
  correlation_id: string;
  context_data: Record<string, unknown>;
  stack_trace: string;
  tags: string[];
}

export interface ErrorStats {
  by_module: Record<string, ModuleErrorStats>;
  total_errors: number;
  total_resolved: number;
  total_unresolved: number;
}

export interface LogStats {
  by_level: Record<string, number>;
  by_module: Record<string, { total: number; errors: number }>;
  total_logs: number;
}

export interface CostProjection {
  modules: Record<
    string,
    {
      current_cost_usd: number;
      total_calls: number;
      hours_tracked: number;
      projected_monthly_usd: number;
    }
  >;
  total_projected_monthly_usd: number;
}

export interface RateLimitData {
  total_rate_limited: number;
  by_module: Record<string, number>;
  by_provider: Record<string, number>;
  recent: ApiCall[];
}

export interface ModuleDetail {
  module: string;
  api_calls: { stats: ModuleApiStats; recent: ApiCall[] };
  errors: { stats: ModuleErrorStats; recent: AuditError[] };
  recent_snapshots: ProcessSnapshot[];
}

// ── Hook ─────────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 15_000;

export function useAuditComplex() {
  const [dashboard, setDashboard] = React.useState<AuditDashboard | null>(null);
  const [apiConsumption, setApiConsumption] =
    React.useState<ApiConsumptionByModule | null>(null);
  const [costProjections, setCostProjections] =
    React.useState<CostProjection | null>(null);
  const [errorStats, setErrorStats] = React.useState<ErrorStats | null>(null);
  const [logStats, setLogStats] = React.useState<LogStats | null>(null);
  const [rateLimits, setRateLimits] = React.useState<RateLimitData | null>(
    null,
  );
  const [isLoading, setIsLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const refresh = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [dash, consumption, projections, eStats, lStats, rl] =
        await Promise.all([
          fetchJson<AuditDashboard>("/api/v1/audit/dashboard"),
          fetchJson<ApiConsumptionByModule>("/api/v1/audit/api-consumption"),
          fetchJson<CostProjection>(
            "/api/v1/audit/api-consumption/projections/cost",
          ),
          fetchJson<ErrorStats>("/api/v1/audit/errors/stats"),
          fetchJson<LogStats>("/api/v1/audit/logs/stats"),
          fetchJson<RateLimitData>("/api/v1/audit/rate-limits"),
        ]);
      setDashboard(dash);
      setApiConsumption(consumption);
      setCostProjections(projections);
      setErrorStats(eStats);
      setLogStats(lStats);
      setRateLimits(rl);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Audit Complex unavailable",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchModuleDetail = React.useCallback(async (module: string) => {
    return fetchJson<ModuleDetail>(`/api/v1/audit/modules/${module}`);
  }, []);

  const fetchSnapshots = React.useCallback(
    async (params?: { module?: string; symbol?: string; limit?: number }) => {
      const qs = new URLSearchParams();
      if (params?.module) qs.set("module", params.module);
      if (params?.symbol) qs.set("symbol", params.symbol);
      if (params?.limit) qs.set("limit", String(params.limit));
      return fetchJson<{ snapshots: ProcessSnapshot[]; total: number }>(
        `/api/v1/audit/process-snapshots?${qs}`,
      );
    },
    [],
  );

  const fetchErrors = React.useCallback(
    async (params?: {
      module?: string;
      severity?: string;
      resolved?: boolean;
      limit?: number;
    }) => {
      const qs = new URLSearchParams();
      if (params?.module) qs.set("module", params.module);
      if (params?.severity) qs.set("severity", params.severity);
      if (params?.resolved !== undefined)
        qs.set("resolved", String(params.resolved));
      if (params?.limit) qs.set("limit", String(params.limit));
      return fetchJson<{ errors: AuditError[]; total: number }>(
        `/api/v1/audit/errors?${qs}`,
      );
    },
    [],
  );

  const fetchLogs = React.useCallback(
    async (params?: {
      query?: string;
      module?: string;
      level?: string;
      correlation_id?: string;
      tag?: string;
      limit?: number;
    }) => {
      const qs = new URLSearchParams();
      if (params?.query) qs.set("query", params.query);
      if (params?.module) qs.set("module", params.module);
      if (params?.level) qs.set("level", params.level);
      if (params?.correlation_id)
        qs.set("correlation_id", params.correlation_id);
      if (params?.tag) qs.set("tag", params.tag);
      if (params?.limit) qs.set("limit", String(params.limit));
      return fetchJson<{ logs: AuditLogEntry[]; total_matching: number }>(
        `/api/v1/audit/logs?${qs}`,
      );
    },
    [],
  );

  const fetchLogTrace = React.useCallback(async (correlationId: string) => {
    return fetchJson<AuditLogEntry[]>(
      `/api/v1/audit/logs/trace/${correlationId}`,
    );
  }, []);

  const resolveError = React.useCallback(
    async (errorId: string, resolvedBy: string, notes: string) => {
      await fetchJson(`/api/v1/audit/errors/${errorId}/resolve`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolved_by: resolvedBy, notes }),
      });
    },
    [],
  );

  React.useEffect(() => {
    const id = setTimeout(() => void refresh(), 0);
    const intervalId = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      clearTimeout(id);
      clearInterval(intervalId);
    };
  }, [refresh]);

  return {
    dashboard,
    apiConsumption,
    costProjections,
    errorStats,
    logStats,
    rateLimits,
    isLoading,
    error,
    refresh,
    fetchModuleDetail,
    fetchSnapshots,
    fetchErrors,
    fetchLogs,
    fetchLogTrace,
    resolveError,
  };
}
