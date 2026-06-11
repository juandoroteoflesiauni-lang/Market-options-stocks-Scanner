"""FTMO Funding Lab service.

This module keeps the funding-test universe deliberately small and treats
``certainty`` as a hard quantitative gate: degraded evidence never becomes a
trade authorization.
"""

from __future__ import annotations

import copy
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.services.ftmo_crypto_options_service import crypto_options_status_for_symbol
from backend.services.ftmo_data_provider_policy import (
    PRIMARY,
    data_provider_policy_payload,
    provider_policy_for_symbol,
)
from backend.services.ftmo_gex_validation import (
    DIRECT_GEX_REQUIRED_SYMBOLS,
    load_ftmo_gex_validation,
)
from backend.services.ftmo_provider_registry import (
    BINANCE_DERIVATIVES_PRIMARY,
    BINANCE_SPOT_PRIMARY,
    BINGX_MARKET_PRIMARY,
    FIDELITY_MIN_SCORE,
    MARKET_DATA_PRIMARY,
    PROXY_ONLY,
    UNAVAILABLE,
    load_provider_readiness,
    provider_registry,
)
from backend.services.ftmo_survival_score import compute_ftmo_survival_score
from backend.services.funding_lab_metric_lake import (
    DEFAULT_METRIC_MODULES,
    METRIC_HORIZONS,
    load_funding_lab_metric_status,
)
from backend.services.prediction_backtest_service import (
    FUNDING_BEST_DAY_WARN_RATIO,
    SUPPORTED_MODULES,
    default_backtest_calibration,
    run_prediction_backtest,
    run_prediction_backtest_batch,
)
from backend.services.scanner_funding_gate import (
    REASON_SCANNER_UNAVAILABLE,
    SUITABILITY_ALLOW,
    SUITABILITY_BLOCK,
    SUITABILITY_INSUFFICIENT,
    SUITABILITY_SIZE_DOWN,
    evaluate_module_evidence,
)

logger = get_logger(__name__)

FTMO_CORE_SYMBOLS = (
    "GOOGL",
    "AAPL",
    "TSLA",
    "XAUUSD",
    "XAGUSD",
    "US100.CASH",
    "BTC/USDT",
)
STRICT_INTRADAY_HORIZONS = ("1h", "4h", "eod")
STRICT_HORIZONS = STRICT_INTRADAY_HORIZONS
MIN_TRADE_SURVIVAL_SCORE = 70.0
DEFAULT_PREDICTIONS_DB = Path("backend/data/predictions.db")
DEFAULT_PRICE_DB = Path("data/quantum_analyzer.duckdb")
DEFAULT_MONITOR_REPORT_DIR = Path("backend/reports/funding-lab")
REQUIRED_SQLITE_TABLES = ("predictions", "feature_snapshots")


@dataclass(frozen=True)
class FundingAssetProfile:
    """Canonical asset contract for the Funding Lab UI/API."""

    symbol: str
    label: str
    asset_class: str
    execution_symbol: str
    data_symbol: str
    backfill_symbol: str
    primary_cfd_symbol: str
    broker_validation_symbol: str
    proxy_symbol: str
    intraday_proxy_symbol: str | None
    preferred_provider_order: tuple[str, ...]
    data_aliases: tuple[str, ...]
    enabled_modules: tuple[str, ...]
    source_tier: str
    required_modules: tuple[str, ...] = ()
    context_modules: tuple[str, ...] = ()
    option_proxy_symbol: str | None = None
    bingx_symbols: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["data_aliases"] = list(self.data_aliases)
        payload["preferred_provider_order"] = list(self.preferred_provider_order)
        payload["enabled_modules"] = list(self.enabled_modules)
        payload["required_modules"] = list(self.required_modules)
        payload["context_modules"] = list(self.context_modules)
        payload["bingx_symbols"] = list(self.bingx_symbols)
        payload["notes"] = list(self.notes)
        return payload


_EQUITY_MODULES = ("predictive", "technical", "options_gex")
_CONTEXT_GEX_MODULES = ("predictive", "technical", "options_gex_context")
_CRYPTO_MODULES = ("predictive", "technical", "crypto_microstructure")
_DEFAULT_PROVIDER_ORDER = ("fmp_massive_polygon", "bingx_market")
_BINGX_PRIMARY_PROVIDER_ORDER = ("bingx_market", "fmp_massive_polygon")
_BTC_PROVIDER_ORDER = ("bingx_market", "binance_spot", "binance_usdm")
_PROVIDER_FRESHNESS_HOURS = 24

_ASSETS: dict[str, FundingAssetProfile] = {
    "GOOGL": FundingAssetProfile(
        symbol="GOOGL",
        label="Alphabet CFD",
        asset_class="equity_cfd",
        execution_symbol="GOOGL",
        data_symbol="GOOGL",
        backfill_symbol="GOOGL",
        primary_cfd_symbol="GOOGL",
        broker_validation_symbol="GOOGL",
        proxy_symbol="GOOGL",
        intraday_proxy_symbol=None,
        preferred_provider_order=_DEFAULT_PROVIDER_ORDER,
        data_aliases=("GOOGL",),
        enabled_modules=_EQUITY_MODULES,
        required_modules=_EQUITY_MODULES,
        source_tier="requires_full_chain_gex",
        option_proxy_symbol="GOOGL",
        bingx_symbols=provider_policy_for_symbol("GOOGL").validation_symbols,
    ),
    "AAPL": FundingAssetProfile(
        symbol="AAPL",
        label="Apple CFD",
        asset_class="equity_cfd",
        execution_symbol="AAPL",
        data_symbol="AAPL",
        backfill_symbol="AAPL",
        primary_cfd_symbol="AAPL",
        broker_validation_symbol="AAPL",
        proxy_symbol="AAPL",
        intraday_proxy_symbol=None,
        preferred_provider_order=_DEFAULT_PROVIDER_ORDER,
        data_aliases=("AAPL",),
        enabled_modules=_EQUITY_MODULES,
        required_modules=_EQUITY_MODULES,
        source_tier="requires_full_chain_gex",
        option_proxy_symbol="AAPL",
        bingx_symbols=provider_policy_for_symbol("AAPL").validation_symbols,
    ),
    "TSLA": FundingAssetProfile(
        symbol="TSLA",
        label="Tesla CFD",
        asset_class="equity_cfd",
        execution_symbol="TSLA",
        data_symbol="TSLA",
        backfill_symbol="TSLA",
        primary_cfd_symbol="TSLA",
        broker_validation_symbol="TSLA",
        proxy_symbol="TSLA",
        intraday_proxy_symbol=None,
        preferred_provider_order=_DEFAULT_PROVIDER_ORDER,
        data_aliases=("TSLA",),
        enabled_modules=_EQUITY_MODULES,
        required_modules=_EQUITY_MODULES,
        source_tier="requires_full_chain_gex",
        option_proxy_symbol="TSLA",
        bingx_symbols=provider_policy_for_symbol("TSLA").validation_symbols,
    ),
    "XAUUSD": FundingAssetProfile(
        symbol="XAUUSD",
        label="Gold CFD",
        asset_class="metal_cfd",
        execution_symbol="XAUUSD",
        data_symbol="GC=F",
        backfill_symbol="GC=F",
        primary_cfd_symbol="XAUUSD",
        broker_validation_symbol="XAUUSD",
        proxy_symbol="GC=F",
        intraday_proxy_symbol=None,
        preferred_provider_order=_BINGX_PRIMARY_PROVIDER_ORDER,
        data_aliases=("GC=F", "XAUUSD=X", "XAU/USD", "XAUUSD"),
        enabled_modules=_CONTEXT_GEX_MODULES,
        required_modules=("predictive", "technical"),
        context_modules=("options_gex", "options_gex_context"),
        source_tier="metals_proxy_until_spot_validated",
        option_proxy_symbol="GLD",
        bingx_symbols=provider_policy_for_symbol("XAUUSD").primary_symbols,
        notes=("GEX/options is contextual unless a validated metals history exists.",),
    ),
    "XAGUSD": FundingAssetProfile(
        symbol="XAGUSD",
        label="Silver CFD",
        asset_class="metal_cfd",
        execution_symbol="XAGUSD",
        data_symbol="SI=F",
        backfill_symbol="SI=F",
        primary_cfd_symbol="XAGUSD",
        broker_validation_symbol="XAGUSD",
        proxy_symbol="SI=F",
        intraday_proxy_symbol=None,
        preferred_provider_order=_BINGX_PRIMARY_PROVIDER_ORDER,
        data_aliases=("SI=F", "XAGUSD=X", "XAG/USD", "XAGUSD"),
        enabled_modules=_CONTEXT_GEX_MODULES,
        required_modules=("predictive", "technical"),
        context_modules=("options_gex", "options_gex_context"),
        source_tier="metals_proxy_until_spot_validated",
        option_proxy_symbol="SLV",
        bingx_symbols=provider_policy_for_symbol("XAGUSD").primary_symbols,
        notes=("GEX/options is contextual unless a validated metals history exists.",),
    ),
    "US100.CASH": FundingAssetProfile(
        symbol="US100.CASH",
        label="US Nasdaq 100 Cash CFD",
        asset_class="index_cfd",
        execution_symbol="US100.cash",
        data_symbol="QQQ",
        backfill_symbol="QQQ",
        primary_cfd_symbol="US100",
        broker_validation_symbol="US100.cash",
        proxy_symbol="QQQ",
        intraday_proxy_symbol="QQQ",
        preferred_provider_order=_BINGX_PRIMARY_PROVIDER_ORDER,
        data_aliases=("QQQ", "NQ=F", "US100.CASH", "US100.cash"),
        enabled_modules=_CONTEXT_GEX_MODULES,
        required_modules=("predictive", "technical"),
        context_modules=("options_gex", "options_gex_context"),
        source_tier="index_proxy_until_derivatives_validated",
        option_proxy_symbol="QQQ",
        bingx_symbols=provider_policy_for_symbol("US100.CASH").primary_symbols,
        notes=(
            "BingX is the production source; QQQ is contextual only.",
            "QQQ is used only as an intraday/options proxy, never as a target asset.",
        ),
    ),
    "BTC/USDT": FundingAssetProfile(
        symbol="BTC/USDT",
        label="Bitcoin analytic pair",
        asset_class="crypto_cfd",
        execution_symbol="BTCUSD",
        data_symbol="BTCUSDT",
        backfill_symbol="BTC-USD",
        primary_cfd_symbol="BTCUSDT",
        broker_validation_symbol="BTCUSDT",
        proxy_symbol="BTCUSDT",
        intraday_proxy_symbol=None,
        preferred_provider_order=_BTC_PROVIDER_ORDER,
        data_aliases=("BTCUSDT", "BTC/USDT", "BTCUSD", "BTC-USD"),
        enabled_modules=_CRYPTO_MODULES,
        required_modules=_CRYPTO_MODULES,
        source_tier="crypto_derivatives_required",
        bingx_symbols=provider_policy_for_symbol("BTC/USDT").primary_symbols,
        notes=(
            "Options/GEX is replaced by crypto volatility, funding, basis and OI/liquidations.",
        ),
    ),
}

_ALIASES: dict[str, str] = {
    "GOOGL": "GOOGL",
    "AAPL": "AAPL",
    "TSLA": "TSLA",
    "XAUUSD": "XAUUSD",
    "XAU/USD": "XAUUSD",
    "XAUUSD=X": "XAUUSD",
    "GC=F": "XAUUSD",
    "XAGUSD": "XAGUSD",
    "XAG/USD": "XAGUSD",
    "XAGUSD=X": "XAGUSD",
    "SI=F": "XAGUSD",
    "US100.CASH": "US100.CASH",
    "US100CASH": "US100.CASH",
    "US100.CASHCFD": "US100.CASH",
    "US100": "US100.CASH",
    "NAS100": "US100.CASH",
    "NDX": "US100.CASH",
    "QQQ": "US100.CASH",
    "NQ=F": "US100.CASH",
    "BTC/USDT": "BTC/USDT",
    "BTCUSDT": "BTC/USDT",
    "BTCUSD": "BTC/USDT",
    "BTC-USD": "BTC/USDT",
    "XBTUSD": "BTC/USDT",
}


def normalize_funding_symbol(raw_symbol: str) -> FundingAssetProfile:
    """Return the canonical Funding Lab profile for an input alias."""
    normalized = str(raw_symbol).strip()
    key = normalized.upper().replace(" ", "")
    canonical = _ALIASES.get(key)
    if canonical is None:
        raise ValueError(f"{raw_symbol} is not part of the ftmo_core universe")
    return _ASSETS[canonical]


def funding_lab_universe() -> list[FundingAssetProfile]:
    return [_ASSETS[symbol] for symbol in FTMO_CORE_SYMBOLS]


