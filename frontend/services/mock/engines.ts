import type { PredictiveEngine, EngineCategory } from "@/types";

function rand(min: number, max: number): number {
  return Math.random() * (max - min) + min;
}

function randInt(min: number, max: number): number {
  return Math.floor(rand(min, max + 1));
}

function signal(): "BULL" | "BEAR" | "NEUTRAL" {
  const r = Math.random();
  if (r < 0.45) return "BULL";
  if (r < 0.75) return "NEUTRAL";
  return "BEAR";
}

function conviction(confidence: number): "LOW" | "MED" | "HIGH" {
  if (confidence >= 70) return "HIGH";
  if (confidence >= 50) return "MED";
  return "LOW";
}

function status(): "ACTIVE" | "TRAINING" | "DEGRADED" {
  const r = Math.random();
  if (r < 0.78) return "ACTIVE";
  if (r < 0.9) return "TRAINING";
  return "DEGRADED";
}

const ENGINE_DEFS: Array<{ name: string; category: EngineCategory }> = [
  // ML/AI Models (8)
  { name: "Momentum LSTM", category: "ML" },
  { name: "Transformer Price", category: "ML" },
  { name: "CNN Pattern Recog", category: "ML" },
  { name: "GRU Sequence", category: "ML" },
  { name: "BERT Sentiment", category: "ML" },
  { name: "Attention Mechanism", category: "ML" },
  { name: "Neural ODE", category: "ML" },
  { name: "Diffusion Forecast", category: "ML" },
  // Statistical Models (7)
  { name: "ARIMA-GARCH", category: "STATISTICAL" },
  { name: "VAR System", category: "STATISTICAL" },
  { name: "Kalman Filter", category: "STATISTICAL" },
  { name: "State Space Model", category: "STATISTICAL" },
  { name: "Cointegration", category: "STATISTICAL" },
  { name: "Regime Switching", category: "STATISTICAL" },
  { name: "Bayesian Structural", category: "STATISTICAL" },
  // Technical Predictive (8)
  { name: "Elliott Wave AI", category: "TECHNICAL" },
  { name: "Harmonic Pattern", category: "TECHNICAL" },
  { name: "Wyckoff Classifier", category: "TECHNICAL" },
  { name: "Fibonacci Predictor", category: "TECHNICAL" },
  { name: "Volume Profile AI", category: "TECHNICAL" },
  { name: "Order Flow Predict", category: "TECHNICAL" },
  { name: "Market Structure AI", category: "TECHNICAL" },
  { name: "SMC Detector", category: "TECHNICAL" },
  // Options-Derived (7)
  { name: "IV Forecast Model", category: "OPTIONS" },
  { name: "GEX Prediction", category: "OPTIONS" },
  { name: "Options Flow AI", category: "OPTIONS" },
  { name: "Put/Call Predictor", category: "OPTIONS" },
  { name: "Volatility Surface", category: "OPTIONS" },
  { name: "Max Pain Tracker", category: "OPTIONS" },
  { name: "DEX Momentum", category: "OPTIONS" },
  // Macro/Sentiment (6)
  { name: "NLP Sentiment", category: "MACRO" },
  { name: "Macro Regime Model", category: "MACRO" },
  { name: "VIX Prediction", category: "MACRO" },
  { name: "Fed Watch Model", category: "MACRO" },
  { name: "Market Breadth AI", category: "MACRO" },
  { name: "Sector Rotation", category: "MACRO" },
  // Hybrid Ensemble (6)
  { name: "Meta Ensemble v1", category: "HYBRID" },
  { name: "Stacking Ensemble", category: "HYBRID" },
  { name: "Bayesian Ensemble", category: "HYBRID" },
  { name: "Adaptive Blend", category: "HYBRID" },
  { name: "Dynamic Weighting", category: "HYBRID" },
  { name: "Grand Ensemble", category: "HYBRID" },
];

export function generateEngines(): PredictiveEngine[] {
  return ENGINE_DEFS.map((def, i) => {
    const conf = randInt(35, 94);
    const sig = signal();
    const move =
      sig === "BULL"
        ? rand(0.5, 4.5)
        : sig === "BEAR"
          ? -rand(0.5, 4.0)
          : rand(-1.0, 1.0);

    return {
      id: i + 1,
      name: def.name,
      category: def.category,
      signal: sig,
      confidence: conf,
      predictedMove: Math.round(move * 10) / 10,
      conviction: conviction(conf),
      accuracy7d: randInt(48, 88),
      accuracy30d: randInt(50, 82),
      accuracy90d: randInt(52, 80),
      status: status(),
    };
  });
}

// Singleton — regenerate on each session start but keep stable during it
let _engines: PredictiveEngine[] | null = null;
export function getEngines(): PredictiveEngine[] {
  if (!_engines) _engines = generateEngines();
  return _engines;
}

export interface EnsembleSummary {
  bullPct: number;
  neutralPct: number;
  bearPct: number;
  meanConfidence: number;
  meanMove: number;
  ci68Low: number;
  ci68High: number;
}

export function getEnsembleSummary(): EnsembleSummary {
  const engines = getEngines();
  const bull = engines.filter((e) => e.signal === "BULL").length;
  const bear = engines.filter((e) => e.signal === "BEAR").length;
  const total = engines.length;
  const meanConf = engines.reduce((s, e) => s + e.confidence, 0) / total;
  const meanMove = engines.reduce((s, e) => s + e.predictedMove, 0) / total;
  const moves = engines.map((e) => e.predictedMove).sort((a, b) => a - b);
  return {
    bullPct: (bull / total) * 100,
    neutralPct: ((total - bull - bear) / total) * 100,
    bearPct: (bear / total) * 100,
    meanConfidence: meanConf,
    meanMove,
    ci68Low: moves[Math.floor(total * 0.16)],
    ci68High: moves[Math.floor(total * 0.84)],
  };
}

export interface ForecastPoint {
  t: number;
  value: number;
}
export interface EngineForecast {
  engineId: number;
  category: string;
  points: ForecastPoint[];
}

// Simple seeded pseudo-random for deterministic jitter per engine
function seededRand(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    return (s >>> 0) / 0xffffffff;
  };
}

export function getEngineForecasts(
  horizonBars: number,
  basePrice: number,
): EngineForecast[] {
  const engines = getEngines();
  return engines.map((e) => {
    const rng = seededRand(e.id * 997 + horizonBars * 31);
    const drift = e.predictedMove / 100 / horizonBars;
    const vol = 0.003 + (1 - e.confidence / 100) * 0.012;
    const pts: ForecastPoint[] = [{ t: 0, value: basePrice }];
    let price = basePrice;
    for (let i = 1; i <= horizonBars; i++) {
      const z = (rng() - 0.5) * 2;
      price = price * (1 + drift + z * vol);
      pts.push({ t: i, value: Math.round(price * 100) / 100 });
    }
    return { engineId: e.id, category: e.category, points: pts };
  });
}
