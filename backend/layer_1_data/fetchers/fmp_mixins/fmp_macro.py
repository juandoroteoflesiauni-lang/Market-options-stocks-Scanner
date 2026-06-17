from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config.logger_setup import get_logger
from backend.domain.fmp_models import *

# ruff: noqa: F403, F405


logger = get_logger(__name__)

if TYPE_CHECKING:
    pass


class FMPMacroMixin:
    """Mixin for FMP Client."""

    async def get_economic_indicator(self, name: str) -> list[FMPEconomicIndicator]:
        """Fetch macroeconomic indicators. GET /v4/economic?name={name}."""
        data = await self._get(
            "/v4/economic",
            module="MACRO",
            params={"name": name},
            ttl_secs=604800.0,  # 1 week cache
        )
        parsed = self._parse_list(data, FMPEconomicIndicator)
        if parsed:
            return parsed
        from backend.layer_1_data.fetchers.macro_fallback_fetcher import (
            fetch_economic_indicator_fred,
            macro_fallback_enabled,
        )

        if macro_fallback_enabled():
            return await fetch_economic_indicator_fred(name)
        return []

    async def get_treasury_rates(self, from_date: str, to_date: str) -> list[FMPTreasuryRate]:
        """Fetch US Treasury rates. GET /v4/treasury."""
        params = {"from": from_date, "to": to_date}
        data = await self._get("/v4/treasury", module="MACRO", params=params, ttl_secs=86400.0)
        parsed = self._parse_list(data, FMPTreasuryRate)
        if parsed:
            return parsed
        from backend.layer_1_data.fetchers.macro_fallback_fetcher import (
            fetch_treasury_rates_fred,
            macro_fallback_enabled,
        )

        if macro_fallback_enabled():
            return await fetch_treasury_rates_fred(from_date, to_date)
        return []
