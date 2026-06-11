import type { Position, Trade } from "@/types";
import { getUniverse } from "./universe";
import { generateGBM } from "./gbm";

function uid() {
  return Math.random().toString(36).slice(2, 8);
}
function rand(a: number, b: number) {
  return Math.random() * (b - a) + a;
}
function randInt(a: number, b: number) {
  return Math.floor(rand(a, b + 1));
}
function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

// ── BingX Synthetic Stock Tickers ──────────────────────────────────────────
export interface BotTicker {
  symbol: string; // display symbol e.g. "AAPL-USDT"
  underlyingSymbol: string;
  price: number;
  direction: "LONG" | "SHORT";
  entryPrice: number;
  takeProfit: number;
  stopLoss: number;
  size: number;
  sizeUnit: string;
  unrealizedPnL: number;
  unrealizedPnLPct: number;
  delta: number;
  gamma: number;
  theta: number;
  iv: number;
  rsi: number;
  macdBull: boolean;
  volRatio: number;
  atrOk: boolean;
  leverage?: number;
  fundingRate?: number;
  fundingCountdown?: number; // seconds
  liquidationPrice?: number;
  liquidationDistancePct?: number;
  orderId?: string;
  orderType?: "MARKET" | "LIMIT" | "STOP_LIMIT";
  fractionalSize?: string;
}

const BINGX_CONFIGS = [
  { symbol: "AAPL-USDT", underlying: "AAPL" },
  { symbol: "MSFT-USDT", underlying: "MSFT" },
  { symbol: "TSLA-USDT", underlying: "TSLA" },
  { symbol: "GOOGL-USDT", underlying: "GOOGL" },
  { symbol: "META-USDT", underlying: "META" },
];

function makeBotTicker(
  sym: string,
  underlying: string,
  price: number,
): BotTicker {
  const dir = Math.random() > 0.4 ? "LONG" : "SHORT";
  const entrySlip = price * (0.005 + Math.random() * 0.015);
  const entry = dir === "LONG" ? price - entrySlip : price + entrySlip;
  const tp =
    dir === "LONG"
      ? price * (1 + rand(0.01, 0.04))
      : price * (1 - rand(0.01, 0.04));
  const sl =
    dir === "LONG"
      ? price * (1 - rand(0.005, 0.02))
      : price * (1 + rand(0.005, 0.02));
  const size = parseFloat(rand(0.1, 2.0).toFixed(2));
  const unPnL = (price - entry) * size * (dir === "LONG" ? 1 : -1);
  const unPct = ((price - entry) / entry) * 100 * (dir === "LONG" ? 1 : -1);

  return {
    symbol: sym,
    underlyingSymbol: underlying,
    price,
    direction: dir,
    entryPrice: parseFloat(entry.toFixed(2)),
    takeProfit: parseFloat(tp.toFixed(2)),
    stopLoss: parseFloat(sl.toFixed(2)),
    size,
    sizeUnit: "BTC",
    unrealizedPnL: parseFloat(unPnL.toFixed(2)),
    unrealizedPnLPct: parseFloat(unPct.toFixed(2)),
    delta: parseFloat(rand(0.3, 0.8).toFixed(3)),
    gamma: parseFloat(rand(0.01, 0.05).toFixed(4)),
    theta: parseFloat((-rand(0.1, 0.5)).toFixed(3)),
    iv: rand(20, 60),
    rsi: randInt(30, 75),
    macdBull: Math.random() > 0.4,
    volRatio: parseFloat(rand(0.5, 2.5).toFixed(1)),
    atrOk: Math.random() > 0.3,
  };
}

let _bingxTickers: BotTicker[] | null = null;
export function getBingXTickers(): BotTicker[] {
  if (!_bingxTickers) {
    const universe = getUniverse();
    _bingxTickers = BINGX_CONFIGS.map((cfg) => {
      const u = universe.find((t) => t.symbol === cfg.underlying);
      return makeBotTicker(cfg.symbol, cfg.underlying, u?.price ?? 100);
    });
  }
  return _bingxTickers;
}

// ── Alpaca Equity Tickers ──────────────────────────────────────────────────
const ALPACA_SYMBOLS = [
  "AAPL",
  "MSFT",
  "TSLA",
  "GOOGL",
  "META",
  "NVDA",
  "AMZN",
  "SPY",
];

let _alpacaTickers: BotTicker[] | null = null;
export function getAlpacaTickers(): BotTicker[] {
  if (!_alpacaTickers) {
    const universe = getUniverse();
    _alpacaTickers = ALPACA_SYMBOLS.map((sym) => {
      const u = universe.find((t) => t.symbol === sym);
      const price = u?.price ?? 100;
      const t = makeBotTicker(sym, sym, price);
      const fractionalSize = parseFloat(rand(0.01, 5).toFixed(4));
      return {
        ...t,
        sizeUnit: "shares",
        size: fractionalSize,
        fractionalSize: `${fractionalSize} shares`,
        orderType: pick(["MARKET", "LIMIT", "STOP_LIMIT"] as const),
        orderId: uid(),
      };
    });
  }
  return _alpacaTickers;
}

// ── Binance Crypto Tickers ─────────────────────────────────────────────────
const BINANCE_CONFIGS = [
  { symbol: "BTC-USDT", price: 67_420 },
  { symbol: "ETH-USDT", price: 3_540 },
  { symbol: "SOL-USDT", price: 178 },
  { symbol: "BNB-USDT", price: 615 },
  { symbol: "AVAX-USDT", price: 38 },
  { symbol: "LINK-USDT", price: 18 },
  { symbol: "ARB-USDT", price: 1.18 },
  { symbol: "OP-USDT", price: 2.45 },
];

