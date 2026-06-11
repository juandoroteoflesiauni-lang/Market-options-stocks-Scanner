import type { Ticker, Signal } from "@/types";
import { generateSparkline } from "./gbm";
import { registerTicker } from "./wsSimulator";

interface SeedTicker {
  symbol: string;
  price: number;
  iv: number;
  phase: "A" | "B" | "C" | "D";
  momentum: number;
}

const SEED: SeedTicker[] = [
  { symbol: "AAPL", price: 194.82, iv: 0.28, phase: "B", momentum: 42 },
  { symbol: "MSFT", price: 418.31, iv: 0.24, phase: "B", momentum: 55 },
  { symbol: "TSLA", price: 248.7, iv: 0.62, phase: "C", momentum: -18 },
  { symbol: "GOOGL", price: 177.4, iv: 0.26, phase: "B", momentum: 38 },
  { symbol: "META", price: 492.17, iv: 0.31, phase: "A", momentum: 71 },
  { symbol: "NVDA", price: 878.45, iv: 0.48, phase: "B", momentum: 68 },
  { symbol: "AMZN", price: 189.62, iv: 0.29, phase: "B", momentum: 44 },
  { symbol: "SPY", price: 531.2, iv: 0.15, phase: "B", momentum: 31 },
  { symbol: "QQQ", price: 445.9, iv: 0.18, phase: "B", momentum: 36 },
  { symbol: "NFLX", price: 674.8, iv: 0.38, phase: "A", momentum: 58 },
  { symbol: "AMD", price: 168.42, iv: 0.52, phase: "A", momentum: 47 },
  { symbol: "INTC", price: 31.84, iv: 0.41, phase: "D", momentum: -62 },
  { symbol: "CRM", price: 285.4, iv: 0.33, phase: "C", momentum: -8 },
  { symbol: "UBER", price: 77.22, iv: 0.44, phase: "A", momentum: 52 },
  { symbol: "COIN", price: 224.88, iv: 0.71, phase: "C", momentum: -24 },
  { symbol: "PLTR", price: 24.68, iv: 0.58, phase: "A", momentum: 61 },
  { symbol: "SNOW", price: 162.3, iv: 0.49, phase: "D", momentum: -41 },
  { symbol: "SQ", price: 77.18, iv: 0.54, phase: "C", momentum: -12 },
  { symbol: "SHOP", price: 72.46, iv: 0.47, phase: "B", momentum: 29 },
  { symbol: "ROKU", price: 70.14, iv: 0.66, phase: "D", momentum: -55 },
];

function buildSignals(): Signal[] {
  const indicators = [
    "RSI Composite",
    "MACD Divergence",
    "Volume Profile",
    "IV Rank",
    "GEX Level",
    "ML Momentum",
  ];
  return indicators.map((name) => ({
    name,
    value: Math.round(Math.random() * 100),
    direction: (["BULL", "BEAR", "NEUTRAL"] as const)[
      Math.floor(Math.random() * 3)
    ],
    weight: Math.round(Math.random() * 20),
  }));
}

function buildTicker(seed: SeedTicker): Ticker {
  const now = Math.floor(Date.now() / 1000);
  const candles = Array.from({ length: 60 }, (_, i) => {
    const time = now - (60 - i) * 60; // 1-minute intervals
    const open = seed.price * (1 + (Math.random() - 0.5) * 0.02);
    const close = open * (1 + (Math.random() - 0.5) * 0.01);
    const high = Math.max(open, close) * (1 + Math.random() * 0.005);
    const low = Math.min(open, close) * (1 - Math.random() * 0.005);
    return {
      time,
      open,
      high,
      low,
      close,
      volume: Math.floor(Math.random() * 10000),
    };
  });

  return {
    symbol: seed.symbol,
    price: seed.price,
    priceChange: (Math.random() - 0.5) * 5,
    priceChangePct: (Math.random() - 0.5) * 2,
    volume: Math.floor(1_000_000 + Math.random() * 50_000_000),
    avgVolume: Math.floor(10_000_000 + Math.random() * 30_000_000),
    iv: seed.iv,
    ivRank: Math.floor(Math.random() * 100),
    phase: seed.phase,
    momentum: seed.momentum,
    signals: buildSignals(),
    greeks: {
      delta: parseFloat((Math.random() * 0.9 + 0.05).toFixed(3)),
      gamma: parseFloat((Math.random() * 0.05).toFixed(4)),
      theta: parseFloat((-Math.random() * 0.5).toFixed(3)),
      vega: parseFloat((Math.random() * 0.3).toFixed(3)),
    },
    candles,
  };
}

let _universe: Ticker[] | null = null;

export function getUniverse(): Ticker[] {
  if (!_universe) {
    _universe = SEED.map(buildTicker);
    _universe.forEach((t) => registerTicker(t));
  }
  return _universe;
}

export function getTickerBySymbol(symbol: string): Ticker | undefined {
  return getUniverse().find((t) => t.symbol === symbol);
}

export const CRYPTO_SEEDS = [
  { symbol: "BTC-USDT", price: 67_420 },
  { symbol: "ETH-USDT", price: 3_540 },
  { symbol: "SOL-USDT", price: 178 },
  { symbol: "BNB-USDT", price: 615 },
  { symbol: "AVAX-USDT", price: 38 },
  { symbol: "LINK-USDT", price: 18 },
  { symbol: "ARB-USDT", price: 1.18 },
  { symbol: "OP-USDT", price: 2.45 },
];
