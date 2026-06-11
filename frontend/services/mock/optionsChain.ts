import type { OptionsChainRow } from "@/types";

const SQRT_2PI = Math.sqrt(2 * Math.PI);

function normCDF(x: number): number {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + p * x);
  const y =
    1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1 + sign * y);
}

function d1(S: number, K: number, r: number, sigma: number, T: number): number {
  return (
    (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T))
  );
}

function blackScholesCall(
  S: number,
  K: number,
  r: number,
  sigma: number,
  T: number,
): number {
  const _d1 = d1(S, K, r, sigma, T);
  const _d2 = _d1 - sigma * Math.sqrt(T);
  return S * normCDF(_d1) - K * Math.exp(-r * T) * normCDF(_d2);
}

function blackScholesPut(
  S: number,
  K: number,
  r: number,
  sigma: number,
  T: number,
): number {
  const _d1 = d1(S, K, r, sigma, T);
  const _d2 = _d1 - sigma * Math.sqrt(T);
  return K * Math.exp(-r * T) * normCDF(-_d2) - S * normCDF(-_d1);
}

function delta(
  S: number,
  K: number,
  r: number,
  sigma: number,
  T: number,
  isCall: boolean,
): number {
  const _d1 = d1(S, K, r, sigma, T);
  return isCall ? normCDF(_d1) : normCDF(_d1) - 1;
}

function gamma(
  S: number,
  K: number,
  r: number,
  sigma: number,
  T: number,
): number {
  const _d1 = d1(S, K, r, sigma, T);
  return Math.exp((-_d1 * _d1) / 2) / (SQRT_2PI * S * sigma * Math.sqrt(T));
}

function theta(
  S: number,
  K: number,
  r: number,
  sigma: number,
  T: number,
  isCall: boolean,
): number {
  const _d1 = d1(S, K, r, sigma, T);
  const _d2 = _d1 - sigma * Math.sqrt(T);
  const base =
    -(S * Math.exp((-_d1 * _d1) / 2) * sigma) / (2 * SQRT_2PI * Math.sqrt(T));
  return isCall
    ? (base - r * K * Math.exp(-r * T) * normCDF(_d2)) / 365
    : (base + r * K * Math.exp(-r * T) * normCDF(-_d2)) / 365;
}

function vega(
  S: number,
  K: number,
  r: number,
  sigma: number,
  T: number,
): number {
  const _d1 = d1(S, K, r, sigma, T);
  return (S * Math.sqrt(T) * Math.exp((-_d1 * _d1) / 2)) / SQRT_2PI / 100;
}

/**
 * Generates a full options chain for a given spot price, IV, and expiry.
 * strikes: number of strikes on each side of ATM.
 */
export function generateOptionsChain(
  spot: number,
  iv = 0.28,
  daysToExpiry = 30,
  strikesPerSide = 10,
  strikeStep?: number,
): OptionsChainRow[] {
  const step = strikeStep ?? Math.round(spot * 0.01);
  const T = daysToExpiry / 365;
  const r = 0.045;
  const rows: OptionsChainRow[] = [];
  const atmStrike = Math.round(spot / step) * step;

  for (let i = -strikesPerSide; i <= strikesPerSide; i++) {
    const K = atmStrike + i * step;
    if (K <= 0) continue;

    // IV skew — puts more expensive than calls
    const skew = i < 0 ? iv * (1 + Math.abs(i) * 0.012) : iv * (1 - i * 0.005);
    const callIV = Math.max(0.05, skew);
    const putIV = Math.max(0.05, skew * 1.04);

    const callMid = blackScholesCall(spot, K, r, callIV, T);
    const putMid = blackScholesPut(spot, K, r, putIV, T);
    const spread = callMid * 0.02;

    const randomOI = () => Math.floor(500 + Math.random() * 5000);
    const randomVol = () => Math.floor(50 + Math.random() * 500);

    rows.push({
      strike: K,
      isATM: i === 0,
      call: {
        bid: Math.max(0.01, callMid - spread),
        ask: callMid + spread,
        iv: callIV * 100,
        delta: delta(spot, K, r, callIV, T, true),
        gamma: gamma(spot, K, r, callIV, T),
        theta: theta(spot, K, r, callIV, T, true),
        vega: vega(spot, K, r, callIV, T),
        oi: randomOI(),
        volume: randomVol(),
      },
      put: {
        bid: Math.max(0.01, putMid - spread * 1.1),
        ask: putMid + spread * 1.1,
        iv: putIV * 100,
        delta: delta(spot, K, r, putIV, T, false),
        gamma: gamma(spot, K, r, putIV, T),
        theta: theta(spot, K, r, putIV, T, false),
        vega: vega(spot, K, r, putIV, T),
        oi: randomOI(),
        volume: randomVol(),
      },
    });
  }

  return rows;
}
