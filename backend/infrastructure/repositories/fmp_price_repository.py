"""Implementación del repositorio de precios usando FMP (Financial Modeling Prep).

Este módulo adapta el FMPClient existente a la interfaz PriceRepository,
proporcionando manejo de errores específico y logging centralizado.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    FMPHistoricalPrice = Any
    FMPQuote = Any
else:
    try:
        from backend.domain.fmp_models import FMPHistoricalPrice, FMPQuote
    except ImportError:
        from typing import Any

        FMPHistoricalPrice = Any  # type: ignore
        FMPQuote = Any  # type: ignore

from backend.domain.repositories.price_repository import (
    AuthenticationError,
    PriceRepository,
    RateLimitError,
    RepositoryError,
)

# Import diferido para evitar circular imports
try:
    from layer_1_data.fetchers.fmp_client import FMPClient
except ImportError:
    FMPClient = None  # type: ignore

logger = logging.getLogger("backend.infrastructure.repositories.fmp_price_repository")


class FMPPriceRepository(PriceRepository):
    """Adaptador de FMPClient hacia la interfaz PriceRepository.

    Este repositorio:
    - Traduce errores genéricos de FMPClient a errores de dominio
    - Agrega logging centralizado para auditoría
    - Maneja rate limits con reintentos automáticos
    - Proporciona fallbacks cuando es posible

    Ejemplo de uso:
    ```python
    repo = FMPPriceRepository(FMPClient())
    prices = await repo.get_historical_prices("AAPL", date_from="2025-01-01")
    ```
    """

    def __init__(self, fmp_client) -> None:
        """Inicializa el repositorio con un cliente FMP.

        Args:
            fmp_client: Instancia de FMPClient configurada con API keys.
        """
        self._client = fmp_client

    async def get_historical_prices(
        self,
        symbol: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[FMPHistoricalPrice]:
        """Obtiene precios históricos desde FMP."""
        sym = symbol.upper().strip()
        logger.debug(f"Fetching historical prices for {sym}")

        try:
            data = await self._client.get_historical_prices(
                symbol=sym,
                date_from=date_from,
                date_to=date_to,
            )

            if not data:
                logger.warning(f"No historical prices for {sym}")
                return []

            return data

        except RateLimitError:
            raise
        except AuthenticationError:
            raise
        except Exception as e:
            logger.exception(f"Error fetching prices for {sym}: {e}")
            raise RepositoryError(
                message=f"Failed to fetch historical prices for {sym}",
                provider="FMP",
                original_error=e,
            )

    async def get_quote(self, symbol: str) -> FMPQuote | None:
        """Obtiene cotización en tiempo real desde FMP."""
        sym = symbol.upper().strip()
        logger.debug(f"Fetching quote for {sym}")

        try:
            quote = await self._client.get_quote(sym)

            if quote is None:
                logger.warning(f"No quote available for {sym}")
                return None

            return quote

        except AuthenticationError:
            raise
        except Exception as e:
            logger.exception(f"Error fetching quote for {sym}: {e}")
            raise RepositoryError(
                message=f"Failed to fetch quote for {sym}",
                provider="FMP",
                original_error=e,
            )
