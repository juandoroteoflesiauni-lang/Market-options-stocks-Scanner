"""Unit tests for options combiner service."""

from __future__ import annotations

from backend.services.options_combiner_service import run_options_combiner


def _minimal_snapshot(spot: float = 100.0) -> dict:
    chain = []
    for strike in (95.0, 100.0, 105.0, 110.0):
        chain.append(
            {
                "strike": strike,
                "call_oi": 100,
                "put_oi": 80,
                "call_delta": 0.5,
                "put_delta": -0.5,
                "call_gamma": 0.02,
                "put_gamma": 0.02,
            }
        )
    return {
        "spot": spot,
        "gex_levels": {
            "zero_gamma_level": 99.0,
            "call_wall": 110.0,
            "put_wall": 90.0,
            "net_gex_total": 1_000_000.0,
            "dealer_bias": "BULLISH",
        },
        "engine_signal": {"total_gex": 1_000_000.0},
        "iv_surface": {"atm_iv": 0.25},
        "chain": chain,
    }


def _klines(n: int = 30, base: float = 100.0) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    price = base
    for i in range(n):
        rows.append(
            {
                "open_time_ms": 1_700_000_000_000 + i * 300_000,
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price + 0.1,
                "volume": 1000.0,
            }
        )
        price += 0.1
    return rows


def test_run_options_combiner_invalid_spot() -> None:
    result = run_options_combiner("AAPL", snapshot=_minimal_snapshot(), klines=_klines(), spot=0.0)
    assert result["ok"] is False
    assert result["reason"] == "invalid_spot"


def test_run_options_combiner_ok_with_klines() -> None:
    result = run_options_combiner(
        "AAPL",
        snapshot=_minimal_snapshot(),
        klines=_klines(),
        spot=100.0,
    )
    assert result["ok"] is True
    assert isinstance(result.get("combiner"), dict)
    assert "direction" in result["combiner"]
    assert "score" in result["combiner"]
    assert isinstance(result["combiner"]["timestamp"], str)
