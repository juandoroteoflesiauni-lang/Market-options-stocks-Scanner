export interface ChallengePreset {
  id: string;
  name: string;
  firm: string;
  accountSize: number;
  profitTarget: number; // absolute $
  dailyLossLimit: number; // absolute $
  maxDrawdown: number; // absolute $
  minTradingDays: number;
  maxTradingDays: number;
  phase: "EVALUATION" | "VERIFICATION" | "FUNDED";
}

export interface DailyPnL {
  day: number;
  date: string;
  pnl: number;
  cumulativePnl: number;
  drawdown: number;
  target: number; // linear trajectory toward profitTarget
  dangerZone: number; // -dailyLossLimit
}

export interface ChallengeRule {
  id: string;
  label: string;
  limit: number;
  current: number;
  unit: "$" | "%" | "days";
  status: "PASS" | "FAIL" | "WARN";
  description: string;
}

export interface BestSetup {
  id: string;
  symbol: string;
  phase: "A" | "B" | "C" | "D";
  direction: "LONG" | "SHORT";
  confidence: number; // 0-100
  compliance: number; // 0-100 how well it respects challenge rules
  score: number; // confidence * compliance / 100
  entry: number;
  tp: number;
  sl: number;
  rr: number;
  strategy: string;
  note: string;
}

export const MFFU_BUILDER_PRESET: ChallengePreset = {
  id: "mffu-builder-50k",
  name: "MFFU Builder $50K",
  firm: "MyFundedFutures",
  accountSize: 50_000,
  profitTarget: 3_000,
  dailyLossLimit: 1_000,
  maxDrawdown: 2_000,
  minTradingDays: 1,
  maxTradingDays: 30,
  phase: "EVALUATION",
};

export const CHALLENGE_PRESETS: ChallengePreset[] = [
  MFFU_BUILDER_PRESET,
  {
    id: "ftmo-10k",
    name: "FTMO $10K Challenge",
    firm: "FTMO",
    accountSize: 10_000,
    profitTarget: 1_000,
    dailyLossLimit: 500,
    maxDrawdown: 1_000,
    minTradingDays: 4,
    maxTradingDays: 30,
    phase: "EVALUATION",
  },
  {
    id: "ftmo-25k",
    name: "FTMO $25K Challenge",
    firm: "FTMO",
    accountSize: 25_000,
    profitTarget: 2_500,
    dailyLossLimit: 1_250,
    maxDrawdown: 2_500,
    minTradingDays: 4,
    maxTradingDays: 30,
    phase: "EVALUATION",
  },
  {
    id: "ftmo-50k",
    name: "FTMO $50K Challenge",
    firm: "FTMO",
    accountSize: 50_000,
    profitTarget: 5_000,
    dailyLossLimit: 2_500,
    maxDrawdown: 5_000,
    minTradingDays: 4,
    maxTradingDays: 30,
    phase: "EVALUATION",
  },
  {
    id: "topstep-50k",
    name: "TopStep $50K Combine",
    firm: "TopStep",
    accountSize: 50_000,
    profitTarget: 3_000,
    dailyLossLimit: 1_000,
    maxDrawdown: 2_000,
    minTradingDays: 10,
    maxTradingDays: 60,
    phase: "EVALUATION",
  },
  {
    id: "topstep-150k",
    name: "TopStep $150K Combine",
    firm: "TopStep",
    accountSize: 150_000,
    profitTarget: 9_000,
    dailyLossLimit: 3_000,
    maxDrawdown: 6_000,
    minTradingDays: 10,
    maxTradingDays: 60,
    phase: "EVALUATION",
  },
  {
    id: "custom",
    name: "Custom Challenge",
    firm: "Custom",
    accountSize: 20_000,
    profitTarget: 2_000,
    dailyLossLimit: 800,
    maxDrawdown: 1_500,
    minTradingDays: 5,
    maxTradingDays: 45,
    phase: "EVALUATION",
  },
];

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

export function generateDailyPnL(
  preset: ChallengePreset,
  days = 14,
): DailyPnL[] {
  const rand = rng(preset.accountSize + preset.profitTarget);
  const result: DailyPnL[] = [];
  let cumulative = 0;
  let peakBalance = preset.accountSize;

  for (let d = 1; d <= days; d++) {
    const dailyMove = (rand() - 0.42) * preset.dailyLossLimit * 0.9;
    cumulative += dailyMove;
    const balance = preset.accountSize + cumulative;
    peakBalance = Math.max(peakBalance, balance);
    const drawdown = peakBalance - balance;
    const date = new Date(2026, 4, 26 + d).toLocaleDateString("en-US", {
      month: "short",
      day: "2-digit",
    });

    result.push({
      day: d,
      date,
      pnl: Math.round(dailyMove),
      cumulativePnl: Math.round(cumulative),
      drawdown: Math.round(drawdown),
      target: Math.round((preset.profitTarget / preset.minTradingDays) * d),
      dangerZone: -preset.dailyLossLimit,
    });
  }
  return result;
}

