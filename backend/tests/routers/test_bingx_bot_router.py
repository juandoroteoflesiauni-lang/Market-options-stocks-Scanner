from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.layer_1_data.datos.bingx_client import BingXKline
from backend.api.routes.bingx_bot_router import configure_audit_store, configure_service, router
from backend.services.bingx_audit_store import BingXAuditStore
from backend.services.bingx_bot_service import BingXMarketSnapshot


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_operations_endpoint_returns_audit_ledger_rows() -> None:
    store = MagicMock()
    store.list_operations.return_value = [
        {
            "operation_id": "cycle-a:BTC-USDT:execution:0",
            "cycle_id": "cycle-a",
            "event_type": "execution",
            "started_at": "2026-05-21T00:02:00Z",
            "finished_at": "2026-05-21T00:03:00Z",
            "dry_run": True,
            "symbol": "BTC-USDT",
            "side": "BUY",
            "suitability": "ALLOW",
            "authorized": True,
            "execution_ok": True,
            "notional_usdt": 25.0,
            "realized_pnl_usdt": 1.25,
            "pnl_pct": 5.0,
            "reason_codes": ["VSA_SPIKE", "RISK_OK"],
        }
    ]
    configure_audit_store(store)

    try:
        response = _client().get("/api/v1/bingx-bot/operations?limit=25")

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["operations"][0]["symbol"] == "BTC-USDT"
        assert body["operations"][0]["pnl_pct"] == 5.0
        store.list_operations.assert_called_once_with(limit=25)
    finally:
        configure_audit_store(BingXAuditStore(":memory:"))


@pytest.fixture(autouse=True)
def _stub_exchange_derivatives(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    result = SimpleNamespace(
        status="unavailable",
        source="none",
        reason="exchange_derivatives_only_for_crypto",
        quality_score=None,
        data_sources=(),
        metrics=None,
        to_dict=lambda: {
            "status": "unavailable",
            "source": "none",
            "reason": "exchange_derivatives_only_for_crypto",
            "quality_score": None,
            "data_sources": [],
            "metrics": None,
        },
    )
    mocked = AsyncMock(return_value=result)
    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.build_exchange_derivatives_bridge",
        mocked,
    )
    return mocked


def test_legacy_app_entrypoint_registers_bingx_routes() -> None:
    from backend.main import app as legacy_app

    paths = {route.path for route in legacy_app.routes}
    assert "/api/v1/bingx-bot/status" in paths
    assert "/api/v1/bingx-bot/scan" in paths
    assert "/api/v1/bingx-bot/analysis/{symbol}" in paths


def _mock_snapshot(symbol: str) -> BingXMarketSnapshot:
    return BingXMarketSnapshot(
        symbol=symbol,
        interval="5m",
        bars=50,
        latest_close=100.0,
        last_volume=500.0,
        volume_mean=400.0,
        volume_std=50.0,
        volume_z_score=2.1,
        close_position_in_range=0.7,
        range_pct=0.3,
        captured_at="2026-05-19T12:00:00Z",
        closes_recent=(98.0, 99.0, 100.0),
    )


def test_scan_response_includes_snapshots_and_timestamps() -> None:
    svc = MagicMock()
    svc.dry_run = True

    snap = _mock_snapshot("BTC-USDT")
    signal = MagicMock()
    signal.snapshot = snap
    signal.to_dict.return_value = {"symbol": "BTC-USDT", "direction": "LONG", "score": 0.8}

    decision = MagicMock()
    decision.to_dict.return_value = {
        "symbol": "BTC-USDT",
        "suitability": "ALLOW",
        "probability": 0.7,
    }

    async def _scan(_syms: object, _customization: object = None) -> list[object]:
        return [signal]

    async def _filter(
        _sigs: object, *, use_scanner_confirmation: bool = True, customization: object = None
    ) -> list[object]:
        assert use_scanner_confirmation is False
        return [decision]

    svc.scan = _scan
    svc.filter_signals = _filter
    configure_service(svc)

    response = _client().post("/api/v1/bingx-bot/scan", json={})
    assert response.status_code == 200
    body = response.json()
    assert "snapshots" in body
    assert "started_at" in body
    assert "finished_at" in body
    assert body["scanner_confirmation"] is False
    snaps = body["snapshots"]
    assert len(snaps) == 1
    assert snaps[0]["symbol"] == "BTC-USDT"
    assert "closes_recent" in snaps[0]


