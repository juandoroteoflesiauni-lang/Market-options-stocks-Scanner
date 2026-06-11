"""Thin wrapper para endpoints REST de Finnhub."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger("backend.layer_1_data.datos.finnhub")


class FinnhubFetcher:
    """Thin typed wrapper over finnhub-python client."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = self._resolve_api_key(api_key)
        self._client: object | None = None

    @staticmethod
    def _resolve_api_key(api_key: str | None) -> str:
        key = (api_key or "").strip()
        if key:
            return key

        try:
            from config.settings import load_settings
        except ModuleNotFoundError:  # pragma: no cover - compatibilidad de import por paquete.
            from backend.config.settings import load_settings

        try:
            settings = load_settings()
            config_key = getattr(settings, "finnhub_api_key", None)
            if isinstance(config_key, str) and config_key.strip():
                return config_key.strip()
        except SystemExit:
            pass

        return os.getenv("FINNHUB_API_KEY", "").strip()

    def _get_client(self) -> object | None:
        if self._client is not None:
            return self._client

        if not self._api_key:
            logger.debug("FINNHUB_API_KEY is not configured")
            return None

        try:
            import finnhub

            self._client = finnhub.Client(api_key=self._api_key)
            return self._client
        except Exception as exc:
            logger.warning("finnhub-python unavailable/incompatible: %s", exc)
            return None

    @staticmethod
    def _default_news_range(days_back: int = 30) -> tuple[str, str]:
        end = date.today()
        start = end - timedelta(days=max(1, days_back))
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    @staticmethod
    def _default_earnings_range(days_forward: int = 30) -> tuple[str, str]:
        start = date.today()
        end = start + timedelta(days=max(1, days_forward))
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    def get_quote(self, symbol: str) -> dict[str, object] | None:
        client = self._get_client()
        if client is None:
            return None

        try:
            payload = client.quote(symbol.upper())
            if not isinstance(payload, dict) or not payload:
                return None
            return payload
        except Exception as exc:
            logger.debug("Finnhub quote failed for %s: %s", symbol, exc)
            return None

    def get_company_profile2(self, symbol: str) -> dict[str, object] | None:
        client = self._get_client()
        if client is None:
            return None

        try:
            payload = client.company_profile2(symbol=symbol.upper())
            if not isinstance(payload, dict) or not payload:
                return None
            return payload
        except Exception as exc:
            logger.debug("Finnhub profile2 failed for %s: %s", symbol, exc)
            return None

    def get_basic_financials(self, symbol: str, metric: str = "all") -> dict[str, object] | None:
        client = self._get_client()
        if client is None:
            return None

        try:
            payload = client.company_basic_financials(symbol.upper(), metric)
            if not isinstance(payload, dict) or not payload:
                return None
            return payload
        except Exception as exc:
            logger.debug("Finnhub basic financials failed for %s: %s", symbol, exc)
            return None

    def get_recommendation_trends(self, symbol: str) -> list[dict[str, object]] | None:
        client = self._get_client()
        if client is None:
            return None

        try:
            payload = client.recommendation_trends(symbol.upper())
            if not isinstance(payload, list) or not payload:
                return None

            rows: list[dict[str, object]] = []
            for item in payload:
                if isinstance(item, dict):
                    rows.append(item)
            return rows if rows else None
        except Exception as exc:
            logger.debug("Finnhub recommendation trends failed for %s: %s", symbol, exc)
            return None

    def get_company_news(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, object]] | None:
        client = self._get_client()
        if client is None:
            return None

        news_start, news_end = start, end
        if not news_start or not news_end:
            news_start, news_end = self._default_news_range(days_back=30)

        try:
            payload = client.company_news(symbol.upper(), _from=news_start, to=news_end)
            if not isinstance(payload, list) or not payload:
                return None

            rows: list[dict[str, object]] = []
            for item in payload:
                if isinstance(item, dict):
                    rows.append(item)
            return rows if rows else None
        except Exception as exc:
            logger.debug("Finnhub company news failed for %s: %s", symbol, exc)
            return None

    def get_earnings_calendar(
        self,
        start: str | None = None,
        end: str | None = None,
        symbol: str = "",
        international: bool = False,
    ) -> list[dict[str, object]] | None:
        client = self._get_client()
        if client is None:
            return None

        cal_start, cal_end = start, end
        if not cal_start or not cal_end:
            cal_start, cal_end = self._default_earnings_range(days_forward=30)

        try:
            payload = client.earnings_calendar(
                _from=cal_start,
                to=cal_end,
                symbol=symbol.upper() if symbol else "",
                international=international,
            )
            if not isinstance(payload, dict):
                return None

            calendar = payload.get("earningsCalendar")
            if not isinstance(calendar, list) or not calendar:
                return None

            rows: list[dict[str, object]] = []
            for item in calendar:
                if isinstance(item, dict):
                    rows.append(item)
            return rows if rows else None
        except Exception as exc:
            logger.debug("Finnhub earnings calendar failed: %s", exc)
            return None

    def get_insider_transactions(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, object]] | None:
        client = self._get_client()
        if client is None:
            return None

        try:
            if start and end:
                payload = client.stock_insider_transactions(symbol.upper(), start, end)
            else:
                payload = client.stock_insider_transactions(symbol.upper())

            if not isinstance(payload, dict):
                return None
            rows_raw = payload.get("data")
            if not isinstance(rows_raw, list) or not rows_raw:
                return None

            rows: list[dict[str, object]] = []
            for item in rows_raw:
                if isinstance(item, dict):
                    rows.append(item)
            return rows if rows else None
        except Exception as exc:
            logger.debug("Finnhub insider transactions failed for %s: %s", symbol, exc)
            return None

    def get_stock_symbols(self, exchange: str = "US") -> list[dict[str, object]] | None:
        client = self._get_client()
        if client is None:
            return None

        try:
            payload = client.stock_symbols(exchange.upper())
            if not isinstance(payload, list) or not payload:
                return None

            rows: list[dict[str, object]] = []
            for item in payload:
                if isinstance(item, dict):
                    rows.append(item)
            return rows if rows else None
        except Exception as exc:
            logger.debug("Finnhub stock symbols failed for %s: %s", exchange, exc)
            return None

    def get_symbol_lookup(self, query: str) -> list[dict[str, object]] | None:
        """Search symbols via Finnhub `/search` endpoint."""
        client = self._get_client()
        if client is None:
            return None

        search_query = str(query).strip()
        if not search_query:
            return None

        try:
            payload = client.symbol_lookup(search_query)
            if not isinstance(payload, dict):
                return None

            rows_raw = payload.get("result")
            if not isinstance(rows_raw, list) or not rows_raw:
                return None

            rows: list[dict[str, object]] = []
            for item in rows_raw:
                if isinstance(item, dict):
                    rows.append(item)
            return rows if rows else None
        except Exception as exc:
            logger.debug("Finnhub symbol lookup failed for %s: %s", query, exc)
            return None

    def get_stock_candles(
        self,
        symbol: str,
        resolution: str = "1",
        start_unix: int | None = None,
        end_unix: int | None = None,
    ) -> dict[str, Any] | None:
        """Fetch intraday/eod candles via Finnhub `/stock/candle` endpoint."""
        client = self._get_client()
        if client is None:
            return None

        clean_symbol = str(symbol).strip().upper()
        if not clean_symbol:
            return None

        try:
            import time

            to_ts = int(end_unix) if end_unix is not None else int(time.time())
            if to_ts <= 0:
                return None

            from_ts = int(start_unix) if start_unix is not None else to_ts - (30 * 24 * 60 * 60)
            if from_ts >= to_ts:
                from_ts = max(0, to_ts - (24 * 60 * 60))

            payload = client.stock_candles(clean_symbol, resolution, from_ts, to_ts)
            if not isinstance(payload, dict) or not payload:
                return None

            status = payload.get("s")
            closes = payload.get("c")
            if status != "ok" and not isinstance(closes, list):
                return None

            return payload
        except Exception as exc:
            logger.debug("Finnhub stock candles failed for %s: %s", symbol, exc)
            return None

    def get_option_chain(
        self,
        symbol: str,
        date: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch options chain via Finnhub `/stock/option-chain` endpoint."""
        client = self._get_client()
        if client is None:
            return None

        clean_symbol = str(symbol).strip().upper()
        if not clean_symbol:
            return None

        try:
            params: dict[str, object] = {"symbol": clean_symbol}
            if date is not None and str(date).strip():
                params["date"] = str(date).strip()

            payload = client.option_chain(**params)
            if not isinstance(payload, dict) or not payload:
                return None
            return payload
        except Exception as exc:
            logger.debug("Finnhub option chain failed for %s: %s", symbol, exc)
            return None


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: finnhub_fetcher.py
# Eliminado: imports/config de sistema anterior y encabezado de procedencia previa
# Preservado: firmas públicas, defaults de rango, parseo de payload y retornos fail-graceful
# Pendientes: ninguno
# ─────────────────────────────────────────────────