def calculate_intraday_outcomes(
    *,
    symbol: str,
    prediction_timestamp: datetime,
    prediction_direction: str,
    entry_price: float,
    bars_same_day: pd.DataFrame | None,
    ny_session_close_time: datetime | None = None,
) -> dict[str, object]:
    """Calculate same-day Funding Lab outcomes for 1h, 4h and EOD exits.

    Inputs are high-resolution same-day OHLCV bars with columns:
    ``timestamp``, ``open``, ``high``, ``low``, ``close`` and ``volume``.
    The EOD exit is forced at RTH close minus 15 minutes, defaulting to
    15:45 America/New_York. No overnight bars, gaps or swaps are included.
    """
    if bars_same_day is None or len(bars_same_day) < 1:
        return _intraday_error("Insufficient bars for same-day analysis")
    if entry_price <= 0:
        return _intraday_error("Invalid entry price")

    ny_tz = ZoneInfo("America/New_York")
    pred_time_ny = _as_ny_datetime(prediction_timestamp)
    eod_execution_time = (
        _as_ny_datetime(ny_session_close_time) - timedelta(minutes=15)
        if ny_session_close_time is not None
        else datetime.combine(pred_time_ny.date(), time(15, 45), tzinfo=ny_tz)
    )
    if pred_time_ny >= eod_execution_time:
        return _intraday_error("Prediction after EOD-15min execution window")

    bars = bars_same_day.copy()
    if "timestamp" not in bars.columns:
        return _intraday_error("Missing timestamp column")
    required_columns = {"high", "low", "close"}
    missing_columns = sorted(required_columns - set(bars.columns))
    if missing_columns:
        return _intraday_error(f"Missing OHLC columns: {','.join(missing_columns)}")

    bars["timestamp"] = pd.to_datetime(bars["timestamp"])
    if getattr(bars["timestamp"].dt, "tz", None) is None:
        bars["timestamp"] = bars["timestamp"].dt.tz_localize(ny_tz)
    else:
        bars["timestamp"] = bars["timestamp"].dt.tz_convert(ny_tz)

    bars_valid = bars[
        (bars["timestamp"] >= pred_time_ny) & (bars["timestamp"] <= eod_execution_time)
    ].reset_index(drop=True)
    if len(bars_valid) < 1:
        return _intraday_error("No valid bars within prediction window")

    direction_mult = -1.0 if str(prediction_direction).upper() == "DOWN" else 1.0
    bars_1h = bars_valid[bars_valid["timestamp"] <= pred_time_ny + timedelta(hours=1)]
    bars_4h = bars_valid[bars_valid["timestamp"] <= pred_time_ny + timedelta(hours=4)]
    bars_eod = bars_valid[bars_valid["timestamp"] <= eod_execution_time]

    outcome_1h = _window_best_directional_return(bars_1h, entry_price, direction_mult)
    outcome_4h = _window_best_directional_return(bars_4h, entry_price, direction_mult)
    outcome_eod = _window_close_directional_return(bars_eod, entry_price, direction_mult)
    outcome_same_day = _window_best_directional_return(bars_valid, entry_price, direction_mult)

    execution_time_eod = (
        bars_eod["timestamp"].iloc[-1].to_pydatetime() if len(bars_eod) > 0 else None
    )
    high_intraday = float(bars_valid["high"].max())
    low_intraday = float(bars_valid["low"].min())
    sharpe_1h = _intraday_sharpe(bars_1h)
    sharpe_4h = _intraday_sharpe(bars_4h)
    sharpe_eod = _intraday_sharpe(bars_eod)
    profit_factor_eod = _intraday_profit_factor(bars_eod)
    max_drawdown_eod = _intraday_max_drawdown(bars_eod)

    logger.info(
        "funding_lab.intraday_outcomes_calculated symbol=%s pred_time=%s direction=%s "
        "outcome_1h=%.4f outcome_4h=%.4f outcome_eod=%.4f",
        symbol,
        prediction_timestamp,
        prediction_direction,
        outcome_1h or 0.0,
        outcome_4h or 0.0,
        outcome_eod or 0.0,
    )
    logger.debug(
        "funding_lab.intraday_metrics symbol=%s sharpe_1h=%.2f sharpe_4h=%.2f "
        "sharpe_eod=%.2f profit_factor_eod=%.2f max_drawdown_eod=%.4f",
        symbol,
        sharpe_1h or 0.0,
        sharpe_4h or 0.0,
        sharpe_eod or 0.0,
        profit_factor_eod or 0.0,
        max_drawdown_eod or 0.0,
    )

    return {
        "outcome_return_1h": _float_or_none(outcome_1h),
        "outcome_return_4h": _float_or_none(outcome_4h),
        "outcome_return_eod": _float_or_none(outcome_eod),
        "outcome_return_same_day": _float_or_none(outcome_same_day),
        "execution_time_eod": execution_time_eod,
        "high_intraday": high_intraday,
        "low_intraday": low_intraday,
        "bars_held_1h_exit": int(len(bars_1h)),
        "bars_held_4h_exit": int(len(bars_4h)),
        "bars_held_eod_exit": int(len(bars_eod)),
        "sharpe_intraday_1h": _float_or_none(sharpe_1h),
        "sharpe_intraday_4h": _float_or_none(sharpe_4h),
        "sharpe_intraday_eod": _float_or_none(sharpe_eod),
        "profit_factor_eod": _float_or_none(profit_factor_eod),
        "max_drawdown_eod": _float_or_none(max_drawdown_eod),
        "error": None,
    }


def evaluate_smc_gex_confluence_for_allow(
    *,
    symbol: str,
    smc_order_blocks: list[Any] | None,
    net_gamma_exposure: float | None,
    call_gamma: float | None,
    put_gamma: float | None,
    prediction_direction: str,
    outcome_return_4h: float | None,
    daily_loss_pct: float = 0.0,
    daily_loss_cap: float = 0.05,
    confidence_ratio_threshold: float = 5.0,
) -> dict[str, object]:
    """Evaluate SMC Order Block + Net GEX confluence for intraday ALLOW."""
    _ = (symbol, call_gamma, put_gamma)
    if daily_loss_pct >= daily_loss_cap:
        return {
            "allow_state_enabled": False,
            "confluence_score": 0.0,
            "smc_strength": 0.0,
            "gex_confirmation": 0.0,
            "ratio_achieved": 0.0,
            "daily_loss_check": False,
            "reasons": [f"Daily loss cap exceeded: {daily_loss_pct:.2%} >= {daily_loss_cap:.2%}"],
            "warnings": ["ALLOW state not enabled because FTMO daily loss cap is breached"],
        }

    direction = str(prediction_direction).upper()
    smc_strength, smc_reasons = _smc_order_block_strength(smc_order_blocks, direction)
    gex_confirmation, gex_reasons = _gex_direction_confirmation(net_gamma_exposure, direction)
    if smc_strength > 0 and gex_confirmation > 0:
        confluence_ratio = smc_strength / max(gex_confirmation, 1e-9)
        ratio_achieved = confluence_ratio >= confidence_ratio_threshold
        ratio_reasons = [
            (
                f"Confluence ratio {confluence_ratio:.2f}:1 >= "
                f"threshold {confidence_ratio_threshold}:1"
                if ratio_achieved
                else f"Confluence ratio {confluence_ratio:.2f}:1 < threshold"
            )
        ]
    else:
        confluence_ratio = 0.0
        ratio_achieved = False
        ratio_reasons = ["SMC strength or GEX confirmation insufficient for ratio calc"]

    outcome_bonus = 0.0
    outcome_reasons: list[str] = []
    if outcome_return_4h is not None:
        confirms_up = direction == "UP" and outcome_return_4h > 0.01
        confirms_down = direction == "DOWN" and outcome_return_4h > 0.01
        if confirms_up or confirms_down:
            outcome_bonus = 0.2
            outcome_reasons.append(f"4h outcome {outcome_return_4h:.2%} confirms prediction")
        else:
            outcome_reasons.append("4h outcome inconclusive or negative")

    confluence_score = min(1.0, smc_strength * 0.6 + gex_confirmation * 0.2 + outcome_bonus)
    allow_state_enabled = bool(
        confluence_score >= 0.70
        and ratio_achieved
        and smc_strength >= 0.70
        and gex_confirmation > 0
    )
    final_reason = (
        "STRONG CONFLUENCE: SMC OB + GEX alignment + 5:1 ratio ALLOW"
        if allow_state_enabled
        else (
            "Partial confluence: monitor further"
            if confluence_score >= 0.50
            else "Insufficient confluence for ALLOW state"
        )
    )
    return {
        "allow_state_enabled": allow_state_enabled,
        "confluence_score": float(confluence_score),
        "smc_strength": float(smc_strength),
        "gex_confirmation": float(gex_confirmation),
        "ratio_achieved": float(confluence_ratio),
        "daily_loss_check": True,
        "reasons": smc_reasons + gex_reasons + ratio_reasons + outcome_reasons + [final_reason],
        "warnings": [] if allow_state_enabled else ["ALLOW state not yet enabled"],
    }


def validate_ftmo_compliance_intraday(
    *,
    daily_loss_pct: float,
    max_drawdown_intraday: float | None,
    sharpe_intraday: float | None,
    daily_loss_cap: float = 0.05,
) -> dict[str, bool]:
    """Validate intraday outcomes against Funding Lab/FTMO risk constraints."""
    daily_ok = daily_loss_pct < daily_loss_cap
    dd_ok = (max_drawdown_intraday or 0.0) > -0.10
    sharpe_ok = (sharpe_intraday or 0.0) > 0.5
    return {
        "ftmo_daily_loss_ok": daily_ok,
        "institutional_dd_ok": dd_ok,
        "sharpe_acceptable": sharpe_ok,
        "all_checks_pass": daily_ok and dd_ok and sharpe_ok,
    }


def sync_predictions_with_intraday_outcomes(
    *,
    symbol: str,
    prediction_id: int | str,
    prediction_timestamp: datetime,
    prediction_direction: str,
    entry_price: float,
    bars_same_day: pd.DataFrame,
    db_path: Path | str = DEFAULT_PREDICTIONS_DB,
) -> dict[str, object]:
    """Calculate intraday outcomes and persist them onto the predictions row."""
    outcomes = calculate_intraday_outcomes(
        symbol=symbol,
        prediction_timestamp=prediction_timestamp,
        prediction_direction=prediction_direction,
        entry_price=entry_price,
        bars_same_day=bars_same_day,
    )
    if outcomes.get("error"):
        return outcomes

    db = Path(db_path)
    con = sqlite3.connect(db)
    try:
        _ensure_intraday_prediction_columns(con)
        _ensure_outcomes_v3_table(con)
        con.execute(
            """
            UPDATE predictions
            SET outcome_return_1h = ?,
                outcome_return_4h = ?,
                outcome_return_eod = ?,
                outcome_return_same_day = ?,
                execution_time_eod = ?,
                high_intraday = ?,
                low_intraday = ?,
                bars_held_1h_exit = ?,
                bars_held_4h_exit = ?,
                bars_held_eod_exit = ?,
                sharpe_intraday_1h = ?,
                sharpe_intraday_4h = ?,
                sharpe_intraday_eod = ?,
                profit_factor_eod = ?,
                max_drawdown_eod = ?
            WHERE prediction_id = ?
            """,
            (
                outcomes["outcome_return_1h"],
                outcomes["outcome_return_4h"],
                outcomes["outcome_return_eod"],
                outcomes["outcome_return_same_day"],
                _datetime_to_iso(outcomes["execution_time_eod"]),
                outcomes["high_intraday"],
                outcomes["low_intraday"],
                outcomes["bars_held_1h_exit"],
                outcomes["bars_held_4h_exit"],
                outcomes["bars_held_eod_exit"],
                outcomes["sharpe_intraday_1h"],
                outcomes["sharpe_intraday_4h"],
                outcomes["sharpe_intraday_eod"],
                outcomes["profit_factor_eod"],
                outcomes["max_drawdown_eod"],
                str(prediction_id),
            ),
        )
        con.execute(
            """
            INSERT OR REPLACE INTO outcomes_v3 (
                prediction_id,
                outcome_return_1h,
                outcome_return_4h,
                outcome_return_eod,
                outcome_return_same_day,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(prediction_id),
                outcomes["outcome_return_1h"],
                outcomes["outcome_return_4h"],
                outcomes["outcome_return_eod"],
                outcomes["outcome_return_same_day"],
                datetime.now(UTC).isoformat(),
            ),
        )
        con.commit()
    finally:
        con.close()

    logger.info(
        "funding_lab.intraday_outcomes_synced symbol=%s prediction_id=%s db=%s",
        symbol,
        prediction_id,
        db,
    )
    return outcomes


def _intraday_error(message: str) -> dict[str, object]:
    return {
        "outcome_return_1h": None,
        "outcome_return_4h": None,
        "outcome_return_eod": None,
        "outcome_return_same_day": None,
        "execution_time_eod": None,
        "high_intraday": None,
        "low_intraday": None,
        "bars_held_1h_exit": 0,
        "bars_held_4h_exit": 0,
        "bars_held_eod_exit": 0,
        "sharpe_intraday_1h": None,
        "sharpe_intraday_4h": None,
        "sharpe_intraday_eod": None,
        "profit_factor_eod": None,
        "max_drawdown_eod": None,
        "error": message,
    }


def _ensure_intraday_prediction_columns(con: sqlite3.Connection) -> None:
    existing_columns = {
        str(row[1]) for row in con.execute("PRAGMA table_info(predictions)").fetchall()
    }
    columns: dict[str, str] = {
        "outcome_return_1h": "REAL",
        "outcome_return_4h": "REAL",
        "outcome_return_eod": "REAL",
        "outcome_return_same_day": "REAL",
        "execution_time_eod": "TEXT",
        "high_intraday": "REAL",
        "low_intraday": "REAL",
        "bars_held_1h_exit": "INTEGER",
        "bars_held_4h_exit": "INTEGER",
        "bars_held_eod_exit": "INTEGER",
        "sharpe_intraday_1h": "REAL",
        "sharpe_intraday_4h": "REAL",
        "sharpe_intraday_eod": "REAL",
        "profit_factor_eod": "REAL",
        "max_drawdown_eod": "REAL",
    }
    for name, sql_type in columns.items():
        if name not in existing_columns:
            con.execute(f"ALTER TABLE predictions ADD COLUMN {name} {sql_type}")


def _ensure_outcomes_v3_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS outcomes_v3 (
            prediction_id TEXT PRIMARY KEY,
            outcome_return_1h REAL,
            outcome_return_4h REAL,
            outcome_return_eod REAL,
            outcome_return_same_day REAL,
            updated_at TEXT
        )
        """
    )


