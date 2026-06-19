"""Tests for route PnL reconciliation and dashboard. # [PD-6][TH]"""

from __future__ import annotations

import json
from pathlib import Path

from backend.domain.route_pnl_models import RoutePnLDailyPoint
from backend.services.route_pnl_reconciliation import (
    allocate_alpaca_equity_delta,
    bingx_fill_dedupe_key,
    reconcile_bingx_realized_pnl,
    rollup_realized_from_rows,
)
from backend.services.route_pnl_service import build_route_pnl_dashboard


def test_rollup_realized_counts_wins_and_losses() -> None:
    rows = [
        {"closed_pnl_vst": 10.0},
        {"closed_pnl_vst": -5.0},
        {"closed_pnl_vst": 0.0},
    ]
    rollup = rollup_realized_from_rows(rows, source="test")
    assert rollup.realized_pnl == 5.0
    assert rollup.win_count == 1
    assert rollup.loss_count == 1
    assert rollup.close_count == 2


def test_bingx_dedupe_key_stable() -> None:
    row = {
        "venue_order_id": "123",
        "symbol": "META-USDT",
        "executed_at_utc": "2026-06-17T12:00:00+00:00",
        "filled_qty": 1.0,
    }
    assert bingx_fill_dedupe_key(row) == bingx_fill_dedupe_key(row)


def test_allocate_alpaca_equity_delta_by_notional() -> None:
    out = allocate_alpaca_equity_delta(
        total_delta_usd=-1000.0,
        r1_notional=8000.0,
        r2_notional=2000.0,
        options_notional=0.0,
    )
    assert out["R1"] == -800.0
    assert out["R2"] == -200.0
    assert out["OPTIONS_R1"] == 0.0


def test_reconcile_bingx_from_report_file(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    payload = {
        "exchange_orders_filled": [
            {
                "venue_order_id": "1",
                "symbol": "INTC-USDT",
                "executed_at_utc": "2026-06-17T15:00:00+00:00",
                "filled_qty": 2.0,
                "avg_price": 25.0,
                "closed_pnl_vst": -100.0,
            },
            {
                "venue_order_id": "2",
                "symbol": "SPX-USDT",
                "executed_at_utc": "2026-06-17T16:00:00+00:00",
                "filled_qty": 1.0,
                "avg_price": 10.0,
                "closed_pnl_vst": 50.0,
            },
        ]
    }
    (reports / "bingx_bot_operations_2026-06-17.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "backend.services.route_pnl_reconciliation._REPORTS_DIR",
        reports,
    )
    rollup, fills = reconcile_bingx_realized_pnl()
    assert len(fills) == 2
    assert rollup.realized_pnl == -50.0
    assert rollup.win_count == 1
    assert rollup.loss_count == 1


def test_build_route_pnl_dashboard_returns_four_buckets() -> None:
    result = build_route_pnl_dashboard(limit=10)
    assert len(result.buckets) == 4
    routes = {b.route for b in result.buckets}
    assert routes == {"R1", "R2", "BINGX", "OPTIONS_R1"}


def test_build_route_pnl_dashboard_bingx_from_reports(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "bingx_bot_operations_2026-06-17.json").write_text(
        json.dumps(
            {
                "exchange_orders_filled": [
                    {
                        "venue_order_id": "99",
                        "symbol": "HOOD-USDT",
                        "executed_at_utc": "2026-06-17T10:00:00+00:00",
                        "filled_qty": 1.0,
                        "avg_price": 100.0,
                        "closed_pnl_vst": 25.5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.services.route_pnl_reconciliation._REPORTS_DIR", reports)
    monkeypatch.setattr(
        "backend.services.route_pnl_service._ALPACA_DB", tmp_path / "missing.duckdb"
    )
    monkeypatch.setattr("backend.services.route_pnl_service._BINGX_DB", tmp_path / "missing.duckdb")
    monkeypatch.setattr(
        "backend.services.route_pnl_service._OPTIONS_DB", tmp_path / "missing.sqlite3"
    )
    monkeypatch.setattr(
        "backend.services.route_pnl_service._JOURNAL_DB", tmp_path / "missing.duckdb"
    )
    monkeypatch.setattr("backend.services.route_pnl_service._EOD_DIR", tmp_path / "eod")

    result = build_route_pnl_dashboard(limit=50)
    bingx = next(b for b in result.buckets if b.route == "BINGX")
    assert bingx.realized_pnl_usd == 25.5
    assert bingx.win_count == 1
    assert bingx.execution_count == 1


def test_alpaca_equity_allocation_note_in_dashboard(tmp_path: Path, monkeypatch) -> None:
    eod = tmp_path / "eod"
    eod.mkdir()
    snapshot = {
        "alpaca_balance": {"equity": "90000"},
        "bingx_perp_balance": {"balance": {"equity": "99000", "unrealizedProfit": "0"}},
    }
    (eod / "eod_audit_20260615.json").write_text(json.dumps(snapshot), encoding="utf-8")
    snapshot2 = {
        "alpaca_balance": {"equity": "100000"},
        "bingx_perp_balance": {"balance": {"equity": "99000", "unrealizedProfit": "0"}},
    }
    (eod / "eod_audit_20260617.json").write_text(json.dumps(snapshot2), encoding="utf-8")

    monkeypatch.setattr("backend.services.route_pnl_service._EOD_DIR", eod)
    monkeypatch.setattr(
        "backend.services.route_pnl_service._ALPACA_DB", tmp_path / "missing.duckdb"
    )
    monkeypatch.setattr("backend.services.route_pnl_service._BINGX_DB", tmp_path / "missing.duckdb")
    monkeypatch.setattr(
        "backend.services.route_pnl_service._OPTIONS_DB", tmp_path / "missing.sqlite3"
    )
    monkeypatch.setattr(
        "backend.services.route_pnl_reconciliation._REPORTS_DIR", tmp_path / "reports"
    )

    from backend.domain.route_pnl_models import RoutePnLBucket
    from backend.services.route_pnl_service import (
        _apply_alpaca_equity_reconciliation,
        _empty_buckets,
    )

    buckets = _empty_buckets()
    buckets["R1"] = RoutePnLBucket(route="R1", notional_usd=1000.0)
    daily = (
        RoutePnLDailyPoint(date="20260615", alpaca_equity_usd=90000.0),
        RoutePnLDailyPoint(date="20260617", alpaca_equity_usd=100000.0),
    )
    notes = _apply_alpaca_equity_reconciliation(buckets, daily)
    assert buckets["R1"].realized_pnl_usd == 10000.0
    assert any("EOD equity delta" in note for note in notes)
