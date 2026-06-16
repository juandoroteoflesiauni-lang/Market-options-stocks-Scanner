from __future__ import annotations
from typing import Any
"""Public read-only Deribit options market-data client."""



import requests


class DeribitOptionsClient:
    """Wrapper for Deribit public option endpoints."""

    BASE_URL = "https://www.deribit.com/api/v2"

    def __init__(self, session: Any | None = None, *, timeout: float = 15.0) -> None:
        self.session = session or requests.Session()
        self.timeout = float(timeout)

    def get_instruments(self, *, currency: str = "BTC") -> list[dict[str, Any]]:
        payload = self._get(
            "/public/get_instruments",
            {"currency": currency, "kind": "option", "expired": "false"},
        )
        result = payload.get("result")
        return result if isinstance(result, list) else []

    def get_book_summary_by_currency(self, *, currency: str = "BTC") -> list[dict[str, Any]]:
        payload = self._get(
            "/public/get_book_summary_by_currency",
            {"currency": currency, "kind": "option"},
        )
        result = payload.get("result")
        return result if isinstance(result, list) else []

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