export function generateRules(
  preset: ChallengePreset,
  series: DailyPnL[],
): ChallengeRule[] {
  const lastDay = series[series.length - 1];
  const worstDaily = Math.min(...series.map((d) => d.pnl));
  const tradingDays = series.filter((d) => Math.abs(d.pnl) > 10).length;
  const currentDD = lastDay?.drawdown ?? 0;

  const dailyLossRatio = Math.abs(worstDaily) / preset.dailyLossLimit;
  const ddRatio = currentDD / preset.maxDrawdown;
  const daysRatio = tradingDays / preset.minTradingDays;

  return [
    {
      id: "daily-loss",
      label: "Daily Loss Limit",
      limit: preset.dailyLossLimit,
      current: Math.abs(worstDaily),
      unit: "$",
      status:
        dailyLossRatio >= 1 ? "FAIL" : dailyLossRatio >= 0.8 ? "WARN" : "PASS",
      description: `Worst day: -$${Math.abs(worstDaily).toFixed(0)} / Limit: -$${preset.dailyLossLimit}`,
    },
    {
      id: "total-dd",
      label: "Max Drawdown",
      limit: preset.maxDrawdown,
      current: currentDD,
      unit: "$",
      status: ddRatio >= 1 ? "FAIL" : ddRatio >= 0.75 ? "WARN" : "PASS",
      description: `Current DD: $${currentDD.toFixed(0)} / Max: $${preset.maxDrawdown}`,
    },
    {
      id: "min-days",
      label: "Min Trading Days",
      limit: preset.minTradingDays,
      current: tradingDays,
      unit: "days",
      status: daysRatio >= 1 ? "PASS" : daysRatio >= 0.6 ? "WARN" : "FAIL",
      description: `${tradingDays} of ${preset.minTradingDays} required days completed`,
    },
    {
      id: "profit-target",
      label: "Profit Target",
      limit: preset.profitTarget,
      current: Math.max(0, lastDay?.cumulativePnl ?? 0),
      unit: "$",
      status:
        (lastDay?.cumulativePnl ?? 0) >= preset.profitTarget
          ? "PASS"
          : (lastDay?.cumulativePnl ?? 0) >= preset.profitTarget * 0.5
            ? "WARN"
            : "FAIL",
      description: `Earned: $${Math.max(0, lastDay?.cumulativePnl ?? 0).toFixed(0)} / Target: $${preset.profitTarget}`,
    },
    {
      id: "max-days",
      label: "Max Trading Days",
      limit: preset.maxTradingDays,
      current: series.length,
      unit: "days",
      status:
        series.length >= preset.maxTradingDays
          ? "FAIL"
          : series.length >= preset.maxTradingDays * 0.8
            ? "WARN"
            : "PASS",
      description: `${series.length} of ${preset.maxTradingDays} max days used`,
    },
  ];
}

export const BEST_SETUPS: BestSetup[] = [
  {
    id: "s1",
    symbol: "AAPL",
    phase: "C",
    direction: "LONG",
    confidence: 88,
    compliance: 95,
    score: 83.6,
    entry: 211.4,
    tp: 217.8,
    sl: 209.1,
    rr: 2.78,
    strategy: "Wyckoff Spring + IV Compression",
    note: "ATM IV below 20-day avg, delta buildup confirmed",
  },
  {
    id: "s2",
    symbol: "NVDA",
    phase: "B",
    direction: "SHORT",
    confidence: 74,
    compliance: 91,
    score: 67.3,
    entry: 138.5,
    tp: 131.2,
    sl: 140.8,
    rr: 3.17,
    strategy: "Distribution Top + Gamma Flip",
    note: "GEX flipped negative at 140 strike cluster",
  },
  {
    id: "s3",
    symbol: "SPY",
    phase: "C",
    direction: "LONG",
    confidence: 82,
    compliance: 88,
    score: 72.2,
    entry: 593.1,
    tp: 601.4,
    sl: 590.3,
    rr: 2.96,
    strategy: "IV Rank < 20 + Delta Skew Bull",
    note: "Risk-on mode, VIX term structure contango",
  },
  {
    id: "s4",
    symbol: "TSLA",
    phase: "A",
    direction: "LONG",
    confidence: 65,
    compliance: 82,
    score: 53.3,
    entry: 262.8,
    tp: 275.0,
    sl: 258.5,
    rr: 2.84,
    strategy: "Accumulation Break + Call Wall Magnet",
    note: "Heavy call OI at 270, positive gamma above",
  },
  {
    id: "s5",
    symbol: "QQQ",
    phase: "C",
    direction: "SHORT",
    confidence: 71,
    compliance: 79,
    score: 56.1,
    entry: 512.4,
    tp: 504.8,
    sl: 515.2,
    rr: 2.71,
    strategy: "Bearish Engulf + Put Sweep Activity",
    note: "Unusual put sweep detected at 510 strike",
  },
];
