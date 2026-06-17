"""Binance Bot Orchestrator

Top-level service for orchestrating Binance Spot and USD-M Futures.
"""

from __future__ import annotations

from typing import Any

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.binance_client import BinanceClient

logger = get_logger(__name__)


class BinanceBotService:
    """Top-level orchestrator for the Binance account bot."""

    def __init__(
        self,
        client: BinanceClient | None = None,
    ) -> None:
        self._owns_client: bool = client is None
        self._client: BinanceClient = client or BinanceClient(dry_run=True)

    @property
    def dry_run(self) -> bool:
        return self._client.dry_run

    @property
    def trading_environment(self) -> str:
        return self._client.trading_environment

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def status(self) -> dict[str, Any]:
        """Return a static snapshot of bot configuration."""
        return {
            "service": "binance_bot",
            "dry_run": self.dry_run,
            "trading_environment": self.trading_environment,
        }

    async def run_cycle(self) -> dict[str, Any]:
        """Run one bot cycle."""
        logger.info("binance_bot.cycle_started env=%s", self.trading_environment)
        # TODO: Implement scanner, filter, risk and execution mixins
        return {"status": "ok", "message": "Cycle complete."}
