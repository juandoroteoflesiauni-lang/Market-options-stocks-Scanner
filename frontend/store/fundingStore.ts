import { create } from "zustand";
import { toWsUrl } from "@/lib/api-client";

export interface GlobalContextSnapshot {
  is_valid: boolean;
  market_regime: "MELTDOWN" | "BEAR" | "NEUTRAL" | "BULL";
  global_factor: string; // decimal string
  vix_level: number | null;
  spy_trend: string | null;
  qqq_trend: string | null;
  timestamp: string;
}

export interface BuilderMetricsSnapshot {
  account_id: string;
  profile_id: string;
  phase: string;
  eval_progress_pct: string;
  distance_to_trailing_dd: string;
  distance_to_dll_soft_pause: string;
  buffer_progress_pct: string;
  consistency_ratio_live: string;
  qualified_days_count: number;
  payout_eligibility_state: string;
  survival_score: string;
  recommended_risk_pct: string;
  survival_status: string;
  reason_codes: string[];
  withdrawable_amount: string;
  projected_eod_floor: string;
  floor_drift_usd: string;
  distance_to_projected_floor: string;
  is_floor_drift_warning: boolean;
  max_profit_today_usd: string;
  is_consistency_at_risk: boolean;
  buffer_remaining: string;
  qualified_days_required: number;
  qualified_days_remaining: number;
  avg_daily_profit: string;
  estimated_days_to_payout: number | null;
}

export interface BuilderEvaluateInput {
  symbol: string;
  direction?: "LONG" | "SHORT";
  entry: number;
  stop?: number;
  stop_ticks?: number;
  prefer_micro?: boolean;
}

export interface BuilderEvaluateResult {
  is_allowed: boolean;
  contracts: number;
  phase: string;
  allowed_risk_pct: string;
  risk_used_usd: string;
  capped_by: string;
  reason: string;
  reason_codes: string[];
  loss_if_stopped_usd: string;
  equity_after_loss: string;
  distance_to_trailing_dd_after: string;
  distance_to_dll_after: string;
  breaches_on_stop: boolean;
  triggers_soft_pause_on_stop: boolean;
}

export interface RiskMetricsSnapshot {
  sample_size: number;
  expectancy_r: string;
  expectancy_by_setup: Record<string, string>;
  profit_factor: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  bur: number;
  buffer_zone: string;
  ulcer: number;
  var95: string;
  cvar95: string;
  cvar99: string;
  kelly_applied: number;
  risk_of_ruin_pct: number;
}

interface FundingStore {
  globalContext: GlobalContextSnapshot | null;
  riskMetrics: RiskMetricsSnapshot | null;
  builderMetrics: BuilderMetricsSnapshot | null;
  isLoading: boolean;
  error: string | null;
  fetchGlobalContext: () => Promise<void>;
  fetchRiskMetrics: () => Promise<void>;
  fetchBuilderMetrics: () => Promise<void>;
  evaluateBuilderCandidate: (
    candidate: BuilderEvaluateInput,
  ) => Promise<BuilderEvaluateResult | null>;
  insertMockTrade: () => Promise<void>;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
}

let ws: WebSocket | null = null;
let reconnectTimer: NodeJS.Timeout | null = null;
let isExplicitlyClosed = false;
let retryCount = 0;
const MAX_RETRIES = 5;

const API_BASE = "http://127.0.0.1:8000/api/v1/funding";

export const useFundingStore = create<FundingStore>((set, get) => ({
  globalContext: null,
  riskMetrics: null,
  builderMetrics: null,
  isLoading: false,
  error: null,

  fetchGlobalContext: async () => {
    try {
      const res = await fetch(`${API_BASE}/global-context`);
      if (!res.ok) throw new Error("Failed to fetch global context");
      const data = await res.json();
      set({ globalContext: data, error: null });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to fetch global context";
      set({ error: message });
    }
  },

  fetchRiskMetrics: async () => {
    try {
      const res = await fetch(`${API_BASE}/risk-metrics?window=100`);
      if (!res.ok) throw new Error("Failed to fetch risk metrics");
      const data = await res.json();
      set({ riskMetrics: data, error: null });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to fetch risk metrics";
      set({ error: message });
    }
  },

  fetchBuilderMetrics: async () => {
    try {
      const res = await fetch(`${API_BASE}/builder/metrics`);
      if (!res.ok) throw new Error("Failed to fetch builder metrics");
      const data = (await res.json()) as BuilderMetricsSnapshot;
      set({ builderMetrics: data, error: null });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to fetch builder metrics";
      set({ error: message });
    }
  },

  evaluateBuilderCandidate: async (candidate: BuilderEvaluateInput) => {
    try {
      const res = await fetch(`${API_BASE}/builder/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(candidate),
      });
      if (!res.ok) throw new Error("Failed to evaluate builder candidate");
      return (await res.json()) as BuilderEvaluateResult;
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to evaluate builder candidate";
      set({ error: message });
      return null;
    }
  },

  insertMockTrade: async () => {
    try {
      set({ isLoading: true });
      const res = await fetch(`${API_BASE}/mock-trade`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to insert mock trade");
      const data = await res.json();
      set({ riskMetrics: data, isLoading: false, error: null });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to insert mock trade";
      set({ error: message, isLoading: false });
    }
  },

  startPolling: (intervalMs = 5000) => {
    isExplicitlyClosed = false;
    retryCount = 0;

    // Fetch initial REST data for instant display
    const { fetchGlobalContext, fetchRiskMetrics, fetchBuilderMetrics } = get();
    fetchGlobalContext();
    fetchRiskMetrics();
    fetchBuilderMetrics();

    const connect = () => {
      if (isExplicitlyClosed) return;
      if (ws) return; // already connected or connecting

      const wsUrl = toWsUrl("/api/v1/ws/funding");
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        set({ error: null });
        retryCount = 0;
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          set({
            globalContext: payload.globalContext,
            riskMetrics: payload.riskMetrics,
            builderMetrics: payload.builderMetrics,
            error: null,
          });
        } catch {
          set({ error: "Failed to parse telemetry update" });
        }
      };

      ws.onerror = () => {
        set({ error: "Funding telemetry stream connection error" });
      };

      ws.onclose = () => {
        ws = null;
        if (!isExplicitlyClosed) {
          if (retryCount < MAX_RETRIES) {
            const delay = Math.min(1000 * Math.pow(2, retryCount), 10000);
            retryCount++;
            reconnectTimer = setTimeout(connect, delay);
          } else {
            set({ error: "Telemetry connection lost. Please refresh." });
          }
        }
      };
    };

    connect();
  },

  stopPolling: () => {
    isExplicitlyClosed = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
  },
}));
