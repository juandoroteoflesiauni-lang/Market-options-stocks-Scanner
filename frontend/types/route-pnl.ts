export type RouteBucket = "R1" | "R2" | "BINGX" | "OPTIONS_R1";

export interface RoutePnLBucket {
  route: RouteBucket;
  trade_count: number;
  execution_count: number;
  realized_pnl_usd: number;
  notional_usd: number;
  win_count: number;
  loss_count: number;
}

export interface RoutePnLDailyPoint {
  date: string;
  alpaca_equity_usd: number | null;
  bingx_equity_usdt: number | null;
  bingx_unrealized_usdt: number | null;
}

export interface RoutePnLDashboardResponse {
  generated_at: string;
  buckets: RoutePnLBucket[];
  daily: RoutePnLDailyPoint[];
  notes: string[];
}
