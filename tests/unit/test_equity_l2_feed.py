"""Tests for equity L2 watchlist feed (Fase 1 + Fase 2). # [TH]"""

from __future__ import annotations

import pytest

from backend.layer_1_data.datos.bingx_trade_adapter import build_microstructure_bundle
from backend.layer_1_data.datos.equity_l2_depth_diff import diff_order_books_to_events
from backend.layer_1_data.datos.equity_l2_watchlist_hub import is_watchlist_symbol
from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBEventType, LOBSide
from backend.services.bingx_l2_integration import order_book_dict_to_lob_analysis
from backend.services.equity_l2_feed_service import EquityL2FeedService


def _sample_trades() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i in range(12):
        side = "buy" if i % 2 == 0 else "sell"
        rows.append(
            {
                "price": str(100.0 + i * 0.01),
                "qty": str(10.0 + i),
                "side": side,
                "time": 1_715_000_000_000 + i * 1000,
            }
        )
    return rows


def _sample_depth() -> dict[str, object]:
    return {
        "symbol": "AAPL-USDT",
        "bids": [["150.10", "5"], ["150.05", "10"]],
        "asks": [["150.20", "4"], ["150.25", "8"]],
        "parsed_bids": [(150.10, 5.0), (150.05, 10.0)],
        "parsed_asks": [(150.20, 4.0), (150.25, 8.0)],
        "timestamp_ms": 1_715_000_000_000,
        "source": "bingx_perp_depth",
    }


def test_watchlist_membership() -> None:
    assert is_watchlist_symbol("AAPL")
    assert is_watchlist_symbol("AAPL-USDT")
    assert not is_watchlist_symbol("BTC")


def test_microstructure_bundle_includes_order_book() -> None:
    bundle = build_microstructure_bundle(
        symbol="AAPL",
        venue_symbol="AAPL-USDT",
        raw_trades=_sample_trades(),
        depth_payload=_sample_depth(),
        market_type="stock_perp",
    )
    assert bundle.ok is True
    assert bundle.order_book is not None
    assert bundle.order_book.get("parsed_bids")


def test_order_book_dict_to_lob_analysis_ok() -> None:
    analysis = order_book_dict_to_lob_analysis(_sample_depth(), symbol="AAPL-USDT")
    assert analysis.ok is True
    assert analysis.result is not None
    assert -1.0 <= analysis.result.imbalance_rho <= 1.0


def test_depth_diff_emits_add_and_cancel() -> None:
    prev = {
        "parsed_bids": [(150.10, 5.0), (150.05, 10.0)],
        "parsed_asks": [(150.20, 4.0)],
        "timestamp_ms": 1_000,
    }
    curr = {
        "parsed_bids": [(150.10, 8.0), (150.05, 10.0)],
        "parsed_asks": [(150.20, 2.0)],
        "timestamp_ms": 2_000,
    }
    events = diff_order_books_to_events(prev, curr)
    assert events
    adds = [e for e in events if e.type is LOBEventType.ADD]
    cancels = [e for e in events if e.type is LOBEventType.CANCEL]
    assert any(e.side is LOBSide.BID and e.price == 150.10 for e in adds)
    assert any(e.side is LOBSide.ASK and e.price == 150.20 for e in cancels)


def test_apply_depth_update_tracks_passive_flow() -> None:
    service = EquityL2FeedService(client=object())
    service._cache["AAPL"] = {"symbol": "AAPL", "ok": True}
    book_a = _sample_depth()
    book_b = {
        **_sample_depth(),
        "parsed_bids": [(150.10, 8.0), (150.05, 10.0)],
        "timestamp_ms": 1_715_000_001_000,
    }
    service.apply_depth_update("AAPL", book_a, source="test_bootstrap")
    service.apply_depth_update("AAPL", book_b, source="test_diff")
    micro = service.get_microstructure("AAPL")
    assert micro is not None
    assert micro.get("passive_order_flow", {}).get("ok") is True
    assert micro.get("lob_stream") is not None


@pytest.mark.asyncio
async def test_feed_service_refresh_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_fetch(_client: object, root: str) -> object:
        return build_microstructure_bundle(
            symbol=root,
            venue_symbol=f"{root}-USDT",
            raw_trades=_sample_trades(),
            depth_payload=_sample_depth(),
            market_type="stock_perp",
        )

    monkeypatch.setattr(
        "backend.services.equity_l2_feed_service.fetch_equity_l2_microstructure",
        _fake_fetch,
    )
    service = EquityL2FeedService(client=object())
    payload = await service.refresh_symbol("AAPL")
    assert payload["ok"] is True
    assert payload.get("order_book")
    assert service.get_microstructure("AAPL") is not None
    assert payload.get("ofi", {}).get("ok") is True
    status = service.snapshot_status()
    assert status.get("phase") == "v2_ws_rest_hybrid"
