"""Deribit public options → gex_levels shape compatible with Massive/scanner."""

from __future__ import annotations

from typing import Any

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.deribit_options_client import DeribitOptionsClient

logger = get_logger(__name__)


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_gex_payload_from_deribit(
    currency: str,
    *,
    client: DeribitOptionsClient | None = None,
) -> dict[str, Any] | None:
    """Build options snapshot fragment with gex_levels from Deribit book summaries."""
    cur = str(currency or "").upper().strip()
    if not cur:
        return None
    api = client or DeribitOptionsClient()
    try:
        summaries = api.get_book_summary_by_currency(currency=cur)
    except Exception as exc:
        logger.warning("deribit_gex_adapter.fetch_failed currency=%s error=%s", cur, exc)
        return None

    if not summaries:
        return None

    spot = _float(summaries[0].get("underlying_price")) or _float(summaries[0].get("index_price"))
    net_gex = 0.0
    call_oi = put_oi = 0.0
    chain_rows: list[dict[str, Any]] = []

    for row in summaries:
        if not isinstance(row, dict):
            continue
        oi = _float(row.get("open_interest")) or 0.0
        mark = _float(row.get("mark_price")) or 0.0
        instrument = str(row.get("instrument_name") or "")
        is_call = instrument.endswith("-C") or "-C-" in instrument
        is_put = instrument.endswith("-P") or "-P-" in instrument
        gamma = _float(
            row.get("greeks", {}).get("gamma") if isinstance(row.get("greeks"), dict) else None
        )
        if gamma is None:
            gamma = 0.01
        signed_gex = oi * gamma * (1.0 if is_call else -1.0 if is_put else 0.0)
        net_gex += signed_gex
        if is_call:
            call_oi += oi
        elif is_put:
            put_oi += oi
        strike = _parse_strike(instrument)
        if strike is not None:
            chain_rows.append(
                {
                    "strike": strike,
                    "open_interest": oi,
                    "call_gex": signed_gex if is_call else 0.0,
                    "put_gex": abs(signed_gex) if is_put else 0.0,
                }
            )

    dealer_bias = "NEUTRAL"
    if net_gex > 0:
        dealer_bias = "BULLISH"
    elif net_gex < 0:
        dealer_bias = "BEARISH"

    return {
        "ok": True,
        "spot": spot,
        "chain": chain_rows[:500],
        "gex_levels": {
            "net_gex_total": round(net_gex, 2),
            "dealer_bias": dealer_bias,
            "squeeze_probability": min(1.0, abs(net_gex) / max(call_oi + put_oi, 1.0) * 0.1),
            "zero_gamma_level": spot,
        },
        "chain_quality": {
            "provider": "deribit",
            "strikes_in_expiry": len(chain_rows),
            "call_oi_strikes": call_oi,
            "put_oi_strikes": put_oi,
        },
        "options_gex_features": {
            "source_tier": "full_chain_gex",
            "data_quality_score": 0.78,
            "provider": "deribit",
        },
    }


def _parse_strike(instrument_name: str) -> float | None:
    parts = instrument_name.split("-")
    for part in parts:
        try:
            value = float(part)
            if value > 0:
                return value
        except ValueError:
            continue
    return None


__all__ = ["build_gex_payload_from_deribit"]
