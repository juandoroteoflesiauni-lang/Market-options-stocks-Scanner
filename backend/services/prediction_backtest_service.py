"""Backtest V1 over the institutional prediction SQLite dataset."""

from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.backtesting.base import SimpleEquityCurve
from backend.services.cfd_friction_simulator import apply_cfd_friction

SUPPORTED_MODULES = {"predictive", "technical", "options_gex", "crypto_microstructure"}
DEFAULT_BATCH_SYMBOLS = ("GOOGL", "AAPL", "TSLA", "XAUUSD", "XAGUSD", "US100.CASH", "BTC/USDT")
INTRADAY_HORIZONS = {
    "1h": "outcome_return_1h",
    "4h": "outcome_return_4h",
    "eod": "outcome_return_eod",
}

# Funding-account survival thresholds. These mirror typical FTMO/Topstep eval rules.
# They are intentionally conservative — used only to simulate breaches against
# historical predictions, never to authorize live risk.
FUNDING_DAILY_LOSS_LIMIT_PCT = 5.0
FUNDING_MAX_LOSS_LIMIT_PCT = 10.0
FUNDING_BEST_DAY_WARN_RATIO = 0.35
FUNDING_BEST_DAY_BLOCK_RATIO = 0.50
FUNDING_DEFAULT_NOTIONAL_EXPOSURE = 0.03


@dataclass(frozen=True)
class BacktestCalibration:
    """Quantitative thresholds used by the Funding Lab backtest gate."""

    min_trades: int = 30
    min_signal_coverage_pct: float = 25.0
    min_profit_factor: float = 1.15
    min_sharpe: float = 0.25
    max_validated_drawdown_pct: float = 35.0
    overfit_drawdown_pct: float = 50.0

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class OosBacktestGate:
    """Out-of-sample walk-forward promotion thresholds."""

    min_folds: int = 2
    min_oos_rows_per_fold: int = 15
    min_mean_oos_sharpe: float = 0.15
    min_mean_oos_profit_factor: float = 1.05
    require_non_negative_oos_return: bool = True

    def to_dict(self) -> dict[str, float | int | bool]:
        return asdict(self)


def default_backtest_calibration() -> BacktestCalibration:
    """Read Funding Lab calibration thresholds from environment variables."""
    return BacktestCalibration(
        min_trades=_env_int("FUNDING_LAB_MIN_TRADES", 30),
        min_signal_coverage_pct=_env_float("FUNDING_LAB_MIN_SIGNAL_COVERAGE_PCT", 25.0),
        min_profit_factor=_env_float("FUNDING_LAB_MIN_PROFIT_FACTOR", 1.15),
        min_sharpe=_env_float("FUNDING_LAB_MIN_SHARPE", 0.25),
        max_validated_drawdown_pct=_env_float("FUNDING_LAB_MAX_VALIDATED_DRAWDOWN_PCT", 35.0),
        overfit_drawdown_pct=_env_float("FUNDING_LAB_OVERFIT_DRAWDOWN_PCT", 50.0),
    )


TECHNICAL_FEATURES = (
    "technical__vsa_signal",
    "technical__market_structure_trend",
    "technical__vwap_distance",
    "price__rsi_14_normalized",
)

OPTIONS_GEX_FEATURES = (
    "options_gex__composite_directional_signal",
    "gamma_flip__directional_signal",
    "tail_risk__directional_signal",
    "dealer_flow__vanna_pressure",
    "shadow_delta__shadow_delta",
)

CRYPTO_MICROSTRUCTURE_FEATURES = (
    "crypto__funding_rate_zscore",
    "crypto__basis_zscore",
    "crypto__open_interest_change_zscore",
    "crypto__taker_buy_sell_ratio_zscore",
    "crypto__realized_volatility_zscore",
)