def test_scan_can_request_scanner_confirmation() -> None:
    svc = MagicMock()
    svc.dry_run = True

    snap = _mock_snapshot("BTC-USDT")
    signal = MagicMock()
    signal.snapshot = snap
    signal.to_dict.return_value = {"symbol": "BTC-USDT", "direction": "LONG", "score": 0.8}

    decision = MagicMock()
    decision.to_dict.return_value = {
        "symbol": "BTC-USDT",
        "suitability": "ALLOW",
        "probability": 0.7,
    }

    async def _scan(_syms: object, _customization: object = None) -> list[object]:
        return [signal]

    async def _filter(
        _sigs: object, *, use_scanner_confirmation: bool = True, customization: object = None
    ) -> list[object]:
        assert use_scanner_confirmation is True
        return [decision]

    svc.scan = _scan
    svc.filter_signals = _filter
    configure_service(svc)

    response = _client().post(
        "/api/v1/bingx-bot/scan",
        json={"scanner_confirmation": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scanner_confirmation"] is True


def _make_kline(i: int) -> BingXKline:
    base = 100.0 + i
    return BingXKline(
        open_time_ms=1_700_000_000_000 + i * 300_000,
        open=base,
        high=base + 1.0,
        low=base - 1.0,
        close=base + 0.5,
        volume=1000.0 + i * 10,
        close_time_ms=1_700_000_000_000 + i * 300_000 + 299_999,
    )


def test_analysis_endpoint_returns_klines_and_ta(monkeypatch) -> None:
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    async def _no_options(
        *_args: object, **_kwargs: object
    ) -> tuple[None, dict[str, object], None]:
        return None, {}, None

    monkeypatch.setattr("backend.api.routes.bingx_bot_router._fetch_options_metrics", _no_options)

    response = _client().get("/api/v1/bingx-bot/analysis/AAPL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "AAPL-USDT"
    assert body["interval"] == "5m"
    assert len(body["klines"]) == 60
    kl = body["klines"][0]
    assert {"time", "open", "high", "low", "close", "volume"} <= kl.keys()
    ta = body["ta"]
    assert "rsi_14" in ta
    assert "ema_9" in ta
    assert "ema_21" in ta
    assert "ema_50" in ta
    assert "vwap" in ta
    assert "vwap_upper_1" in ta
    assert "vwap_lower_1" in ta
    assert "vsa_delta" in ta
    assert "vsa_z_score" in ta
    assert "trend" in ta
    assert ta["trend"] in ("bullish", "bearish", "neutral")
    assert body["options"] is None


def test_analysis_endpoint_prefers_canonical_service_snapshot() -> None:
    svc = MagicMock()
    payload = {
        "symbol": "GOOGL-USDT",
        "interval": "5m",
        "klines": [],
        "ta": {"trend": "neutral"},
        "options": None,
        "venue_symbol": "GOOGL-USDT",
        "underlying_symbol": "GOOGL",
        "market_type": "stock_perp",
        "candidate_analysis": {"venue_symbol": "GOOGL-USDT"},
        "data_sources": ["venue_klines"],
        "errors": {},
    }
    svc.build_analysis_snapshot = AsyncMock(return_value=payload)

    async def _legacy_fetch_should_not_run(
        symbol: str, interval: str, limit: int
    ) -> list[BingXKline]:
        raise AssertionError("legacy analysis path must not run")

    svc.fetch_klines_for_analysis = _legacy_fetch_should_not_run
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")

    assert response.status_code == 200
    assert response.json()["candidate_analysis"]["venue_symbol"] == "GOOGL-USDT"
    svc.build_analysis_snapshot.assert_awaited_once_with("GOOGL-USDT", interval="5m")


def test_get_analysis_returns_options_metrics_for_stock_perp(monkeypatch) -> None:
    """Stock perp /analysis surfaces the full institutional options block.

    The bridge populates legacy fields (gex_wall_*, iv_percentile,
    put_call_ratio, delta_exposure_usd) AND the richer institutional
    extensions (max_pain, dealer_bias, net_gex_total, atm_iv, dealer
    flow regimes, confluence). ``options_bridge`` carries the full result
    including ``source`` and ``proxy_symbol`` (None for direct underlyings)."""
    from backend.services.bingx_options_bridge import REASON_NO_FETCHER

    # ``REASON_NO_FETCHER`` import sanity-checks the public reason-code
    # contract that this test depends on indirectly via the bridge result.
    assert isinstance(REASON_NO_FETCHER, str)

    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    async def _options_snapshot(symbol: str, expiry: object, r: float) -> object:
        assert symbol == "GOOGL"  # underlying — NOT the venue symbol
        assert expiry is None
        return SimpleNamespace(
            ok=True,
            spot=180.0,
            error=None,
            gex_levels=SimpleNamespace(
                call_wall=185.0,
                put_wall=175.0,
                call_wall_moderate=186.0,
                put_wall_moderate=174.0,
                zero_gamma_level=180.0,
                max_pain=180.5,
                net_gex_total=2_500_000.0,
                call_gex_total=1_800_000.0,
                put_gex_total=-700_000.0,
                dealer_bias="BULLISH",
                squeeze_probability=0.22,
            ),
            iv_surface=SimpleNamespace(
                atm_iv=0.28,
                iv_rank_hv_rolling=0.45,
                iv_rank_cross_expiry=0.55,
                iv_percentile_cross_term=0.62,
                vrp=0.04,
            ),
            confluence=SimpleNamespace(
                score=0.72,
                signal="BULLISH",
                confidence=0.66,
                pcr_oi=0.85,
                pcr_volume=0.91,
                total_vanna_exposure=250_000.0,
                total_vex=180_000.0,
                total_cex=120_000.0,
                vanna_exposure_regime="BULLISH",
                vex_regime="NEUTRAL",
                cex_regime="BEARISH",
            ),
            chain=[
                SimpleNamespace(call_oi=500, put_oi=400, net_dex=600_000.0),
                SimpleNamespace(call_oi=300, put_oi=250, net_dex=-100_000.0),
            ]
            * 6,
            total_dex=500_000.0,
            dex_flip_level=179.5,
            chain_quality={"provider": "massive_polygon", "strikes": 12},
        )

    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _options_snapshot,
    )
    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    # ── Routing context ──────────────────────────────────────────────────────
    assert body["venue_symbol"] == "GOOGL-USDT"
    assert body["underlying_symbol"] == "GOOGL"
    assert body["market_type"] == "stock_perp"

    # ── Legacy options block (preserved for frontend backward-compat) ────────
    options = body["options"]
    assert options is not None
    assert options["gex_wall_price"] == 180.0  # zero_gamma nearest to spot
    assert options["gex_wall_direction"] == "above"
    assert options["gex_wall_distance_pct"] == 0.0
    assert options["iv_percentile"] == 62.0  # 0.62 → 62%
    assert options["put_call_ratio"] == 0.85
    assert options["delta_exposure_usd"] == 500_000.0

    # ── Institutional extensions surface alongside legacy fields ─────────────
    assert options["call_wall"] == 185.0
    assert options["put_wall"] == 175.0
    assert options["zero_gamma"] == 180.0
    assert options["max_pain"] == 180.5
    assert options["net_gex_total"] == 2_500_000.0
    assert options["dealer_bias"] == "BULLISH"
    assert options["atm_iv"] == 0.28
    assert options["iv_rank_hv_rolling"] == 0.45
    assert options["total_vanna"] == 250_000.0
    assert options["total_vex"] == 180_000.0
    assert options["total_cex"] == 120_000.0
    assert options["confluence_score"] == 0.72
    assert options["confluence_signal"] == "BULLISH"
    assert options["vanna_exposure_regime"] == "BULLISH"
    assert options["chain_contracts"] == 12

    # ── options_bridge carries the full bridge result ───────────────────────
    bridge = body["options_bridge"]
    assert bridge["status"] == "available"
    assert bridge["source"] == "underlying_options"
    assert bridge["proxy_symbol"] is None
    assert bridge["options_symbol"] == "GOOGL"
    assert bridge["quality_score"] == 1.0
    assert bridge["chain_quality"] == {"provider": "massive_polygon", "strikes": 12}

    # ── Provenance: bridge source tag lands in data_sources ──────────────────
    assert "underlying_options" in body["data_sources"]
    # No error entry on success.
    assert "options" not in body["errors"]


def test_get_analysis_returns_options_metrics_for_stock_index_perp(monkeypatch) -> None:
    """Stock-index perp routes through the ETF proxy (SPX → SPY)."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    async def _options_snapshot(symbol: str, expiry: object, r: float) -> object:
        # Critical routing invariant: SPX-USDT must query SPY (the optionable
        # ETF proxy), never SPX directly — SPX has no listed ETF chain on
        # Massive/Finnhub.
        assert symbol == "SPY"
        return SimpleNamespace(
            ok=True,
            spot=520.0,
            gex_levels=SimpleNamespace(
                call_wall=530.0,
                put_wall=510.0,
                zero_gamma_level=520.0,
                max_pain=521.0,
                net_gex_total=10_000_000.0,
                call_gex_total=7_000_000.0,
                put_gex_total=-3_000_000.0,
                dealer_bias="BULLISH",
                squeeze_probability=0.1,
            ),
            iv_surface=SimpleNamespace(
                atm_iv=0.15,
                iv_rank_hv_rolling=0.3,
                iv_rank_cross_expiry=0.4,
                iv_percentile_cross_term=0.35,
                vrp=0.02,
            ),
            confluence=SimpleNamespace(
                score=0.5,
                signal="NEUTRAL",
                confidence=0.5,
                pcr_oi=1.0,
                pcr_volume=1.1,
                total_vanna_exposure=None,
                total_vex=None,
                total_cex=None,
                vanna_exposure_regime="NEUTRAL",
                vex_regime="NEUTRAL",
                cex_regime="NEUTRAL",
            ),
            chain=[SimpleNamespace(call_oi=1000, put_oi=1000, net_dex=0.0)] * 15,
            total_dex=0.0,
            dex_flip_level=None,
            chain_quality={"provider": "polygon", "strikes": 15},
        )

    # SPX must classify as stock_index_perp for the bridge proxy path to fire.
    # Force-classify in case the universe policy maps it differently.
    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.classify_underlying",
        lambda symbol: "stock_index_perp",
    )
    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _options_snapshot,
    )
    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/SPX-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["market_type"] == "stock_index_perp"
    bridge = body["options_bridge"]
    assert bridge["status"] == "available"
    assert bridge["source"] == "index_proxy_options"
    assert bridge["proxy_symbol"] == "SPY"
    assert bridge["options_symbol"] == "SPY"
    assert bridge["underlying_symbol"] == "SPX"
    # The legacy options block still surfaces normally, sourced via the proxy.
    assert body["options"]["call_wall"] == 530.0
    assert "index_proxy_options" in body["data_sources"]


def test_get_analysis_rejects_crypto_symbols() -> None:
    """Crypto: bridge returns unavailable with stable reason; options stays
    null without an ``errors.options`` entry (absence is expected)."""
    svc = MagicMock()
    svc.fetch_klines_for_analysis = AsyncMock()
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=5m")
    assert response.status_code == 400
    assert response.json()["detail"] == "bingx_bot_synthetic_stocks_only"
    svc.fetch_klines_for_analysis.assert_not_called()
    return

    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["market_type"] == "crypto_standard"
    assert body["options"] is None
    bridge = body["options_bridge"]
    assert bridge["status"] == "unavailable"
    assert bridge["reason"] == "no_equity_options_for_crypto"
    # Crypto: silent — no options error entry.
    assert "options" not in body["errors"]


def test_get_analysis_rejects_crypto_market_data_route() -> None:
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=5m")
    assert response.status_code == 400
    assert response.json()["detail"] == "bingx_bot_synthetic_stocks_only"


def test_get_analysis_rejects_crypto_exchange_derivatives(monkeypatch) -> None:
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    derivatives_result = SimpleNamespace(
        status="available",
        source="exchange_derivatives_public",
        reason=None,
        quality_score=0.82,
        data_sources=("binance_public_derivatives", "deribit_public_derivatives"),
        metrics={
            "provider_count": 2,
            "available_provider_count": 2,
            "funding_rates": {"binance": 0.0001, "deribit": 0.0002},
            "avg_mark_iv": 0.51,
        },
        to_dict=lambda: {
            "status": "available",
            "source": "exchange_derivatives_public",
            "reason": None,
            "quality_score": 0.82,
            "data_sources": ["binance_public_derivatives", "deribit_public_derivatives"],
            "metrics": {
                "provider_count": 2,
                "available_provider_count": 2,
                "funding_rates": {"binance": 0.0001, "deribit": 0.0002},
                "avg_mark_iv": 0.51,
            },
        },
    )
    bridge_mock = AsyncMock(return_value=derivatives_result)
    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.build_exchange_derivatives_bridge",
        bridge_mock,
    )

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=5m")
    assert response.status_code == 400
    assert response.json()["detail"] == "bingx_bot_synthetic_stocks_only"
    bridge_mock.assert_not_awaited()


def test_analysis_endpoint_returns_options_gex_metrics(monkeypatch) -> None:
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    async def _options_snapshot(symbol: str, expiry: object, r: float) -> object:
        assert symbol == "AAPL"
        assert expiry is None
        assert r == 0.04
        return SimpleNamespace(
            ok=True,
            spot=100.0,
            gex_levels=SimpleNamespace(
                call_wall=105.0,
                put_wall=95.0,
                zero_gamma_level=101.0,
            ),
            iv_surface=SimpleNamespace(
                iv_percentile_cross_term=0.63,
                iv_rank_hv_rolling=None,
            ),
            chain=[
                SimpleNamespace(call_oi=100.0, put_oi=70.0, net_dex=1_500_000.0),
                SimpleNamespace(call_oi=50.0, put_oi=80.0, net_dex=-500_000.0),
            ],
        )

    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _options_snapshot,
    )
    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/AAPL-USDT?interval=5m")

    assert response.status_code == 200
    options = response.json()["options"]
    assert options["gex_wall_price"] == 101.0
    assert options["gex_wall_direction"] == "above"
    assert options["gex_wall_distance_pct"] == 1.0
    assert options["iv_percentile"] == 63.0
    assert options["put_call_ratio"] == 1.0
    assert options["delta_exposure_usd"] == 1_000_000.0


def test_analysis_endpoint_invalid_interval_returns_422() -> None:
    svc = MagicMock()
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=99x")
    assert response.status_code == 422


def test_analysis_endpoint_empty_klines_returns_null_ta() -> None:
    svc = MagicMock()

    async def _fetch_empty(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return []

    svc.fetch_klines_for_analysis = _fetch_empty
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/AAPL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()
    ta = body["ta"]
    assert ta["rsi_14"] is None
    assert ta["ema_9"] is None
    assert ta["trend"] == "neutral"
    assert body["klines"] == []


def test_analysis_endpoint_reports_non_empty_error_for_empty_exception() -> None:
    svc = MagicMock()

    async def _fetch_timeout(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        raise TimeoutError()

    svc.fetch_klines_for_analysis = _fetch_timeout
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/AAPL-USDT?interval=5m")

    assert response.status_code == 502
    assert response.json()["detail"] == "bingx_analysis_failed: TimeoutError"


def test_account_positions_orders_and_universe_endpoints() -> None:
    svc = MagicMock()
    svc.dry_run = True
    svc.get_account_state.return_value = {
        "total_equity_usdt": 100.0,
        "open_positions": [{"symbol": "BTC-USDT"}],
        "open_orders": [{"symbol": "BTC-USDT"}],
        "position_count": 1,
        "dry_run": True,
    }
    svc.get_universe.return_value = [{"symbol": "BTC-USDT", "volume_24h_usdt": 25_000_000.0}]
    configure_service(svc)
    client = _client()

    assert client.get("/api/v1/bingx-bot/account").json()["total_equity_usdt"] == 100.0
    assert client.get("/api/v1/bingx-bot/positions").json()["positions"] == [{"symbol": "BTC-USDT"}]
    assert client.get("/api/v1/bingx-bot/orders").json()["orders"] == [{"symbol": "BTC-USDT"}]
    assert client.get("/api/v1/bingx-bot/universe").json()["universe"][0]["symbol"] == "BTC-USDT"


def test_universe_endpoint_degrades_when_provider_times_out() -> None:
    svc = MagicMock()
    svc.dry_run = True
    svc.get_universe.side_effect = TimeoutError("ConnectTimeout")
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/universe")

    assert response.status_code == 200
    assert response.json() == {
        "universe": [],
        "degraded": True,
        "error": "bingx_universe_failed: ConnectTimeout",
    }


def test_kill_switch_requires_confirmation_and_calls_service() -> None:
    svc = MagicMock()
    svc.dry_run = True
    svc.kill_switch.return_value = {"kill_switch": True, "reason": "operator"}
    svc.close_all_positions.return_value = {"closed": True}
    configure_service(svc)
    client = _client()

    missing = client.post("/api/v1/bingx-bot/kill-switch", json={})
    assert missing.status_code == 422

    response = client.post(
        "/api/v1/bingx-bot/kill-switch",
        json={"confirm": True, "cancel_orders": True},
    )

    assert response.status_code == 200
    assert response.json()["closed"] is True
    assert response.json()["risk_desk"]["kill_switch"] is True
    svc.kill_switch.assert_called_once_with(reason="operator")
    svc.close_all_positions.assert_called_once_with(cancel_orders=True, confirm=True)


def test_leverage_margin_and_funding_endpoints_call_service() -> None:
    svc = MagicMock()
    svc.dry_run = True
    svc.set_leverage.return_value = {"symbol": "BTC-USDT", "leverage": 3}
    svc.set_margin_type.return_value = {"symbol": "BTC-USDT", "marginType": "ISOLATED"}
    svc.get_funding_rate.return_value = {"symbol": "BTC-USDT", "lastFundingRate": "0.0001"}
    configure_service(svc)
    client = _client()

    lev = client.post("/api/v1/bingx-bot/leverage", json={"symbol": "BTC-USDT", "leverage": 3})
    margin = client.post(
        "/api/v1/bingx-bot/margin-type",
        json={"symbol": "BTC-USDT", "margin_type": "ISOLATED"},
    )
    funding = client.get("/api/v1/bingx-bot/funding-rate/BTC-USDT")

    assert lev.json()["leverage"] == 3
    assert margin.json()["marginType"] == "ISOLATED"
    assert funding.json()["lastFundingRate"] == "0.0001"


# ─── /analysis/{symbol} — venue vs underlying routing ──────────────────────────


def test_analysis_endpoint_stock_perp_routes_underlying_to_options(monkeypatch) -> None:
    """GOOGL-USDT → underlying GOOGL; options engine receives 'GOOGL', not the venue symbol."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    received: dict[str, object] = {}

    async def _options_snapshot(symbol: str, expiry: object, r: float) -> object:
        # The router MUST hand the options engine the underlying root,
        # never the BingX venue symbol — this is the survival-critical
        # routing invariant for synthetic-stock perps.
        received["symbol"] = symbol
        return SimpleNamespace(
            ok=True,
            spot=100.0,
            gex_levels=SimpleNamespace(call_wall=105.0, put_wall=95.0, zero_gamma_level=101.0),
            iv_surface=SimpleNamespace(iv_percentile_cross_term=0.50, iv_rank_hv_rolling=None),
            chain=[SimpleNamespace(call_oi=100.0, put_oi=70.0, net_dex=1_000_000.0)],
        )

    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _options_snapshot,
    )

    async def _fake_ta_snapshot(self_inst: object) -> dict[str, object]:
        return {"ok": False, "reason": "no_equity_data_source", "ticker": "GOOGL"}

    async def _fake_prob_summary(ticker: str) -> dict[str, object]:
        return {"ok": False, "reason": "no_equity_data_source", "ticker": ticker}

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _fake_ta_snapshot,
    )
    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.equity_probabilistic_summary",
        _fake_prob_summary,
    )
    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["venue_symbol"] == "GOOGL-USDT"
    assert body["underlying_symbol"] == "GOOGL"
    assert body["market_type"] == "stock_perp"
    # Underlying routing invariant
    assert received["symbol"] == "GOOGL"
    # venue_ta is populated from the venue klines
    assert body["venue_ta"]["rsi_14"] is not None
    # underlying TA / probabilistic are not wired — must surface UNAVAILABLE
    assert body["underlying_ta"] is None
    assert body["probabilistic"] is None
    assert body["errors"].get("underlying_ta", "").startswith("UNAVAILABLE")
    assert body["errors"].get("probabilistic", "").startswith("UNAVAILABLE")
    # Options succeeded → no error entry, payload present, data_source recorded
    assert "options" not in body["errors"]
    assert body["options"] is not None
    assert "underlying_options" in body["data_sources"]
    assert "venue_klines" in body["data_sources"]


