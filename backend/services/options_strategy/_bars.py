"""Adaptador OHLCV para capas Options Strategy. # [TH]"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from backend.config.r1_enrichment_thresholds import INTRADAY_5M_MIN_BARS
from backend.config.options_strategy_loader import OptionsStrategyConfigBundle
from backend.models.market_snapshot import OHLCVBar
from backend.models.options_strategy import OptionsStrategyInput

MIN_TECHNICAL_BARS = 30
MIN_PREDICTIVE_BARS = 20


def _bar_to_row(bar: OHLCVBar) -> dict[str, Any]:
    return {
        "date": bar.time,
        "timestamp": bar.time,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.volume),
    }


def _intraday_row(bar: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return {
            "date": bar.get("t"),
            "timestamp": bar.get("t"),
            "open": float(bar.get("open") or bar.get("o") or 0),
            "high": float(bar.get("high") or bar.get("h") or 0),
            "low": float(bar.get("low") or bar.get("l") or 0),
            "close": float(bar.get("close") or bar.get("c") or 0),
            "volume": float(bar.get("volume") or bar.get("v") or 0),
        }
    except (TypeError, ValueError):
        return None


def ohlcv_frame_from_input(
    inp: OptionsStrategyInput,
    *,
    min_bars: int = MIN_TECHNICAL_BARS,
) -> pd.DataFrame | None:
    """Construye OHLCV: prioriza barras 5m R1, fallback a ``market_snapshot`` diario."""
    enrichment = inp.r1_enrichment
    if enrichment is not None and enrichment.intraday_bars_5m:
        intraday_min = max(min_bars, INTRADAY_5M_MIN_BARS)
        rows = [
            row
            for bar in enrichment.intraday_bars_5m
            if (row := _intraday_row(bar)) is not None
        ]
        if len(rows) >= intraday_min:
            return pd.DataFrame(rows)

    if inp.market_snapshot is None or not inp.market_snapshot.ohlcv:
        return None
    rows = [_bar_to_row(bar) for bar in inp.market_snapshot.ohlcv]
    if len(rows) < min_bars:
        return None
    return pd.DataFrame(rows)


def resolve_spot_price(inp: OptionsStrategyInput, frame: pd.DataFrame | None) -> float:
    if inp.market_snapshot is not None:
        return float(inp.market_snapshot.price)
    if frame is not None and not frame.empty:
        return float(frame["close"].iloc[-1])
    if inp.options_context is not None:
        snap = inp.options_context.snapshot or {}
        spot = snap.get("spot")
        if spot is not None:
            return float(spot)
    return 0.0


def resolve_atm_iv(inp: OptionsStrategyInput, default: float = 0.25) -> float:
    ctx = inp.options_context
    if ctx is None:
        return default
    snap = ctx.snapshot or {}
    iv_surface = snap.get("iv_surface") or {}
    atm = iv_surface.get("atm_iv")
    if atm is not None and float(atm) > 0:
        return float(atm)
    return default


def resolve_target_dte(config: OptionsStrategyConfigBundle) -> int:
    universe = config.universe
    return max(1, (universe.dte_min + universe.dte_max) // 2)


def chain_rows_for_tail_risk(inp: OptionsStrategyInput, spot: float) -> pd.DataFrame | None:
    """Expande filas de cadena R1 a formato smile para ``TailRiskEngine``."""
    ctx = inp.options_context
    if ctx is None or spot <= 0:
        return None
    chain = ctx.snapshot.get("chain") if ctx.snapshot else None
    if not isinstance(chain, list) or not chain:
        return None

    rows: list[dict[str, Any]] = []
    for item in chain:
        if not isinstance(item, dict):
            continue
        strike = item.get("strike")
        if strike is None:
            continue
        strike_f = float(strike)
        call_iv = item.get("call_iv")
        put_iv = item.get("put_iv")
        call_delta = item.get("call_delta")
        put_delta = item.get("put_delta")
        if call_iv is not None and float(call_iv) > 0:
            rows.append(
                {
                    "strike": strike_f,
                    "iv": float(call_iv),
                    "option_type": "CALL",
                    "delta": float(call_delta or 0.0),
                    "spot_price": spot,
                }
            )
        if put_iv is not None and float(put_iv) > 0:
            rows.append(
                {
                    "strike": strike_f,
                    "iv": float(put_iv),
                    "option_type": "PUT",
                    "delta": float(put_delta or 0.0),
                    "spot_price": spot,
                }
            )
    if len(rows) < 4:
        return None
    return pd.DataFrame(rows)


def safe_decimal_price(value: Decimal | float | int) -> float:
    return float(value)
