"""Unit tests for BingX fill price normalization. # [PD-6][TH]"""

from __future__ import annotations

from datetime import UTC

from backend.layer_1_data.datos.bingx_fill_price import (
    parse_bingx_executed_at_utc,
    resolve_fill_price_from_row,
)


def test_resolve_fill_price_prefers_avg_over_zero_price() -> None:
    row = {"price": "0", "avgPrice": "289.23"}
    assert resolve_fill_price_from_row(row) == 289.23


def test_resolve_fill_price_uses_avg_fill_price() -> None:
    row = {"price": "0.0", "avgFillPrice": "95.12"}
    assert resolve_fill_price_from_row(row) == 95.12


def test_resolve_fill_price_returns_none_when_all_zero() -> None:
    row = {"price": "0", "avgPrice": "0.0"}
    assert resolve_fill_price_from_row(row) is None


def test_parse_executed_at_ms_to_utc() -> None:
    ts_ms = 1_718_640_000_000
    dt = parse_bingx_executed_at_utc({"time": ts_ms})
    assert dt is not None
    assert dt.tzinfo == UTC


def test_parse_executed_at_iso_plus08_to_utc() -> None:
    dt = parse_bingx_executed_at_utc({"filledTime": "2026-06-17T15:23:00+08:00"})
    assert dt is not None
    assert dt.hour == 7
    assert dt.tzinfo == UTC