def test_analysis_endpoint_crypto_is_rejected_before_options_or_underlying_ta() -> None:
    """BTC-USDT → crypto; options is null with no error, underlying_ta is null."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    # No monkeypatch on options_snapshot_service — if the router calls it,
    # it would hit the real implementation. The assertion below guards
    # against that by checking the option metrics are skipped.
    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=5m")
    assert response.status_code == 400
    assert response.json()["detail"] == "bingx_bot_synthetic_stocks_only"


def test_analysis_endpoint_options_engine_failure_returns_200_with_error(monkeypatch) -> None:
    """When the options engine raises, the endpoint stays 200 OK; only options is degraded."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    async def _broken_options(symbol: str, expiry: object, r: float) -> object:
        raise RuntimeError("upstream provider down")

    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _broken_options,
    )
    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    # Endpoint must not break — venue TA still flows
    assert body["venue_symbol"] == "GOOGL-USDT"
    assert body["underlying_symbol"] == "GOOGL"
    assert body["market_type"] == "stock_perp"
    assert body["venue_ta"]["rsi_14"] is not None
    # Options failure surfaces as an error code, data is null
    assert body["options"] is None
    assert "UNAVAILABLE" in body["errors"]["options"]
    assert "underlying_options" not in body["data_sources"]
    assert "venue_klines" in body["data_sources"]


