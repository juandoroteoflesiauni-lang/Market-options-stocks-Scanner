from __future__ import annotations
from typing import Any
"""Public read-only OKX options market-data client."""



import requests


class OKXOptionsClient:
    """Small wrapper over OKX public options endpoints."""

    BASE_URL = "https://www.okx.com"

    def __init__(self, session: Any | None = None, *, timeout: float = 15.0) -> None:
        self.session = session or requests.Session()
        self.timeout = float(timeout)

    def get_instruments(self, *, inst_family: str = "BTC-USD") -> list[dict[str, Any]]:
        payload = self._get(
            "/api/v5/public/instruments",
            {"instType": "OPTION", "instFamily": inst_family},
        )
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def get_option_summary(self, *, inst_family: str = "BTC-USD") -> list[dict[str, Any]]:
        payload = self._get(
            "/api/v5/public/opt-summary",
            {"instFamily": inst_family},
        )
        data = payload.get("data")
        return data if isinstance(data, list) else []

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
