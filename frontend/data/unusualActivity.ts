export type FlowSide = "CALL" | "PUT";
export type FlowType = "SWEEP" | "BLOCK" | "SPLIT";
export type PremiumTier = 10_000 | 50_000 | 100_000;

export interface UnusualFlowRow {
  id: string;
  timestamp: Date;
  symbol: string;
  expiry: string;
  strike: number;
  side: FlowSide;
  type: FlowType;
  premium: number;
  size: number;
  oi: number;
  iv: number;
  delta: number;
  sentiment: "BULLISH" | "BEARISH" | "NEUTRAL";
}

const SYMBOLS = [
  "AAPL",
  "NVDA",
  "TSLA",
  "SPY",
  "QQQ",
  "MSFT",
  "META",
  "AMZN",
  "AMD",
  "PLTR",
  "COIN",
  "GOOGL",
];
const EXPIRIES = [
  "Jun-20",
  "Jun-27",
  "Jul-18",
  "Jul-25",
  "Aug-15",
  "Sep-19",
  "Dec-19",
];
const FLOW_TYPES: FlowType[] = ["SWEEP", "BLOCK", "SPLIT"];

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function pick<T>(arr: T[], r: number): T {
  return arr[Math.floor(r * arr.length)];
}

export function generateUnusualFlow(count = 40, seed = 42): UnusualFlowRow[] {
  const rand = rng(seed);
  const rows: UnusualFlowRow[] = [];
  const now = new Date();

  for (let i = 0; i < count; i++) {
    const side: FlowSide = rand() > 0.5 ? "CALL" : "PUT";
    const premium = Math.round((rand() * 490_000 + 10_000) / 1000) * 1000;
    const iv = 0.15 + rand() * 0.7;
    const delta = side === "CALL" ? rand() * 0.5 + 0.1 : -(rand() * 0.5 + 0.1);
    const sentiment: UnusualFlowRow["sentiment"] =
      side === "CALL"
        ? delta > 0.4
          ? "BULLISH"
          : "NEUTRAL"
        : delta < -0.4
          ? "BEARISH"
          : "NEUTRAL";

    const minutesAgo = Math.floor(rand() * 90);
    const timestamp = new Date(now.getTime() - minutesAgo * 60_000);

    rows.push({
      id: `flow-${i}-${seed}`,
      timestamp,
      symbol: pick(SYMBOLS, rand()),
      expiry: pick(EXPIRIES, rand()),
      strike: Math.round((50 + rand() * 500) / 5) * 5,
      side,
      type: pick(FLOW_TYPES, rand()),
      premium,
      size: Math.round(rand() * 2000 + 10) * 10,
      oi: Math.round(rand() * 50000 + 500),
      iv,
      delta,
      sentiment,
    });
  }

  return rows.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
}

export const INITIAL_FLOW = generateUnusualFlow(40, 42);

export function addNewFlowRow(existing: UnusualFlowRow[]): UnusualFlowRow[] {
  const seed = Date.now() % 99999;
  const rand = rng(seed);
  const side: FlowSide = rand() > 0.5 ? "CALL" : "PUT";
  const premium = Math.round((rand() * 490_000 + 10_000) / 1000) * 1000;
  const iv = 0.15 + rand() * 0.7;
  const delta = side === "CALL" ? rand() * 0.5 + 0.1 : -(rand() * 0.5 + 0.1);
  const sentiment: UnusualFlowRow["sentiment"] =
    side === "CALL"
      ? delta > 0.4
        ? "BULLISH"
        : "NEUTRAL"
      : delta < -0.4
        ? "BEARISH"
        : "NEUTRAL";

  const SYMBOLS2 = [
    "AAPL",
    "NVDA",
    "TSLA",
    "SPY",
    "QQQ",
    "MSFT",
    "META",
    "AMZN",
    "AMD",
    "PLTR",
  ];
  const EXPIRIES2 = ["Jun-20", "Jun-27", "Jul-18", "Aug-15", "Sep-19"];

  const newRow: UnusualFlowRow = {
    id: `flow-live-${Date.now()}`,
    timestamp: new Date(),
    symbol: pick(SYMBOLS2, rand()),
    expiry: pick(EXPIRIES2, rand()),
    strike: Math.round((50 + rand() * 500) / 5) * 5,
    side,
    type: pick(["SWEEP", "BLOCK", "SPLIT"] as FlowType[], rand()),
    premium,
    size: Math.round(rand() * 2000 + 10) * 10,
    oi: Math.round(rand() * 50000 + 500),
    iv,
    delta,
    sentiment,
  };

  return [newRow, ...existing.slice(0, 59)];
}

export function generateOptionsChain(underlyingPrice: number, expiry: string) {
  const strikes: number[] = [];
  const atm = Math.round(underlyingPrice / 5) * 5;
  for (let i = -10; i <= 10; i++) {
    strikes.push(atm + i * 5);
  }

  return strikes.map((strike) => {
    const moneyness = (strike - underlyingPrice) / underlyingPrice;
    const callIV =
      0.25 + Math.abs(moneyness) * 0.8 + (moneyness < 0 ? 0.02 : 0);
    const putIV = 0.28 + Math.abs(moneyness) * 0.9 + (moneyness > 0 ? 0.03 : 0);
    const callDelta = Math.max(0.01, Math.min(0.99, 0.5 - moneyness * 2.5));
    const putDelta = callDelta - 1;
    const gamma = 0.05 * Math.exp(-Math.pow(moneyness * 8, 2));
    const callTheta = -(callIV * underlyingPrice * 0.01 * (1 / Math.sqrt(252)));
    const putTheta = callTheta * 1.02;
    const vega = underlyingPrice * gamma * 0.1;
    const midCall = Math.max(
      0.01,
      underlyingPrice * Math.max(0, callDelta - 0.01) + Math.random() * 0.5,
    );
    const midPut = Math.max(
      0.01,
      underlyingPrice * Math.max(0, -putDelta - 0.01) + Math.random() * 0.5,
    );

    return {
      strike,
      expiry,
      isATM: strike === atm,
      call: {
        bid: +(midCall * 0.97).toFixed(2),
        ask: +(midCall * 1.03).toFixed(2),
        iv: +callIV.toFixed(3),
        delta: +callDelta.toFixed(3),
        gamma: +gamma.toFixed(4),
        theta: +callTheta.toFixed(3),
        vega: +vega.toFixed(3),
        oi: Math.round(Math.random() * 5000 + 100),
        volume: Math.round(Math.random() * 1000 + 10),
      },
      put: {
        bid: +(midPut * 0.97).toFixed(2),
        ask: +(midPut * 1.03).toFixed(2),
        iv: +putIV.toFixed(3),
        delta: +putDelta.toFixed(3),
        gamma: +gamma.toFixed(4),
        theta: +putTheta.toFixed(3),
        vega: +vega.toFixed(3),
        oi: Math.round(Math.random() * 6000 + 150),
        volume: Math.round(Math.random() * 1200 + 20),
      },
    };
  });
}

export type OptionsChainRowFull = ReturnType<
  typeof generateOptionsChain
>[number];