def run_prediction_backtest(
    *,
    db_path: Path | str,
    module: str,
    symbol: str | None = None,
    n_days: int | str = 5,
    min_abs_signal: float = 0.1,
    limit: int = 50_000,
    fee_bps: float = 2.0,
    slippage_bps: float = 1.0,
    half_spread_bps: float = 0.0,
    funding_notional_exposure: float = FUNDING_DEFAULT_NOTIONAL_EXPOSURE,
    position_size_usd: float | None = None,
    calibration: BacktestCalibration | None = None,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
) -> dict[str, Any]:
    """Run a deterministic directional backtest from predictions + outcomes.

    This intentionally uses the already-backfilled institutional SQLite dataset
    instead of the empty OHLCV V3 DuckDB gate. Each qualifying row is evaluated
    as one round-trip idea: sign(signal) * forward return minus round-trip costs.
    """
    mod = module.strip().lower()
    if mod not in SUPPORTED_MODULES:
        raise ValueError(f"unsupported module {module}")

    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(str(db))

    horizon = _normalize_backtest_horizon(n_days)
    _ensure_indexes(db)
    rows = _load_rows(
        db,
        symbol=symbol,
        horizon=horizon,
        limit=limit,
        include_features=mod != "predictive",
        timestamp_from=timestamp_from,
        timestamp_to=timestamp_to,
    )
    cost = 2.0 * (float(fee_bps) + float(slippage_bps) + float(half_spread_bps)) / 10_000.0
    account_exposure = _bounded_account_exposure(funding_notional_exposure)
    active_calibration = calibration or default_backtest_calibration()
    returns: list[float] = []
    trade_returns: list[float] = []
    signal_values: list[float | None] = []
    signal_sources: set[str] = set()
    source_tiers: list[str] = []
    data_quality_scores: list[float] = []
    missing_components: set[str] = set()
    friction_samples: list[dict[str, float]] = []

    trade_dates: list[str] = []
    for row in rows:
        features = _json_dict(row["features_json"])
        quality = _json_dict(row["source_quality"])
        if mod == "options_gex":
            source_tiers.append(_options_source_tier(features, quality))
            quality_score = _float_or_none(
                features.get("options_gex__data_quality_score")
            ) or _float_or_none(quality.get("options_gex_data_quality_score"))
            if quality_score is not None:
                data_quality_scores.append(max(0.0, min(1.0, quality_score)))
            for component in quality.get("options_gex_missing_components") or []:
                missing_components.add(str(component))
        elif mod == "crypto_microstructure":
            source_tiers.append(_crypto_source_tier(features, quality))
            quality_score = _float_or_none(
                features.get("crypto__data_quality_score")
            ) or _float_or_none(quality.get("crypto_derivatives_data_quality_score"))
            if quality_score is not None:
                data_quality_scores.append(max(0.0, min(1.0, quality_score)))
            for component in quality.get("crypto_derivatives_missing_components") or []:
                missing_components.add(str(component))
        signal, signal_source = _module_signal(mod, row, features)
        signal_values.append(signal)
        if signal is not None and signal_source:
            signal_sources.add(signal_source)
        if signal is None or signal == 0.0 or abs(signal) < min_abs_signal:
            returns.append(0.0)
            continue
        direction = 1.0 if signal > 0 else -1.0
        direction_label = "LONG" if direction > 0 else "SHORT"
        market_return = float(row["outcome_return"] or 0.0)
        entry_price = _entry_price_from_row(row)
        exit_price = entry_price * (1.0 + market_return)
        friction = apply_cfd_friction(
            symbol=str(row["symbol"]),
            entry_price=entry_price,
            exit_price=exit_price,
            direction=direction_label,
            holding_duration_hours=_holding_duration_hours(horizon),
            position_size_usd=position_size_usd,
        )
        raw_trade_return = friction["adjusted_return_pct"] - cost
        net_return = raw_trade_return * account_exposure
        returns.append(net_return)
        trade_returns.append(net_return)
        trade_dates.append(_iso_date_key(row["timestamp"]))
        friction_samples.append(friction)

    curve = SimpleEquityCurve(returns)
    wins = sum(1 for value in trade_returns if value > 0)
    losses = [abs(value) for value in trade_returns if value < 0]
    gains = [value for value in trade_returns if value > 0]
    coverage = sum(1 for value in signal_values if value is not None)
    total_return = sum(trade_returns)
    profit_factor = round(sum(gains) / sum(losses), 4) if losses and gains else None
    sharpe = curve.sharpe()
    max_drawdown = curve.max_drawdown_pct()
    funding_risk_metrics = _compute_funding_risk_metrics(trade_returns, trade_dates, max_drawdown)
    grade = classify_backtest_grade(
        trades=len(trade_returns),
        signal_coverage_pct=round((coverage / len(rows)) * 100.0, 2) if rows else 0.0,
        profit_factor=profit_factor,
        sharpe=sharpe,
        max_drawdown_pct=max_drawdown,
        module=mod,
        min_abs_signal=min_abs_signal,
        n_days=horizon if isinstance(horizon, int) else None,
        trade_frequency_pct=round((len(trade_returns) / len(rows)) * 100.0, 2) if rows else 0.0,
        funding_risk_metrics=funding_risk_metrics,
        calibration=active_calibration,
    )
    source_tier = (
        _dominant_source_tier(source_tiers)
        if mod in {"options_gex", "crypto_microstructure"}
        else None
    )
    if mod == "options_gex":
        grade = _apply_options_source_quality_to_grade(grade, source_tier)
    if mod == "crypto_microstructure":
        grade = _apply_crypto_source_quality_to_grade(grade, source_tier)

    return {
        "module": mod,
        "symbol": symbol.upper().strip() if symbol else "ALL",
        "n_days": horizon if isinstance(horizon, int) else None,
        "horizon": f"{horizon}d" if isinstance(horizon, int) else horizon,
        "rows": len(rows),
        "signal_coverage_pct": round((coverage / len(rows)) * 100.0, 2) if rows else 0.0,
        "trades": len(trade_returns),
        "win_rate": round(wins / len(trade_returns), 4) if trade_returns else None,
        "total_return_pct": round(total_return * 100.0, 4),
        "avg_trade_return_pct": (
            round((total_return / len(trade_returns)) * 100.0, 4) if trade_returns else None
        ),
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "max_drawdown_pct": max_drawdown,
        "module_backtest_grade": grade["grade"],
        "grade_reasons": grade["reasons"],
        "diagnostics": grade["diagnostics"],
        "risk_action": grade["risk_action"],
        "backfill_priority": grade["backfill_priority"],
        "signal_sources": sorted(signal_sources),
        "source_tier": source_tier,
        "data_quality_score": (
            round(sum(data_quality_scores) / len(data_quality_scores), 4)
            if data_quality_scores
            else None
        ),
        "missing_components": sorted(missing_components),
        "funding_risk_metrics": funding_risk_metrics,
        "friction_metrics": _friction_summary(friction_samples),
        "costs": {
            "fee_bps": float(fee_bps),
            "slippage_bps": float(slippage_bps),
            "half_spread_bps": float(half_spread_bps),
            "round_trip_cost_bps": round(cost * 10_000.0, 4),
        },
        "thresholds": {
            "min_abs_signal": float(min_abs_signal),
            "funding_notional_exposure_pct": round(account_exposure * 100.0, 4),
            "position_size_usd": position_size_usd,
            "calibration": active_calibration.to_dict(),
        },
        "data_source": str(db),
    }


def classify_backtest_grade(
    *,
    trades: int,
    signal_coverage_pct: float,
    profit_factor: float | None,
    sharpe: float | None,
    max_drawdown_pct: float | None,
    module: str | None = None,
    min_abs_signal: float | None = None,
    n_days: int | None = None,
    trade_frequency_pct: float | None = None,
    funding_risk_metrics: dict[str, Any] | None = None,
    calibration: BacktestCalibration | None = None,
) -> dict[str, Any]:
    active_calibration = calibration or default_backtest_calibration()
    reasons: list[str] = []
    if trades < active_calibration.min_trades:
        reasons.append(f"sample_size_below_{active_calibration.min_trades}_trades")
    if signal_coverage_pct < active_calibration.min_signal_coverage_pct:
        reasons.append(
            f"signal_coverage_below_{_reason_number(active_calibration.min_signal_coverage_pct)}pct"
        )
    if reasons:
        return {
            "grade": "insufficient_data",
            "reasons": reasons,
            "diagnostics": _grade_diagnostics(reasons),
            "risk_action": "block_until_backfill",
            "backfill_priority": "high",
        }

    if max_drawdown_pct is not None and max_drawdown_pct >= active_calibration.overfit_drawdown_pct:
        reasons = _overfit_reasons(
            module=module,
            min_abs_signal=min_abs_signal,
            n_days=n_days,
            trade_frequency_pct=trade_frequency_pct,
        )
        reasons.extend(_funding_reason_codes(funding_risk_metrics))
        return {
            "grade": "overfit_risk",
            "reasons": reasons,
            "diagnostics": _grade_diagnostics(reasons),
            "risk_action": "reduce_size_and_revalidate",
            "backfill_priority": "high",
        }

    # Funding-rule pre-check: if the simulated history would have breached a
    # standard prop-firm rule (daily-loss or max-loss cap), the edge can never
    # be promoted to `validated` regardless of profit factor / Sharpe.
    funding_reasons = _funding_reason_codes(funding_risk_metrics)
    funding_grade = (funding_risk_metrics or {}).get("funding_survival_grade")
    funding_blocks_validation = funding_grade in {"would_breach", "at_risk"}

    edge_ok = (profit_factor or 0.0) >= active_calibration.min_profit_factor and (
        sharpe or 0.0
    ) >= active_calibration.min_sharpe
    if (
        edge_ok
        and (
            max_drawdown_pct is None
            or max_drawdown_pct <= active_calibration.max_validated_drawdown_pct
        )
        and not funding_blocks_validation
    ):
        validated_reasons = ["profit_factor_and_sharpe_pass", *funding_reasons]
        return {
            "grade": "validated",
            "reasons": validated_reasons,
            "diagnostics": _grade_diagnostics(validated_reasons),
            "risk_action": "allow_normal_risk",
            "backfill_priority": "low",
        }

    weak_reasons: list[str] = []
    if (profit_factor or 0.0) < active_calibration.min_profit_factor:
        weak_reasons.append(
            f"profit_factor_below_{_reason_number(active_calibration.min_profit_factor)}"
        )
    if (sharpe or 0.0) < active_calibration.min_sharpe:
        weak_reasons.append(f"sharpe_below_{_reason_number(active_calibration.min_sharpe)}")
    if (
        max_drawdown_pct is not None
        and max_drawdown_pct > active_calibration.max_validated_drawdown_pct
    ):
        weak_reasons.append(
            f"max_drawdown_above_{_reason_number(active_calibration.max_validated_drawdown_pct)}pct"
        )
    if funding_blocks_validation:
        weak_reasons.extend(funding_reasons)
    if not weak_reasons:
        weak_reasons.append("edge_below_validation_threshold")
    return {
        "grade": "weak_edge",
        "reasons": weak_reasons,
        "diagnostics": _grade_diagnostics(weak_reasons),
        "risk_action": "size_down_or_observe",
        "backfill_priority": "medium",
    }


