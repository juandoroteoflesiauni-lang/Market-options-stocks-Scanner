"""Shared backtesting primitives (pure math / deterministic simulation)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BacktestConfig:
    """Execution assumptions for research backtests."""

    initial_capital: float = 100_000.0
    fee_bps: float = 2.0
    slippage_bps: float = 1.0
    half_spread_bps: float = 0.0
    daily_loss_limit_pct: float = 5.0
    max_loss_limit_pct: float = 10.0


@dataclass
class BacktestResult:
    symbol: str
    module: str
    sharpe: float | None
    max_drawdown_pct: float | None
    win_rate: float | None
    trades: int
    detail: dict[str, Any]
    funding_risk_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimpleEquityCurve:
    """Close-to-close equity multiplier curve (1.0 = flat)."""

    returns: list[float]

    def max_drawdown_pct(self: SimpleEquityCurve) -> float | None:
        if not self.returns:
            return None
        peak = 1.0
        eq = 1.0
        max_dd = 0.0
        for r in self.returns:
            eq *= 1.0 + r
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)
        return round(max_dd * 100.0, 3)

    def sharpe(self: SimpleEquityCurve, rf: float = 0.0) -> float | None:
        if len(self.returns) < 5:
            return None
        xs = [float(x) - rf for x in self.returns]
        mu = statistics.fmean(xs)
        sd = statistics.pstdev(xs)
        if sd <= 1e-12:
            return None
        return float(round((mu / sd) * (252**0.5), 4))  # naive annualization


def compute_funding_risk_metrics(
    returns_pct: Sequence[float],
    *,
    daily_loss_limit_pct: float = 5.0,
    max_loss_limit_pct: float = 10.0,
    min_sample: int = 20,
) -> dict[str, Any]:
    """Compute funding-account survival metrics from a return series.

    Returns a flat dict. Never raises — falls back to
    ``{"funding_survival_grade": "insufficient_data"}`` on any error.
    """
    try:
        rets = [float(r) for r in returns_pct]
        n = len(rets)

        if n < min_sample:
            return {"funding_survival_grade": "insufficient_data"}

        # ── concentration / best-day contribution ──────────────────────────
        pos_rets = [r for r in rets if r > 0]
        total_positive = sum(pos_rets)

        if total_positive > 0:
            sorted_pos = sorted(pos_rets, reverse=True)
            best_day_contribution_pct = sorted_pos[0] / total_positive
            top3 = sorted_pos[:3]
            top3_days_contribution_pct = sum(top3) / total_positive
        else:
            best_day_contribution_pct = 0.0
            top3_days_contribution_pct = 0.0

        # ── daily loss breach ───────────────────────────────────────────────
        daily_loss_threshold = daily_loss_limit_pct / 100.0
        daily_loss_breach_count = sum(1 for r in rets if r < -daily_loss_threshold)

        # ── running peak-to-trough drawdown breach ──────────────────────────
        max_loss_threshold = max_loss_limit_pct / 100.0
        peak = 1.0
        eq = 1.0
        max_loss_breach_count = 0
        for r in rets:
            eq *= 1.0 + r
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_loss_threshold:
                max_loss_breach_count += 1

        # ── consistency risk ────────────────────────────────────────────────
        if best_day_contribution_pct >= 0.35:
            consistency_risk = "high"
        elif best_day_contribution_pct >= 0.25:
            consistency_risk = "medium"
        else:
            consistency_risk = "low"

        # ── recovery factor ─────────────────────────────────────────────────
        total_return = sum(rets)
        curve_dd = SimpleEquityCurve(rets).max_drawdown_pct()
        if curve_dd is not None and curve_dd > 0:
            recovery_factor: float | None = round(total_return / (curve_dd / 100.0), 4)
        else:
            recovery_factor = None

        # ── streak depth (longest consecutive losing sequence) ───────────────
        streak_depth = 0
        current_streak = 0
        for r in rets:
            if r < 0:
                current_streak += 1
                streak_depth = max(streak_depth, current_streak)
            else:
                current_streak = 0

        # ── loss cluster risk ───────────────────────────────────────────────
        max_consec_loss = 0
        current_consec = 0
        for r in rets:
            if r < 0:
                current_consec += 1
                max_consec_loss = max(max_consec_loss, current_consec)
            else:
                current_consec = 0

        if max_consec_loss >= 3:
            loss_cluster_risk = "high"
        elif max_consec_loss >= 2:
            loss_cluster_risk = "medium"
        else:
            loss_cluster_risk = "low"

        # ── regime fragility ────────────────────────────────────────────────
        rolling_window = 10
        if n >= rolling_window:
            rolling_means = [
                abs(statistics.fmean(rets[i : i + rolling_window]))
                for i in range(n - rolling_window + 1)
            ]
            rolling_stds = [
                statistics.pstdev(rets[i : i + rolling_window])
                for i in range(n - rolling_window + 1)
            ]
            mean_rm = statistics.fmean(rolling_means) if rolling_means else 0.0
            mean_std = statistics.fmean(rolling_stds) if rolling_stds else 0.0
            if mean_rm > 0:
                ratio = mean_std / mean_rm
                if ratio > 2.0:
                    regime_fragility = "high"
                elif ratio > 1.5:
                    regime_fragility = "medium"
                else:
                    regime_fragility = "low"
            else:
                regime_fragility = "high"
        else:
            regime_fragility = "low"

        # ── funding survival grade ──────────────────────────────────────────
        if daily_loss_breach_count > 0 or max_loss_breach_count > 0:
            funding_survival_grade = "would_breach"
        elif consistency_risk == "high" or loss_cluster_risk == "high":
            funding_survival_grade = "at_risk"
        elif consistency_risk == "medium" or streak_depth >= 5:
            funding_survival_grade = "monitor"
        else:
            funding_survival_grade = "ok"

        return {
            "best_day_contribution_pct": round(best_day_contribution_pct, 6),
            "top3_days_contribution_pct": round(top3_days_contribution_pct, 6),
            "daily_loss_breach_count": daily_loss_breach_count,
            "max_loss_breach_count": max_loss_breach_count,
            "consistency_risk": consistency_risk,
            "recovery_factor": recovery_factor,
            "streak_depth": streak_depth,
            "loss_cluster_risk": loss_cluster_risk,
            "regime_fragility": regime_fragility,
            "funding_survival_grade": funding_survival_grade,
        }

    except Exception:
        return {"funding_survival_grade": "insufficient_data"}


def _per_leg_cost_frac(cost_config: BacktestConfig | None) -> float:
    if cost_config is None:
        return 0.0
    return (
        float(cost_config.fee_bps)
        + float(cost_config.slippage_bps)
        + float(cost_config.half_spread_bps)
    ) / 10_000.0


def run_long_only_threshold_backtest(
    returns_pct: Sequence[float],
    signal: Sequence[float],
    *,
    symbol: str,
    module: str,
    threshold: float = 0.0,
    cost_config: BacktestConfig | None = None,
) -> BacktestResult:
    """Long-only strategy: hold when signal>threshold else flat.

    Optional ``cost_config`` applies one-way fee+slippage+spread (as fraction of notional)
    on each position change (entry or exit), in addition to the period's market return
    when long.
    """
    rets = [float(r) for r in returns_pct]
    sigs = [float(s) for s in signal]
    n = min(len(rets), len(sigs))
    strat: list[float] = []
    wins = 0
    trades = 0
    leg = _per_leg_cost_frac(cost_config)
    pos_prev = False

    total_turnover = 0
    total_cost_drag = 0.0

    hold_lengths: list[int] = []
    current_hold = 0

    for i in range(n):
        pos = sigs[i] > threshold
        turnover = abs(int(pos) - int(pos_prev))
        friction = turnover * leg

        total_turnover += turnover
        total_cost_drag += friction

        if pos:
            gross = rets[i]
            r_net = gross - friction
            strat.append(r_net)
            trades += 1
            if r_net > 0:
                wins += 1
            current_hold += 1
        else:
            strat.append(0.0 - friction)
            if current_hold > 0:
                hold_lengths.append(current_hold)
                current_hold = 0

        pos_prev = pos

    if current_hold > 0:
        hold_lengths.append(current_hold)

    curve = SimpleEquityCurve(strat)
    wr = wins / trades if trades else None

    avg_hold = sum(hold_lengths) / len(hold_lengths) if hold_lengths else 0.0

    detail = {
        "threshold": threshold,
        "bars": n,
        "turnover_count": total_turnover,
        "cost_drag_bps": round(total_cost_drag * 10_000.0, 2),
        "avg_hold_bars": round(avg_hold, 1),
    }
    if cost_config is not None:
        detail.update(
            {
                "fee_bps": cost_config.fee_bps,
                "slippage_bps": cost_config.slippage_bps,
                "half_spread_bps": cost_config.half_spread_bps,
            }
        )

    daily_loss_limit_pct = cost_config.daily_loss_limit_pct if cost_config is not None else 5.0
    max_loss_limit_pct = cost_config.max_loss_limit_pct if cost_config is not None else 10.0
    funding_risk_metrics = compute_funding_risk_metrics(
        strat,
        daily_loss_limit_pct=daily_loss_limit_pct,
        max_loss_limit_pct=max_loss_limit_pct,
    )

    return BacktestResult(
        symbol=symbol,
        module=module,
        sharpe=curve.sharpe(),
        max_drawdown_pct=curve.max_drawdown_pct(),
        win_rate=round(wr, 4) if wr is not None else None,
        trades=trades,
        detail=detail,
        funding_risk_metrics=funding_risk_metrics,
    )


@dataclass
class WalkForwardFoldResult:
    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    selected_threshold: float
    train_sharpe: float | None
    test_result: BacktestResult
    funding_risk_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class WalkForwardSummary:
    symbol: str
    module: str
    folds: list[WalkForwardFoldResult]
    mean_test_sharpe: float | None
    detail: dict[str, Any]


def _aggregate_walk_forward_survival_grade(folds: list[WalkForwardFoldResult]) -> str:
    """Aggregate per-fold ``funding_survival_grade`` into a summary grade."""
    grades = [
        f.funding_risk_metrics.get("funding_survival_grade", "insufficient_data") for f in folds
    ]
    non_insufficient = [g for g in grades if g != "insufficient_data"]
    if not non_insufficient:
        return "insufficient_data"
    if "would_breach" in non_insufficient:
        return "would_breach"
    if "at_risk" in non_insufficient:
        return "at_risk"
    if "monitor" in non_insufficient:
        return "monitor"
    if all(g == "ok" for g in non_insufficient):
        return "ok"
    return "ok"


def run_walk_forward_threshold_grid(
    returns_pct: Sequence[float],
    signal: Sequence[float],
    *,
    symbol: str,
    module: str,
    train_window: int,
    test_window: int,
    step: int,
    thresholds: Sequence[float],
    cost_config: BacktestConfig | None = None,
) -> WalkForwardSummary:
    """Rolling walk-forward: pick threshold by train Sharpe, evaluate on next test window."""
    rets = [float(r) for r in returns_pct]
    sigs = [float(s) for s in signal]
    n = min(len(rets), len(sigs))
    if train_window < 10 or test_window < 3 or step < 1 or not thresholds:
        return WalkForwardSummary(
            symbol=symbol,
            module=module,
            folds=[],
            mean_test_sharpe=None,
            detail={"error": "invalid_windows_or_thresholds"},
        )

    folds: list[WalkForwardFoldResult] = []
    start = 0
    fold_idx = 0
    thr = [float(t) for t in thresholds]

    while start + train_window + test_window <= n:
        tr_r = rets[start : start + train_window]
        tr_s = sigs[start : start + train_window]
        best_t = thr[0]
        best_sh: float | None = None
        for t in thr:
            res_tr = run_long_only_threshold_backtest(
                tr_r,
                tr_s,
                symbol=symbol,
                module=module,
                threshold=t,
                cost_config=cost_config,
            )
            sh = res_tr.sharpe
            if sh is None:
                continue
            if best_sh is None or sh > best_sh:
                best_sh = sh
                best_t = t

        ts = start + train_window
        te_r = rets[ts : ts + test_window]
        te_s = sigs[ts : ts + test_window]
        test_res = run_long_only_threshold_backtest(
            te_r,
            te_s,
            symbol=symbol,
            module=module,
            threshold=best_t,
            cost_config=cost_config,
        )
        folds.append(
            WalkForwardFoldResult(
                fold_index=fold_idx,
                train_start=start,
                train_end=start + train_window,
                test_start=ts,
                test_end=ts + test_window,
                selected_threshold=best_t,
                train_sharpe=best_sh,
                test_result=test_res,
                funding_risk_metrics=test_res.funding_risk_metrics,
            )
        )
        fold_idx += 1
        start += step

    sharpes = [f.test_result.sharpe for f in folds if f.test_result.sharpe is not None]
    mean_ts = round(sum(sharpes) / len(sharpes), 4) if sharpes else None

    walk_forward_survival_grade = _aggregate_walk_forward_survival_grade(folds)

    return WalkForwardSummary(
        symbol=symbol,
        module=module,
        folds=folds,
        mean_test_sharpe=mean_ts,
        detail={
            "folds": len(folds),
            "bars": n,
            "walk_forward_survival_grade": walk_forward_survival_grade,
        },
    )