let _binanceTickers: BotTicker[] | null = null;
export function getBinanceTickers(): BotTicker[] {
  if (!_binanceTickers) {
    _binanceTickers = BINANCE_CONFIGS.map((cfg) => {
      const t = makeBotTicker(cfg.symbol, cfg.symbol, cfg.price);
      const leverage = pick([3, 5, 10, 20]);
      const fundingRate = parseFloat((rand(-0.02, 0.08) / 100).toFixed(4));
      const liquidationDist = parseFloat(rand(3, 30).toFixed(1));
      const liquidationPrice =
        t.direction === "LONG"
          ? t.price * (1 - liquidationDist / 100)
          : t.price * (1 + liquidationDist / 100);

      return {
        ...t,
        sizeUnit: "USDT",
        size: parseFloat(rand(100, 5000).toFixed(0)),
        leverage,
        fundingRate,
        fundingCountdown: randInt(0, 28800),
        liquidationPrice: parseFloat(
          liquidationPrice.toFixed(cfg.price > 100 ? 0 : 4),
        ),
        liquidationDistancePct: liquidationDist,
      };
    });
  }
  return _binanceTickers;
}

// ── Performance Stats ──────────────────────────────────────────────────────
export interface PerfStats {
  totalPnL: number;
  dailyPnL: number;
  dailyPnLPct: number;
  winRate: number;
  sharpe: number;
  sortino: number;
  maxDrawdown: number;
  profitFactor: number;
  activePositions: number;
  realizedPnL: number;
  equity: number;
}

export function generatePerfStats(baseEquity = 48_000): PerfStats {
  return {
    totalPnL: parseFloat(rand(2000, 18000).toFixed(2)),
    dailyPnL: parseFloat(rand(-400, 1200).toFixed(2)),
    dailyPnLPct: parseFloat(rand(-0.8, 2.5).toFixed(2)),
    winRate: parseFloat(rand(55, 80).toFixed(1)),
    sharpe: parseFloat(rand(1.2, 3.1).toFixed(2)),
    sortino: parseFloat(rand(1.5, 4.0).toFixed(2)),
    maxDrawdown: parseFloat(rand(3, 12).toFixed(1)),
    profitFactor: parseFloat(rand(1.4, 2.8).toFixed(2)),
    activePositions: randInt(2, 5),
    realizedPnL: parseFloat(rand(800, 8000).toFixed(2)),
    equity: baseEquity,
  };
}

// ── Trade History ──────────────────────────────────────────────────────────
export interface MockTrade {
  id: string;
  ticker: string;
  direction: "LONG" | "SHORT";
  entry: number;
  exit: number;
  pnl: number;
  pnlPct: number;
  duration: string;
  strategy: string;
  time: Date;
}

const STRATEGIES = [
  "EMA Cross",
  "RSI Divergence",
  "Volume Breakout",
  "GEX Flip",
  "IV Squeeze",
  "Momentum LSTM",
];

export function generateTrades(symbols: string[], count = 20): MockTrade[] {
  const trades: MockTrade[] = [];
  const now = Date.now();
  for (let i = 0; i < count; i++) {
    const sym = pick(symbols);
    const dir = Math.random() > 0.4 ? "LONG" : "SHORT";
    const entry = parseFloat(rand(50, 500).toFixed(2));
    const pnlPct = parseFloat(
      (rand(-2.5, 4.5) * (Math.random() > 0.35 ? 1 : -1)).toFixed(2),
    );
    const exit = parseFloat((entry * (1 + pnlPct / 100)).toFixed(2));
    const mins = randInt(5, 480);
    const dur =
      mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`;

    trades.push({
      id: uid(),
      ticker: sym,
      direction: dir,
      entry,
      exit,
      pnl: parseFloat(
        ((exit - entry) * (dir === "LONG" ? 1 : -1) * rand(0.5, 5)).toFixed(2),
      ),
      pnlPct,
      duration: dur,
      strategy: pick(STRATEGIES),
      time: new Date(now - i * 900_000 - rand(0, 600_000)),
    });
  }
  return trades;
}

// ── PDT (Pattern Day Trader) tracking for Alpaca ──────────────────────────
export interface PDTStatus {
  dayTradesUsed: number;
  dayTradesRemaining: number;
  isPatternDayTrader: boolean;
  buyingPower: number;
  sessionCountdown: number; // seconds to next session change
}

export function getPDTStatus(): PDTStatus {
  const used = randInt(0, 3);
  return {
    dayTradesUsed: used,
    dayTradesRemaining: Math.max(0, 3 - used),
    isPatternDayTrader: used >= 3,
    buyingPower: parseFloat(rand(8000, 50000).toFixed(2)),
    sessionCountdown: randInt(0, 3600),
  };
}

// ── Crypto Market Context for Binance ─────────────────────────────────────
export interface CryptoContext {
  btcDominance: number;
  fearGreedIndex: number;
  fearGreedLabel: string;
  totalOI: number;
  oiChange24h: number;
}

export function getCryptoContext(): CryptoContext {
  const fg = randInt(20, 85);
  const label =
    fg < 25
      ? "EXTREME FEAR"
      : fg < 45
        ? "FEAR"
        : fg < 55
          ? "NEUTRAL"
          : fg < 75
            ? "GREED"
            : "EXTREME GREED";
  return {
    btcDominance: parseFloat(rand(48, 58).toFixed(1)),
    fearGreedIndex: fg,
    fearGreedLabel: label,
    totalOI: parseFloat(rand(20, 80).toFixed(1)),
    oiChange24h: parseFloat(rand(-15, 20).toFixed(1)),
  };
}