# ─── /analysis/{symbol} — underlying TA / probabilistic wiring ─────────────────


def test_analysis_underlying_ta_populated_when_equity_source_available(monkeypatch) -> None:
    """Equity perp: EquityTASnapshotService returns ok=True → underlying_ta is populated, no error."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    class _StubSnapshotService:
        def __init__(self, ticker: str) -> None:
            self._ticker = ticker

        async def snapshot(self) -> dict[str, object]:
            return {
                "ok": True,
                "ticker": self._ticker,
                "rsi_14": 54.2,
                "ema_fast": 175.3,
                "ema_slow": 172.1,
                "trend_direction": "bullish",
                "source": "fmp",
                "bars_used": 120,
            }

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService",
        _StubSnapshotService,
    )

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["underlying_symbol"] == "GOOGL"
    assert body["market_type"] == "stock_perp"
    underlying_ta = body["underlying_ta"]
    assert underlying_ta is not None
    assert underlying_ta["ticker"] == "GOOGL"
    assert underlying_ta["rsi_14"] == 54.2
    assert underlying_ta["trend_direction"] == "bullish"
    assert "underlying_ta" not in body["errors"]
    assert "underlying_equity_ta" in body["data_sources"]


def test_analysis_underlying_ta_unavailable_when_equity_source_fails(monkeypatch) -> None:
    """Equity perp: snapshot returns ok=False → underlying_ta is null and errors carries UNAVAILABLE."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    class _UnavailableSnapshotService:
        def __init__(self, ticker: str) -> None:
            self._ticker = ticker

        async def snapshot(self) -> dict[str, object]:
            return {
                "ok": False,
                "ticker": self._ticker,
                "reason": "no_equity_data_source",
            }

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService",
        _UnavailableSnapshotService,
    )

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["underlying_ta"] is None
    assert "UNAVAILABLE" in body["errors"]["underlying_ta"]
    assert "no_equity_data_source" in body["errors"]["underlying_ta"]
    assert "underlying_equity_ta" not in body["data_sources"]


def test_analysis_probabilistic_populated_for_stock_perp(monkeypatch) -> None:
    """Equity perp: equity_probabilistic_summary returns ok=True → probabilistic carries bull_probability."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    async def _stub_probabilistic(ticker: str) -> dict[str, object]:
        return {
            "ok": True,
            "ticker": ticker,
            "bull_probability": 0.62,
            "bear_probability": 0.28,
            "neutral_probability": 0.10,
            "confidence": 0.71,
            "source": "meta_learner",
        }

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.equity_probabilistic_summary",
        _stub_probabilistic,
    )

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    probabilistic = body["probabilistic"]
    assert probabilistic is not None
    assert probabilistic["ticker"] == "GOOGL"
    assert probabilistic["bull_probability"] == 0.62
    assert probabilistic["confidence"] == 0.71
    assert "probabilistic" not in body["errors"]
    assert "underlying_probabilistic" in body["data_sources"]


# ── Healthcheck endpoint ───────────────────────────────────────────────────────


def _mock_instruments() -> list[dict]:
    """Four-instrument fixture covering every market_type and capability combination."""
    return [
        {
            "market_type": "stock_perp",
            "execution_allowed": True,
            "massive_available": True,
            "fmp_symbol": "GOOGL",
        },
        {
            "market_type": "stock_perp",
            "execution_allowed": True,
            "massive_available": False,
            "fmp_symbol": "AAPL",
        },
        {
            "market_type": "crypto_standard",
            "execution_allowed": True,
            "massive_available": False,
            "fmp_symbol": None,
        },
        {
            "market_type": "stock_index_perp",
            "execution_allowed": False,
            "massive_available": False,
            "fmp_symbol": None,
        },
    ]


def test_healthcheck_returns_correct_counts() -> None:
    svc = MagicMock()
    svc.dry_run = True

    async def _get_universe() -> list[dict]:
        return _mock_instruments()

    svc.get_universe = _get_universe
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/healthcheck")
    assert response.status_code == 200
    body = response.json()

    assert body["universe_count"] == 4
    assert body["stock_perp_count"] == 2
    assert body["stock_index_perp_count"] == 1
    assert body["crypto_count"] == 1
    # l2_active: equity perps with execution_allowed=True = 2 stock_perps
    assert body["l2_active_count"] == 2
    # l2_pending: equity perps with execution_allowed=False = 1 stock_index_perp
    assert body["l2_pending_count"] == 1
    assert body["options_available_count"] == 1  # only GOOGL has massive_available=True
    assert body["predictive_available_count"] == 2  # GOOGL + AAPL have fmp_symbol
    assert body["execution_allowed_count"] == 3
    assert body["dry_run"] is True
    assert "providers" in body
    # Provider values must be bool — key contents must never leak.
    for v in body["providers"].values():
        assert isinstance(v, bool)


def test_healthcheck_degrades_gracefully_when_universe_fails() -> None:
    svc = MagicMock()
    svc.dry_run = True

    async def _get_universe() -> list[dict]:
        raise RuntimeError("network_timeout")

    svc.get_universe = _get_universe
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/healthcheck")
    # Must not crash — endpoint catches the exception and returns zeros.
    assert response.status_code == 200
    body = response.json()
    assert body["universe_count"] == 0
    assert body["stock_perp_count"] == 0
    assert body["stock_index_perp_count"] == 0
    assert body["crypto_count"] == 0
    assert body["l2_active_count"] == 0
    assert body["l2_pending_count"] == 0
    assert body["options_available_count"] == 0
    assert body["predictive_available_count"] == 0
    assert body["execution_allowed_count"] == 0
    assert body["dry_run"] is True
    assert "providers" in body
    for v in body["providers"].values():
        assert isinstance(v, bool)


# ─── /analysis/{symbol} — L2 / LOB dynamics wiring ─────────────────────────────


def _equity_engine_stubs(monkeypatch) -> None:
    """Silence equity TA + probabilistic engines for L2-focused tests."""

    async def _fake_ta_snapshot(self_inst: object) -> dict[str, object]:
        return {"ok": False, "reason": "no_equity_data_source", "ticker": "GOOGL"}

    async def _fake_prob_summary(ticker: str) -> dict[str, object]:
        return {"ok": False, "reason": "no_equity_data_source", "ticker": ticker}

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _fake_ta_snapshot,
    )
    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.equity_probabilistic_summary",
        _fake_prob_summary,
    )


def test_analysis_l2_active_for_stock_perp_when_lob_ok(monkeypatch) -> None:
    """Stock perp with ok=True L2 → lob_status=active, source tagged, quality surfaced."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    async def _l2(symbol: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(
            ok=True,
            source="bingx_l2_snapshot_rest",
            data_quality_score=0.82,
        )

    svc.fetch_klines_for_analysis = _fetch
    svc.l2_analysis_for_symbol = _l2
    configure_service(svc)
    _equity_engine_stubs(monkeypatch)

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["market_type"] == "stock_perp"
    assert body["lob_status"] == "active"
    assert body["lob_quality_score"] == 0.82
    assert body["lob_analysis"] is not None
    assert body["lob_analysis"]["ok"] is True
    assert body["lob_analysis"]["source"] == "bingx_l2_snapshot_rest"
    assert "bingx_l2_snapshot_rest" in body["data_sources"]
    assert "l2" not in body["errors"]


