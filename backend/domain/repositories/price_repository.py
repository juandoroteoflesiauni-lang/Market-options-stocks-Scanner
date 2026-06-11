"""Repositorio abstracto para acceso a precios históricos.

Este módulo define el contrato de dominio para obtener datos OHLCV,
aislando la lógica de negocio de los detalles de implementación de fetchers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

# Import relativo para funcionar correctamente dentro del package backend
if TYPE_CHECKING:
    from typing import Any

    FMPHistoricalPrice = Any
    FMPQuote = Any
else:
    try:
        from ..fmp_models import FMPHistoricalPrice, FMPQuote
    except ImportError:
        # Fallback para testing directo del módulo
        from typing import Any

        FMPHistoricalPrice = Any  # type: ignore
        FMPQuote = Any  # type: ignore


class PriceRepository(ABC):
    """Contrato de dominio para repositorios de precios.

    Este repositorio abstracto define la interfaz que deben implementar
    todos los proveedores de datos históricos (FMP, Polygon, Yahoo Finance, etc.).

    La capa de dominio (Layer 3 Specialists) depende de esta abstracción,
    no de implementaciones concretas. Esto permite:

    - Testing unitario sin dependencias externas
    - Intercambiar proveedores sin cambiar la lógica de negocio
    - Múltiples implementaciones (ej: FMP + Polygon para redundancia)
    """

    @abstractmethod
    async def get_historical_prices(
        self,
        symbol: str,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[FMPHistoricalPrice]:
        """Obtiene precios históricos OHLCV para un símbolo.

        Args:
            symbol: Ticker del activo (ej: "AAPL", "SPY")
            date_from: Fecha inicial en formato "YYYY-MM-DD". Si es None,
                       el proveedor decide el default.
            date_to: Fecha final en formato "YYYY-MM-DD". Si es None,
                     se asume "hoy".

        Returns:
            Lista de FMPHistoricalPrice ordenados por fecha (más antiguo primero).
            Lista vacía si no hay datos o error.

        Raises:
            RepositoryError: Si hay un error de infraestructura (red, auth, etc.).
                            El caller debe manejarlo con un fallback o error 5xx.
        """
        pass

    @abstractmethod
    async def get_quote(self, symbol: str) -> FMPQuote | None:
        """Obtiene cotización en tiempo real para un símbolo.

        Args:
            symbol: Ticker del activo

        Returns:
            FMPQuote con el precio actual o None si no disponible.
        """
        pass


class RepositoryError(Exception):
    """Error de infraestructura en repositorio.

    Se lanza cuando un repositorio falla por causas externas:
    - Error de red (timeout, conexión)
    - Error de autenticación (API key inválida)
    - Error del proveedor (5xx, rate limit)

    El caller debe capturar este error y decidir si:
    - Reintentar con backoff exponencial
    - Usar un fallback (cache, otro proveedor)
    - Retornar error 5xx al cliente
    """

    def __init__(self, message: str, provider: str, original_error: Exception | None = None):
        self.message = message
        self.provider = provider
        self.original_error = original_error
        super().__init__(f"[{provider}] {message}")


class RateLimitError(RepositoryError):
    """Error de rate limit del proveedor.

    Se lanza cuando el proveedor rechaza requests por exceder límites.
    El caller debe implementar backoff exponencial.
    """

    def __init__(self, message: str, provider: str, retry_after_secs: int | None = None):
        super().__init__(message, provider)
        self.retry_after_secs = retry_after_secs


class AuthenticationError(RepositoryError):
    """Error de autenticación con el proveedor.

    Se lanza cuando la API key es inválida o expiró.
    El caller debe loguear el error y alertar al operador.
    """

    def __init__(self, message: str, provider: str):
        super().__init__(message, provider)
