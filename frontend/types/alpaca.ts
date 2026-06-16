/**
 * Tipos del frontend para el bot Alpaca (equities, LONG-only).
 * Reflejan los contratos del backend nativo: EquityCycleResult,
 * AlpacaCandidateAnalysis, AlpacaDecision, EquityOrderIntent,
 * EquityRiskDecision, además del status y las posiciones de Alpaca.
 */

export type Suitability = "ALLOW" | "SIZE_DOWN" | "BLOCK" | "INSUFFICIENT_DATA";
export type EquityDirection = "LONG" | "FLAT";
export type AlpacaRoute = "priority" | "scan";
export type OptionsDirection = "BULL" | "BEAR" | "NEUTRAL";
export type R2ConfluenceTier = "NONE" | "S1" | "S2" | "S3";

export interface OptionsConfluence {
  score: number;
  by_family: Record<string, number>;
  by_engine: Record<string, number>;
  dominant_direction: OptionsDirection;
  critical: boolean;
  moderate: boolean;
  reason_codes: string[];
}

export interface AlpacaCandidateAnalysis {
  symbol: string;
  timestamp: string;
  market_type: "stock";
  latest_close: number | null;
  atr: number | null;
  macd_histogram: number | null;
  relative_strength: number | null;
  volume_z_score: number | null;
  close_position_in_range: number | null;
  technical_ok: boolean;
  route?: AlpacaRoute;
  options_confluence?: OptionsConfluence | null;
  r2_technical_score?: Record<string, unknown>;
  r2_confluence_tier?: R2ConfluenceTier;
}

export interface AlpacaDecision {
  symbol: string;
  decision: Suitability;
  direction: EquityDirection;
  score: number;
  probability: number | null;
  reason_codes: string[];
  route?: AlpacaRoute;
}

export interface EquityOrderIntent {
  symbol: string;
  side: "BUY";
  quantity: number;
  entry_type: "MARKET" | "LIMIT";
  reference_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  notional_usd: number;
  client_order_id: string;
  cycle_id: string | null;
  reason_codes: string[];
}

export interface EquityRiskDecision {
  authorized: boolean;
  intent: EquityOrderIntent;
  idempotency_key: string;
  reason_codes: string[];
  adjusted_quantity: number | null;
  already_seen: boolean;
}

export interface EquityCycleResult {
  started_at: string;
  finished_at: string;
  universe: string[];
  prefiltered: string[];
  route1_symbols?: string[];
  route2_symbols?: string[];
  analyses: AlpacaCandidateAnalysis[];
  decisions: AlpacaDecision[];
  order_intents: EquityOrderIntent[];
  risk_decisions: EquityRiskDecision[];
  executions: Array<Record<string, unknown>>;
  dry_run: boolean;
  trading_environment: string;
  blocked_reasons: Record<string, string[]>;
}

export interface AlpacaBalance {
  equity?: string;
  cash?: string;
  buying_power?: string;
  pattern_day_trader?: boolean;
  dry_run?: boolean;
}

export interface AlpacaStatusResponse {
  service: string;
  dry_run: boolean;
  trading_mode: string;
  is_live: boolean;
  trading_environment: string;
  universe: string[];
  balance: AlpacaBalance;
}

export interface AlpacaPosition {
  symbol: string;
  qty: string;
  avg_entry_price: string;
  current_price: string;
  market_value: string;
  unrealized_pl: string;
  unrealized_plpc: string;
  side: string;
}

export type MarketSession = "PRE" | "OPEN" | "AFTER" | "CLOSED";
