"""
backend/layer_1_data/fetchers/primary_fetcher.py
════════════════════════════════════════════════════════════════════════════════
Primary (Matba Rofex) — REST connector using official pyRofex SDK.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pyRofex

from backend.config.settings import load_settings
from backend.domain.primary_models import PrimaryInstrument

logger = logging.getLogger("backend.layer_1_data.fetchers.primary")


class PrimaryFetcher:
    """
    Wrapper for pyRofex REST methods.
    Handles initialization and data retrieval using the official SDK.
    """

    def __init__(self) -> None:
        self.settings = load_settings()
        self._initialized = False

    def _ensure_initialized(self):
        """Native pyRofex initialization (Environment: REMARKETS)."""
        if not self._initialized:
            try:
                pyRofex.initialize(
                    user=self.settings.primary_user,
                    password=self.settings.primary_password,
                    account=self.settings.primary_account,
                    environment=pyRofex.Environment.REMARKET,
                )
                self._initialized = True
                logger.info("pyRofex SDK initialized successfully (REMARKETS).")
            except Exception as e:
                logger.error(f"Failed to initialize pyRofex SDK: {e}")
                raise

    async def get_instruments(self) -> list[PrimaryInstrument]:
        """Fetch all available instruments using pyRofex."""
        self._ensure_initialized()
        try:
            # pyRofex is sync, wrap in thread for async compatibility
            data = await asyncio.to_thread(pyRofex.get_all_instruments)
            if not data or data.get("status") != "OK":
                return []

            instruments_raw = data.get("instruments", [])
            instruments = []
            for item in instruments_raw:
                try:
                    # El SDK anida los IDs, los aplanamos para el modelo Pydantic
                    flat_item = item.get("instrumentId", {}).copy()
                    flat_item.update({k: v for k, v in item.items() if k != "instrumentId"})
                    instruments.append(PrimaryInstrument(**flat_item))
                except Exception:
                    continue
            return instruments
        except Exception as e:
            logger.error(f"Error fetching instruments via pyRofex: {e}")
            return []

    async def get_historical_trades(
        self, market_id: str, symbol: str, date_from: str, date_to: str
    ) -> list[dict[str, Any]]:
        """Fetch historical trades using pyRofex."""
        self._ensure_initialized()
        try:
            # Note: pyRofex.get_trade_history expects datetime or strings depending on version
            data = await asyncio.to_thread(
                pyRofex.get_trade_history, ticker=symbol, start_date=date_from, end_date=date_to
            )
            if not data or data.get("status") != "OK":
                return []
            return data.get("trades", [])
        except Exception as e:
            logger.error(f"Error fetching historical trades via pyRofex: {e}")
            return []

    async def close(self):
        """pyRofex handles its own sessions, no explicit close needed for REST."""
        pass


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : primary_fetcher.py
# Sub-capa         : Fetchers (REST)
# Enfoque          : Utiliza el SDK oficial pyRofex.
# ─────────────────────────────────────────────────────────────────────
