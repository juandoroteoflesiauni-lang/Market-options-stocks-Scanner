"""Tests unitarios para PriceRepository - Domain Layer.

Estos tests demuestran las ventaja de Clean Architecture:
- Testear dominio sin infraestructura real (FMPClient)
- Mockear repositorios para tests de servicios
- Validar manejo de errores sin llamadas HTTP reales

Ejecutar: pytest backend/domain/repositories/test_price_repository.py -v
"""

from datetime import date, timedelta

import pytest

# Import using absolute paths within backend package
from domain.fmp_models import FMPHistoricalPrice
from domain.repositories.price_repository import (
    AuthenticationError,
    PriceRepository,
    RateLimitError,
    RepositoryError,
)


def test_price_repository_is_abstract():
    """Verifica que PriceRepository sea una clase abstracto."""
    import inspect

    assert inspect.isabstract(PriceRepository)


def test_repository_error_creation():
    """Testea creación de RepositoryError."""
    error = RepositoryError("Test error", "FMP")
    assert "Test error" in str(error)
    assert "FMP" in str(error)


def test_rate_limit_error_creation():
    """Testea creación de RateLimitError."""
    error = RateLimitError("Rate limit", "FMP", retry_after_secs=60)
    assert error.retry_after_secs == 60
    assert "Rate limit" in str(error)


def test_authentication_error_creation():
    """Testea creación de AuthenticationError."""
    error = AuthenticationError("Invalid API key", "FMP")
    assert error.provider == "FMP"
    assert "Invalid API key" in str(error)


def create_mock_historical_prices(n_days: int = 100) -> list[FMPHistoricalPrice]:
    """Crea datos históricos mock para tests."""
    base_date = date(2025, 1, 1)
    return [
        FMPHistoricalPrice(
            date=(base_date + timedelta(days=i)).isoformat(),
            open=100.0 + i * 0.1,
            high=101.0 + i * 0.1,
            low=99.0 + i * 0.1,
            close=100.5 + i * 0.1,
            adjClose=100.5 + i * 0.1,
            volume=1000000,
        )
        for i in range(n_days)
    ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