def _datetime_to_iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _as_ny_datetime(value: datetime) -> datetime:
    ny_tz = ZoneInfo("America/New_York")
    if value.tzinfo is None:
        return value.replace(tzinfo=ny_tz)
    return value.astimezone(ny_tz)


def _window_best_directional_return(
    bars: pd.DataFrame,
    entry_price: float,
    direction_mult: float,
) -> float | None:
    if len(bars) < 1:
        return None
    exit_price = float(bars["low"].min()) if direction_mult < 0 else float(bars["high"].max())
    return direction_mult * (exit_price - entry_price) / entry_price


def _window_close_directional_return(
    bars: pd.DataFrame,
    entry_price: float,
    direction_mult: float,
) -> float | None:
    if len(bars) < 1:
        return None
    exit_price = float(bars["close"].iloc[-1])
    return direction_mult * (exit_price - entry_price) / entry_price


def _intraday_close_returns(bars: pd.DataFrame) -> pd.Series:
    if len(bars) < 2:
        return pd.Series(dtype="float64")
    return bars["close"].astype(float).pct_change().dropna()


def _intraday_sharpe(bars: pd.DataFrame) -> float | None:
    returns = _intraday_close_returns(bars)
    if len(returns) < 1:
        return None
    std = float(returns.std())
    if std <= 0.0 or not math.isfinite(std):
        return 0.0
    annualization = math.sqrt(252 * 6.5 * 60)
    return float(returns.mean() / std * annualization)


def _intraday_profit_factor(bars: pd.DataFrame) -> float | None:
    returns = _intraday_close_returns(bars)
    if len(returns) < 1:
        return None
    wins = float(returns[returns > 0].sum())
    losses = abs(float(returns[returns < 0].sum()))
    if losses > 0:
        return wins / losses
    if wins > 0:
        return 100.0
    return 1.0


def _intraday_max_drawdown(bars: pd.DataFrame) -> float | None:
    if len(bars) < 1:
        return None
    closes = bars["close"].astype(float)
    cumulative_high = closes.expanding().max()
    drawdown = (closes - cumulative_high) / cumulative_high
    return float(drawdown.min())


def _smc_order_block_strength(
    smc_order_blocks: list[Any] | None,
    prediction_direction: str,
) -> tuple[float, list[str]]:
    if not smc_order_blocks:
        return 0.0, ["No active Order Blocks detected"]
    directional_obs = []
    for order_block in smc_order_blocks:
        ob_direction = str(getattr(order_block, "direction", "")).upper()
        if (
            (prediction_direction == "UP" and ob_direction == "BULLISH")
            or (prediction_direction == "DOWN" and ob_direction == "BEARISH")
            or prediction_direction == "NEUTRAL"
        ):
            directional_obs.append(order_block)
    if not directional_obs:
        return 0.0, ["No directional Order Block alignment"]
    strengths = [
        max(0.0, min(1.0, float(getattr(order_block, "strength", 0.0) or 0.0)))
        for order_block in directional_obs
    ]
    smc_strength = sum(strengths) / len(strengths)
    return smc_strength, [
        f"SMC: {len(directional_obs)} {prediction_direction} Order Block(s) detected",
        f"Average OB strength: {smc_strength:.2f}",
    ]


def _gex_direction_confirmation(
    net_gamma_exposure: float | None,
    prediction_direction: str,
) -> tuple[float, list[str]]:
    if net_gamma_exposure is None:
        return 0.0, ["Net Gamma Exposure not available"]
    gamma = float(net_gamma_exposure)
    if prediction_direction == "UP" and gamma > 0:
        return min(1.0, abs(gamma) / 100.0), [
            "Net Gamma BULLISH: long gamma aligns with prediction"
        ]
    if prediction_direction == "DOWN" and gamma < 0:
        return min(1.0, abs(gamma) / 100.0), [
            "Net Gamma BEARISH: short gamma aligns with prediction"
        ]
    return 0.0, ["Net Gamma does NOT align with prediction direction"]


def strict_signal_decision_from_evidence(
    *,
    symbol: str,
    readiness_ready: bool,
    module_evidence: list[dict[str, Any]],
    required_horizons: tuple[int | str, ...] | None = None,
) -> dict[str, Any]:
    """Collapse module evidence into Funding Lab's strict ALLOW/NO_TRADE decision."""
    return _strict_signal_decision(
        symbol=symbol,
        readiness_ready=readiness_ready,
        module_evidence=module_evidence,
        provider_readiness=None,
        required_horizons=required_horizons,
        profile=None,
    )


def strict_signal_decision_for_profile(
    *,
    profile: FundingAssetProfile,
    readiness_ready: bool,
    module_evidence: list[dict[str, Any]],
    provider_readiness: dict[str, Any] | None = None,
    production_ready: bool | None = None,
    production_blockers: list[str] | tuple[str, ...] | None = None,
    gex_validation: dict[str, Any] | None = None,
    crypto_options_readiness: dict[str, Any] | None = None,
    account_state: dict[str, Any] | None = None,
    required_horizons: tuple[int | str, ...] | None = STRICT_HORIZONS,
) -> dict[str, Any]:
    """Apply the profile-specific Funding Lab gate for an FTMO core asset."""
    return _strict_signal_decision(
        symbol=profile.symbol,
        readiness_ready=readiness_ready,
        module_evidence=module_evidence,
        provider_readiness=provider_readiness,
        production_ready=production_ready,
        production_blockers=production_blockers,
        gex_validation=gex_validation,
        crypto_options_readiness=crypto_options_readiness,
        account_state=account_state,
        required_horizons=required_horizons,
        profile=profile,
    )