def run_prediction_backtest_batch(
    *,
    db_path: Path | str,
    symbols: list[str] | tuple[str, ...] | None = None,
    modules: list[str] | tuple[str, ...] | None = None,
    n_days: int | str = 5,
    limit_per_symbol: int = 5_000,
    min_abs_signal_by_module: dict[str, float] | None = None,
    fee_bps: float = 2.0,
    slippage_bps: float = 1.0,
    half_spread_bps: float = 0.0,
    funding_notional_exposure: float = FUNDING_DEFAULT_NOTIONAL_EXPOSURE,
    position_size_usd: float | None = None,
    calibration: BacktestCalibration | None = None,
) -> dict[str, Any]:
    selected_symbols = _normalize_csv_values(symbols or DEFAULT_BATCH_SYMBOLS, upper=True)
    selected_modules = [
        module
        for module in _normalize_csv_values(
            modules or tuple(sorted(SUPPORTED_MODULES)), upper=False
        )
        if module in SUPPORTED_MODULES
    ]
    thresholds = {
        "predictive": 0.1,
        "technical": 0.05,
        "options_gex": 0.05,
        "crypto_microstructure": 0.05,
        **(min_abs_signal_by_module or {}),
    }

    results: list[dict[str, Any]] = []
    for symbol in selected_symbols:
        for module in selected_modules:
            results.append(
                run_prediction_backtest(
                    db_path=db_path,
                    module=module,
                    symbol=symbol,
                    n_days=n_days,
                    min_abs_signal=float(thresholds.get(module, 0.1)),
                    limit=limit_per_symbol,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    half_spread_bps=half_spread_bps,
                    funding_notional_exposure=funding_notional_exposure,
                    position_size_usd=position_size_usd,
                    calibration=calibration,
                )
            )

    ranked = sorted(results, key=_rank_score, reverse=True)
    module_counts: dict[str, dict[str, int]] = {}
    reason_counts: dict[str, int] = {}
    source_tier_counts: dict[str, int] = {}
    missing_component_counts: dict[str, int] = {}
    quality_values: list[float] = []
    funding_grade_counts: dict[str, int] = {}
    consistency_counts: dict[str, int] = {}
    funding_breach_symbols: list[str] = []
    recovery_factors: list[float] = []
    for result in results:
        bucket = module_counts.setdefault(
            str(result["module"]),
            {
                "results": 0,
                "validated": 0,
                "weak_edge": 0,
                "overfit_risk": 0,
                "insufficient_data": 0,
                "low_coverage": 0,
            },
        )
        bucket["results"] += 1
        grade = str(result.get("module_backtest_grade"))
        if grade in bucket:
            bucket[grade] += 1
        if float(result.get("signal_coverage_pct") or 0.0) < 25.0:
            bucket["low_coverage"] += 1
        for reason in result.get("grade_reasons") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
        tier = result.get("source_tier")
        if tier:
            source_tier_counts[str(tier)] = source_tier_counts.get(str(tier), 0) + 1
        quality = _float_or_none(result.get("data_quality_score"))
        if quality is not None:
            quality_values.append(quality)
        for component in result.get("missing_components") or []:
            missing_component_counts[str(component)] = (
                missing_component_counts.get(str(component), 0) + 1
            )
        funding_metrics = result.get("funding_risk_metrics") or {}
        survival = str(funding_metrics.get("funding_survival_grade") or "insufficient_data")
        funding_grade_counts[survival] = funding_grade_counts.get(survival, 0) + 1
        consistency = str(funding_metrics.get("consistency_risk") or "insufficient_data")
        consistency_counts[consistency] = consistency_counts.get(consistency, 0) + 1
        if funding_metrics.get("max_drawdown_breach") or funding_metrics.get(
            "daily_loss_breach_count"
        ):
            funding_breach_symbols.append(f"{result.get('symbol')}:{result.get('module')}")
        rec = _float_or_none(funding_metrics.get("recovery_factor"))
        if rec is not None:
            recovery_factors.append(rec)

    return {
        "symbols": selected_symbols,
        "modules": selected_modules,
        "results": results,
        "ranked": ranked,
        "summary": {
            "symbols_scanned": len(selected_symbols),
            "module_counts": module_counts,
            "reason_counts": reason_counts,
            "source_tier_counts": source_tier_counts,
            "missing_components": missing_component_counts,
            "data_quality_avg": (
                round(sum(quality_values) / len(quality_values), 4) if quality_values else None
            ),
            "validated_results": sum(
                1 for item in results if item.get("module_backtest_grade") == "validated"
            ),
            "blocked_results": sum(
                1 for item in results if item.get("module_backtest_grade") == "insufficient_data"
            ),
            "funding_risk_summary": {
                "survival_grade_counts": funding_grade_counts,
                "consistency_counts": consistency_counts,
                "would_breach_symbols": sorted(set(funding_breach_symbols)),
                "recovery_factor_avg": (
                    round(sum(recovery_factors) / len(recovery_factors), 4)
                    if recovery_factors
                    else None
                ),
                "rule_thresholds": {
                    "daily_loss_limit_pct": FUNDING_DAILY_LOSS_LIMIT_PCT,
                    "max_loss_limit_pct": FUNDING_MAX_LOSS_LIMIT_PCT,
                    "best_day_warn_ratio": FUNDING_BEST_DAY_WARN_RATIO,
                    "best_day_block_ratio": FUNDING_BEST_DAY_BLOCK_RATIO,
                },
                "backtest_calibration": (calibration or default_backtest_calibration()).to_dict(),
            },
        },
        "risk_desk_recommendations": _batch_recommendations(results),
    }


