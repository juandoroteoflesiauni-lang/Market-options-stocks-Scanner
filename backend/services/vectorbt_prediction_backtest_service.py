"""Optional vectorbt engine over the institutional prediction SQLite dataset."""

from __future__ import annotations

import importlib
import math
from pathlib import Path
from typing import Any

import pandas as pd

from backend.backtesting.base import SimpleEquityCurve
from backend.config.logger_setup import get_logger
from backend.services.cfd_friction_simulator import apply_cfd_friction
from backend.services.prediction_backtest_service import (
    DEFAULT_BATCH_SYMBOLS,
    FUNDING_DEFAULT_NOTIONAL_EXPOSURE,
    SUPPORTED_MODULES,
    BacktestCalibration,
    apply_options_source_quality_to_grade,
    bounded_account_exposure,
    build_backtest_batch_recommendations,
    classify_backtest_grade,
    compute_prediction_funding_risk_metrics,
    default_backtest_calibration,
    dominant_source_tier,
    ensure_prediction_backtest_indexes,
    entry_price_from_row,
    float_or_none,
    holding_duration_hours,
    iso_date_key,
    json_dict,
    load_prediction_backtest_rows,
    module_signal,
    normalize_backtest_horizon,
    normalize_csv_values,
    options_source_tier,
    rank_backtest_result,
)

logger = get_logger(__name__)


class VectorBTUnavailableError(RuntimeError):
    """Raised when the optional vectorbt dependency is not installed."""


def _load_vectorbt() -> Any:
    try:
        return importlib.import_module("vectorbt")
    except ImportError as exc:
        raise VectorBTUnavailableError(
            "vectorbt is optional. Install the research extra to enable this backtest engine."
        ) from exc


