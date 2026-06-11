from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from backend.layer_1_data.datos import bingx_client as bingx_client_module
from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient


class RecordingClient(BingXClient):
    def __init__(self, *, dry_run: bool = True) -> None:
        super().__init__(
            api_key="key",
            secret_key="secret",
            dry_run=dry_run,
            allow_env_dry_run_override=False,
        )
        self.public_calls: list[tuple[str, dict[str, Any]]] = []
        self.signed_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _resolve_perp_symbol(self, display_name: str) -> str:
        return f"API-{display_name}" if display_name == "AAPL-USDT" else display_name

    async def _public_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.public_calls.append((path, params))
        if path.endswith("/ticker"):
            if params:
                return {"data": {"symbol": params["symbol"], "lastPrice": "123.45"}}
            return {
                "data": [
                    {
                        "symbol": "BTC-USDT",
                        "lastPrice": "100",
                        "quoteVolume": "25000000",
                    }
                ]
            }
        if path.endswith("/price"):
            return {"data": {"symbol": params["symbol"], "price": "42.5"}}
        if path.endswith("/depth"):
            return {"data": {"bids": [["100", "1"]], "asks": [["101", "2"]]}}
        if path.endswith("/premiumIndex"):
            return {"data": {"lastFundingRate": "0.0001", "markPrice": "101"}}
        if path.endswith("/trades"):
            return {"data": [{"price": "100", "qty": "0.1"}]}
        if path.endswith("/openInterest"):
            return {"data": {"openInterest": "2500000"}}
        if path.endswith("/symbols"):
            return {"data": {"symbols": [{"symbol": "BTC-USDT"}]}}
        return {"data": {}}

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.signed_calls.append((method, path, params))
        return {"data": {"ok": True, "path": path, **params}}


def test_trading_environment_reports_vst_when_demo_base_url() -> None:
    client = BingXClient(
        api_key="key",
        secret_key="secret",
        base_url=BINGX_REST_VST_BASE,
        dry_run=False,
        allow_env_dry_run_override=False,
    )
    assert client.trading_environment == "prod-vst"
    assert client.dry_run is False


class ConcurrentMapClient(BingXClient):
    def __init__(self) -> None:
        super().__init__(api_key="key", secret_key="secret", dry_run=True)
        self.map_calls = 0

    async def fetch_perp_symbol_map(self) -> dict[str, str]:
        self.map_calls += 1
        await asyncio.sleep(0.01)
        return {"AAPL-USDT": "API-AAPL-USDT"}

    async def _public_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        assert path == "/openApi/swap/v2/quote/klines"
        assert params["symbol"] == "API-AAPL-USDT"
        return {
            "data": [
                [1, "100", "101", "99", "100.5", "10", 59],
            ]
        }


class PaginatedKlineClient(BingXClient):
    def __init__(self) -> None:
        super().__init__(api_key="key", secret_key="secret", dry_run=True)
        self.public_calls: list[tuple[str, dict[str, Any]]] = []

    async def _resolve_perp_symbol(self, display_name: str) -> str:
        return display_name

    async def _public_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.public_calls.append((path, params))
        page_index = len(self.public_calls) - 1
        if page_index == 0:
            start = 560
            count = 1440
        else:
            start = 0
            count = 561
        rows = [
            {
                "time": (start + i) * 60_000,
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100.5",
                "volume": "10",
                "closeTime": (start + i) * 60_000 + 59_999,
            }
            for i in range(count)
        ]
        return {"data": rows}


class FailingHttpClient:
    is_closed = False

    async def get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            status_code=502,
            request=httpx.Request("GET", f"https://open-api.bingx.com{path}"),
            text='{"msg":"upstream unavailable"}',
        )

    async def aclose(self) -> None:
        self.is_closed = True


class TimeoutHttpClient:
    is_closed = False

    async def get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        raise httpx.ReadTimeout(
            "", request=httpx.Request("GET", f"https://open-api.bingx.com{path}")
        )

    async def aclose(self) -> None:
        self.is_closed = True


class PoolTimeoutHttpClient:
    is_closed = False

    async def get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        raise httpx.PoolTimeout(
            "", request=httpx.Request("GET", f"https://open-api.bingx.com{path}")
        )

    async def aclose(self) -> None:
        self.is_closed = True


@pytest.mark.asyncio
async def test_public_perp_market_methods_resolve_symbol_and_unwrap_payloads() -> None:
    client = RecordingClient()

    price = await client.fetch_latest_price_perp("AAPL-USDT")
    order_book = await client.fetch_order_book_perp("AAPL-USDT", limit=10)
    funding = await client.fetch_funding_rate("AAPL-USDT")
    trades = await client.fetch_recent_trades_perp("AAPL-USDT", limit=5)
    tickers = await client.fetch_all_tickers_perp()
    oi = await client.fetch_open_interest("AAPL-USDT")

    assert price == 123.45
    assert order_book["bids"] == [["100", "1"]]
    assert funding["lastFundingRate"] == "0.0001"
    assert trades == [{"price": "100", "qty": "0.1"}]
    assert tickers[0]["symbol"] == "BTC-USDT"
    assert oi["openInterest"] == "2500000"
    paths = [path for path, _params in client.public_calls]
    assert "/openApi/swap/v2/quote/depth" in paths
    assert all(
        params.get("symbol") == "API-AAPL-USDT"
        for path, params in client.public_calls
        if path != "/openApi/swap/v2/quote/ticker" or params
    )