def test_analysis_l2_pending_when_lob_unavailable(monkeypatch) -> None:
    """Stock perp with ok=False L2 → lob_status=pending, error captured, source NOT in data_sources."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    async def _l2(symbol: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(
            ok=False,
            source="bingx_l2_unavailable",
            error="l2_unavailable:snapshot_empty",
        )

    svc.fetch_klines_for_analysis = _fetch
    svc.l2_analysis_for_symbol = _l2
    configure_service(svc)
    _equity_engine_stubs(monkeypatch)

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["lob_status"] == "pending"
    assert body["lob_analysis"] is not None
    assert body["lob_analysis"]["ok"] is False
    assert body["lob_quality_score"] is None
    assert "bingx_l2_snapshot_rest" not in body["data_sources"]
    assert body["errors"]["l2"].startswith("UNAVAILABLE")
    assert "snapshot_empty" in body["errors"]["l2"]


def test_analysis_l2_unavailable_when_service_raises(monkeypatch) -> None:
    """Stock perp where l2_analysis_for_symbol raises → endpoint stays 200, error surfaces."""
    svc = MagicMock()
    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    async def _l2_broken(symbol: str) -> object:
        raise RuntimeError("bingx_l2_hub_offline")

    svc.fetch_klines_for_analysis = _fetch
    svc.l2_analysis_for_symbol = _l2_broken
    configure_service(svc)
    _equity_engine_stubs(monkeypatch)

    response = _client().get("/api/v1/bingx-bot/analysis/GOOGL-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["lob_status"] == "unavailable"
    assert body["lob_analysis"] is None
    assert body["lob_quality_score"] is None
    assert "bingx_l2_snapshot_rest" not in body["data_sources"]
    assert body["errors"]["l2"].startswith("UNAVAILABLE")
    assert "l2_fetch_failed" in body["errors"]["l2"]


def test_analysis_rejects_crypto_before_l2(monkeypatch) -> None:
    """Crypto: L2 not wired → lob_status=unavailable, no error, no source."""
    svc = MagicMock()
    svc.fetch_klines_for_analysis = AsyncMock()
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=5m")
    assert response.status_code == 400
    assert response.json()["detail"] == "bingx_bot_synthetic_stocks_only"
    svc.fetch_klines_for_analysis.assert_not_called()
    return

    klines = [_make_kline(i) for i in range(60)]

    async def _fetch(symbol: str, interval: str, limit: int) -> list[BingXKline]:
        return klines

    # l2_analysis_for_symbol must NOT be invoked for crypto — leave the mock
    # unconfigured and assert via the unchanged default fields.
    svc.fetch_klines_for_analysis = _fetch
    configure_service(svc)

    response = _client().get("/api/v1/bingx-bot/analysis/BTC-USDT?interval=5m")
    assert response.status_code == 200
    body = response.json()

    assert body["market_type"] == "crypto_standard"
    assert body["lob_status"] == "unavailable"
    assert body["lob_analysis"] is None
    assert body["lob_quality_score"] is None
    assert "l2" not in body["errors"]
    assert "bingx_l2_snapshot_rest" not in body["data_sources"]


# ─── BingXBotService.l2_analysis_for_symbol — public delegator ─────────────────


def test_bot_service_l2_analysis_public_delegates_to_internal() -> None:
    """The public ``l2_analysis_for_symbol`` must return whatever the internal
    private helper returns — proves routers can call the contract without
    reaching into private state."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis
    from backend.services.bingx_bot_service import BingXBotService

    service = BingXBotService()
    expected = LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    async def _fake_private(_sym: str) -> LOBDynamicsAnalysis:
        return expected

    # Replace the private helper — public method must forward unchanged.
    service._lob_analysis_for_symbol = _fake_private  # type: ignore[method-assign]
    result = asyncio.run(service.l2_analysis_for_symbol("GOOGL-USDT"))
    assert result is expected


# ─── /healthcheck — probe mode ─────────────────────────────────────────────────


def _mock_instruments_with_symbols() -> list[dict]:
    """Mirrors ``_mock_instruments`` but exposes the ``symbol`` field that
    probe-mode needs to sample equity perps."""
    return [
        {
            "symbol": "GOOGL-USDT",
            "market_type": "stock_perp",
            "execution_allowed": True,
            "massive_available": True,
            "fmp_symbol": "GOOGL",
        },
        {
            "symbol": "AAPL-USDT",
            "market_type": "stock_perp",
            "execution_allowed": True,
            "massive_available": False,
            "fmp_symbol": "AAPL",
        },
        {
            "symbol": "TSLA-USDT",
            "market_type": "stock_perp",
            "execution_allowed": True,
            "massive_available": False,
            "fmp_symbol": "TSLA",
        },
        {
            "symbol": "BTC-USDT",
            "market_type": "crypto_standard",
            "execution_allowed": True,
            "massive_available": False,
            "fmp_symbol": None,
        },
        {
            "symbol": "SPX-USDT",
            "market_type": "stock_index_perp",
            "execution_allowed": False,
            "massive_available": False,
            "fmp_symbol": None,
        },
    ]


def _configure_probe_service(*, l2_handler) -> MagicMock:
    """Wire a service mock that returns the standard fixture and an L2 handler."""
    svc = MagicMock()
    svc.dry_run = True

    async def _get_universe() -> list[dict]:
        return _mock_instruments_with_symbols()

    svc.get_universe = _get_universe
    svc.l2_analysis_for_symbol = l2_handler
    configure_service(svc)
    return svc


def _clear_optional_env(monkeypatch) -> None:
    """Make probe-mode env-var assertions deterministic by deleting any
    operator-set probe knobs that could leak from the dev shell."""
    for name in (
        "FMP_API_KEY",
        "FMP_KEY_QUOTES",
        "FMP_KEY_STATEMENTS",
        "FMP_KEY_ANALYST",
        "FMP_KEY_TECHNICAL",
        "FMP_KEY_NEWS",
        "FMP_KEY_SCREENING",
        "MASSIVE_KEY_OPTIONS_PRIMARY",
        "MASSIVE_KEY_OPTIONS_SECONDARY",
        "MASSIVE_KEY_OPTIONS",
        "FINNHUB_API_KEY",
        "BINGX_HEALTHCHECK_L2_SAMPLE",
        "BINGX_HEALTHCHECK_L2_TIMEOUT_S",
        "BINGX_HEALTHCHECK_PROBE_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BINGX_BOT_LIVE_SYMBOL_ALLOWLIST", "")
    from backend.config.settings import load_settings

    load_settings.cache_clear()


