/**
 * Domain types for the Deep Funnel Station frontend.
 *
 * CRITICAL: All monetary values (price, volume, strike, etc.) are
 * represented as `string` — never `number`. The backend serializes
 * Decimal fields as strings to prevent floating-point drift.
 * The UI displays them as-is; no parseFloat() is permitted.
 */

// ── Shared Types ──────────────────────────────────────────────

export interface Greeks {
  readonly delta: string;
  readonly gamma: string;
  readonly theta: string;
  readonly vega: string;
  readonly rho?: string;
}

export type WyckoffPhase = "A" | "B" | "C" | "D";
export type SignalDirection = "BULL" | "BEAR" | "NEUTRAL";
export type ConvictionLevel = "LOW" | "MED" | "HIGH";

export interface EngineSignal {
  readonly engineName: string;
  readonly value: string;
  readonly direction: SignalDirection;
  readonly weight: number;
}

// ── Tab 1: Scanner Types ──────────────────────────────────────

export interface Ticker {
  readonly symbol: string;
  readonly price: string;
  readonly priceChange: string;
  readonly priceChangePct: string;
  readonly volume: string;
  readonly avgVolume: string;
  readonly iv: string;
  readonly ivRank: string;
  readonly phase: WyckoffPhase;
  readonly momentum: string;
  readonly signals: EngineSignal[];
  readonly greeks: Greeks;
  readonly sparkline: number[];
}

export interface Universe {
  readonly id: string;
  readonly name: string;
  readonly tickers: string[];
}

// ── Tab 2-4: Bot / Positions Types ────────────────────────────

export type PositionDirection = "LONG" | "SHORT";

export interface Position {
  readonly id: string;
  readonly ticker: string;
  readonly direction: PositionDirection;
  readonly size: string;
  readonly entryPrice: string;
  readonly currentPrice: string;
  readonly takeProfit: string;
  readonly stopLoss: string;
  readonly unrealizedPnL: string;
  readonly unrealizedPnLPct: string;
  readonly realizedPnL: string;
  readonly openTime: string; // ISO 8601
  readonly strategy: string;

  // Specific to options or synthetic
  readonly greeks?: Greeks;
  readonly iv?: string;

  // Specific to crypto (Binance)
  readonly liquidationDistancePct?: string;
  readonly leverage?: string;
}

// ── Tab 6: Options Types ──────────────────────────────────────

export type OptionType = "CALL" | "PUT";

export interface OptionContract {
  readonly ticker: string;
  readonly strike: string;
  readonly expiration: string;
  readonly optionType: OptionType;
  readonly bid: string;
  readonly ask: string;
  readonly volume: string;
  readonly openInterest: string;
  readonly impliedVolatility: string;
  readonly greeks: Greeks;
}

// ── System Health (Legacy/Required) ───────────────────────────

export type ProviderStatus = "HEALTHY" | "DEGRADED" | "DOWN";

export interface ProviderHealth {
  readonly name: string;
  readonly status: ProviderStatus;
  readonly circuit_state: "CLOSED" | "OPEN" | "HALF_OPEN";
  readonly latency_ms: number;
}

export interface QueueMetrics {
  readonly standard_size: number;
  readonly standard_max: number;
  readonly priority_size: number;
  readonly priority_max: number;
}

export interface SystemHealth {
  readonly status: "ONLINE" | "DEGRADED" | "OFFLINE";
  readonly uptime_seconds: number;
  readonly providers: ProviderHealth[];
  readonly queues: QueueMetrics;
  readonly last_scan_at: string | null;
}
