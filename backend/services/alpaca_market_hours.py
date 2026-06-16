"""Guard de horario de mercado para acciones (Alpaca). # [PD-3][IM][TH]

Las acciones no operan 24/7. Este guard consulta el reloj de mercado de
Alpaca (``/v2/clock``) y decide si la sesión regular está abierta antes de
permitir un ciclo de trading.
"""

from __future__ import annotations

from typing import Protocol

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


class _ClockClient(Protocol):
    async def get_clock(self) -> dict[str, object]: ...


class AlpacaMarketHoursGuard:
    """Decide si el mercado de acciones está abierto para operar."""

    def __init__(self, client: _ClockClient) -> None:
        self._client = client

    async def is_market_open(self) -> bool:
        """``True`` si Alpaca reporta la sesión regular abierta."""
        try:
            clock = await self._client.get_clock()
        except Exception as exc:
            logger.error("alpaca_market_hours.clock_failed error=%s", exc)
            return False
        return bool(clock.get("is_open", False))