def test_healthcheck_default_skips_probe_block(monkeypatch) -> None:
    """probe=false (the default) must remain the fast, config-only path."""
    _clear_optional_env(monkeypatch)

    # If the router ran the L2 probe, this stub would raise — its
    # presence in the assertions below proves it was never invoked.
    async def _l2_should_not_run(_sym: str) -> object:
        raise AssertionError("l2 probe must not run when probe=false")

    _configure_probe_service(l2_handler=_l2_should_not_run)

    response = _client().get("/api/v1/bingx-bot/healthcheck")
    assert response.status_code == 200
    body = response.json()
    assert body["probe_mode"] is False
    assert "l2_probe_active_count" not in body
    assert "fmp_probe" not in body
    assert "options_probe" not in body


def test_healthcheck_probe_l2_counts_active_and_failed(monkeypatch) -> None:
    """probe=true with mixed ok/failed L2 results — counts must split correctly."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)

    # Map symbol → outcome the stub should produce.
    outcomes: dict[str, LOBDynamicsAnalysis] = {
        "GOOGL-USDT": LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest"),
        "AAPL-USDT": LOBDynamicsAnalysis(
            ok=False, source="bingx_l2_unavailable", error="snapshot_empty"
        ),
        "TSLA-USDT": LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest"),
        "SPX-USDT": LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest"),
    }

    async def _l2(symbol: str) -> LOBDynamicsAnalysis:
        return outcomes[symbol]

    _configure_probe_service(l2_handler=_l2)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    assert response.status_code == 200
    body = response.json()

    assert body["probe_mode"] is True
    assert body["l2_probe_sample_size"] == 4
    assert set(body["l2_probe_symbols_sampled"]) == {
        "GOOGL-USDT",
        "AAPL-USDT",
        "TSLA-USDT",
        "SPX-USDT",
    }
    assert body["l2_probe_active_count"] == 3
    assert body["l2_probe_failed_count"] == 1
    failures = body["l2_probe_failures"]
    assert len(failures) == 1
    assert failures[0]["symbol"] == "AAPL-USDT"
    assert failures[0]["reason"] == "snapshot_empty"


def test_healthcheck_probe_l2_captures_exceptions_as_failures(monkeypatch) -> None:
    """A symbol whose L2 fetch raises is reported as a failure, not a 500."""
    _clear_optional_env(monkeypatch)

    async def _l2(symbol: str) -> object:
        raise RuntimeError(f"hub_offline_{symbol}")

    _configure_probe_service(l2_handler=_l2)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    assert response.status_code == 200
    body = response.json()

    assert body["l2_probe_active_count"] == 0
    assert body["l2_probe_failed_count"] == body["l2_probe_sample_size"]
    failure_symbols = {f["symbol"] for f in body["l2_probe_failures"]}
    assert failure_symbols == set(body["l2_probe_symbols_sampled"])
    for failure in body["l2_probe_failures"]:
        assert failure["reason"].startswith("hub_offline_")


def test_healthcheck_probe_l2_respects_sample_env_var(monkeypatch) -> None:
    """``BINGX_HEALTHCHECK_L2_SAMPLE`` caps the probe sample size."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("BINGX_HEALTHCHECK_L2_SAMPLE", "2")

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    assert response.status_code == 200
    body = response.json()

    assert body["l2_probe_sample_size"] == 2
    assert len(body["l2_probe_symbols_sampled"]) == 2


def test_healthcheck_probe_l2_samples_full_live_allowlist(monkeypatch) -> None:
    """Live allowlist is production-critical and must not be truncated by sample cap."""
    import backend.api.routes.bingx_bot_router as mod
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("BINGX_HEALTHCHECK_L2_SAMPLE", "1")

    class _FakeCfg:
        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset({"GOOGL-USDT", "AAPL-USDT"})

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    assert body["l2_probe_sample_size"] == 2
    assert set(body["l2_probe_symbols_sampled"]) == {"GOOGL-USDT", "AAPL-USDT"}


def test_healthcheck_probe_l2_samples_crypto_live_allowlist(monkeypatch) -> None:
    """Crypto-only live allowlists must still be able to satisfy the L2 probe."""
    import backend.api.routes.bingx_bot_router as mod
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("BINGX_HEALTHCHECK_L2_SAMPLE", "1")

    class _FakeCfg:
        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset({"AAPL-USDT"})

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    async def _l2(symbol: str) -> LOBDynamicsAnalysis:
        assert symbol == "AAPL-USDT"
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    assert body["l2_probe_sample_size"] == 1
    assert body["l2_probe_symbols_sampled"] == ["AAPL-USDT"]
    assert body["l2_probe_active_count"] == 1


def test_healthcheck_probe_l2_timeout_recorded(monkeypatch) -> None:
    """A symbol whose L2 fetch hangs is reported with reason='timeout'."""
    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("BINGX_HEALTHCHECK_L2_TIMEOUT_S", "0.5")

    async def _hang(_sym: str) -> object:
        await asyncio.sleep(2.0)
        raise AssertionError("should have timed out")  # pragma: no cover

    _configure_probe_service(l2_handler=_hang)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    assert response.status_code == 200
    body = response.json()

    assert body["l2_probe_active_count"] == 0
    assert body["l2_probe_failed_count"] == body["l2_probe_sample_size"]
    for failure in body["l2_probe_failures"]:
        assert failure["reason"] == "timeout"


def test_healthcheck_probe_fmp_skipped_without_api_key(monkeypatch) -> None:
    """FMP probe skips cleanly when ``FMP_API_KEY`` is absent."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    fmp = body["fmp_probe"]
    assert fmp["status"] == "skipped"
    assert fmp["reason"] == "no_api_key"
    assert fmp["latency_ms"] is None
    assert fmp["ticker"] == "SPY"


def test_healthcheck_probe_fmp_ok_when_snapshot_succeeds(monkeypatch) -> None:
    """FMP probe reports ok when EquityTASnapshotService returns ok=True."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("FMP_API_KEY", "sk-test-not-real")

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    async def _ok_snapshot(self_inst) -> dict[str, object]:
        return {"ok": True, "ticker": "SPY", "rsi_14": 55.0, "source": "fmp"}

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _ok_snapshot,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    fmp = body["fmp_probe"]
    assert fmp["status"] == "ok"
    assert fmp["ticker"] == "SPY"
    assert fmp["reason"] is None
    assert isinstance(fmp["latency_ms"], int) and fmp["latency_ms"] >= 0


def test_healthcheck_probe_fmp_uses_repo_fmp_key_aliases(monkeypatch) -> None:
    """FMP probe must honor the configured FMP_KEY_* envs used by FMPClient."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("FMP_KEY_QUOTES", "sk-test-not-real")

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    async def _ok_snapshot(self_inst) -> dict[str, object]:
        return {"ok": True, "ticker": "SPY", "source": "fmp"}

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _ok_snapshot,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    assert body["providers"]["fmp_api_key"] is True
    assert body["fmp_probe"]["status"] == "ok"


def test_healthcheck_probe_fmp_failure_carries_reason(monkeypatch) -> None:
    """FMP probe surfaces snapshot reason on ok=False."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("FMP_API_KEY", "sk-test-not-real")

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    async def _failed_snapshot(self_inst) -> dict[str, object]:
        return {"ok": False, "reason": "fmp_unauthorized", "ticker": "SPY"}

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _failed_snapshot,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    fmp = body["fmp_probe"]
    assert fmp["status"] == "failed"
    assert fmp["reason"] == "fmp_unauthorized"


