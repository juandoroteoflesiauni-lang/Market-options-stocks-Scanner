"""Utilidades de cadena de opciones para Options Strategy. # [TH]"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd

from backend.services.options_strategy._bars import resolve_atm_iv, resolve_spot_price
from backend.models.options_strategy import OptionsStrategyInput


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def chain_rows(inp: OptionsStrategyInput) -> list[dict[str, Any]]:
    ctx = inp.options_context
    if ctx is None or not ctx.snapshot:
        return []
    chain = ctx.snapshot.get("chain")
    if not isinstance(chain, list):
        return []
    return [row for row in chain if isinstance(row, dict)]


def parse_expiry_date(raw: str | None) -> date | None:
    if not raw:
        return None
    token = str(raw).strip()
    if not token:
        return None
    try:
        if "T" in token:
            return datetime.fromisoformat(token.replace("Z", "+00:00")).date()
        return date.fromisoformat(token[:10])
    except ValueError:
        return None


def dte_from_expiry(expiry: date, *, as_of: datetime | date) -> int:
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    return max((expiry - as_of_date).days, 0)


def flow_rows_from_chain(inp: OptionsStrategyInput) -> list[dict[str, Any]]:
    spot = resolve_spot_price(inp, None)
    rows: list[dict[str, Any]] = []
    for item in chain_rows(inp):
        strike = _safe_float(item.get("strike"))
        if strike <= 0:
            continue
        expiry = item.get("expiration") or item.get("expiry")
        exp_date = parse_expiry_date(str(expiry) if expiry else None)
        dte = dte_from_expiry(exp_date, as_of=inp.as_of) if exp_date else None
        base = {
            "underlying": inp.symbol,
            "ticker": inp.symbol,
            "strike": strike,
            "expiry": str(expiry or ""),
            "spot": spot,
            "dte": dte,
        }
        call_vol = _safe_float(item.get("call_volume"))
        put_vol = _safe_float(item.get("put_volume"))
        call_oi = _safe_float(item.get("call_oi"))
        put_oi = _safe_float(item.get("put_oi"))
        if call_vol > 0 or call_oi > 0:
            rows.append(
                {
                    **base,
                    "right": "call",
                    "volume": call_vol,
                    "open_interest": call_oi,
                    "mark": _safe_float(item.get("call_mark") or item.get("call_mid"), 0.0),
                    "side": "buy" if call_vol >= put_vol else "unknown",
                }
            )
        if put_vol > 0 or put_oi > 0:
            rows.append(
                {
                    **base,
                    "right": "put",
                    "volume": put_vol,
                    "open_interest": put_oi,
                    "mark": _safe_float(item.get("put_mark") or item.get("put_mid"), 0.0),
                    "side": "buy" if put_vol > call_vol else "unknown",
                }
            )
    return rows


def dealer_flow_frame(inp: OptionsStrategyInput) -> pd.DataFrame | None:
    rows: list[dict[str, Any]] = []
    for item in chain_rows(inp):
        strike = _safe_float(item.get("strike"))
        if strike <= 0:
            continue
        call_oi = _safe_float(item.get("call_oi"))
        put_oi = _safe_float(item.get("put_oi"))
        total_oi = call_oi + put_oi
        if total_oi <= 0:
            continue
        call_delta = _safe_float(item.get("call_delta"))
        put_delta = _safe_float(item.get("put_delta"))
        call_gamma = _safe_float(item.get("call_gamma"))
        put_gamma = _safe_float(item.get("put_gamma"))
        rows.append(
            {
                "strike": strike,
                "call_oi": call_oi,
                "put_oi": put_oi,
                "delta": (call_delta * call_oi + put_delta * put_oi) / total_oi,
                "gamma": (call_gamma * call_oi + put_gamma * put_oi) / total_oi,
                "vanna": (
                    _safe_float(item.get("call_vanna")) * call_oi
                    + _safe_float(item.get("put_vanna")) * put_oi
                )
                / total_oi,
                "charm": (
                    _safe_float(item.get("call_charm")) * call_oi
                    + _safe_float(item.get("put_charm")) * put_oi
                )
                / total_oi,
                "implied_vol": _safe_float(item.get("call_iv") or item.get("put_iv")),
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows)


def dex_frame(inp: OptionsStrategyInput) -> pd.DataFrame | None:
    spot = resolve_spot_price(inp, None)
    if spot <= 0:
        return None
    rows: list[dict[str, Any]] = []
    for item in chain_rows(inp):
        strike = _safe_float(item.get("strike"))
        if strike <= 0:
            continue
        for opt_type, delta_key, oi_key in (
            ("call", "call_delta", "call_oi"),
            ("put", "put_delta", "put_oi"),
        ):
            oi = _safe_float(item.get(oi_key))
            if oi <= 0:
                continue
            delta = _safe_float(item.get(delta_key))
            rows.append(
                {
                    "ticker": inp.symbol.upper(),
                    "strike": strike,
                    "option_type": opt_type,
                    "delta": delta,
                    "open_interest": oi,
                    "spot_price": spot,
                }
            )
    if not rows:
        return None
    return pd.DataFrame(rows)


def gamma_flip_array(inp: OptionsStrategyInput) -> np.ndarray | None:
    entries: list[list[float]] = []
    for item in chain_rows(inp):
        strike = _safe_float(item.get("strike"))
        if strike <= 0:
            continue
        call_oi = _safe_float(item.get("call_oi"))
        put_oi = _safe_float(item.get("put_oi"))
        if call_oi > 0:
            entries.append([strike, 1.0, call_oi])
        if put_oi > 0:
            entries.append([strike, 0.0, put_oi])
    if not entries:
        return None
    return np.asarray(entries, dtype=np.float64)


def classify_iv_state(atm_iv: float, hv: float | None) -> str:
    if hv is None or hv <= 0 or atm_iv <= 0:
        return "unknown"
    ratio = atm_iv / hv
    if ratio >= 1.25:
        return "extreme" if ratio >= 1.45 else "rich"
    if ratio <= 0.90:
        return "cheap"
    return "fair"


def resolve_chain_leg_mark(row: dict[str, Any], prefix: str) -> float:
    """Mark operativo por pata: mark/mid → NBBO → last → day close."""
    mark = _safe_float(row.get(f"{prefix}_mark") or row.get(f"{prefix}_mid"), 0.0)
    if mark > 0:
        return mark
    bid = _safe_float(row.get(f"{prefix}_bid"), 0.0)
    ask = _safe_float(row.get(f"{prefix}_ask"), 0.0)
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 4)
    last = _safe_float(row.get(f"{prefix}_last"), 0.0)
    if last > 0:
        return last
    return _safe_float(row.get(f"{prefix}_day_close"), 0.0)


def leg_has_liquidity(
    row: dict[str, Any],
    *,
    prefix: str,
    min_daily_volume: int = 0,
) -> bool:
    """OI positivo o volumen del día por encima del piso configurado."""
    oi = int(_safe_float(row.get(f"{prefix}_oi")))
    vol = int(_safe_float(row.get(f"{prefix}_volume")))
    floor = max(min_daily_volume, 1)
    return oi > 0 or vol >= floor


def leg_is_tradeable(
    row: dict[str, Any],
    *,
    prefix: str,
    min_daily_volume: int = 0,
) -> bool:
    """Pata elegible: liquidez mínima, delta y mark positivos."""
    if not leg_has_liquidity(row, prefix=prefix, min_daily_volume=min_daily_volume):
        return False
    if row.get(f"{prefix}_delta") is None:
        return False
    return resolve_chain_leg_mark(row, prefix) > 0
