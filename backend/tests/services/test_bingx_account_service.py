from __future__ import annotations
from typing import Any

from dataclasses import dataclass

import pytest

from backend.services.bingx_account_service import BingXAccountService


class FakeClient:
    dry_run = True

    async def fetch_perp_balance(self) -> dict[str, Any]:
        return {
            "balance": {
                "asset": "USDT",
                "equity": "100",
                "availableMargin": "70",
                "usedMargin": "30",
                "unrealizedProfit": "5",
            }
        }

    async def fetch_account_balance(self) -> dict[str, Any]:
        return {"balances": [{"asset": "USDT", "free": "10", "locked": "0"}]}

    async def fetch_perp_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "AAPL-USDT",
                "positionSide": "LONG",
                "positionAmt": "0.5",
                "entryPrice": "190",
                "markPrice": "195",
                "unrealizedProfit": "2.5",
                "leverage": "5",
                "liquidationPrice": "120",
                "marginType": "ISOLATED",
            },
            {
                "symbol": "BTC-USDT",
                "positionSide": "SHORT",
                "positionAmt": "-0.01",
                "entryPrice": "50000",
                "markPrice": "49000",
                "unrealizedProfit": "10",
                "leverage": "2",
                "marginType": "CROSSED",
            },
        ]

    async def fetch_open_orders_perp(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return [{"symbol": "AAPL-USDT", "orderId": "abc", "side": "BUY", "price": "190"}]

    async def fetch_open_orders_spot(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return []

    async def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        return {"lastFundingRate": "0.0002"}


@dataclass
class FakeQuote:
    symbol: str
    price: float

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return {"symbol": self.symbol, "price": self.price}


class FakeFMP:
    async def get_quote(self, symbol: str) -> FakeQuote | None:
        return FakeQuote(symbol, 194.0) if symbol == "AAPL" else None


@pytest.mark.asyncio
async def test_account_state_aggregates_balances_positions_orders_and_risk() -> None:
    service = BingXAccountService(client=FakeClient(), fmp_client=FakeFMP())

    state = await service.get_account_state()

    assert state.total_equity_usdt == 100.0
    assert state.available_margin_usdt == 70.0
    assert state.used_margin_usdt == 30.0
    assert state.unrealized_pnl_usdt == 5.0
    assert state.position_count == 2
    assert state.margin_ratio == 0.3
    assert state.largest_position_pct == 4.9
    assert state.open_positions[0].fmp_quote == {"symbol": "AAPL", "price": 194.0}
    assert state.open_positions[0].funding_rate == 0.0002
    assert state.open_orders[0].venue_order_id == "abc"