def test_healthcheck_probe_options_skipped_without_credentials(monkeypatch) -> None:
    """Options probe skips cleanly when no Massive/Finnhub credential is set."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    options = body["options_probe"]
    assert options["status"] == "skipped"
    assert options["reason"] == "no_api_key"
    assert options["ticker"] == "GOOGL"
    assert options["latency_ms"] is None


def test_healthcheck_probe_options_ok_when_snapshot_succeeds(monkeypatch) -> None:
    """Options probe reports ok when options_snapshot_service yields ok=True."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("MASSIVE_KEY_OPTIONS_PRIMARY", "kp-test-not-real")

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    requested: list[str] = []

    async def _options_snapshot(symbol: str, expiry: object, r: float) -> object:
        requested.append(symbol)
        return SimpleNamespace(
            ok=True,
            spot=180.0,
            gex_levels=SimpleNamespace(call_wall=185.0, put_wall=175.0, zero_gamma_level=180.0),
            iv_surface=SimpleNamespace(iv_percentile_cross_term=0.5, iv_rank_hv_rolling=None),
            chain=[SimpleNamespace(call_oi=50.0, put_oi=40.0, net_dex=900_000.0)],
        )

    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _options_snapshot,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    opt = body["options_probe"]
    assert opt["status"] == "ok"
    assert opt["ticker"] == "GOOGL"
    assert set(opt["tickers"]) == {"GOOGL", "AAPL", "TSLA", "SPY"}
    assert set(requested) == {"GOOGL", "AAPL", "TSLA", "SPY"}
    assert opt["reason"] is None
    assert isinstance(opt["latency_ms"], int) and opt["latency_ms"] >= 0


def test_healthcheck_probe_options_checks_all_allowlisted_equities(monkeypatch) -> None:
    """Options must be verified for the production equity allowlist, not only GOOGL."""
    import backend.api.routes.bingx_bot_router as mod
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("MASSIVE_KEY_OPTIONS_PRIMARY", "kp-test-not-real")

    class _FakeCfg:
        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset({"GOOGL-USDT", "AAPL-USDT"})

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    requested: list[str] = []

    async def _options_snapshot(symbol: str, expiry: object, r: float) -> object:
        requested.append(symbol)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _options_snapshot,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()

    assert response.status_code == 200
    assert body["options_probe"]["status"] == "ok"
    assert set(body["options_probe"]["tickers"]) == {"GOOGL", "AAPL"}
    assert requested == ["GOOGL", "AAPL"]


