/**
 * Domain types for the Deep Funnel Station frontend.
 *
 * CRITICAL: All monetary values (price, volume, strike, etc.) are
 * represented as `string` — never `number`. The backend serializes
 * Decimal fields as strings to prevent floating-point drift.
 * The UI displays them as-is; no parseFloat() is permitted.
 */

// ── Phase A — Scanner Candidates ──────────────────────────────

export interface DataLineage {
  readonly source: string;
  readonly ingestion_latency_ms: number;
  readonly raw_field_count: number;
}

export interface CandidateSnapshot {
  readonly ticker: string;
  readonly exchange: string;
  readonly price: string;
  readonly volume: string;
  readonly exchange_timestamp: string;
  readonly data_lineage: DataLineage;
}

// ── Phase B — Filtered Assets ─────────────────────────────────

export interface FilteredAsset extends CandidateSnapshot {
  readonly vpin_score: string;
  readonly ofi_score: string;
  readonly rank: number;
}

// ── Phase C — Option Contracts ────────────────────────────────

export type OptionType = "CALL" | "PUT";

export interface OptionContract {
  readonly ticker: string;
  readonly strike: string;
  readonly expiration: string;
  readonly option_type: OptionType;
  readonly delta: string;
  readonly gamma: string;
  readonly theta: string;
  readonly vega: string;
  readonly open_interest: string;
  readonly implied_volatility: string;
}

// ── Phase D — Execution Signals ───────────────────────────────

export type SignalType = "BUY" | "SELL" | "NEUTRAL";
export type SignalStrength = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface ExecutionSignal {
  readonly id: string;
  readonly ticker: string;
  readonly signal_type: SignalType;
  readonly strength: SignalStrength;
  readonly price_at_signal: string;
  readonly emitted_at: string;
  readonly reason: string;
}

// ── System Health ─────────────────────────────────────────────

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

// ── Funnel Overview ───────────────────────────────────────────

export type PhaseStatus = "ACTIVE" | "IDLE" | "ERROR" | "DISABLED";

export interface PhaseMetrics {
  readonly phase_id: "A" | "B" | "C" | "D";
  readonly label: string;
  readonly status: PhaseStatus;
  readonly input_count: number;
  readonly output_count: number;
  readonly last_processed_at: string | null;
  readonly processing_time_ms: number | null;
}

export interface FunnelOverview {
  readonly phases: PhaseMetrics[];
  readonly total_signals_emitted: number;
  readonly updated_at: string;
}
