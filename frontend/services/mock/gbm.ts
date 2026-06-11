import type { OHLCV } from "@/types";

/**
 * Generates OHLCV candles using Geometric Brownian Motion.
 * mu = drift, sigma = volatility (annualized), dt = time step fraction of year.
 */
export function generateGBM(
  initialPrice: number,
  bars: number,
  mu = 0.0002,
  sigma = 0.015,
  dt = 1 / 252,
): OHLCV[] {
  const result: OHLCV[] = [];
  let price = initialPrice;
  const now = Date.now();
  const barMs = 3_600_000;

  for (let i = 0; i < bars; i++) {
    const z = randn();
    const ret = mu * dt + sigma * Math.sqrt(dt) * z;
    const open = price;
    const close = price * Math.exp(ret);
    const high = Math.max(open, close) * (1 + Math.abs(randn()) * 0.003);
    const low = Math.min(open, close) * (1 - Math.abs(randn()) * 0.003);
    const vol = Math.floor(500_000 + Math.abs(randn()) * 300_000);

    result.push({
      time: now - (bars - i) * barMs,
      open: round(open),
      high: round(high),
      low: round(low),
      close: round(close),
      volume: vol,
    });

    price = close;
  }

  return result;
}

/** Box-Muller normal random variable */
function randn(): number {
  const u = Math.random();
  const v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function round(n: number, d = 2): number {
  return Math.round(n * 10 ** d) / 10 ** d;
}

/** Returns a simple price sparkline (last N closing prices) */
export function generateSparkline(initialPrice: number, length = 20): number[] {
  return generateGBM(initialPrice, length).map((c) => c.close);
}