def _rank_score(result: dict[str, Any]) -> float:
    grade_bonus = {
        "validated": 100.0,
        "weak_edge": 40.0,
        "overfit_risk": 10.0,
        "insufficient_data": -100.0,
    }.get(str(result.get("module_backtest_grade")), 0.0)
    source_tier_bonus = {
        "full_chain_gex": 15.0,
        "snapshot_chain": 0.0,
        "light_proxy": -25.0,
    }.get(str(result.get("source_tier")), 0.0)
    return (
        grade_bonus
        + source_tier_bonus
        + float(result.get("data_quality_score") or 0.0) * 10.0
        + float(result.get("profit_factor") or 0.0) * 10.0
        + float(result.get("sharpe") or 0.0) * 5.0
        + float(result.get("total_return_pct") or 0.0) * 0.05
        - float(result.get("max_drawdown_pct") or 0.0) * 0.1
    )


def rank_backtest_result(result: dict[str, Any]) -> float:
    """Score a normalized backtest result for batch ranking."""
    return _rank_score(result)


def _batch_recommendations(results: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    low_gex = [
        item["symbol"]
        for item in results
        if item.get("module") == "options_gex"
        and float(item.get("signal_coverage_pct") or 0.0) < 25.0
    ]
    if low_gex:
        recommendations.append(
            "Options/GEX coverage is too low for: "
            + ", ".join(sorted(set(map(str, low_gex))))
            + "."
        )
    overfit = [
        f"{item['symbol']}:{item['module']}"
        for item in results
        if item.get("module_backtest_grade") == "overfit_risk"
    ]
    if overfit:
        recommendations.append(
            "Risk Desk should cap overfit-risk modules at reduced size: "
            + ", ".join(overfit[:12])
            + "."
        )
    validated = [
        f"{item['symbol']}:{item['module']}"
        for item in results
        if item.get("module_backtest_grade") == "validated"
    ]
    if validated:
        recommendations.append(
            "Validated modules can be allowed normal risk when funding rules also pass: "
            + ", ".join(validated[:12])
            + "."
        )
    funding_would_breach = [
        f"{item['symbol']}:{item['module']}"
        for item in results
        if (item.get("funding_risk_metrics") or {}).get("funding_survival_grade") == "would_breach"
    ]
    if funding_would_breach:
        recommendations.append(
            "Funding-rule simulation flags would-breach scenarios; never authorize live risk for: "
            + ", ".join(sorted(set(funding_would_breach))[:12])
            + "."
        )
    if not recommendations:
        recommendations.append(
            "No validated edge found in this batch; keep Risk Desk in conservative mode."
        )
    return recommendations


def build_backtest_batch_recommendations(results: list[dict[str, Any]]) -> list[str]:
    """Build Risk Desk recommendations for a normalized batch result set."""
    return _batch_recommendations(results)


def _grade_diagnostics(reasons: list[str]) -> list[str]:
    labels = {
        "sample_size_below_30_trades": "Less than 30 realized trades; sample is too small for funding-risk sizing.",
        "signal_coverage_below_25pct": "Signal coverage below 25%; backfill or feature extraction is incomplete.",
        "max_drawdown_above_50pct": "Historical drawdown exceeded 50%; edge is unstable under stress.",
        "max_drawdown_above_35pct": "Drawdown is above the validation cap; reduce size until stability improves.",
        "profit_factor_and_sharpe_pass": "Profit factor and Sharpe pass validation thresholds.",
        "profit_factor_below_1_15": "Profit factor below 1.15; edge is not strong enough for normal risk.",
        "sharpe_below_0_25": "Sharpe below 0.25; return quality is too noisy for normal risk.",
        "edge_below_validation_threshold": "Edge failed validation thresholds.",
        "technical_drawdown_extreme": "Technical module produced extreme historical drawdown; cap size before funding use.",
        "technical_threshold_too_loose": "Technical signal threshold is too loose; too many weak signals are entering the test.",
        "technical_signal_churn": "Technical module trades too frequently; signal churn is increasing drawdown risk.",
        "technical_horizon_mismatch": "Technical signal horizon likely mismatches the 5-day outcome window; retest shorter horizons.",
        "source_tier_not_full_chain_gex": "Options/GEX validation is capped because the source is not full-chain historical GEX.",
        "funding_daily_loss_breach_in_history": (
            "A simulated trading day would have breached the funding daily-loss cap; "
            "this edge cannot be promoted to normal sizing."
        ),
        "funding_max_loss_breach_in_history": (
            "Cumulative drawdown would have crossed the funding max-loss cap; account "
            "would have been terminated before any payout."
        ),
        "funding_concentration_risk_high": (
            "Profit concentration is too high — the best day or top-3 days carry the "
            "majority of total return, which triggers consistency-cap penalties."
        ),
        "funding_recovery_factor_low": (
            "Recovery factor is below 1.0; drawdown is larger than total return — "
            "size must be cut until stability improves."
        ),
    }
    return [labels.get(reason, reason) for reason in reasons]


def _iso_date_key(value: object) -> str:
    """Return a YYYY-MM-DD key for any sqlite timestamp value (or empty string)."""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        # Tolerate both 'YYYY-MM-DDTHH:MM:SS' and 'YYYY-MM-DD HH:MM:SS'
        return text[:10]
    return str(value)[:10]


def iso_date_key(value: object) -> str:
    """Return a YYYY-MM-DD key for a prediction timestamp."""
    return _iso_date_key(value)


def _bounded_account_exposure(value: float) -> float:
    """Clamp simulated funding notional exposure to a sane account-return range."""
    if not math.isfinite(float(value)):
        return FUNDING_DEFAULT_NOTIONAL_EXPOSURE
    return max(0.0, min(float(value), 1.0))


def bounded_account_exposure(value: float) -> float:
    """Clamp simulated funding notional exposure to a sane account-return range."""
    return _bounded_account_exposure(value)


def _compute_funding_risk_metrics(
    trade_returns: list[float],
    trade_dates: list[str],
    max_drawdown_pct: float | None,
) -> dict[str, Any]:
    """Aggregate per-trade returns into funding-account survival metrics.

    Returns a dictionary that is safe to JSON-serialize. When no trades are
    present we emit ``funding_survival_grade='insufficient_data'`` rather than
    silently filling zeros — this preserves the survival-first rule that an
    absence of evidence is never converted into permission.
    """
    if not trade_returns:
        return {
            "trading_days": 0,
            "best_day_pct": None,
            "worst_day_pct": None,
            "best_day_contribution_pct": None,
            "top3_days_contribution_pct": None,
            "profit_concentration_hhi": None,
            "avg_adverse_excursion_pct": None,
            "recovery_factor": None,
            "daily_loss_breach_count": 0,
            "daily_loss_breach_rate_pct": 0.0,
            "max_drawdown_breach": False,
            "consistency_risk": "insufficient_data",
            "funding_survival_grade": "insufficient_data",
            "funding_rule_thresholds": {
                "daily_loss_limit_pct": FUNDING_DAILY_LOSS_LIMIT_PCT,
                "max_loss_limit_pct": FUNDING_MAX_LOSS_LIMIT_PCT,
                "best_day_warn_ratio": FUNDING_BEST_DAY_WARN_RATIO,
                "best_day_block_ratio": FUNDING_BEST_DAY_BLOCK_RATIO,
            },
        }

    # Aggregate trade returns into calendar-day P&L. Trades without a parseable
    # date fall into a synthetic "unknown" bucket; we still account for them so
    # nothing is silently dropped, but we surface that via trading_days.
    daily_pnl: dict[str, float] = {}
    for i, ret in enumerate(trade_returns):
        key = trade_dates[i] if i < len(trade_dates) and trade_dates[i] else f"__unknown_{i}"
        daily_pnl[key] = daily_pnl.get(key, 0.0) + float(ret)

    day_values = list(daily_pnl.values())
    trading_days = len(day_values)
    best_day = max(day_values)
    worst_day = min(day_values)
    total_return = sum(day_values)
    positive_days = [v for v in day_values if v > 0]
    total_positive = sum(positive_days)

    # Best-day & top-3 contributions (funding consistency cap proxy).
    if total_positive > 0:
        best_day_contrib = max(positive_days) / total_positive
        top3 = sorted(positive_days, reverse=True)[:3]
        top3_contrib = sum(top3) / total_positive
        # HHI on positive-day shares — 1.0 means a single day carries all profit.
        shares = [v / total_positive for v in positive_days]
        hhi = sum(s * s for s in shares)
    else:
        best_day_contrib = None
        top3_contrib = None
        hhi = None

    # Daily-loss breach simulation: count days whose loss exceeds the cap.
    daily_loss_cap = FUNDING_DAILY_LOSS_LIMIT_PCT / 100.0
    breach_days = sum(1 for v in day_values if v <= -daily_loss_cap)
    breach_rate = breach_days / trading_days if trading_days else 0.0

    # Max-loss breach simulation against the curve's peak-to-trough drawdown.
    max_loss_cap = FUNDING_MAX_LOSS_LIMIT_PCT
    max_loss_breach = (max_drawdown_pct or 0.0) >= max_loss_cap

    # Adverse excursion proxy: mean of losing trades.
    losing = [r for r in trade_returns if r < 0]
    avg_adverse = sum(losing) / len(losing) if losing else 0.0

    recovery_factor: float | None
    if max_drawdown_pct and max_drawdown_pct > 0.0:
        recovery_factor = round((total_return * 100.0) / max_drawdown_pct, 4)
    else:
        recovery_factor = None

    # Consistency risk grading using the same thresholds the Risk Desk enforces.
    if best_day_contrib is None:
        consistency = "insufficient_data"
    elif best_day_contrib >= FUNDING_BEST_DAY_BLOCK_RATIO:
        consistency = "blocked"
    elif best_day_contrib >= FUNDING_BEST_DAY_WARN_RATIO:
        consistency = "warning"
    else:
        consistency = "ok"

    # Overall survival grade — strictly worse than 'safe' if any rule trips.
    # Any hard breach (daily-loss, max-loss, consistency block) is treated as
    # 'would_breach' — never softened below that even with strong P&L stats.
    if max_loss_breach or breach_days > 0 or consistency == "blocked":
        survival = "would_breach"
    elif consistency == "warning":
        survival = "at_risk"
    elif (recovery_factor is not None and recovery_factor < 1.0) or breach_rate > 0.0:
        survival = "monitor"
    else:
        survival = "safe"

    return {
        "trading_days": trading_days,
        "best_day_pct": round(best_day * 100.0, 4),
        "worst_day_pct": round(worst_day * 100.0, 4),
        "best_day_contribution_pct": (
            round(best_day_contrib * 100.0, 2) if best_day_contrib is not None else None
        ),
        "top3_days_contribution_pct": (
            round(top3_contrib * 100.0, 2) if top3_contrib is not None else None
        ),
        "profit_concentration_hhi": round(hhi, 4) if hhi is not None else None,
        "avg_adverse_excursion_pct": round(avg_adverse * 100.0, 4),
        "recovery_factor": recovery_factor,
        "daily_loss_breach_count": int(breach_days),
        "daily_loss_breach_rate_pct": round(breach_rate * 100.0, 4),
        "max_drawdown_breach": bool(max_loss_breach),
        "consistency_risk": consistency,
        "funding_survival_grade": survival,
        "funding_rule_thresholds": {
            "daily_loss_limit_pct": FUNDING_DAILY_LOSS_LIMIT_PCT,
            "max_loss_limit_pct": FUNDING_MAX_LOSS_LIMIT_PCT,
            "best_day_warn_ratio": FUNDING_BEST_DAY_WARN_RATIO,
            "best_day_block_ratio": FUNDING_BEST_DAY_BLOCK_RATIO,
        },
    }


def compute_prediction_funding_risk_metrics(
    trade_returns: list[float],
    trade_dates: list[str],
    max_drawdown_pct: float | None,
) -> dict[str, Any]:
    """Aggregate per-trade returns into funding-account survival metrics."""
    return _compute_funding_risk_metrics(trade_returns, trade_dates, max_drawdown_pct)


def _funding_reason_codes(metrics: dict[str, Any] | None) -> list[str]:
    """Translate funding-risk metrics into stable reason codes for the grade."""
    if not metrics:
        return []
    codes: list[str] = []
    if metrics.get("daily_loss_breach_count"):
        codes.append("funding_daily_loss_breach_in_history")
    if metrics.get("max_drawdown_breach"):
        codes.append("funding_max_loss_breach_in_history")
    if metrics.get("consistency_risk") in {"warning", "blocked"}:
        codes.append("funding_concentration_risk_high")
    recovery = metrics.get("recovery_factor")
    if isinstance(recovery, int | float) and recovery < 1.0:
        codes.append("funding_recovery_factor_low")
    return codes


def _apply_options_source_quality_to_grade(
    grade: dict[str, Any], source_tier: str | None
) -> dict[str, Any]:
    if grade.get("grade") != "validated" or source_tier == "full_chain_gex":
        return grade
    reasons = [*grade.get("reasons", []), "source_tier_not_full_chain_gex"]
    return {
        "grade": "weak_edge",
        "reasons": reasons,
        "diagnostics": _grade_diagnostics(reasons),
        "risk_action": "size_down_or_observe",
        "backfill_priority": "medium",
    }


def apply_options_source_quality_to_grade(
    grade: dict[str, Any], source_tier: str | None
) -> dict[str, Any]:
    """Apply the options/GEX source-tier cap to a normalized grade payload."""
    return _apply_options_source_quality_to_grade(grade, source_tier)


def _apply_crypto_source_quality_to_grade(
    grade: dict[str, Any], source_tier: str | None
) -> dict[str, Any]:
    if grade.get("grade") != "validated" or source_tier in {
        "binance_public_derivatives",
        "validated_crypto_derivatives",
    }:
        return grade
    reasons = [*grade.get("reasons", []), "crypto_derivatives_unvalidated"]
    return {
        "grade": "weak_edge",
        "reasons": reasons,
        "diagnostics": _grade_diagnostics(reasons),
        "risk_action": "size_down_or_observe",
        "backfill_priority": "medium",
    }


def _overfit_reasons(
    *,
    module: str | None,
    min_abs_signal: float | None,
    n_days: int | None,
    trade_frequency_pct: float | None,
) -> list[str]:
    if module == "technical":
        reasons = ["technical_drawdown_extreme"]
        if min_abs_signal is not None and min_abs_signal < 0.10:
            reasons.append("technical_threshold_too_loose")
        if trade_frequency_pct is not None and trade_frequency_pct >= 75.0:
            reasons.append("technical_signal_churn")
        if n_days is not None and n_days >= 5:
            reasons.append("technical_horizon_mismatch")
        return reasons
    return ["max_drawdown_above_50pct"]


def _normalize_csv_values(values: list[str] | tuple[str, ...], *, upper: bool) -> list[str]:
    out: list[str] = []
    for value in values:
        for part in str(value).split(","):
            cleaned = part.strip()
            if cleaned:
                out.append(cleaned.upper() if upper else cleaned.lower())
    return out


def normalize_csv_values(values: list[str] | tuple[str, ...], *, upper: bool) -> list[str]:
    """Normalize list/CSV values with optional uppercase coercion."""
    return _normalize_csv_values(values, upper=upper)


def _normalize_backtest_horizon(value: int | str) -> int | str:
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in INTRADAY_HORIZONS:
            return cleaned
        if cleaned.endswith("d"):
            cleaned = cleaned[:-1]
        return int(cleaned)
    return int(value)


def normalize_backtest_horizon(value: int | str) -> int | str:
    """Normalize day/intraday backtest horizons."""
    return _normalize_backtest_horizon(value)


def _holding_duration_hours(horizon: int | str) -> float:
    if horizon == "1h":
        return 1.0
    if horizon == "4h":
        return 4.0
    if horizon == "eod":
        return 6.25
    return float(int(horizon)) * 24.0


def holding_duration_hours(horizon: int | str) -> float:
    """Return the holding duration in hours for a normalized horizon."""
    return _holding_duration_hours(horizon)


def _entry_price_from_row(row: sqlite3.Row) -> float:
    value = _float_or_none(row["price_t0"])
    return value if value is not None and value > 0.0 else 1.0


def entry_price_from_row(row: sqlite3.Row) -> float:
    """Return a positive entry price for one historical prediction row."""
    return _entry_price_from_row(row)


def _friction_summary(samples: list[dict[str, float]]) -> dict[str, Any]:
    keys = (
        "spread_cost_pct",
        "slippage_cost_pct",
        "overnight_financing_cost_pct",
        "total_friction_pct",
    )
    if not samples:
        return {
            "applied": True,
            "trade_count": 0,
            "avg_spread_cost_pct": 0.0,
            "avg_slippage_cost_pct": 0.0,
            "avg_overnight_financing_cost_pct": 0.0,
            "avg_total_friction_pct": 0.0,
            "total_friction_pct": 0.0,
            "max_total_friction_pct": 0.0,
        }

    count = len(samples)
    summary: dict[str, Any] = {
        "applied": True,
        "trade_count": count,
        "total_friction_pct": round(sum(item["total_friction_pct"] for item in samples), 8),
        "max_total_friction_pct": round(max(item["total_friction_pct"] for item in samples), 8),
    }
    for key in keys:
        summary[f"avg_{key}"] = round(sum(item[key] for item in samples) / count, 8)
    return summary


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def default_oos_backtest_gate() -> OosBacktestGate:
    return OosBacktestGate(
        min_folds=_env_int("SCANNER_OOS_MIN_FOLDS", 2),
        min_oos_rows_per_fold=_env_int("SCANNER_OOS_MIN_ROWS_PER_FOLD", 15),
        min_mean_oos_sharpe=_env_float("SCANNER_OOS_MIN_MEAN_SHARPE", 0.15),
        min_mean_oos_profit_factor=_env_float("SCANNER_OOS_MIN_MEAN_PF", 1.05),
        require_non_negative_oos_return=os.getenv("SCANNER_OOS_REQUIRE_NON_NEGATIVE_RETURN", "true")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"},
    )


def _reason_number(value: float) -> str:
    text = str(int(value)) if float(value).is_integer() else f"{value:.2f}"
    return text.replace(".", "_")


def _load_rows(
    db_path: Path,
    *,
    symbol: str | None,
    horizon: int | str,
    limit: int,
    include_features: bool,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
) -> list[sqlite3.Row]:
    if isinstance(horizon, str):
        return _load_intraday_rows(
            db_path,
            symbol=symbol,
            horizon=horizon,
            limit=limit,
            include_features=include_features,
            timestamp_from=timestamp_from,
            timestamp_to=timestamp_to,
        )
    clauses = ["o.n_days = ?", "o.outcome_return IS NOT NULL"]
    params: list[Any] = [int(horizon)]
    if symbol:
        clauses.append("p.symbol = ?")
        params.append(symbol.upper().strip())
    if timestamp_from:
        clauses.append("p.timestamp >= ?")
        params.append(timestamp_from)
    if timestamp_to:
        clauses.append("p.timestamp < ?")
        params.append(timestamp_to)
    params.append(max(1, min(int(limit), 250_000)))

    feature_select = (
        "fs.features_json, fs.source_quality"
        if include_features
        else "NULL AS features_json, NULL AS source_quality"
    )
    feature_join = (
        "LEFT JOIN feature_snapshots fs ON fs.prediction_id = p.prediction_id"
        if include_features
        else ""
    )
    query = f"""
        SELECT p.prediction_id, p.symbol, p.timestamp, p.direction, p.signal,
               p.confidence, p.should_trade, p.conflict_score, p.price_t0,
               o.outcome_return, {feature_select}
        FROM predictions p
        JOIN outcomes o ON o.prediction_id = p.prediction_id
        {feature_join}
        WHERE {" AND ".join(clauses)}
        ORDER BY p.timestamp ASC
        LIMIT ?
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return list(con.execute(query, params))
    finally:
        con.close()


def load_prediction_backtest_rows(
    db_path: Path,
    *,
    symbol: str | None,
    horizon: int | str,
    limit: int,
    include_features: bool,
) -> list[sqlite3.Row]:
    """Load the shared historical prediction/outcome dataset."""
    return _load_rows(
        db_path,
        symbol=symbol,
        horizon=horizon,
        limit=limit,
        include_features=include_features,
    )


def _load_intraday_rows(
    db_path: Path,
    *,
    symbol: str | None,
    horizon: str,
    limit: int,
    include_features: bool,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
) -> list[sqlite3.Row]:
    outcome_column = INTRADAY_HORIZONS[horizon]
    clauses = [f"p.{outcome_column} IS NOT NULL"]
    params: list[Any] = []
    if symbol:
        clauses.append("p.symbol = ?")
        params.append(symbol.upper().strip())
    if timestamp_from:
        clauses.append("p.timestamp >= ?")
        params.append(timestamp_from)
    if timestamp_to:
        clauses.append("p.timestamp < ?")
        params.append(timestamp_to)
    params.append(max(1, min(int(limit), 250_000)))
    feature_select = (
        "fs.features_json, fs.source_quality"
        if include_features
        else "NULL AS features_json, NULL AS source_quality"
    )
    feature_join = (
        "LEFT JOIN feature_snapshots fs ON fs.prediction_id = p.prediction_id"
        if include_features
        else ""
    )
    query = f"""
        SELECT p.prediction_id, p.symbol, p.timestamp, p.direction, p.signal,
               p.confidence, p.should_trade, p.conflict_score, p.price_t0,
               p.{outcome_column} AS outcome_return, {feature_select}
        FROM predictions p
        {feature_join}
        WHERE {" AND ".join(clauses)}
        ORDER BY p.timestamp ASC
        LIMIT ?
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(predictions)").fetchall()}
        if outcome_column not in columns:
            return []
        return list(con.execute(query, params))
    finally:
        con.close()


def _ensure_indexes(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_predictions_symbol_timestamp "
            "ON predictions(symbol, timestamp)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_outcomes_prediction_days "
            "ON outcomes(prediction_id, n_days)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_feature_snapshots_prediction "
            "ON feature_snapshots(prediction_id)"
        )
        con.commit()
    finally:
        con.close()


def ensure_prediction_backtest_indexes(db_path: Path) -> None:
    """Create the idempotent indexes used by prediction backtests."""
    _ensure_indexes(db_path)


def _module_signal(
    module: str, row: sqlite3.Row, features: dict[str, Any]
) -> tuple[float | None, str | None]:
    if module == "predictive":
        return _float_or_none(row["signal"]), "prediction_signal"
    if module == "technical":
        return _mean_feature_signal(features, TECHNICAL_FEATURES), "technical_features"
    if module == "options_gex":
        full_chain_available = (
            _float_or_none(features.get("options__full_chain_available")) or 0.0
        ) > 0.0
        if full_chain_available:
            full_chain_signal = _options_full_chain_signal(features)
            if full_chain_signal is not None:
                return full_chain_signal, "options_gex_full_chain"
        return _options_light_proxy_signal(features), "options_light_proxy"
    if module == "crypto_microstructure":
        signal = _mean_feature_signal(features, CRYPTO_MICROSTRUCTURE_FEATURES)
        return signal, "crypto_derivatives_binance" if signal is not None else None
    return None, None


def module_signal(
    module: str, row: sqlite3.Row, features: dict[str, Any]
) -> tuple[float | None, str | None]:
    """Extract the module signal from one historical prediction row."""
    return _module_signal(module, row, features)


def _crypto_source_tier(features: dict[str, Any], quality: dict[str, Any]) -> str:
    raw = str(quality.get("crypto_derivatives_source_tier") or "").strip()
    if raw in {"binance_public_derivatives", "validated_crypto_derivatives"}:
        return raw
    if bool(quality.get("crypto_derivatives")):
        return "binance_public_derivatives"
    available = _float_or_none(features.get("crypto__derivatives_available")) or 0.0
    if available > 0.0:
        return "binance_public_derivatives"
    return "unvalidated_crypto_derivatives"


def _options_source_tier(features: dict[str, Any], quality: dict[str, Any]) -> str:
    raw = str(quality.get("options_gex_source_tier") or "").strip()
    if raw in {"light_proxy", "snapshot_chain", "full_chain_gex"}:
        return raw
    score = _float_or_none(features.get("options_gex__source_tier_score"))
    if score is not None:
        if score >= 0.95:
            return "full_chain_gex"
        if score >= 0.55:
            return "snapshot_chain"
    if bool(quality.get("options_full")):
        return "snapshot_chain"
    if bool(quality.get("options_light")) or bool(quality.get("options_light_rehydrated")):
        return "light_proxy"
    return "light_proxy"


def options_source_tier(features: dict[str, Any], quality: dict[str, Any]) -> str:
    """Classify the source tier for an options/GEX historical row."""
    return _options_source_tier(features, quality)


def _dominant_source_tier(tiers: list[str]) -> str | None:
    if not tiers:
        return None
    weights = {"light_proxy": 1, "snapshot_chain": 2, "full_chain_gex": 3}
    counts: dict[str, int] = {}
    for tier in tiers:
        counts[tier] = counts.get(tier, 0) + 1
    return max(counts, key=lambda tier: (counts[tier], weights.get(tier, 0)))


def dominant_source_tier(tiers: list[str]) -> str | None:
    """Reduce row-level source tiers to the dominant batch tier."""
    return _dominant_source_tier(tiers)


def _options_full_chain_signal(features: dict[str, Any]) -> float | None:
    values: list[float] = []
    for key in OPTIONS_GEX_FEATURES:
        value = _float_or_none(features.get(key))
        if value is None:
            continue
        values.append(max(-1.0, min(1.0, math.tanh(value) if abs(value) > 1.0 else value)))
    if not values:
        return None
    full_chain_available = _float_or_none(features.get("options__full_chain_available")) or 0.0
    if full_chain_available <= 0.0 and all(abs(value) < 1e-9 for value in values):
        return None
    return sum(values) / len(values)


def _options_light_proxy_signal(features: dict[str, Any]) -> float | None:
    canonical_proxy = _mean_feature_signal(features, OPTIONS_GEX_FEATURES)
    if canonical_proxy is not None:
        return canonical_proxy
    values: list[float] = []
    skew = _float_or_none(features.get("options__put_call_iv_skew"))
    iv_hv_spread = _float_or_none(features.get("options__iv_hv_spread_20d"))
    iv_rank = _float_or_none(features.get("options__iv_rank_252d"))
    iv_percentile = _float_or_none(features.get("options__iv_percentile_252d"))
    if skew is not None:
        values.append(-math.tanh(skew * 4.0))
    if iv_hv_spread is not None:
        values.append(-math.tanh(iv_hv_spread * 3.0))
    if iv_rank is not None:
        values.append(-math.tanh((iv_rank - 0.5) * 2.0))
    if iv_percentile is not None:
        values.append(-math.tanh((iv_percentile - 0.5) * 2.0))
    if not values:
        return None
    return sum(values) / len(values)


def _mean_feature_signal(features: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for key in keys:
        value = _float_or_none(features.get(key))
        if value is None:
            continue
        values.append(max(-1.0, min(1.0, math.tanh(value) if abs(value) > 1.0 else value)))
    if not values:
        return None
    return sum(values) / len(values)


def _json_dict(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_dict(raw: object) -> dict[str, Any]:
    """Parse a JSON object field, returning an empty dict for invalid input."""
    return _json_dict(raw)


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def float_or_none(value: object) -> float | None:
    """Parse a finite float, returning None for invalid input."""
    return _float_or_none(value)


def evaluate_oos_gate(
    fold_results: list[dict[str, Any]],
    *,
    gate: OosBacktestGate | None = None,
) -> dict[str, Any]:
    """Promotion gate from chronological walk-forward OOS folds."""
    active = gate or default_oos_backtest_gate()
    reasons: list[str] = []
    usable = [f for f in fold_results if int(f.get("oos_rows", 0)) >= active.min_oos_rows_per_fold]
    if len(usable) < active.min_folds:
        reasons.append(f"folds_below_{active.min_folds}")
        return {
            "passed": False,
            "grade": "insufficient_data",
            "reasons": reasons,
            "folds_evaluated": len(fold_results),
            "folds_usable": len(usable),
            "thresholds": active.to_dict(),
        }

    sharpes = [float(f["sharpe"]) for f in usable if f.get("sharpe") is not None]
    pfs = [float(f["profit_factor"]) for f in usable if f.get("profit_factor") is not None]
    returns = [
        float(f["total_return_pct"]) for f in usable if f.get("total_return_pct") is not None
    ]
    mean_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0.0
    mean_pf = sum(pfs) / len(pfs) if pfs else 0.0
    mean_return = sum(returns) / len(returns) if returns else 0.0

    if mean_sharpe < active.min_mean_oos_sharpe:
        reasons.append(f"mean_oos_sharpe_below_{_reason_number(active.min_mean_oos_sharpe)}")
    if mean_pf < active.min_mean_oos_profit_factor:
        reasons.append(
            f"mean_oos_profit_factor_below_{_reason_number(active.min_mean_oos_profit_factor)}"
        )
    if active.require_non_negative_oos_return and mean_return < 0:
        reasons.append("mean_oos_return_negative")

    passed = not reasons
    return {
        "passed": passed,
        "grade": "validated" if passed else "weak_edge",
        "reasons": reasons or ["oos_walk_forward_pass"],
        "folds_evaluated": len(fold_results),
        "folds_usable": len(usable),
        "mean_oos_sharpe": round(mean_sharpe, 4),
        "mean_oos_profit_factor": round(mean_pf, 4) if pfs else None,
        "mean_oos_total_return_pct": round(mean_return, 4),
        "thresholds": active.to_dict(),
    }


def run_walk_forward_oos_backtest(
    *,
    db_path: Path | str,
    module: str,
    symbol: str | None = None,
    n_days: int | str = 5,
    min_abs_signal: float = 0.1,
    limit: int = 50_000,
    n_folds: int = 3,
    oos_fraction: float = 0.25,
    fee_bps: float = 2.0,
    slippage_bps: float = 1.0,
    half_spread_bps: float = 0.0,
    funding_notional_exposure: float = FUNDING_DEFAULT_NOTIONAL_EXPOSURE,
    calibration: BacktestCalibration | None = None,
    oos_gate: OosBacktestGate | None = None,
) -> dict[str, Any]:
    """Chronological walk-forward: evaluate only the OOS tail of each fold."""
    db = Path(db_path)
    horizon = _normalize_backtest_horizon(n_days)
    rows = load_prediction_backtest_rows(
        db,
        symbol=symbol,
        horizon=horizon,
        limit=limit,
        include_features=module.strip().lower() != "predictive",
    )
    if len(rows) < 10:
        return {
            "module": module.strip().lower(),
            "symbol": symbol.upper().strip() if symbol else "ALL",
            "horizon": horizon,
            "rows": len(rows),
            "folds": [],
            "walk_forward_summary": {"folds_evaluated": 0},
            "oos_gate": evaluate_oos_gate([], gate=oos_gate),
        }

    timestamps = sorted({str(r["timestamp"]) for r in rows})
    n_folds = max(2, min(int(n_folds), 6))
    oos_fraction = max(0.1, min(float(oos_fraction), 0.5))
    fold_size = max(1, len(timestamps) // n_folds)
    fold_results: list[dict[str, Any]] = []

    for fold_idx in range(n_folds):
        start_i = fold_idx * fold_size
        end_i = len(timestamps) if fold_idx == n_folds - 1 else (fold_idx + 1) * fold_size
        if start_i >= end_i:
            continue
        fold_ts = timestamps[start_i:end_i]
        oos_count = max(1, int(len(fold_ts) * oos_fraction))
        oos_from = fold_ts[-oos_count]
        oos_to = fold_ts[-1]
        # Include rows through end of fold (inclusive upper bound via next ts if exists)
        next_idx = end_i
        ts_to = timestamps[next_idx] if next_idx < len(timestamps) else None

        oos_result = run_prediction_backtest(
            db_path=db,
            module=module,
            symbol=symbol,
            n_days=horizon,
            min_abs_signal=min_abs_signal,
            limit=limit,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            half_spread_bps=half_spread_bps,
            funding_notional_exposure=funding_notional_exposure,
            calibration=calibration,
            timestamp_from=oos_from,
            timestamp_to=ts_to,
        )
        fold_results.append(
            {
                "fold_index": fold_idx,
                "timestamp_from": oos_from,
                "timestamp_to": oos_to,
                "oos_rows": oos_result.get("rows", 0),
                "trades": oos_result.get("trades", 0),
                "sharpe": oos_result.get("sharpe"),
                "profit_factor": oos_result.get("profit_factor"),
                "total_return_pct": oos_result.get("total_return_pct"),
                "module_backtest_grade": oos_result.get("module_backtest_grade"),
            }
        )

    gate_result = evaluate_oos_gate(fold_results, gate=oos_gate)
    validated_folds = sum(1 for f in fold_results if f.get("module_backtest_grade") == "validated")
    return {
        "module": module.strip().lower(),
        "symbol": symbol.upper().strip() if symbol else "ALL",
        "horizon": horizon if isinstance(horizon, str) else f"{horizon}d",
        "rows": len(rows),
        "n_folds": n_folds,
        "oos_fraction": oos_fraction,
        "folds": fold_results,
        "walk_forward_summary": {
            "horizons": [horizon] if isinstance(horizon, str) else [f"{horizon}d"],
            "folds_evaluated": len(fold_results),
            "validated_folds": validated_folds,
            "results_evaluated": len(fold_results),
        },
        "oos_gate": gate_result,
    }