def _strict_signal_decision(
    *,
    symbol: str,
    readiness_ready: bool,
    module_evidence: list[dict[str, Any]],
    provider_readiness: dict[str, Any] | None,
    required_horizons: tuple[int | str, ...] | None,
    profile: FundingAssetProfile | None,
    production_ready: bool | None = None,
    production_blockers: list[str] | tuple[str, ...] | None = None,
    gex_validation: dict[str, Any] | None = None,
    crypto_options_readiness: dict[str, Any] | None = None,
    account_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason_codes: list[str] = []
    blocking_module: str | None = None
    context_modules = set(profile.context_modules if profile else ())
    required_modules = tuple(profile.required_modules if profile else ())
    source_tier = str((provider_readiness or {}).get("source_tier") or "").lower()
    fidelity_score = _float_or_none((provider_readiness or {}).get("fidelity_score"))
    fidelity_audit = (provider_readiness or {}).get("fidelity_audit")
    fidelity_audit_tier = (
        str(fidelity_audit.get("source_tier") or "").lower()
        if isinstance(fidelity_audit, dict)
        else ""
    )

    if not readiness_ready:
        reason_codes.append("backfill_incomplete")
        blocking_module = "data_readiness"

    explicit_production_blockers = [
        str(reason) for reason in (production_blockers or []) if str(reason).strip()
    ]
    if production_ready is False or explicit_production_blockers:
        reason_codes.append("production_not_ready")
        reason_codes.extend(explicit_production_blockers)
        blocking_module = blocking_module or "production_readiness"

    gex_blockers = [
        str(reason)
        for reason in ((gex_validation or {}).get("gex_blockers") or [])
        if str(reason).strip()
    ]
    if gex_blockers:
        reason_codes.extend(gex_blockers)
        blocking_module = blocking_module or "options_gex"

    crypto_option_blockers = [
        str(reason)
        for reason in ((crypto_options_readiness or {}).get("crypto_options_blockers") or [])
        if str(reason).strip()
    ]
    if profile and profile.symbol == "BTC/USDT" and crypto_option_blockers:
        reason_codes.extend(crypto_option_blockers)
        blocking_module = blocking_module or "crypto_options"

    if not module_evidence:
        reason_codes.append("missing_backtest_evidence")
        blocking_module = blocking_module or "backtesting"

    acceptable_source_tiers = {
        MARKET_DATA_PRIMARY,
        BINANCE_SPOT_PRIMARY,
        BINANCE_DERIVATIVES_PRIMARY,
        BINGX_MARKET_PRIMARY,
        "cfd_primary",
        "broker_validated",
    }
    if source_tier in {PROXY_ONLY, UNAVAILABLE} or (
        source_tier and source_tier not in acceptable_source_tiers
    ):
        reason_codes.append(
            "proxy_only_source" if source_tier == PROXY_ONLY else "cfd_source_unavailable"
        )
        blocking_module = blocking_module or "provider_fidelity"
    if (
        fidelity_score is not None
        and fidelity_score < FIDELITY_MIN_SCORE
        and source_tier != "broker_validated"
        and fidelity_audit_tier != "broker_validated"
    ):
        reason_codes.append("low_fidelity_score")
        blocking_module = blocking_module or "provider_fidelity"
    for reason in _provider_production_blockers(profile, provider_readiness):
        reason_codes.append(reason)
        blocking_module = blocking_module or "provider_fidelity"

    if required_horizons:
        observed = {
            _evidence_horizon_key(evidence)
            for evidence in module_evidence
            if _evidence_horizon_key(evidence) is not None
        }
        required_keys = {_normalize_horizon_key(horizon) for horizon in required_horizons}
        missing_horizons = sorted(required_keys - observed)
        if missing_horizons:
            reason_codes.append("missing_oos_horizons")
            blocking_module = blocking_module or "walk_forward_oos"

    if profile and required_horizons:
        evidence_index = {
            (str(evidence.get("module") or "").lower(), _evidence_horizon_key(evidence))
            for evidence in module_evidence
        }
        for module in required_modules:
            for horizon in required_horizons:
                if (module, _normalize_horizon_key(horizon)) not in evidence_index:
                    reason = (
                        "crypto_derivatives_unvalidated"
                        if profile.symbol == "BTC/USDT" and module == "crypto_microstructure"
                        else f"required_module_missing:{module}"
                    )
                    reason_codes.append(reason)
                    blocking_module = blocking_module or module

        if _profile_requires_direct_gex(
            profile, gex_validation
        ) and not _has_validated_full_chain_gex_oos(
            module_evidence,
            required_horizons,
        ):
            reason_codes.append("gex_backtest_missing")
            blocking_module = blocking_module or "options_gex"

    for evidence in module_evidence:
        module = str(evidence.get("module") or "unknown")
        module_key = module.lower()
        grade = str(evidence.get("module_backtest_grade") or "").lower()
        survival = str(evidence.get("funding_survival_grade") or "").lower()
        suitability = str(evidence.get("suitability") or "").lower()
        tier = str(evidence.get("source_tier") or "").lower()
        is_context_only = module_key in context_modules and module_key not in required_modules
        if is_context_only:
            continue

        if grade in {"weak_edge", "overfit_risk", "insufficient_data", ""}:
            reason_codes.append(grade or "insufficient_data")
            blocking_module = blocking_module or module

        total_return = _float_or_none(evidence.get("total_return_pct"))
        if total_return is not None and total_return <= 0.0:
            reason_codes.append("non_positive_oos")
            blocking_module = blocking_module or module

        if survival == "would_breach":
            reason_codes.append("would_breach")
            blocking_module = blocking_module or "risk_management"
        elif survival == "at_risk":
            reason_codes.append("funding_at_risk")
            blocking_module = blocking_module or "risk_management"

        if suitability in {SUITABILITY_SIZE_DOWN, SUITABILITY_BLOCK, SUITABILITY_INSUFFICIENT}:
            reason_codes.append(suitability)
            blocking_module = blocking_module or module

        if (
            module_key == "options_gex"
            and not is_context_only
            and tier not in {"", "full_chain_gex"}
        ):
            reason_codes.append("gex_source_not_validated")
            blocking_module = blocking_module or module

        metrics = evidence.get("funding_risk_metrics")
        if isinstance(metrics, dict):
            if metrics.get("daily_loss_breach_count"):
                reason_codes.append("daily_loss_breach")
                blocking_module = blocking_module or "risk_management"
            if metrics.get("max_drawdown_breach"):
                reason_codes.append("max_loss_breach")
                blocking_module = blocking_module or "risk_management"
            best_day = _float_or_none(metrics.get("best_day_profit_ratio"))
            best_day_pct = _float_or_none(metrics.get("best_day_contribution_pct"))
            best_day_ratio = (
                best_day
                if best_day is not None
                else (best_day_pct / 100.0 if best_day_pct is not None else None)
            )
            if best_day_ratio is not None and best_day_ratio >= FUNDING_BEST_DAY_WARN_RATIO:
                reason_codes.append("best_day_concentration")
                blocking_module = blocking_module or "risk_management"

    deduped_reasons = _dedupe(reason_codes)
    funding_survival = _funding_survival_summary(
        module_evidence=module_evidence,
        reason_codes=deduped_reasons,
        required_horizons=required_horizons,
        profile=profile,
        account_state=account_state,
    )
    survival_reason_codes = [
        reason
        for reason in funding_survival.get("reason_codes", [])
        if isinstance(reason, str) and reason
    ]
    if survival_reason_codes:
        deduped_reasons = _dedupe([*deduped_reasons, *survival_reason_codes])
    survival_score = _float_or_none(funding_survival.get("score"))
    survival_status = str(funding_survival.get("status") or "").upper()
    if (
        survival_status != "INSUFFICIENT"
        and survival_score is not None
        and survival_score < MIN_TRADE_SURVIVAL_SCORE
    ):
        deduped_reasons = _dedupe([*deduped_reasons, "survival_score_below_minimum"])
        blocking_module = blocking_module or "risk_management"
        funding_survival = _funding_survival_summary(
            module_evidence=module_evidence,
            reason_codes=deduped_reasons,
            required_horizons=required_horizons,
            profile=profile,
            account_state=account_state,
        )
        survival_status = str(funding_survival.get("status") or "").upper()

    decision = "NO_TRADE" if deduped_reasons else "ALLOW"
    trade_ready = decision == "ALLOW" and survival_status == "SAFE"
    return {
        "symbol": symbol,
        "decision": decision,
        "reason_codes": deduped_reasons,
        "blocking_module": blocking_module,
        "blocker_resolution_plan": _blocker_resolution_plan(deduped_reasons),
        "funding_survival_score": funding_survival["score"] or 0.0,
        "funding_survival": funding_survival,
        "trade_ready": trade_ready,
        "trade_blockers": [] if trade_ready else deduped_reasons,
    }


def _funding_survival_summary(
    *,
    module_evidence: list[dict[str, Any]],
    reason_codes: list[str],
    required_horizons: tuple[int | str, ...] | None,
    profile: FundingAssetProfile | None,
    account_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return compute_ftmo_survival_score(
        module_evidence=module_evidence,
        account_state=account_state,
        reason_codes=reason_codes,
        required_horizons=required_horizons,
        profile=profile,
    )


def _profile_requires_direct_gex(
    profile: FundingAssetProfile | None,
    gex_validation: dict[str, Any] | None,
) -> bool:
    if profile is None:
        return False
    if isinstance(gex_validation, dict) and gex_validation.get("gex_required") is not None:
        return bool(gex_validation.get("gex_required"))
    return profile.symbol in DIRECT_GEX_REQUIRED_SYMBOLS


def _has_validated_full_chain_gex_oos(
    module_evidence: list[dict[str, Any]],
    required_horizons: tuple[int | str, ...] | None,
) -> bool:
    if not required_horizons:
        return False
    observed = {
        _evidence_horizon_key(evidence)
        for evidence in module_evidence
        if str(evidence.get("module") or "").lower() == "options_gex"
        and str(evidence.get("module_backtest_grade") or "").lower() == "validated"
        and str(evidence.get("source_tier") or "").lower() == "full_chain_gex"
    }
    required = {_normalize_horizon_key(horizon) for horizon in required_horizons}
    return required.issubset(observed)


def _provider_production_blockers(
    profile: FundingAssetProfile | None,
    provider_readiness: dict[str, Any] | None,
) -> list[str]:
    if profile is None:
        return []
    policy = provider_policy_for_symbol(profile.symbol)
    if provider_readiness is None:
        blockers: list[str] = []
        if policy.primary_provider == "bingx_market":
            blockers.append("bingx_primary_missing")
        elif PRIMARY in policy.required_roles:
            blockers.append("primary_provider_missing")
        if policy.validation_provider == "bingx_market":
            blockers.extend(("market_data_primary_missing", "bingx_validation_missing"))
        return _dedupe(blockers)
    counts = provider_readiness.get("provider_counts")
    if not isinstance(counts, dict):
        counts = {}
    blockers: list[str] = []
    if policy.primary_provider == "bingx_market":
        if int(counts.get("bingx_market") or 0) <= 0:
            blockers.append("bingx_primary_missing")
        else:
            if not _provider_health_ok(provider_readiness, "bingx_market"):
                blockers.append("provider_health_missing")
            if not _provider_snapshot_fresh(provider_readiness, "bingx_market"):
                blockers.append("stale_provider_snapshot")
    else:
        if int(counts.get(policy.primary_provider) or 0) <= 0:
            blockers.append("market_data_primary_missing")
        elif not _provider_snapshot_fresh(provider_readiness, policy.primary_provider):
            blockers.append("stale_provider_snapshot")
    if policy.validation_provider == "bingx_market":
        if int(counts.get("fmp_massive_polygon") or 0) <= 0:
            blockers.append("market_data_primary_missing")
        if int(counts.get("bingx_market") or 0) <= 0:
            blockers.append("bingx_validation_missing")
        else:
            if not _provider_health_ok(provider_readiness, "bingx_market"):
                blockers.append("provider_health_missing")
            if not _provider_snapshot_fresh(provider_readiness, "bingx_market"):
                blockers.append("stale_provider_snapshot")
            audit = provider_readiness.get("fidelity_audit")
            score = _float_or_none(
                (audit or {}).get("fidelity_score") if isinstance(audit, dict) else None
            )
            if score is None:
                score = _float_or_none(provider_readiness.get("fidelity_score"))
            if score is None:
                blockers.append("bingx_validation_fidelity_missing")
            elif (
                score < FIDELITY_MIN_SCORE
                and str(audit.get("source_tier") or "") != "broker_validated"
            ):
                blockers.append("low_bingx_validation_fidelity")
    if policy.context_required and policy.context_provider:
        if int(counts.get(policy.context_provider) or 0) <= 0:
            blockers.append("context_provider_missing")
        elif not _provider_snapshot_fresh(provider_readiness, policy.context_provider):
            blockers.append("context_provider_stale")
    return _dedupe(blockers)


def _provider_health_ok(provider_readiness: dict[str, Any], provider: str) -> bool:
    health_by_provider = provider_readiness.get("provider_health")
    if not isinstance(health_by_provider, dict):
        return False
    health = health_by_provider.get(provider)
    return isinstance(health, dict) and bool(health.get("ok"))


def _provider_snapshot_fresh(provider_readiness: dict[str, Any], provider: str) -> bool:
    latest_created_by_provider = provider_readiness.get("latest_created_by_provider")
    latest_by_provider = provider_readiness.get("latest_by_provider")
    latest: object = None
    if isinstance(latest_created_by_provider, dict):
        latest = latest_created_by_provider.get(provider)
    if isinstance(latest_by_provider, dict):
        latest = latest or latest_by_provider.get(provider)
    if latest is None and provider_readiness.get("primary_provider") == provider:
        latest = provider_readiness.get("latest_timestamp")
    parsed = _parse_iso_datetime(latest)
    if parsed is None:
        return False
    return datetime.now(tz=UTC) - parsed <= timedelta(hours=_PROVIDER_FRESHNESS_HOURS)


def _resolved_provider_symbol(
    profile: FundingAssetProfile,
    provider_readiness: dict[str, Any],
) -> str:
    latest_symbols = provider_readiness.get("latest_provider_symbol_by_provider")
    primary_provider = provider_readiness.get("primary_provider")
    if isinstance(latest_symbols, dict) and primary_provider:
        latest_symbol = latest_symbols.get(str(primary_provider))
        if latest_symbol:
            return str(latest_symbol)
    policy = provider_policy_for_symbol(profile.symbol)
    if policy.primary_symbols:
        return policy.primary_symbols[0]
    return profile.intraday_proxy_symbol or profile.backfill_symbol


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_context_evidence(profile: FundingAssetProfile | None, evidence: dict[str, Any]) -> bool:
    if not profile:
        return False
    module = str(evidence.get("module") or "").lower()
    return module in set(profile.context_modules) and module not in set(profile.required_modules)


class FundingLabService:
    """Read-only Funding Lab orchestration over existing backfill/backtest data."""

    def __init__(
        self,
        *,
        predictions_db: Path | str = DEFAULT_PREDICTIONS_DB,
        price_db: Path | str = DEFAULT_PRICE_DB,
        scanner_confirmation_provider: Any | None = None,
        scanner_snapshot_provider: Any | None = None,
        command_deck_cache_ttl_seconds: float = 30.0,
    ) -> None:
        self.predictions_db = Path(predictions_db)
        self.price_db = Path(price_db)
        self.scanner_confirmation_provider = scanner_confirmation_provider
        self.scanner_snapshot_provider = scanner_snapshot_provider
        self.command_deck_cache_ttl_seconds = max(0.0, float(command_deck_cache_ttl_seconds))
        self._command_deck_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def universe(self) -> dict[str, Any]:
        assets = funding_lab_universe()
        return {
            "ok": True,
            "profile": "FTMO 2-Step Standard",
            "policy": "strict_no_trade",
            "universe": [asset.symbol for asset in assets],
            "assets": [asset.to_dict() for asset in assets],
        }

    async def command_deck(
        self,
        *,
        symbols: list[str] | None = None,
        modules: list[str] | None = None,
        limit_per_symbol: int = 5_000,
    ) -> dict[str, Any]:
        """Return the single Funding Lab dashboard snapshot consumed by the UI.

        This intentionally centralizes the expensive Funding Lab reads and
        optional Scanner enrichment behind one short-lived cache so the browser
        no longer fans out into multiple heavy endpoints per refresh.
        """
        profiles = self._profiles_from_symbols(symbols)
        normalized_symbols = [profile.symbol for profile in profiles]
        selected_modules = self._supported_modules(modules)
        cache_key = _command_deck_cache_key(
            symbols=normalized_symbols,
            modules=selected_modules,
            limit_per_symbol=limit_per_symbol,
        )
        now = datetime.now(tz=UTC).timestamp()
        cached = self._command_deck_cache.get(cache_key)
        if cached and now - cached[0] <= self.command_deck_cache_ttl_seconds:
            payload = copy.deepcopy(cached[1])
            payload["cache"] = {
                **payload.get("cache", {}),
                "hit": True,
                "ttl_seconds": self.command_deck_cache_ttl_seconds,
            }
            return payload

        status_payload = self.status()
        backtest_payload = self.backtest(
            symbols=normalized_symbols,
            modules=selected_modules,
            n_days="eod",
            limit_per_symbol=limit_per_symbol,
        )
        scanner_rows: list[dict[str, Any]] = []
        errors: list[str] = []
        if self.scanner_snapshot_provider is not None:
            try:
                raw_scanner = await _maybe_await(self.scanner_snapshot_provider(normalized_symbols))
                scanner_rows = _normalize_payload_rows(raw_scanner)
            except Exception as exc:  # pragma: no cover - defensive degradation path
                logger.info(
                    "funding_lab.command_deck_scanner_unavailable symbols=%s error=%s",
                    ",".join(normalized_symbols),
                    exc,
                )
                errors.append("scanner_unavailable")

        readiness_by_symbol = _payload_by_symbol(status_payload.get("symbols"))
        decision_by_symbol = _payload_by_symbol(backtest_payload.get("strict_decisions"))
        scanner_by_symbol = _payload_by_symbol(scanner_rows)
        rows: list[dict[str, Any]] = []
        for profile in profiles:
            scanner = _lookup_payload_for_profile(scanner_by_symbol, profile)
            decision = decision_by_symbol.get(profile.symbol)
            readiness = readiness_by_symbol.get(profile.symbol)
            gex = _gex_summary_from_scanner(scanner)
            predictive = _predictive_summary_from_context(
                symbol=profile.symbol,
                scanner=scanner,
                decision=decision,
            )
            rows.append(
                {
                    "symbol": profile.symbol,
                    "asset": profile.to_dict(),
                    "readiness": readiness,
                    "decision": decision,
                    "scanner": scanner,
                    "predictive": predictive,
                    "gex": gex,
                }
            )

        payload = {
            "ok": True,
            "profile": "FTMO 2-Step Standard",
            "policy": "strict_no_trade",
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "cache": {
                "hit": False,
                "ttl_seconds": self.command_deck_cache_ttl_seconds,
                "key": cache_key,
            },
            "universe": normalized_symbols,
            "assets": [profile.to_dict() for profile in profiles],
            "status": status_payload,
            "backtest": backtest_payload,
            "summary": _command_deck_summary(rows, status_payload, backtest_payload),
            "rows": rows,
            "errors": errors,
        }
        self._command_deck_cache[cache_key] = (now, copy.deepcopy(payload))
        return payload

    def status(self) -> dict[str, Any]:
        profiles = funding_lab_universe()
        metric_status = self._metric_status(profiles)
        rows = [
            self._readiness_for_profile(profile, metric_status=metric_status)
            for profile in profiles
        ]
        ready_count = sum(1 for row in rows if row["ready"])
        production_ready_count = sum(1 for row in rows if row["production_ready"])
        not_production_ready_symbols = [
            row["symbol"] for row in rows if not row["production_ready"]
        ]
        production_blockers = {
            row["symbol"]: list(row.get("production_blockers") or [])
            for row in rows
            if row.get("production_blockers")
        }
        gex_validation = {row["symbol"]: row.get("gex_validation", {}) for row in rows}
        crypto_options_readiness = {
            row["symbol"]: row.get("crypto_options_readiness", {}) for row in rows
        }
        provider_policy = data_provider_policy_payload()
        coverage_by_horizon = {
            profile.symbol: self._coverage_by_horizon(profile) for profile in profiles
        }
        provider_readiness = {
            profile.symbol: self._provider_readiness_for_profile(profile) for profile in profiles
        }
        latest_backfill_run = self._latest_backfill_run()
        latest_monitor_run, operational_monitor = _latest_monitor_report()
        generated_at = datetime.now(tz=UTC).isoformat()
        return {
            "ok": True,
            "profile": "FTMO 2-Step Standard",
            "policy": "strict_no_trade",
            "generated_at": generated_at,
            "readiness_summary": {
                "total_symbols": len(rows),
                "ready_symbols": ready_count,
                "not_ready_symbols": len(rows) - ready_count,
                "production_ready_symbols": production_ready_count,
                "not_production_ready_symbols": len(rows) - production_ready_count,
            },
            "production_ready_symbols": production_ready_count,
            "total_symbols": len(rows),
            "not_production_ready_symbols": not_production_ready_symbols,
            "production_blockers": production_blockers,
            "coverage_by_horizon": coverage_by_horizon,
            "metric_coverage": metric_status["metric_coverage"],
            "latest_metric_run": metric_status["latest_metric_run"],
            "missing_metric_families": metric_status["missing_metric_families"],
            "metric_quality": metric_status["quality_by_symbol"],
            "latest_backfill_run": latest_backfill_run,
            "latest_monitor_run": latest_monitor_run,
            "operational_monitor": operational_monitor,
            "source_tier": {row["symbol"]: row["source_tier"] for row in rows},
            "data_provider_policy": provider_policy,
            "gex_validation": gex_validation,
            "crypto_options_ready": {
                row["symbol"]: row.get("crypto_options_ready") for row in rows
            },
            "crypto_options_provider_health": {
                row["symbol"]: row.get("crypto_options_provider_health") for row in rows
            },
            "crypto_options_metrics": {
                row["symbol"]: row.get("crypto_options_metrics") for row in rows
            },
            "crypto_options_blockers": {
                symbol: payload.get("crypto_options_blockers", [])
                for symbol, payload in crypto_options_readiness.items()
                if payload.get("crypto_options_blockers")
            },
            "provider_readiness": provider_readiness,
            "provider_health": self._provider_health_summary(provider_readiness),
            "next_actions": self._next_actions(rows),
            "operational_readiness": _operational_readiness_summary(rows),
            "calibration_policy": _calibration_policy_summary(),
            "last_verified_at": _last_verified_at(
                rows=rows,
                latest_backfill_run=latest_backfill_run,
                latest_metric_run=metric_status["latest_metric_run"],
                generated_at=generated_at,
            ),
            "symbols": rows,
        }

    def backtest(
        self,
        *,
        symbols: list[str] | None = None,
        modules: list[str] | None = None,
        n_days: int | str = "eod",
        limit_per_symbol: int = 5_000,
    ) -> dict[str, Any]:
        profiles = self._profiles_from_symbols(symbols)
        errors: list[str] = []
        results: list[dict[str, Any]] = []
        for profile in profiles:
            profile_modules = self._profile_modules(profile, modules)
            for horizon in STRICT_INTRADAY_HORIZONS:
                try:
                    horizon_report = run_prediction_backtest_batch(
                        db_path=self.predictions_db,
                        symbols=[self._backtest_symbol(profile)],
                        modules=profile_modules,
                        n_days=horizon,
                        limit_per_symbol=limit_per_symbol,
                    )
                except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
                    logger.info(
                        "funding_lab.backtest_unavailable symbol=%s horizon=%s requested_n_days=%s error=%s",
                        profile.symbol,
                        horizon,
                        n_days,
                        exc,
                    )
                    errors.append(f"{profile.symbol} {horizon}d: {exc}")
                    continue
                horizon_results = horizon_report.get("results")
                if isinstance(horizon_results, list):
                    results.extend([row for row in horizon_results if isinstance(row, dict)])
        report = {
            "results": results,
            "horizons": list(STRICT_INTRADAY_HORIZONS),
            "requested_n_days": n_days,
            "errors": errors,
            "data_source": str(self.predictions_db),
        }

        status_payload = self.status()
        status_by_symbol = {row["symbol"]: row for row in status_payload["symbols"]}
        strict_decisions = []
        for profile in profiles:
            evidence = self._evidence_from_report(profile, report)
            decision = strict_signal_decision_for_profile(
                profile=profile,
                readiness_ready=bool(
                    status_by_symbol[profile.symbol].get("data_ready")
                    if status_by_symbol[profile.symbol].get("data_ready") is not None
                    else status_by_symbol[profile.symbol]["ready"]
                ),
                module_evidence=evidence,
                provider_readiness=status_by_symbol[profile.symbol].get("provider_readiness"),
                production_ready=bool(status_by_symbol[profile.symbol].get("production_ready")),
                production_blockers=status_by_symbol[profile.symbol].get("production_blockers")
                or [],
                gex_validation=status_by_symbol[profile.symbol].get("gex_validation"),
                crypto_options_readiness=status_by_symbol[profile.symbol].get(
                    "crypto_options_readiness"
                ),
                required_horizons=STRICT_INTRADAY_HORIZONS,
            )
            decision["module_evidence"] = evidence
            strict_decisions.append(decision)

        report["metric_summary"] = _metric_summary_for_profiles(
            profiles=profiles,
            metric_status={
                "metric_coverage": status_payload["metric_coverage"],
                "missing_metric_families": status_payload["missing_metric_families"],
                "quality_by_symbol": status_payload["metric_quality"],
                "latest_metric_run": status_payload["latest_metric_run"],
            },
        )
        report["risk_summary"] = _risk_summary_for_decisions(strict_decisions)
        report["walk_forward_summary"] = _walk_forward_summary(results)

        return {
            "ok": True,
            "profile": "FTMO 2-Step Standard",
            "policy": "strict_no_trade",
            "universe": [profile.symbol for profile in profiles],
            "report": report,
            "strict_decisions": strict_decisions,
        }

    async def signal_check(
        self,
        *,
        symbol: str,
        entry_direction: str | None = None,
        account_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = normalize_funding_symbol(symbol)
        readiness = self._readiness_for_profile(profile)
        module_evidence: list[dict[str, Any]] = []

        if readiness["ready"]:
            for horizon in STRICT_INTRADAY_HORIZONS:
                for module in self._supported_modules(list(profile.enabled_modules)):
                    try:
                        result = run_prediction_backtest(
                            db_path=self.predictions_db,
                            module=module,
                            symbol=self._backtest_symbol(profile),
                            n_days=horizon,
                        )
                    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
                        logger.info(
                            "funding_lab.signal_check_backtest_unavailable symbol=%s module=%s horizon=%s error=%s",
                            profile.symbol,
                            module,
                            horizon,
                            exc,
                        )
                        continue
                    module_evidence.append(_module_evidence_from_backtest(result))

        decision = strict_signal_decision_for_profile(
            profile=profile,
            readiness_ready=bool(readiness.get("data_ready", readiness["ready"])),
            module_evidence=module_evidence,
            provider_readiness=readiness.get("provider_readiness"),
            production_ready=bool(readiness.get("production_ready")),
            production_blockers=readiness.get("production_blockers") or [],
            gex_validation=readiness.get("gex_validation"),
            crypto_options_readiness=readiness.get("crypto_options_readiness"),
            account_state=account_state,
            required_horizons=STRICT_INTRADAY_HORIZONS,
        )
        scanner_confirmation: dict[str, Any] | None = None
        if self.scanner_confirmation_provider and entry_direction:
            try:
                scanner_confirmation = await self.scanner_confirmation_provider(
                    symbol=profile.symbol,
                    entry_direction=entry_direction,
                    min_score=self._min_score_for_symbol(profile.symbol),
                )
            except Exception as exc:  # pragma: no cover - defensive fail-closed guard
                logger.info(
                    "funding_lab.scanner_confirmation_unavailable symbol=%s error=%s",
                    profile.symbol,
                    exc,
                )
                scanner_confirmation = {
                    "status": "FAIL",
                    "reasons": [REASON_SCANNER_UNAVAILABLE],
                    "trend_score": 0.0,
                }

            side_meta_confirmation = scanner_confirmation.get("side_meta_confirmation")
            if (
                decision["decision"] == "ALLOW"
                and isinstance(side_meta_confirmation, dict)
                and side_meta_confirmation.get("status") != "PASS"
            ):
                side_reasons = side_meta_confirmation.get("reasons")
                decision["decision"] = "NO_TRADE"
                decision["blocking_module"] = "side_meta_learner"
                decision["reason_codes"] = _dedupe(
                    [
                        *decision["reason_codes"],
                        *(side_reasons if isinstance(side_reasons, list) else []),
                    ]
                )
            elif decision["decision"] == "ALLOW" and scanner_confirmation.get("status") != "PASS":
                scanner_reasons = scanner_confirmation.get("reasons")
                decision["decision"] = "NO_TRADE"
                decision["blocking_module"] = "market_scanner"
                decision["reason_codes"] = _dedupe(
                    [
                        *decision["reason_codes"],
                        *(scanner_reasons if isinstance(scanner_reasons, list) else []),
                    ]
                )
        if decision["decision"] != "ALLOW":
            decision["trade_ready"] = False
            decision["trade_blockers"] = _dedupe(decision["reason_codes"])
        return {
            "ok": True,
            "profile": "FTMO 2-Step Standard",
            "policy": "strict_no_trade",
            "symbol": profile.symbol,
            "asset": profile.to_dict(),
            "readiness": readiness,
            "decision": decision["decision"],
            "reason_codes": decision["reason_codes"],
            "blocking_module": decision["blocking_module"],
            "funding_survival_score": decision["funding_survival_score"],
            "funding_survival": decision["funding_survival"],
            "trade_ready": decision["trade_ready"],
            "trade_blockers": decision["trade_blockers"],
            "scanner_confirmation": scanner_confirmation,
            "module_evidence": module_evidence,
            "metric_summary": _metric_summary_for_profiles(
                profiles=[profile],
                metric_status=self._metric_status([profile]),
            ),
            "critical_metrics": _critical_metrics_for_signal(
                profile=profile,
                readiness=readiness,
                decision=decision,
                evidence=module_evidence,
            ),
        }

    def _min_score_for_symbol(self, symbol: str) -> float:
        mapping = {
            "AAPL": 82.0,
            "GOOGL": 82.0,
            "XAUUSD": 83.0,
            "US100.CASH": 84.0,
            "TSLA": 85.0,
            "XAGUSD": 85.0,
            "BTC/USDT": 86.0,
        }
        return mapping.get(symbol.upper(), 85.0)

    def _profiles_from_symbols(self, symbols: list[str] | None) -> list[FundingAssetProfile]:
        if not symbols:
            return funding_lab_universe()
        profiles: list[FundingAssetProfile] = []
        seen: set[str] = set()
        for raw_symbol in symbols:
            profile = normalize_funding_symbol(raw_symbol)
            if profile.symbol not in seen:
                seen.add(profile.symbol)
                profiles.append(profile)
        return profiles

    def _supported_modules(self, modules: list[str] | None) -> list[str]:
        raw_modules = modules or ["predictive", "technical", "options_gex", "crypto_microstructure"]
        selected = []
        for module in raw_modules:
            cleaned = str(module).strip().lower()
            if cleaned in SUPPORTED_MODULES and cleaned not in selected:
                selected.append(cleaned)
        return selected or ["predictive"]

    def _profile_modules(
        self, profile: FundingAssetProfile, requested_modules: list[str] | None
    ) -> list[str]:
        profile_supported = {
            module for module in profile.enabled_modules if module in SUPPORTED_MODULES
        }
        selected = [
            module
            for module in self._supported_modules(requested_modules)
            if module in profile_supported
        ]
        return selected or ["predictive"]

    def _backtest_symbol(self, profile: FundingAssetProfile) -> str:
        return profile.backfill_symbol

    def _readiness_for_profile(
        self,
        profile: FundingAssetProfile,
        *,
        metric_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sqlite_counts = self._sqlite_counts(profile)
        price_rows = self._price_row_count(profile)
        missing = [table for table in REQUIRED_SQLITE_TABLES if sqlite_counts[f"{table}_rows"] <= 0]
        if price_rows <= 0:
            missing.append("price_bars")
        crypto_rows = self._crypto_derivatives_row_count(profile)
        if profile.symbol == "BTC/USDT" and crypto_rows <= 0:
            missing.append("crypto_derivatives_snapshots")
        provider_readiness = self._provider_readiness_for_profile(profile)
        if provider_readiness["source_tier"] == UNAVAILABLE:
            missing.append("cfd_provider_snapshots")
        gex_validation = self._gex_validation_for_profile(profile)
        crypto_options_readiness = self._crypto_options_readiness_for_profile(profile)
        metric_payload = metric_status or self._metric_status([profile])
        metric_coverage = metric_payload["metric_coverage"].get(
            profile.symbol, _empty_profile_metric_coverage(profile)
        )
        missing_metric_families = metric_payload["missing_metric_families"].get(
            profile.symbol, sorted(_required_metric_modules(profile))
        )
        quality_score = metric_payload["quality_by_symbol"].get(profile.symbol)
        data_ready = not missing and not missing_metric_families
        production_blockers = _provider_production_blockers(profile, provider_readiness)
        production_blockers.extend(gex_validation.get("gex_blockers") or [])
        if not data_ready:
            production_blockers.append("data_not_ready")
        production_blockers = _dedupe(production_blockers)
        ready = data_ready
        production_ready = data_ready and not production_blockers
        provider_symbol = _resolved_provider_symbol(profile, provider_readiness)
        return {
            "symbol": profile.symbol,
            "data_symbol": profile.data_symbol,
            "backfill_symbol": profile.backfill_symbol,
            "execution_symbol": profile.execution_symbol,
            "provider_symbol": provider_symbol,
            "data_provider_policy": provider_policy_for_symbol(profile.symbol).to_dict(),
            "proxy_source": profile.intraday_proxy_symbol,
            "asset_class": profile.asset_class,
            "ready": ready,
            "data_ready": data_ready,
            "production_ready": production_ready,
            "production_blockers": production_blockers,
            "missing": missing,
            "prediction_rows": sqlite_counts["predictions_rows"],
            "outcome_rows": sqlite_counts["outcomes_rows"],
            "feature_snapshot_rows": sqlite_counts["feature_snapshots_rows"],
            "ohlcv_rows": price_rows,
            "crypto_derivatives_rows": crypto_rows,
            "source_tier": (
                provider_readiness["source_tier"] if data_ready else profile.source_tier
            ),
            "gex_validation": gex_validation,
            "gex_validated": gex_validation["gex_validated"],
            "gex_context_ready": gex_validation["gex_context_ready"],
            "gex_source_tier": gex_validation["gex_source_tier"],
            "gex_provider": gex_validation["gex_provider"],
            "gex_data_quality_score": gex_validation["gex_data_quality_score"],
            "gex_last_snapshot_at": gex_validation["gex_last_snapshot_at"],
            "gex_blockers": gex_validation["gex_blockers"],
            "crypto_options_readiness": crypto_options_readiness,
            "crypto_options_ready": crypto_options_readiness["crypto_options_ready"],
            "crypto_options_status": crypto_options_readiness["crypto_options_status"],
            "crypto_options_context_only": crypto_options_readiness["crypto_options_context_only"],
            "crypto_options_gex_authorization": crypto_options_readiness[
                "crypto_options_gex_authorization"
            ],
            "crypto_options_provider_health": crypto_options_readiness[
                "crypto_options_provider_health"
            ],
            "crypto_options_metrics": crypto_options_readiness["crypto_options_metrics"],
            "crypto_options_blockers": crypto_options_readiness["crypto_options_blockers"],
            "provider_readiness": provider_readiness,
            "primary_provider": provider_readiness["primary_provider"],
            "broker_validation_status": provider_readiness["broker_validation_status"],
            "fidelity_score": provider_readiness["fidelity_score"],
            "proxy_fallback_reason": provider_readiness["proxy_fallback_reason"],
            "required_horizons": list(STRICT_HORIZONS),
            "metric_coverage": metric_coverage,
            "missing_metric_families": missing_metric_families,
            "quality_score": quality_score,
        }

    def _metric_status(self, profiles: list[FundingAssetProfile]) -> dict[str, Any]:
        symbols = [profile.symbol for profile in profiles]
        modules_by_symbol = {
            profile.symbol: _required_metric_modules(profile) for profile in profiles
        }
        return load_funding_lab_metric_status(
            self.predictions_db,
            symbols=symbols,
            modules_by_symbol=modules_by_symbol,
        )

    def _provider_readiness_for_profile(self, profile: FundingAssetProfile) -> dict[str, Any]:
        readiness = load_provider_readiness(self.predictions_db, profile.symbol)
        readiness["preferred_provider_order"] = list(profile.preferred_provider_order)
        readiness["primary_cfd_symbol"] = profile.primary_cfd_symbol
        readiness["broker_validation_symbol"] = profile.broker_validation_symbol
        readiness["proxy_symbol"] = profile.proxy_symbol
        readiness["intraday_proxy_symbol"] = profile.intraday_proxy_symbol
        return readiness

    def _gex_validation_for_profile(self, profile: FundingAssetProfile) -> dict[str, Any]:
        return load_ftmo_gex_validation(self.predictions_db, profile.symbol)

    def _crypto_options_readiness_for_profile(self, profile: FundingAssetProfile) -> dict[str, Any]:
        return crypto_options_status_for_symbol(self.predictions_db, profile.symbol)

    def _provider_health_summary(
        self, provider_readiness: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        summary = {
            name: {
                "status": "missing",
                "ok": False,
                "latency_ms": None,
                "error": "no health probe recorded",
                "rate_limit_remaining": None,
                "updated_at": None,
            }
            for name in provider_registry()
        }
        for readiness in provider_readiness.values():
            health = readiness.get("provider_health")
            if not isinstance(health, dict):
                continue
            for provider, payload in health.items():
                if isinstance(payload, dict):
                    summary[str(provider)] = payload
        return summary

    def _sqlite_counts(self, profile: FundingAssetProfile) -> dict[str, int]:
        counts = {
            "predictions_rows": 0,
            "outcomes_rows": 0,
            "feature_snapshots_rows": 0,
        }
        if not self.predictions_db.exists():
            return counts

        symbols = _sqlite_symbol_aliases(profile)
        placeholders = ",".join("?" for _ in symbols)
        con = sqlite3.connect(self.predictions_db)
        try:
            existing_tables = _sqlite_tables(con)
            if "predictions" in existing_tables:
                counts["predictions_rows"] = int(
                    con.execute(
                        f"SELECT COUNT(*) FROM predictions WHERE UPPER(symbol) IN ({placeholders})",
                        symbols,
                    ).fetchone()[0]
                )
            if "predictions" in existing_tables and "outcomes" in existing_tables:
                counts["outcomes_rows"] = int(
                    con.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM outcomes o
                        JOIN predictions p ON p.prediction_id = o.prediction_id
                        WHERE UPPER(p.symbol) IN ({placeholders})
                        """,
                        symbols,
                    ).fetchone()[0]
                )
            if "predictions" in existing_tables and "feature_snapshots" in existing_tables:
                counts["feature_snapshots_rows"] = int(
                    con.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM feature_snapshots fs
                        JOIN predictions p ON p.prediction_id = fs.prediction_id
                        WHERE UPPER(p.symbol) IN ({placeholders})
                        """,
                        symbols,
                    ).fetchone()[0]
                )
        except sqlite3.Error as exc:
            logger.info(
                "funding_lab.sqlite_readiness_failed symbol=%s error=%s", profile.symbol, exc
            )
        finally:
            con.close()
        return counts

    def _price_row_count(self, profile: FundingAssetProfile) -> int:
        if not self.price_db.exists():
            return 0
        try:
            con = duckdb.connect(str(self.price_db), read_only=True)
            try:
                tables = {
                    str(row[0]).lower()
                    for row in con.execute("SHOW TABLES").fetchall()
                    if row and row[0] is not None
                }
                if "ohlcv_daily_v3" not in tables:
                    return 0
                symbols = _duckdb_symbol_aliases(profile)
                placeholders = ",".join("?" for _ in symbols)
                return int(
                    con.execute(
                        f"SELECT COUNT(*) FROM ohlcv_daily_v3 WHERE UPPER(symbol) IN ({placeholders})",
                        symbols,
                    ).fetchone()[0]
                )
            finally:
                con.close()
        except Exception as exc:
            logger.info(
                "funding_lab.price_readiness_failed symbol=%s error=%s", profile.symbol, exc
            )
            return 0

    def _crypto_derivatives_row_count(self, profile: FundingAssetProfile) -> int:
        if profile.symbol != "BTC/USDT" or not self.predictions_db.exists():
            return 0
        con = sqlite3.connect(self.predictions_db)
        try:
            tables = _sqlite_tables(con)
            if "crypto_derivatives_snapshots" not in tables:
                return 0
            return int(
                con.execute(
                    "SELECT COUNT(*) FROM crypto_derivatives_snapshots WHERE UPPER(symbol) = ?",
                    ("BTCUSDT",),
                ).fetchone()[0]
            )
        except sqlite3.Error as exc:
            logger.info(
                "funding_lab.crypto_derivatives_readiness_failed symbol=%s error=%s",
                profile.symbol,
                exc,
            )
            return 0
        finally:
            con.close()

    def _coverage_by_horizon(self, profile: FundingAssetProfile) -> dict[str, int]:
        coverage = {horizon: 0 for horizon in STRICT_INTRADAY_HORIZONS}
        if not self.predictions_db.exists():
            return coverage
        aliases = _sqlite_symbol_aliases(profile)
        placeholders = ",".join("?" for _ in aliases)
        con = sqlite3.connect(self.predictions_db)
        try:
            tables = _sqlite_tables(con)
            if "predictions" not in tables:
                return coverage
            columns = {
                str(row[1]) for row in con.execute("PRAGMA table_info(predictions)").fetchall()
            }
            column_by_horizon = {
                "1h": "outcome_return_1h",
                "4h": "outcome_return_4h",
                "eod": "outcome_return_eod",
            }
            for horizon, column in column_by_horizon.items():
                if column not in columns:
                    continue
                coverage[horizon] = int(
                    con.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM predictions
                        WHERE UPPER(symbol) IN ({placeholders})
                          AND {column} IS NOT NULL
                        """,
                        aliases,
                    ).fetchone()[0]
                )
        except sqlite3.Error as exc:
            logger.info("funding_lab.coverage_failed symbol=%s error=%s", profile.symbol, exc)
        finally:
            con.close()
        return coverage

    def _legacy_coverage_by_horizon(self, profile: FundingAssetProfile) -> dict[str, int]:
        coverage = {f"{days}d": 0 for days in (1, 3, 5)}
        if not self.predictions_db.exists():
            return coverage
        aliases = _sqlite_symbol_aliases(profile)
        placeholders = ",".join("?" for _ in aliases)
        con = sqlite3.connect(self.predictions_db)
        try:
            tables = _sqlite_tables(con)
            if not {"predictions", "outcomes"}.issubset(tables):
                return coverage
            rows = con.execute(
                f"""
                SELECT o.n_days, COUNT(*)
                FROM outcomes o
                JOIN predictions p ON p.prediction_id = o.prediction_id
                WHERE UPPER(p.symbol) IN ({placeholders})
                  AND o.n_days IN (1,3,5)
                GROUP BY o.n_days
                """,
                aliases,
            ).fetchall()
            for days, count in rows:
                coverage[f"{int(days)}d"] = int(count)
        except sqlite3.Error as exc:
            logger.info("funding_lab.coverage_failed symbol=%s error=%s", profile.symbol, exc)
        finally:
            con.close()
        return coverage

    def _latest_backfill_run(self) -> dict[str, Any]:
        if not self.predictions_db.exists():
            return {"status": "missing", "updated_at": None, "source": str(self.predictions_db)}
        con = sqlite3.connect(self.predictions_db)
        try:
            tables = _sqlite_tables(con)
            candidates: list[str] = []
            if "backfill_coverage_audit" in tables:
                rows = con.execute(
                    "SELECT updated_at FROM backfill_coverage_audit WHERE updated_at IS NOT NULL"
                ).fetchall()
                candidates.extend(str(row[0]) for row in rows if row and row[0])
            if "crypto_derivatives_snapshots" in tables:
                rows = con.execute(
                    "SELECT created_at FROM crypto_derivatives_snapshots WHERE created_at IS NOT NULL"
                ).fetchall()
                candidates.extend(str(row[0]) for row in rows if row and row[0])
            if not candidates:
                return {
                    "status": "missing",
                    "updated_at": None,
                    "source": str(self.predictions_db),
                }
            return {
                "status": "available",
                "updated_at": max(candidates),
                "source": str(self.predictions_db),
            }
        except sqlite3.Error as exc:
            logger.info("funding_lab.latest_backfill_failed error=%s", exc)
            return {"status": "error", "updated_at": None, "source": str(self.predictions_db)}
        finally:
            con.close()

    def _next_actions(self, rows: list[dict[str, Any]]) -> list[str]:
        if all(row["production_ready"] for row in rows):
            return []
        missing = sorted({item for row in rows for item in row.get("missing", [])})
        actions = [
            "Run powershell -ExecutionPolicy Bypass -File scripts/run-funding-lab-backfill.ps1"
        ]
        provider_blockers = sorted(
            {
                blocker
                for row in rows
                for blocker in row.get("production_blockers", [])
                if blocker != "data_not_ready"
            }
        )
        if provider_blockers:
            actions.append(
                "Refresh provider snapshots and run backend/scripts/funding_lab_readiness_monitor.py --json --strict --require-command-deck-check."
            )
        if "crypto_derivatives_snapshots" in missing:
            actions.append("Validate Binance USD-M derivatives coverage for BTCUSDT.")
        if "price_bars" in missing:
            actions.append("Backfill OHLCV V3 for GOOGL,AAPL,TSLA,GC=F,SI=F,QQQ,BTC-USD.")
        metric_missing = any(row.get("missing_metric_families") for row in rows)
        if metric_missing:
            actions.append("Materialize funding_lab_metric_snapshots from intraday outcomes.")
        return actions

    def _evidence_from_report(
        self, profile: FundingAssetProfile, report: dict[str, Any]
    ) -> list[dict[str, Any]]:
        results = report.get("results") if isinstance(report, dict) else None
        if not isinstance(results, list):
            return []
        aliases = set(_sqlite_symbol_aliases(profile))
        evidence: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            symbol = str(result.get("symbol") or "").upper().strip()
            if symbol in aliases:
                evidence.append(_module_evidence_from_backtest(result))
        return evidence


def _module_evidence_from_backtest(result: dict[str, Any]) -> dict[str, Any]:
    funding_metrics = result.get("funding_risk_metrics")
    if not isinstance(funding_metrics, dict):
        funding_metrics = {}
    source_tier = result.get("source_tier") or "full_chain_gex"
    suitability = evaluate_module_evidence(
        module=str(result.get("module") or "unknown"),
        backtest_evidence=result,
        source_tier=str(source_tier),
        data_quality_score=_float_or_none(result.get("data_quality_score")),
        signal_coverage=_float_or_none(result.get("signal_coverage_pct")),
    )
    return {
        "module": result.get("module"),
        "n_days": result.get("n_days"),
        "horizon": result.get("horizon"),
        "module_backtest_grade": result.get("module_backtest_grade"),
        "funding_survival_grade": funding_metrics.get("funding_survival_grade"),
        "source_tier": source_tier,
        "data_quality_score": result.get("data_quality_score"),
        "signal_coverage_pct": result.get("signal_coverage_pct"),
        "total_return_pct": result.get("total_return_pct"),
        "profit_factor": result.get("profit_factor"),
        "sharpe": result.get("sharpe"),
        "max_drawdown_pct": result.get("max_drawdown_pct"),
        "funding_risk_metrics": funding_metrics,
        "suitability": suitability.get("suitability", SUITABILITY_ALLOW),
        "reason_codes": suitability.get("reasons", []),
    }


def _command_deck_cache_key(
    *,
    symbols: list[str],
    modules: list[str],
    limit_per_symbol: int,
) -> str:
    return "|".join(
        [
            ",".join(symbols),
            ",".join(modules),
            str(max(1, min(int(limit_per_symbol), 100_000))),
        ]
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _normalize_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        if "symbol" in payload:
            return [payload]
        return []
    if isinstance(payload, list):
        out: list[dict[str, Any]] = []
        for row in payload:
            if hasattr(row, "model_dump"):
                row = row.model_dump(mode="json")
            if isinstance(row, dict):
                out.append(row)
        return out
    return []


def _payload_by_symbol(payload: Any) -> dict[str, dict[str, Any]]:
    rows = _normalize_payload_rows(payload)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        out[_symbol_key(symbol)] = row
        out[symbol] = row
    return out


def _lookup_payload_for_profile(
    payload_by_symbol: dict[str, dict[str, Any]],
    profile: FundingAssetProfile,
) -> dict[str, Any] | None:
    aliases = {
        profile.symbol,
        profile.data_symbol,
        profile.backfill_symbol,
        profile.execution_symbol,
        profile.primary_cfd_symbol,
        profile.broker_validation_symbol,
        profile.proxy_symbol,
        *(profile.data_aliases or ()),
    }
    for alias in aliases:
        if alias in payload_by_symbol:
            return payload_by_symbol[alias]
        key = _symbol_key(alias)
        if key in payload_by_symbol:
            return payload_by_symbol[key]
    return None


def _symbol_key(value: str) -> str:
    return str(value or "").upper().replace("/", "").replace(".", "").replace("-", "").strip()


def _gex_summary_from_scanner(scanner: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(scanner, dict):
        return None
    overlay = scanner.get("institutional_overlay")
    if not isinstance(overlay, dict) or not overlay.get("snapshot_ok"):
        return None
    return {
        "snapshot_ok": bool(overlay.get("snapshot_ok")),
        "spot": overlay.get("spot"),
        "gamma_flip": overlay.get("gamma_flip"),
        "net_gex_total": overlay.get("net_gex_total"),
        "dealer_bias": overlay.get("dealer_bias"),
        "call_wall": overlay.get("call_wall"),
        "put_wall": overlay.get("put_wall"),
        "pressure_by_strike": overlay.get("pressure_by_strike") or [],
        "microstructure": overlay.get("microstructure") or {},
        "iv_term_structure": overlay.get("iv_term_structure") or {},
    }


def _predictive_summary_from_context(
    *,
    symbol: str,
    scanner: dict[str, Any] | None,
    decision: dict[str, Any] | None,
) -> dict[str, Any]:
    module_signal: dict[str, Any] | None = None
    if isinstance(scanner, dict):
        module_signals = scanner.get("module_signals")
        if isinstance(module_signals, dict):
            raw_signal = module_signals.get("probabilistic") or module_signals.get("predictive")
            if isinstance(raw_signal, dict):
                module_signal = raw_signal
    evidence = _best_predictive_evidence(decision)
    if module_signal:
        direction = _predictive_direction_from_scanner(scanner)
        return {
            "status": "available",
            "symbol": symbol,
            "score": module_signal.get("score"),
            "direction": direction,
            "confidence": module_signal.get("confidence"),
            "conviction_level": _conviction_from_confidence(module_signal.get("confidence")),
            "conflict_score": (evidence or {}).get("conflict_score"),
            "should_trade": (decision or {}).get("decision") == "ALLOW",
            "source": "scanner_probabilistic",
        }
    if evidence:
        score = evidence.get("total_return_pct")
        return {
            "status": "evidence_only",
            "symbol": symbol,
            "score": score,
            "direction": "NEUTRAL",
            "confidence": evidence.get("data_quality_score"),
            "conviction_level": "INSUFFICIENT",
            "conflict_score": None,
            "should_trade": (decision or {}).get("decision") == "ALLOW",
            "source": "funding_backtest_evidence",
        }
    return {
        "status": "unavailable",
        "symbol": symbol,
        "score": None,
        "direction": "NEUTRAL",
        "confidence": None,
        "conviction_level": "INSUFFICIENT",
        "conflict_score": None,
        "should_trade": False,
        "source": "not_materialized",
    }


def _best_predictive_evidence(decision: dict[str, Any] | None) -> dict[str, Any] | None:
    evidence = decision.get("module_evidence") if isinstance(decision, dict) else None
    if not isinstance(evidence, list):
        return None
    predictive = [
        row for row in evidence if isinstance(row, dict) and row.get("module") == "predictive"
    ]
    if not predictive:
        return None
    return next((row for row in predictive if row.get("horizon") == "eod"), None) or predictive[0]


def _predictive_direction_from_scanner(scanner: dict[str, Any] | None) -> str:
    direction = str((scanner or {}).get("direction") or "").lower()
    if direction == "bullish":
        return "UP"
    if direction == "bearish":
        return "DOWN"
    return "NEUTRAL"


def _conviction_from_confidence(value: Any) -> str:
    confidence = _float_or_none(value)
    if confidence is None:
        return "INSUFFICIENT"
    if confidence >= 0.85:
        return "VERY_HIGH"
    if confidence >= 0.7:
        return "HIGH"
    if confidence >= 0.55:
        return "MEDIUM"
    return "LOW"


def _command_deck_summary(
    rows: list[dict[str, Any]],
    status_payload: dict[str, Any],
    backtest_payload: dict[str, Any],
) -> dict[str, Any]:
    decisions = [row.get("decision") for row in rows if isinstance(row.get("decision"), dict)]
    return {
        "asset_count": len(rows),
        "ready_count": int(
            (status_payload.get("readiness_summary") or {}).get("ready_symbols") or 0
        ),
        "allow_count": sum(
            1 for decision in decisions if decision and decision.get("decision") == "ALLOW"
        ),
        "no_trade_count": sum(
            1 for decision in decisions if decision and decision.get("decision") == "NO_TRADE"
        ),
        "scanner_available_count": sum(1 for row in rows if row.get("scanner")),
        "predictive_available_count": sum(
            1
            for row in rows
            if isinstance(row.get("predictive"), dict)
            and row["predictive"].get("status") != "unavailable"
        ),
        "gex_available_count": sum(1 for row in rows if row.get("gex")),
        "risk_summary": (backtest_payload.get("report") or {}).get("risk_summary"),
        "walk_forward_summary": (backtest_payload.get("report") or {}).get("walk_forward_summary"),
    }


def _required_metric_modules(profile: FundingAssetProfile) -> tuple[str, ...]:
    modules = [module for module in profile.required_modules if module in DEFAULT_METRIC_MODULES]
    for context_module in profile.context_modules:
        normalized = "options_gex" if context_module == "options_gex_context" else context_module
        if normalized in DEFAULT_METRIC_MODULES and normalized not in modules:
            modules.append(normalized)
    if "risk" not in modules:
        modules.append("risk")
    return tuple(modules)


def _empty_profile_metric_coverage(profile: FundingAssetProfile) -> dict[str, dict[str, int]]:
    return {
        module: {horizon: 0 for horizon in METRIC_HORIZONS}
        for module in _required_metric_modules(profile)
    }


def _operational_readiness_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    asset_validation = {str(row["symbol"]): _asset_validation_from_readiness(row) for row in rows}
    ready_assets = sum(
        1
        for payload in asset_validation.values()
        if payload["validation_status"] == "production_ready"
    )
    proxy_assets = [
        symbol
        for symbol, payload in asset_validation.items()
        if payload["data_source_status"] == "proxy"
    ]
    return {
        "schedule": {
            "cadence": "daily_after_us_rth_close",
            "timezone": "America/New_York",
            "recommended_command": (
                "powershell -ExecutionPolicy Bypass -File scripts/run-funding-lab-backfill.ps1"
            ),
            "preflight_command": (
                "python backend/scripts/funding_lab_provider_check.py --days 90 --live --skip-options-gex"
            ),
            "monitor_command": (
                "python backend/scripts/funding_lab_readiness_monitor.py --json --strict --require-command-deck-check"
            ),
            "quality_gate": (
                "FUNDING_LAB_RUN_FULL_QA=true powershell -ExecutionPolicy Bypass "
                "-File scripts/qa-funding-lab.ps1"
            ),
        },
        "asset_validation": asset_validation,
        "summary": {
            "total_assets": len(asset_validation),
            "ready_assets": ready_assets,
            "not_ready_assets": len(asset_validation) - ready_assets,
            "proxy_assets": proxy_assets,
        },
    }


def _last_verified_at(
    *,
    rows: list[dict[str, Any]],
    latest_backfill_run: dict[str, Any],
    latest_metric_run: dict[str, Any],
    generated_at: str,
) -> str:
    candidates: list[str] = []
    for payload in (latest_backfill_run, latest_metric_run):
        value = payload.get("updated_at") if isinstance(payload, dict) else None
        if isinstance(value, str) and value.strip():
            candidates.append(value)
    for row in rows:
        provider = row.get("provider_readiness")
        if not isinstance(provider, dict):
            continue
        latest_by_provider = provider.get("latest_by_provider")
        if isinstance(latest_by_provider, dict):
            candidates.extend(str(value) for value in latest_by_provider.values() if value)
        health_by_provider = provider.get("provider_health")
        if isinstance(health_by_provider, dict):
            for health in health_by_provider.values():
                if not isinstance(health, dict):
                    continue
                for key in ("updated_at", "checked_at"):
                    value = health.get(key)
                    if isinstance(value, str) and value.strip():
                        candidates.append(value)
    parsed = [_parse_iso_datetime(value) for value in candidates]
    parsed_valid = [value for value in parsed if value is not None]
    if not parsed_valid:
        return generated_at
    return max(parsed_valid).isoformat()


def _latest_monitor_report(
    report_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if report_dir is None:
        report_dir = DEFAULT_MONITOR_REPORT_DIR
    try:
        candidates = sorted(report_dir.glob("readiness-monitor-*.json"))
    except OSError as exc:
        logger.info("funding_lab.latest_monitor_scan_failed error=%s", exc)
        return (
            {
                "status": "error",
                "updated_at": None,
                "source": str(report_dir),
            },
            None,
        )
    if not candidates:
        return (
            {
                "status": "missing",
                "updated_at": None,
                "source": str(report_dir),
            },
            None,
        )
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.info("funding_lab.latest_monitor_read_failed path=%s error=%s", latest, exc)
        return (
            {
                "status": "error",
                "updated_at": None,
                "source": str(latest),
            },
            None,
        )
    if not isinstance(payload, dict):
        return (
            {
                "status": "error",
                "updated_at": None,
                "source": str(latest),
            },
            None,
        )
    report = {**payload, "report_path": str(latest)}
    return (
        {
            "status": "available",
            "updated_at": payload.get("generated_at"),
            "monitor_id": payload.get("monitor_id") or latest.stem,
            "exit_code": payload.get("exit_code"),
            "source": str(latest),
            "report_path": str(latest),
        },
        report,
    )


def _calibration_policy_summary() -> dict[str, Any]:
    calibration = default_backtest_calibration().to_dict()
    return {
        **calibration,
        "horizons": list(STRICT_INTRADAY_HORIZONS),
        "source": "environment_or_defaults",
        "env_overrides": {
            "min_trades": "FUNDING_LAB_MIN_TRADES",
            "min_signal_coverage_pct": "FUNDING_LAB_MIN_SIGNAL_COVERAGE_PCT",
            "min_profit_factor": "FUNDING_LAB_MIN_PROFIT_FACTOR",
            "min_sharpe": "FUNDING_LAB_MIN_SHARPE",
            "max_validated_drawdown_pct": "FUNDING_LAB_MAX_VALIDATED_DRAWDOWN_PCT",
            "overfit_drawdown_pct": "FUNDING_LAB_OVERFIT_DRAWDOWN_PCT",
        },
    }


def _asset_validation_from_readiness(row: dict[str, Any]) -> dict[str, Any]:
    missing = [str(item) for item in row.get("missing", [])]
    provider = row.get("provider_readiness")
    if not isinstance(provider, dict):
        provider = {}
    source_tier = str(provider.get("source_tier") or row.get("source_tier") or "")
    proxy_source = row.get("proxy_source")
    data_source_status = "proxy" if proxy_source else "primary"
    if source_tier in {PROXY_ONLY, UNAVAILABLE}:
        data_source_status = "unavailable" if source_tier == UNAVAILABLE else "proxy"
    if row.get("production_ready"):
        validation_status = "production_ready"
    elif row.get("data_ready"):
        validation_status = "data_ready"
    else:
        validation_status = "missing_data"
    next_actions: list[str] = []
    if missing:
        next_actions.append("run_backfill")
    if row.get("missing_metric_families"):
        next_actions.append("rebuild_metric_lake")
    if source_tier in {PROXY_ONLY, UNAVAILABLE}:
        next_actions.append("validate_provider_source")
    for blocker in row.get("production_blockers") or []:
        if blocker.startswith("bingx_") or blocker == "stale_provider_snapshot":
            next_actions.append("validate_bingx_market_data")
    if row.get("symbol") == "BTC/USDT" and row.get("crypto_derivatives_rows", 0) <= 0:
        next_actions.append("validate_binance_derivatives")
    return {
        "validation_status": validation_status,
        "production_ready": bool(row.get("production_ready")),
        "production_blockers": list(row.get("production_blockers") or []),
        "data_source_status": data_source_status,
        "provider": provider.get("primary_provider") or row.get("primary_provider"),
        "provider_symbol": row.get("provider_symbol"),
        "proxy_source": proxy_source,
        "source_tier": source_tier,
        "missing": missing,
        "coverage_by_horizon": {
            "1h": int((row.get("metric_coverage") or {}).get("risk", {}).get("1h", 0)),
            "4h": int((row.get("metric_coverage") or {}).get("risk", {}).get("4h", 0)),
            "eod": int((row.get("metric_coverage") or {}).get("risk", {}).get("eod", 0)),
        },
        "quality_score": row.get("quality_score"),
        "next_actions": _dedupe(next_actions),
    }


def _blocker_resolution_plan(reason_codes: list[str]) -> dict[str, dict[str, Any]]:
    plan: dict[str, dict[str, Any]] = {}
    for reason in reason_codes:
        family, action, detail = _resolution_for_reason(reason)
        payload = plan.setdefault(
            family,
            {
                "action": action,
                "reason_codes": [],
                "detail": detail,
            },
        )
        payload["reason_codes"].append(reason)
    for payload in plan.values():
        payload["reason_codes"] = _dedupe(payload["reason_codes"])
    return plan


def _resolution_for_reason(reason: str) -> tuple[str, str, str]:
    if reason in {
        "proxy_only_source",
        "cfd_source_unavailable",
        "low_fidelity_score",
        "bingx_primary_missing",
        "bingx_validation_missing",
        "provider_health_missing",
        "bingx_validation_fidelity_missing",
        "low_bingx_validation_fidelity",
        "market_data_primary_missing",
        "primary_provider_missing",
        "contract_missing",
        "context_provider_missing",
        "context_provider_stale",
        "stale_provider_snapshot",
        "production_not_ready",
        "data_not_ready",
    }:
        return (
            "provider_fidelity",
            "validate_primary_market_data_or_keep_no_trade",
            "Refresh provider snapshots and confirm the selected source tier before allowing risk.",
        )
    if reason in {"weak_edge", "overfit_risk", "non_positive_oos"}:
        return (
            "model_validation",
            "extend_walk_forward_or_retrain",
            "Extend intraday walk-forward evidence or retrain before considering ALLOW.",
        )
    if reason in {"missing_backtest_evidence", "missing_oos_horizons", "insufficient_data"}:
        return (
            "backtesting",
            "rerun_intraday_backfill",
            "Run the 90-day Funding Lab backfill and rebuild metric snapshots.",
        )
    if reason.startswith("required_module_missing:") or reason == "crypto_derivatives_unvalidated":
        return (
            "module_coverage",
            "complete_required_metric_family",
            "Materialize the missing module across 1h, 4h and eod horizons.",
        )
    if reason in {
        "would_breach",
        "funding_at_risk",
        "daily_loss_breach",
        "daily_loss_usage_high",
        "max_loss_breach",
        "max_loss_usage_high",
        "best_day_concentration",
        "consistency_warning",
        "survival_score_below_minimum",
        SUITABILITY_SIZE_DOWN,
        SUITABILITY_BLOCK,
        SUITABILITY_INSUFFICIENT,
    }:
        return (
            "risk_management",
            "reduce_risk_or_keep_no_trade",
            "The FTMO survival gate dominates positive signal evidence.",
        )
    if reason in {"historical_would_breach", "historical_at_risk"}:
        return (
            "model_validation",
            "extend_walk_forward_or_retrain",
            "Historical/OOS evidence violates FTMO survival constraints.",
        )
    if reason == "gex_source_not_validated":
        return (
            "options_gex",
            "validate_full_chain_gex",
            "Options/GEX can authorize only when the source tier is full_chain_gex.",
        )
    return (
        "general",
        "inspect_blocker",
        "Inspect the Funding Lab status and backtest evidence for this blocker.",
    )


def _metric_summary_for_profiles(
    *,
    profiles: list[FundingAssetProfile],
    metric_status: dict[str, Any],
) -> dict[str, Any]:
    symbols = [profile.symbol for profile in profiles]
    coverage = metric_status.get("metric_coverage") or {}
    missing = metric_status.get("missing_metric_families") or {}
    quality = metric_status.get("quality_by_symbol") or {}
    return {
        "symbols": symbols,
        "latest_metric_run": metric_status.get("latest_metric_run"),
        "coverage": {symbol: coverage.get(symbol, {}) for symbol in symbols},
        "missing_metric_families": {symbol: missing.get(symbol, []) for symbol in symbols},
        "quality_by_symbol": {symbol: quality.get(symbol) for symbol in symbols},
        "ready_symbols": [symbol for symbol in symbols if not (missing.get(symbol) or [])],
    }


def _risk_summary_for_decisions(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    max_daily_usage = 0.0
    max_loss_usage = 0.0
    max_best_day = 0.0
    for decision in decisions:
        survival = decision.get("funding_survival")
        if not isinstance(survival, dict):
            continue
        status = str(survival.get("status") or "INSUFFICIENT")
        status_counts[status] = status_counts.get(status, 0) + 1
        max_daily_usage = max(
            max_daily_usage, _float_or_none(survival.get("daily_loss_usage_pct")) or 0.0
        )
        max_loss_usage = max(
            max_loss_usage, _float_or_none(survival.get("max_loss_usage_pct")) or 0.0
        )
        max_best_day = max(
            max_best_day, _float_or_none(survival.get("best_day_contribution_pct")) or 0.0
        )
    return {
        "symbols_evaluated": len(decisions),
        "allow": sum(1 for decision in decisions if decision.get("decision") == "ALLOW"),
        "no_trade": sum(1 for decision in decisions if decision.get("decision") == "NO_TRADE"),
        "survival_status_counts": status_counts,
        "max_daily_loss_usage_pct": round(max_daily_usage, 2),
        "max_loss_usage_pct": round(max_loss_usage, 2),
        "max_best_day_contribution_pct": round(max_best_day, 2),
    }


def _walk_forward_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    grade_matrix: dict[str, dict[str, dict[str, str]]] = {}
    validated = 0
    for result in results:
        symbol = str(result.get("symbol") or "")
        module = str(result.get("module") or "")
        horizon = str(result.get("horizon") or "")
        grade = str(result.get("module_backtest_grade") or "missing")
        if not symbol or not module or not horizon:
            continue
        grade_matrix.setdefault(symbol, {}).setdefault(module, {})[horizon] = grade
        if grade == "validated":
            validated += 1
    return {
        "horizons": list(STRICT_INTRADAY_HORIZONS),
        "validated_results": validated,
        "results_evaluated": len(results),
        "grade_matrix": grade_matrix,
    }


def _critical_metrics_for_signal(
    *,
    profile: FundingAssetProfile,
    readiness: dict[str, Any],
    decision: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    best = _best_evidence(evidence)
    provider = readiness.get("provider_readiness")
    if not isinstance(provider, dict):
        provider = {}
    return {
        "symbol": profile.symbol,
        "provider_symbol": readiness.get("provider_symbol"),
        "proxy_source": readiness.get("proxy_source"),
        "source_tier": readiness.get("source_tier"),
        "quality_score": readiness.get("quality_score"),
        "missing_metric_families": readiness.get("missing_metric_families", []),
        "coverage": readiness.get("metric_coverage", {}),
        "blocking_module": decision.get("blocking_module"),
        "reason_codes": decision.get("reason_codes", []),
        "profit_factor": best.get("profit_factor") if best else None,
        "sharpe": best.get("sharpe") if best else None,
        "best_day_contribution_pct": (
            (best.get("funding_risk_metrics") or {}).get("best_day_contribution_pct")
            if best
            else None
        ),
        "primary_provider": provider.get("primary_provider"),
        "fidelity_score": provider.get("fidelity_score"),
    }


def _best_evidence(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in evidence:
        if row.get("horizon") == "eod" and row.get("module") == "predictive":
            return row
    for row in evidence:
        if row.get("horizon") == "eod":
            return row
    return evidence[0] if evidence else None


def _sqlite_tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_symbol_aliases(profile: FundingAssetProfile) -> list[str]:
    aliases = {
        profile.symbol,
        profile.data_symbol,
        profile.backfill_symbol,
        profile.execution_symbol,
        *profile.data_aliases,
    }
    return sorted({alias.upper().strip() for alias in aliases if alias})


def _duckdb_symbol_aliases(profile: FundingAssetProfile) -> list[str]:
    return _sqlite_symbol_aliases(profile)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _normalize_horizon_key(value: object) -> int | str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in STRICT_INTRADAY_HORIZONS:
            return text
        parsed = _int_or_none(text)
        return parsed if parsed is not None else text
    parsed = _int_or_none(value)
    return parsed if parsed is not None else str(value).strip().lower()


def _evidence_horizon_key(evidence: dict[str, Any]) -> int | str | None:
    raw_horizon = evidence.get("horizon")
    if raw_horizon is not None:
        return _normalize_horizon_key(raw_horizon)
    raw_days = evidence.get("n_days")
    if raw_days is None:
        return None
    return _normalize_horizon_key(raw_days)


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
