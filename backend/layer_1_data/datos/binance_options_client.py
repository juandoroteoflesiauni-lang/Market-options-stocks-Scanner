from __future__ import annotations
from typing import Any
"""Public read-only Binance Options market-data client."""



import requests


class BinanceOptionsClient:
    """Wrapper for Binance Options EAPI public endpoints.

    Intentionally uses eapi.binance.com. The old vapi host is not used for FTMO.
    """

    BASE_URL = "https://eapi.binance.com/eapi/v1"

    def __init__(self, session: Any | None = None, *, timeout: float = 15.0) -> None:
        self.session = session or requests.Session()
        self.timeout = float(timeout)

    def exchange_info(self) -> dict[str, Any]:
        payload = self._get("/exchangeInfo", {})
        return payload if isinstance(payload, dict) else {}

    def mark(self, *, underlying: str = "BTCUSDT") -> list[dict[str, Any]]:
        payload = self._get("/mark", {"underlying": underlying})
        return payload if isinstance(payload, list) else []

    def ticker(self, *, underlying: str = "BTCUSDT") -> list[dict[str, Any]]:
        payload = self._get("/ticker", {"underlying": underlying})
        return payload if isinstance(payload, list) else []

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
