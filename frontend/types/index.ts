export interface Greeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho?: number;
}

export interface Signal {
  name: string;
  value: number | string;
  direction: "BULL" | "BEAR" | "NEUTRAL";
  weight: number;
}

export interface Ticker {
  symbol: string;
  price: number;
  priceChange: number;
  priceChangePct: number;
  afterMarketPrice?: number;
  afterMarketChangePct?: number;
  volume: number;
  avgVolume: number;
  iv: number;
  ivRank: number;
  phase: "A" | "B" | "C" | "D";
  momentum: number;
  signals: Signal[];
  greeks: Greeks;
  candles: OHLCV[];
}

export interface Trade {
  id: string;
  ticker: string;
  direction: "LONG" | "SHORT";
  entryPrice: number;
  exitPrice: number;
  pnl: number;
  pnlPct: number;
  duration: number;
  strategy: string;
  openTime: Date;
  closeTime: Date;
}

export interface Position {
  id: string;
  ticker: string;
  direction: "LONG" | "SHORT";
  size: number;
  entryPrice: number;
  currentPrice: number;
  takeProfit: number;
  stopLoss: number;
  unrealizedPnL: number;
  realizedPnL: number;
  openTime: Date;
  strategy: string;
}

export interface BotStatus {
  name: string;
  status: "RUNNING" | "PAUSED" | "ERROR" | "IDLE";
  totalPnL: number;
  dailyPnL: number;
  winRate: number;
  sharpe: number;
  drawdown: number;
  positions: Position[];
  trades: Trade[];
}

export type EngineCategory =
  | "ML"
  | "STATISTICAL"
  | "TECHNICAL"
  | "OPTIONS"
  | "MACRO"
  | "HYBRID";

// ── Strategy Weights (4-phase funnel) ─────────────────────────

export interface PhaseAWeights {
  phaseWeight: number;
  validationStrictness: number;
  minPrice: number;
  minVolume: number;
  maxSpreadPct: number;
}

export interface PhaseBWeights {
  phaseWeight: number;
  ofiWeight: number;
  smcWeight: number;
  vpinWeight: number;
  ofiSensitivity: number;
  smcLookbackPeriods: number;
  vpinBuckets: number;
}

export interface PhaseCEngineWeights {
  gexScore: number;
  gammaFlip: number;
  dexExposure: number;
  flowSignal: number;
  zeroDay: number;
  shadowDelta: number;
  deltaFlow: number;
  phaseBMomentum: number;
}

export interface PhaseCContractFilters {
  minVolume: number;
  minOpenInterest: number;
  maxSpreadPct: number;
  minDte: number;
  maxDte: number;
  deltaTargetCall: number;
  deltaTargetPut: number;
  minCompositeScore: number;
  ivMin: number;
  ivMax: number;
  optimalDte: number;
}

export interface PhaseCContractScoreWeights {
  basicMetrics: number;
  engineAverage: number;
  liquidity: number;
  delta: number;
  iv: number;
  dte: number;
}

export interface PhaseCWeights {
  phaseWeight: number;
  engineWeights: PhaseCEngineWeights;
  contractScoreWeights: PhaseCContractScoreWeights;
  contractFilters: PhaseCContractFilters;
  topNTickers: number;
  topNContracts: number;
}

export interface PhaseDWeights {
  phaseWeight: number;
  momentumWeight: number;
  volatilityWeight: number;
  volumeSpikeWeight: number;
  vwapWeight: number;
  phaseCConfluenceWeight: number;
  entryMomentumThreshold: number;
  exitMomentumThreshold: number;
  volumeSpikeMultiplier: number;
  minConfidence: number;
  cooldownSeconds: number;
  minTicksForSignal: number;
  stopLossPct: number;
  takeProfitPct: number;
  momentumWindow: number;
  volatilityWindow: number;
}

export interface StrategyWeights {
  regimeAdaptationEnabled: boolean;
  phaseA: PhaseAWeights;
  phaseB: PhaseBWeights;
  phaseC: PhaseCWeights;
  phaseD: PhaseDWeights;
}

export type WeightCategory =
  | "phase_a"
  | "phase_b"
  | "phase_c_engines"
  | "phase_c_filters"
  | "phase_d";

export interface PredictiveEngine {
  id: number;
  name: string;
  category: EngineCategory;
  signal: "BULL" | "BEAR" | "NEUTRAL";
  confidence: number;
  predictedMove: number;
  conviction: "LOW" | "MED" | "HIGH";
  accuracy7d: number;
  accuracy30d: number;
  accuracy90d: number;
  status: "ACTIVE" | "TRAINING" | "DEGRADED";
}

export interface OptionsChainRow {
  strike: number;
  call: {
    bid: number;
    ask: number;
    iv: number;
    delta: number;
    gamma: number;
    theta: number;
    vega: number;
    oi: number;
    volume: number;
  };
  put: {
    bid: number;
    ask: number;
    iv: number;
    delta: number;
    gamma: number;
    theta: number;
    vega: number;
    oi: number;
    volume: number;
  };
  isATM: boolean;
}

export interface OHLCV {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type TabId =
  | "scanner"
  | "bingx"
  | "alpaca"
  | "binance"
  | "funding"
  | "derivatives"
  | "technical"
  | "predictive"
  | "consumption"
  | "audit"
  | "route-pnl";

export interface TabDef {
  id: TabId;
  label: string;
  index: number;
}

export const TABS: TabDef[] = [
  { id: "scanner", label: "01 SCANNER", index: 0 },
  { id: "bingx", label: "02 BINGX", index: 1 },
  { id: "alpaca", label: "03 ALPACA", index: 2 },
  { id: "binance", label: "04 BINANCE", index: 3 },
  { id: "funding", label: "05 FUNDING", index: 4 },
  { id: "derivatives", label: "06 DERIVADOS", index: 5 },
  { id: "technical", label: "07 TÉCNICO", index: 6 },
  { id: "predictive", label: "08 PREDICTIVO", index: 7 },
  { id: "consumption", label: "09 CONSUMO", index: 8 },
  { id: "audit", label: "10 AUDIT", index: 9 },
  { id: "route-pnl", label: "11 PnL RUTAS", index: 10 },
];