def run_vectorbt_prediction_backtest(
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
) -> dict[str, Any]:
    """Run the optional vectorbt engine and normalize to the legacy evidence contract."""
    vbt = _load_vectorbt()
    mod = "predictive" if module.strip().lower() == "probabilistic" else module.strip().lower()
    if mod not in SUPPORTED_MODULES:
        raise ValueError(f"unsupported module {module}")

    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(str(db))

    horizon = normalize_backtest_horizon(n_days)
    ensure_prediction_backtest_indexes(db)
    rows = load_prediction_backtest_rows(
        db,
        symbol=symbol,
        horizon=horizon,
        limit=limit,
        include_features=mod != "predictive",
    )
    account_exposure = bounded_account_exposure(funding_notional_exposure)
    active_calibration = calibration or default_backtest_calibration()

    signals: list[float | None] = []
    trade_returns: list[float] = []
    strategy_returns: list[float] = []
    trade_dates: list[str] = []
    signal_sources: set[str] = set()
    source_tiers: list[str] = []
    data_quality_scores: list[float] = []
    missing_components: set[str] = set()
    current_price = entry_price_from_row(rows[0]) if rows else 1.0
    prices: list[float] = [current_price]
    timestamps: list[object] = [rows[0]["timestamp"] if rows else "1970-01-01T00:00:00"]
    entries: list[bool] = [False] * (len(rows) + 1)
    exits: list[bool] = [False] * (len(rows) + 1)
    short_entries: list[bool] = [False] * (len(rows) + 1)
    short_exits: list[bool] = [False] * (len(rows) + 1)

    cost = 2.0 * (float(fee_bps) + float(slippage_bps) + float(half_spread_bps)) / 10_000.0
    for idx, row in enumerate(rows):
        features = json_dict(row["features_json"])
        quality = json_dict(row["source_quality"])
        if mod == "options_gex":
            source_tiers.append(options_source_tier(features, quality))
            quality_score = float_or_none(features.get("options_gex__data_quality_score"))
            if quality_score is None:
                quality_score = float_or_none(quality.get("options_gex_data_quality_score"))
            if quality_score is not None:
                data_quality_scores.append(max(0.0, min(1.0, quality_score)))
            for component in quality.get("options_gex_missing_components") or []:
                missing_components.add(str(component))

        signal, signal_source = module_signal(mod, row, features)
        signals.append(signal)
        if signal is not None and signal_source:
            signal_sources.add(signal_source)

        active = signal is not None and signal != 0.0 and abs(signal) >= min_abs_signal
        long_entry = bool(active and signal is not None and signal > 0.0)
        short_entry = bool(active and signal is not None and signal < 0.0)
        entries[idx] = long_entry
        short_entries[idx] = short_entry

        market_return = float(row["outcome_return"] or 0.0)
        if active:
            entry_price = entry_price_from_row(row)
            direction = 1.0 if signal and signal > 0.0 else -1.0
            direction_label = "LONG" if direction > 0.0 else "SHORT"
            exit_price = entry_price * (1.0 + market_return)
            friction = apply_cfd_friction(
                symbol=str(row["symbol"]),
                entry_price=entry_price,
                exit_price=exit_price,
                direction=direction_label,
                holding_duration_hours=holding_duration_hours(horizon),
                position_size_usd=position_size_usd,
            )
            raw_trade_return = friction["adjusted_return_pct"] - cost
            net_return = raw_trade_return * account_exposure
            trade_returns.append(net_return)
            trade_dates.append(iso_date_key(row["timestamp"]))
            strategy_returns.append(net_return)
        else:
            strategy_returns.append(0.0)

        current_price = current_price * (1.0 + market_return)
        prices.append(current_price)
        timestamps.append(row["timestamp"])
        exits[idx + 1] = long_entry
        short_exits[idx + 1] = short_entry

    portfolio = _build_vectorbt_portfolio(
        vbt,
        prices=prices,
        timestamps=timestamps,
        entries=entries,
        exits=exits,
        short_entries=short_entries,
        short_exits=short_exits,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        half_spread_bps=half_spread_bps,
    )

    curve = SimpleEquityCurve(strategy_returns)
    wins = sum(1 for value in trade_returns if value > 0.0)
    losses = [abs(value) for value in trade_returns if value < 0.0]
    gains = [value for value in trade_returns if value > 0.0]
    coverage = sum(1 for value in signals if value is not None)
    total_return = _portfolio_total_return(portfolio)
    if total_return is None:
        total_return = sum(trade_returns)
    profit_factor = _profit_factor(gains, losses)
    sharpe = _portfolio_sharpe(portfolio)
    if sharpe is None:
        sharpe = curve.sharpe()
    max_drawdown = _portfolio_max_drawdown_pct(portfolio)
    if max_drawdown is None:
        max_drawdown = curve.max_drawdown_pct()

    funding_risk_metrics = compute_prediction_funding_risk_metrics(
        trade_returns, trade_dates, max_drawdown
    )
    rows_count = len(rows)
    signal_coverage_pct = round((coverage / rows_count) * 100.0, 2) if rows_count else 0.0
    trade_frequency_pct = round((len(trade_returns) / rows_count) * 100.0, 2) if rows_count else 0.0
    grade = classify_backtest_grade(
        trades=len(trade_returns),
        signal_coverage_pct=signal_coverage_pct,
        profit_factor=profit_factor,
        sharpe=sharpe,
        max_drawdown_pct=max_drawdown,
        module=mod,
        min_abs_signal=min_abs_signal,
        n_days=horizon if isinstance(horizon, int) else None,
        trade_frequency_pct=trade_frequency_pct,
        funding_risk_metrics=funding_risk_metrics,
        calibration=active_calibration,
    )
    source_tier = dominant_source_tier(source_tiers) if mod == "options_gex" else None
    if mod == "options_gex":
        grade = apply_options_source_quality_to_grade(grade, source_tier)

    return {
        "module": mod,
        "symbol": symbol.upper().strip() if symbol else "ALL",
        "n_days": horizon if isinstance(horizon, int) else None,
        "horizon": f"{horizon}d" if isinstance(horizon, int) else horizon,
        "rows": rows_count,
        "signal_coverage_pct": signal_coverage_pct,
        "trades": len(trade_returns),
        "win_rate": round(wins / len(trade_returns), 4) if trade_returns else None,
        "total_return_pct": round(total_return * 100.0, 4),
        "avg_trade_return_pct": (
            round((sum(trade_returns) / len(trade_returns)) * 100.0, 4) if trade_returns else None
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
        "engine": "vectorbt",
    }


def run_vectorbt_prediction_backtest_batch(
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
    """Run vectorbt evidence for a scanner-compatible symbol/module batch."""
    selected_symbols = normalize_csv_values(symbols or DEFAULT_BATCH_SYMBOLS, upper=True)
    selected_modules = [
        "predictive" if module == "probabilistic" else module
        for module in normalize_csv_values(modules or tuple(sorted(SUPPORTED_MODULES)), upper=False)
    ]
    selected_modules = [module for module in selected_modules if module in SUPPORTED_MODULES]
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
                run_vectorbt_prediction_backtest(
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

    ranked = sorted(results, key=rank_backtest_result, reverse=True)
    recommendations = build_backtest_batch_recommendations(results)
    primary = ranked[0] if ranked else None
    source_tier = dominant_source_tier(
        [str(item["source_tier"]) for item in results if item.get("source_tier") is not None]
    )
    quality_values = [
        float(item["data_quality_score"])
        for item in results
        if float_or_none(item.get("data_quality_score")) is not None
    ]
    return {
        "symbols": selected_symbols,
        "modules": selected_modules,
        "results": results,
        "ranked": ranked,
        "summary": {
            "symbols_scanned": len(selected_symbols),
            "validated_results": sum(
                1 for item in results if item.get("module_backtest_grade") == "validated"
            ),
            "engine": "vectorbt",
        },
        "recommendations": recommendations,
        "risk_desk_recommendations": recommendations,
        "module_backtest_grade": primary.get("module_backtest_grade") if primary else None,
        "funding_risk_metrics": primary.get("funding_risk_metrics") if primary else None,
        "source_tier": source_tier or (primary.get("source_tier") if primary else None),
        "data_quality_score": (
            round(sum(quality_values) / len(quality_values), 4) if quality_values else None
        ),
        "engine": "vectorbt",
    }


def _build_vectorbt_portfolio(
    vbt: Any,
    *,
    prices: list[float],
    timestamps: list[object],
    entries: list[bool],
    exits: list[bool],
    short_entries: list[bool],
    short_exits: list[bool],
    fee_bps: float,
    slippage_bps: float,
    half_spread_bps: float,
) -> Any:
    close = pd.Series(prices, index=pd.RangeIndex(len(prices)))
    if len(timestamps) == len(prices):
        parsed_index = pd.to_datetime(pd.Series(timestamps), errors="coerce")
        if not parsed_index.isna().all():
            close.index = pd.Index(parsed_index.ffill().bfill())
    return vbt.Portfolio.from_signals(
        close,
        entries,
        exits,
        short_entries,
        short_exits,
        fees=(float(fee_bps) + float(half_spread_bps)) / 10_000.0,
        slippage=float(slippage_bps) / 10_000.0,
        freq="D",
    )


def _profit_factor(gains: list[float], losses: list[float]) -> float | None:
    if gains and losses:
        return round(sum(gains) / sum(losses), 4)
    if gains and not losses:
        return 9999.0
    return None


def _portfolio_total_return(portfolio: Any) -> float | None:
    return _metric_value(portfolio, ("total_return", "total_return_pct"), pct=False)


def _portfolio_sharpe(portfolio: Any) -> float | None:
    return _metric_value(portfolio, ("sharpe_ratio", "sharpe"), pct=False)


def _portfolio_max_drawdown_pct(portfolio: Any) -> float | None:
    value = _metric_value(portfolio, ("max_drawdown", "max_drawdown_pct"), pct=False)
    if value is None:
        return None
    return round(value * 100.0, 3) if abs(value) <= 1.0 else round(value, 3)


def _metric_value(portfolio: Any, names: tuple[str, ...], *, pct: bool) -> float | None:
    for name in names:
        attr = getattr(portfolio, name, None)
        if attr is None:
            continue
        try:
            raw = attr() if callable(attr) else attr
        except Exception as exc:
            logger.debug("vectorbt.metric_failed metric=%s error=%s", name, str(exc)[:120])
            continue
        value = _scalar_float(raw)
        if value is not None:
            return value / 100.0 if pct else value
    return None


def _scalar_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if math.isfinite(number) else None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _scalar_float(item())
        except Exception:
            pass
    iloc = getattr(value, "iloc", None)
    if iloc is not None:
        try:
            return _scalar_float(iloc[-1])
        except Exception:
            pass
    return float_or_none(value)