@pytest.mark.asyncio
async def test_concurrent_perp_resolution_loads_contract_map_once() -> None:
    client = ConcurrentMapClient()

    rows = await asyncio.gather(
        client.fetch_klines_perp("AAPL-USDT"),
        client.fetch_klines_perp("AAPL-USDT"),
        client.fetch_klines_perp("AAPL-USDT"),
        client.fetch_klines_perp("AAPL-USDT"),
    )

    assert [len(item) for item in rows] == [1, 1, 1, 1]
    assert client.map_calls == 1


@pytest.mark.asyncio
async def test_fetch_klines_perp_paginates_to_requested_2000_bars() -> None:
    client = PaginatedKlineClient()

    rows = await client.fetch_klines_perp("BTC-USDT", limit=2000)

    assert len(rows) == 2000
    assert rows[0].open_time_ms == 0
    assert rows[-1].open_time_ms == 1999 * 60_000
    assert len(client.public_calls) == 2
    assert client.public_calls[0][1]["limit"] == 1440
    assert client.public_calls[1][1]["limit"] == 561
    assert client.public_calls[1][1]["endTime"] == 560 * 60_000


@pytest.mark.asyncio
async def test_public_get_http_error_preserves_status_and_body() -> None:
    client = BingXClient()
    client._client = FailingHttpClient()  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as exc_info:
        await client.fetch_klines_perp("AAPL-USDT")

    message = str(exc_info.value)
    assert "status=502" in message
    assert "upstream unavailable" in message


@pytest.mark.asyncio
async def test_public_get_logs_exception_type_when_message_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BingXClient()
    client._client = TimeoutHttpClient()  # type: ignore[assignment]
    warnings: list[str] = []

    def fake_warning(template: str, *args: Any) -> None:
        warnings.append(template % args)

    monkeypatch.setattr(bingx_client_module.logger, "warning", fake_warning)

    with pytest.raises(RuntimeError) as exc_info:
        await client.fetch_open_interest("AAPL-USDT")

    assert "ReadTimeout" in str(exc_info.value)
    assert warnings
    assert "ReadTimeout" in warnings[0]


@pytest.mark.asyncio
async def test_public_get_treats_pool_timeout_as_debug_backpressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BingXClient()
    client._client = PoolTimeoutHttpClient()  # type: ignore[assignment]
    warnings: list[str] = []
    debug_logs: list[str] = []

    def fake_warning(template: str, *args: Any) -> None:
        warnings.append(template % args)

    def fake_debug(template: str, *args: Any) -> None:
        debug_logs.append(template % args)

    monkeypatch.setattr(bingx_client_module.logger, "warning", fake_warning)
    monkeypatch.setattr(bingx_client_module.logger, "debug", fake_debug)

    with pytest.raises(RuntimeError) as exc_info:
        await client.fetch_open_interest("AAPL-USDT")

    assert "pool timeout" in str(exc_info.value)
    assert warnings == []
    assert debug_logs
    assert "pool_timeout" in debug_logs[0]


@pytest.mark.asyncio
async def test_signed_perp_methods_return_dry_run_stubs_without_network() -> None:
    client = RecordingClient(dry_run=True)

    balance = await client.fetch_perp_balance()
    positions = await client.fetch_perp_positions()
    open_orders = await client.fetch_open_orders_perp("BTC-USDT")
    cancel = await client.cancel_order_perp("BTC-USDT", venue_order_id="abc")
    cancel_all = await client.cancel_all_orders_perp("BTC-USDT")
    close_all = await client.close_all_positions(confirm=True)
    leverage = await client.set_leverage_perp("BTC-USDT", 3)
    margin = await client.set_margin_type_perp("BTC-USDT", "ISOLATED")
    spot_orders = await client.fetch_open_orders_spot("BTC-USDT")

    assert balance["dry_run"] is True
    assert positions == []
    assert open_orders == []
    assert cancel["dry_run"] is True
    assert cancel_all["dry_run"] is True
    assert close_all["dry_run"] is True
    assert leverage["leverage"] == 3
    assert margin["marginType"] == "ISOLATED"
    assert spot_orders == []
    assert client.signed_calls == []


@pytest.mark.asyncio
async def test_signed_perp_methods_call_expected_live_paths() -> None:
    client = RecordingClient(dry_run=False)

    await client.fetch_perp_balance()
    await client.fetch_perp_positions("AAPL-USDT")
    await client.fetch_open_orders_perp("AAPL-USDT")
    await client.cancel_order_perp("AAPL-USDT", venue_order_id="abc")
    await client.cancel_all_orders_perp("AAPL-USDT")
    await client.close_all_positions(confirm=True)
    await client.set_leverage_perp("AAPL-USDT", 4, side="LONG")
    await client.set_margin_type_perp("AAPL-USDT", "CROSSED")
    await client.fetch_trade_history_perp("AAPL-USDT", limit=20)

    assert [call[1] for call in client.signed_calls] == [
        "/openApi/swap/v2/user/balance",
        "/openApi/swap/v2/user/positions",
        "/openApi/swap/v2/trade/openOrders",
        "/openApi/swap/v2/trade/order",
        "/openApi/swap/v2/trade/allOpenOrders",
        "/openApi/swap/v2/trade/closeAllPositions",
        "/openApi/swap/v2/user/leverage",
        "/openApi/swap/v2/user/marginType",
        "/openApi/swap/v2/trade/allFillOrders",
    ]
    assert client.signed_calls[1][2]["symbol"] == "API-AAPL-USDT"
    assert client.signed_calls[6][2]["side"] == "LONG"
