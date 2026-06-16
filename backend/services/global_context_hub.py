from typing import Any

from backend.hub.market_data_hub import MarketDataHub


class GlobalContextHub:
    """Fetches macro and index data from MarketDataHub for Global Context."""

    def __init__(self, market_hub: MarketDataHub) -> None:
        self.market_hub = market_hub

    async def fetch_context_data(self) -> dict[str, Any]:
        """Fetch VIX, SPY, QQQ and construct context dict."""
        # Fetch VIX
        vix_res = await self.market_hub.get_vix_level()
        vix_val = vix_res.unwrap() if vix_res.is_success else 0.0

        # Fetch SPY
        spy_res = await self.market_hub.get_market_snapshot("SPY")
        spy_val = spy_res.unwrap() if spy_res.is_success else None

        # Fetch QQQ
        qqq_res = await self.market_hub.get_market_snapshot("QQQ")
        qqq_val = qqq_res.unwrap() if qqq_res.is_success else None

        return {
            "vix": vix_val,
            "spy": spy_val,
            "qqq": qqq_val,
        }