def test_healthcheck_probe_response_never_includes_credential_values(monkeypatch) -> None:
    """Survival guarantee: probe response must NEVER leak the actual key text."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    fmp_secret = "sk-fmp-redacted-12345-DO-NOT-LEAK"  # nosec # NOSONAR
    massive_secret = "kp-massive-redacted-67890-DO-NOT-LEAK"  # nosec # NOSONAR
    monkeypatch.setenv("FMP_API_KEY", fmp_secret)
    monkeypatch.setenv("MASSIVE_KEY_OPTIONS_PRIMARY", massive_secret)
    monkeypatch.setenv("BINGX_API_KEY", "kp-bingx-redacted-99999-DO-NOT-LEAK")

    async def _l2(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2)

    async def _ok_snapshot(self_inst) -> dict[str, object]:
        return {"ok": True, "ticker": "SPY", "source": "fmp"}

    async def _options_snapshot(symbol: str, expiry: object, r: float) -> object:
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _ok_snapshot,
    )
    monkeypatch.setattr(
        "backend.api.routes.options_router.options_snapshot_service",
        _options_snapshot,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    raw = response.text  # serialised JSON — survives any nested-dict masking gaps

    assert fmp_secret not in raw
    assert massive_secret not in raw
    assert "DO-NOT-LEAK" not in raw
    # Provider presence still surfaces as booleans only.
    providers = response.json()["providers"]
    for value in providers.values():
        assert isinstance(value, bool)


# ─── /live-readiness ───────────────────────────────────────────────────────────


def _make_simple_service(*, dry_run: bool = True) -> MagicMock:
    svc = MagicMock()
    svc.dry_run = dry_run
    svc.universe = ("AAPL-USDT", "GOOGL-USDT")
    return svc


def test_live_readiness_dry_run_service_not_ready() -> None:
    """Default dry-run service must return ready=false."""
    configure_service(_make_simple_service(dry_run=True))
    response = _client().get("/api/v1/bingx-bot/live-readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["client_live"] is False
    assert body["enable_live"] is False


def test_live_readiness_includes_all_required_keys() -> None:
    configure_service(_make_simple_service())
    response = _client().get("/api/v1/bingx-bot/live-readiness")
    assert response.status_code == 200
    body = response.json()
    for key in (
        "ready",
        "enable_live",
        "client_live",
        "paper_trading",
        "allowlist",
        "healthcheck_gate",
        "gates",
    ):
        assert key in body, f"missing key: {key}"
    hc = body["healthcheck_gate"]
    for k in ("required", "fresh", "last_result_ok", "age_s", "ttl_s"):
        assert k in hc, f"missing healthcheck_gate.{k}"


def test_live_readiness_gates_dict_present() -> None:
    configure_service(_make_simple_service())
    body = _client().get("/api/v1/bingx-bot/live-readiness").json()
    gates = body["gates"]
    assert "enable_live" in gates
    assert "client_configured_live" in gates
    assert "healthcheck" in gates
    assert "paper_trading" in gates


def test_live_readiness_healthcheck_gate_reflects_cache(monkeypatch) -> None:
    """When the cache is marked fresh the healthcheck gate should be green."""
    import backend.api.routes.bingx_bot_router as mod

    configure_service(_make_simple_service())
    # Inject a fresh successful cache entry
    original = dict(mod._hc_cache)
    try:
        mod._hc_cache["ok"] = True
        mod._hc_cache["cached_at"] = mod.monotonic()  # now = age 0
        body = _client().get("/api/v1/bingx-bot/live-readiness").json()
        assert body["healthcheck_gate"]["fresh"] is True
        assert body["healthcheck_gate"]["last_result_ok"] is True
    finally:
        mod._hc_cache.update(original)


def test_live_readiness_requires_production_gates(monkeypatch) -> None:
    """Ready cannot be true when only the legacy three gates are green."""
    import backend.api.routes.bingx_bot_router as mod

    configure_service(_make_simple_service(dry_run=False))

    class _FakeCfg:
        bingx_bot_enable_live = True
        bingx_bot_live_require_healthcheck = True
        bingx_bot_live_healthcheck_ttl_s = 300
        bingx_bot_paper_trading = True
        bingx_bot_allow_all_live = False

        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset()

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())
    original_cache = dict(mod._hc_cache)
    original_scheduler = mod._scheduler
    try:
        mod._hc_cache["ok"] = True
        mod._hc_cache["cached_at"] = mod.monotonic()
        mod._scheduler = None

        body = _client().get("/api/v1/bingx-bot/live-readiness").json()
    finally:
        mod._hc_cache.update(original_cache)
        mod._scheduler = original_scheduler

    assert body["ready"] is False
    assert body["gates"]["enable_live"] is True
    assert body["gates"]["client_configured_live"] is True
    assert body["gates"]["healthcheck"] is True
    assert body["gates"]["paper_trading"] is False
    assert body["gates"]["allowlist"] is False
    assert body["gates"]["scheduler"] is False
    assert body["gates"]["audit_store"] is False


# ─── /trade live-mode gates ─────────────────────────────────────────────────────


def _make_trade_service(*, dry_run: bool, trading_environment: str = "paper") -> MagicMock:
    from unittest.mock import AsyncMock

    svc = MagicMock()
    svc.dry_run = dry_run
    svc.trading_environment = trading_environment
    svc.universe = ("AAPL-USDT",)
    cycle_result = MagicMock()
    cycle_result.to_dict.return_value = {"ok": True, "dry_run": dry_run}
    svc.run_cycle = AsyncMock(return_value=cycle_result)
    return svc


def test_trade_dry_run_with_allow_live_false_succeeds() -> None:
    """Default path: dry-run service + allow_live=false → cycle runs."""
    configure_service(_make_trade_service(dry_run=True))
    response = _client().post("/api/v1/bingx-bot/trade", json={"allow_live": False})
    assert response.status_code == 200
    assert response.json()["dry_run"] is True


def test_trade_allow_live_true_but_client_dry_run_returns_409() -> None:
    """allow_live=true on a dry-run client is an explicit mismatch → 409."""
    configure_service(_make_trade_service(dry_run=True))
    response = _client().post("/api/v1/bingx-bot/trade", json={"allow_live": True})
    assert response.status_code == 409
    assert "dry_run" in response.json()["detail"].lower()


def test_trade_client_live_but_allow_live_false_returns_409() -> None:
    """Live client + allow_live=false safety block → 409."""
    configure_service(_make_trade_service(dry_run=False))
    response = _client().post("/api/v1/bingx-bot/trade", json={"allow_live": False})
    assert response.status_code == 409
    assert "allow_live=false" in response.json()["detail"]


def test_trade_live_blocked_when_paper_trading_enabled(monkeypatch) -> None:
    """Live client + allow_live=true + paper mode must never send orders."""
    import backend.api.routes.bingx_bot_router as mod

    configure_service(_make_trade_service(dry_run=False))

    class _FakeCfg:
        bingx_bot_live_require_healthcheck = False
        bingx_bot_paper_trading = True
        bingx_bot_allow_all_live = True

        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset()

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    response = _client().post("/api/v1/bingx-bot/trade", json={"allow_live": True})
    assert response.status_code == 409
    assert "PAPER_TRADING_ENABLED" in response.json()["detail"]


def test_trade_vst_demo_ignores_paper_trading_gate(monkeypatch) -> None:
    """VST demo can submit external demo orders while production live remains gated."""
    import backend.api.routes.bingx_bot_router as mod

    svc = _make_trade_service(dry_run=False, trading_environment="prod-vst")
    configure_service(svc)

    class _FakeCfg:
        bingx_bot_live_require_healthcheck = True
        bingx_bot_paper_trading = True
        bingx_bot_allow_all_live = False

        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset({"AAPL-USDT"})

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    response = _client().post(
        "/api/v1/bingx-bot/trade",
        json={"allow_live": True, "symbols": ["AAPL-USDT"]},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_trade_live_blocked_by_allowlist(monkeypatch) -> None:
    """Live client + allow_live=true but symbol not in allowlist → 403."""
    import backend.api.routes.bingx_bot_router as mod

    configure_service(_make_trade_service(dry_run=False))

    # Patch load_settings to return an allowlist that excludes AAPL-USDT
    class _FakeCfg:
        bingx_bot_live_require_healthcheck = False
        bingx_bot_paper_trading = False
        bingx_bot_allow_all_live = False

        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset({"GOOGL-USDT"})  # AAPL-USDT not included

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    response = _client().post(
        "/api/v1/bingx-bot/trade",
        json={"allow_live": True, "symbols": ["AAPL-USDT"]},
    )
    assert response.status_code == 403
    assert "allowlist" in response.json()["detail"].lower()


def test_trade_live_blocked_when_allowlist_empty(monkeypatch) -> None:
    """Live client + allow_live=true + empty allowlist is not production-safe."""
    import backend.api.routes.bingx_bot_router as mod

    configure_service(_make_trade_service(dry_run=False))

    class _FakeCfg:
        bingx_bot_live_require_healthcheck = False
        bingx_bot_paper_trading = False
        bingx_bot_allow_all_live = False

        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset()

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    response = _client().post("/api/v1/bingx-bot/trade", json={"allow_live": True})
    assert response.status_code == 403
    assert "LIVE_ALLOWLIST_EMPTY" in response.json()["detail"]


def test_trade_live_blocked_by_stale_healthcheck(monkeypatch) -> None:
    """Live + allow_live=true + require_healthcheck=true but cache stale → 409."""
    import backend.api.routes.bingx_bot_router as mod

    configure_service(_make_trade_service(dry_run=False))

    class _FakeCfg:
        bingx_bot_live_require_healthcheck = True
        bingx_bot_live_healthcheck_ttl_s = 300
        bingx_bot_paper_trading = False
        bingx_bot_allow_all_live = True

        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset()  # all allowed

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    # Ensure cache is stale
    original = dict(mod._hc_cache)
    try:
        mod._hc_cache["ok"] = False
        mod._hc_cache["cached_at"] = 0.0
        response = _client().post("/api/v1/bingx-bot/trade", json={"allow_live": True})
        assert response.status_code == 409
        assert "healthcheck" in response.json()["detail"].lower()
    finally:
        mod._hc_cache.update(original)


def test_trade_live_passes_all_gates_and_runs_cycle(monkeypatch) -> None:
    """All gates green → cycle runs normally."""
    import backend.api.routes.bingx_bot_router as mod

    svc = _make_trade_service(dry_run=False)
    configure_service(svc)

    class _FakeCfg:
        bingx_bot_live_require_healthcheck = False  # skip healthcheck gate
        bingx_bot_paper_trading = False
        bingx_bot_allow_all_live = True

        def get_bingx_live_allowlist(self) -> frozenset[str]:
            return frozenset()  # all allowed

    monkeypatch.setattr(mod, "load_settings", lambda: _FakeCfg())

    response = _client().post("/api/v1/bingx-bot/trade", json={"allow_live": True})
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_trade_never_live_by_default(monkeypatch) -> None:
    """A freshly constructed BingXBotService must always start in dry-run mode.

    This test must pass without any env overrides — it is the safety-net that
    ensures CI never runs live orders. We create a fresh instance rather than
    reading the module-level singleton (which other tests may have replaced
    with a live mock).
    """
    from backend.services.bingx_bot_service import BingXBotService

    # BINGX_DRY_RUN must not be overridden to "false"/"live"/"0" in the test env.
    monkeypatch.delenv("BINGX_DRY_RUN", raising=False)
    monkeypatch.delenv("BINGX_BOT_ENABLE_LIVE", raising=False)
    monkeypatch.delenv("BINGX_BOT_TRADING_ENV", raising=False)

    fresh = BingXBotService()
    assert fresh.dry_run is True, (
        "BingXBotService must start in dry-run mode by default. "
        "Live mode requires BINGX_BOT_ENABLE_LIVE=true at server startup."
    )


# ─── /healthcheck probe_ok field ──────────────────────────────────────────────


def test_healthcheck_probe_ok_false_when_options_probe_is_skipped(monkeypatch) -> None:
    """probe_ok=false unless L2, FMP and options probes are all production-green."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    async def _l2_ok(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2_ok)

    async def _snapshot_ok(self_inst) -> dict[str, object]:
        return {"ok": True, "ticker": "SPY", "source": "fmp"}

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _snapshot_ok,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()
    assert response.status_code == 200
    assert body["probe_ok"] is False
    assert body["options_probe"]["status"] == "skipped"


def test_healthcheck_probe_ok_false_when_fmp_fails(monkeypatch) -> None:
    """probe_ok=false when fmp probe fails even if l2 is active."""
    from backend.quant_engine.engines.technical.lob_dynamics_engine import LOBDynamicsAnalysis

    _clear_optional_env(monkeypatch)
    monkeypatch.setenv("FMP_API_KEY", "test-key")

    async def _l2_ok(_sym: str) -> LOBDynamicsAnalysis:
        return LOBDynamicsAnalysis(ok=True, source="bingx_l2_snapshot_rest")

    _configure_probe_service(l2_handler=_l2_ok)

    async def _snapshot_fail(self_inst) -> dict[str, object]:
        raise RuntimeError("fmp down")

    monkeypatch.setattr(
        "backend.api.routes.bingx_bot_router.EquityTASnapshotService.snapshot",
        _snapshot_fail,
    )

    response = _client().get("/api/v1/bingx-bot/healthcheck?probe=true")
    body = response.json()
    assert response.status_code == 200
    assert body["probe_ok"] is False
