from __future__ import annotations
"""Buffer circular para almacenar y analizar ticks en tiempo real.

Proporciona métricas de momentum, volatilidad, volume spike y VWAP
para la generación de señales de ejecución en Phase D.
"""


from collections import deque
from decimal import Decimal


class TickBuffer:
    """Buffer circular para almacenar ticks recientes de un contrato."""

    def __init__(self, max_size: int = 100) -> None:
        self._prices: deque[Decimal] = deque(maxlen=max_size)
        self._volumes: deque[int] = deque(maxlen=max_size)
        self._timestamps: deque[float] = deque(maxlen=max_size)
        self._vwap_prices: deque[Decimal] = deque(maxlen=max_size)

    def add(self, price: Decimal, volume: int, timestamp: float) -> None:
        self._prices.append(price)
        self._volumes.append(volume)
        self._timestamps.append(timestamp)
        self._vwap_prices.append(price * volume)

    @property
    def count(self) -> int:
        return len(self._prices)

    @property
    def last_price(self) -> Decimal | None:
        return self._prices[-1] if self._prices else None

    @property
    def prices(self) -> list[Decimal]:
        return list(self._prices)

    @property
    def volumes(self) -> list[int]:
        return list(self._volumes)

    def vwap(self) -> float:
        if not self._vwap_prices or not self._volumes:
            return 0.0
        total_value = sum(self._vwap_prices)
        total_volume = sum(self._volumes)
        return float(total_value / max(total_volume, 1))

    def price_change_pct(self, window: int = 10) -> float:
        if len(self._prices) < 2:
            return 0.0
        recent = list(self._prices)[-window:]
        if not recent or recent[0] == 0:
            return 0.0
        return float((recent[-1] - recent[0]) / recent[0])

    def volatility(self, window: int = 20) -> float:
        if len(self._prices) < window:
            return 0.0
        recent = list(self._prices)[-window:]
        returns = [
            float((recent[i] - recent[i - 1]) / max(recent[i - 1], Decimal("1e-8")))
            for i in range(1, len(recent))
        ]
        if not returns:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        variance: float = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return float(variance**0.5)

    def volume_spike(self, threshold: float = 2.5) -> bool:
        if len(self._volumes) < 10:
            return False
        recent = list(self._volumes)[-10:]
        avg = sum(recent[:-1]) / max(len(recent) - 1, 1)
        return recent[-1] > avg * threshold
