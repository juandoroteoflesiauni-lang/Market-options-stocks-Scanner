"""
backend/layer_1_data/fetchers/hypertracker_fetcher.py
════════════════════════════════════════════════════════════════════════════════
HyperTracker (CoinMarketMan) ASYNC fetcher.
Provides institutional sentiment, perp volume metrics, and leaderboard analytics.
════════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final

try:
    import httpx as _httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _httpx = None  # type: ignore
    _HTTPX_AVAILABLE = False

from backend.config.settings import load_settings

logger = logging.getLogger("backend.layer_1_data.fetchers.hypertracker")

_BASE_URL: Final[str] = "https://ht-api.coinmarketman.com"
_TIMEOUT_DEFAULT: Final[int] = 15
_LIMIT_DEFAULT: Final[int] = 100
_LIMIT_MAX: Final[int] = 500

_LEADERBOARD_LIMIT_ALLOWED: Final[set[int]] = {25, 50, 100}
_ORDER_ALLOWED: Final[set[str]] = {"asc", "desc"}
_PNL_FIELD_ALLOWED: Final[set[str]] = {"pnlDay", "pnlWeek", "pnlMonth", "pnlAllTime"}
_HYPE_ORDER_BY_ALLOWED: Final[set[str]] = {"balance", "percentage", "address", "updatedAt"}
_FILL_SIDE_ALLOWED: Final[set[str]] = {"A", "B"}


class HyperTrackerFetcher:
    """
    Async wrapper over HyperTracker HTTP API.
    Stateless and fail-graceful.
    """

    def __init__(self, timeout: int = _TIMEOUT_DEFAULT) -> None:
        self.settings = load_settings()
        self._timeout = timeout

    @property
    def api_token(self) -> str:
        """Resolves API token from central settings."""
        return self.settings.hypertracker_api_token or ""

    @staticmethod
    def _coerce_limit(limit: int) -> int:
        return max(1, min(_LIMIT_MAX, int(limit)))

    @staticmethod
    def _coerce_order(order: str) -> str:
        value = str(order).strip().lower()
        return value if value in _ORDER_ALLOWED else "desc"

    @staticmethod
    def _coerce_list(values: str | list[str] | None) -> list[str]:
        if values is None:
            return []
        if isinstance(values, list):
            return [str(v).strip() for v in values if str(v).strip()]
        text = str(values).strip()
        return [text] if text else []

    @staticmethod
    def _parse_iso8601_utc(value: str | None) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except Exception:
            return None

    @staticmethod
    def _to_iso8601_utc(value: datetime) -> str:
        value_utc = value.astimezone(UTC)
        return value_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        """Internal async requester using httpx."""
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx not installed — HyperTrackerFetcher disabled.")
            return None

        token = self.api_token
        if not token:
            logger.debug("HyperTrackerFetcher: No API token configured.")
            return None

        url = f"{_BASE_URL}/{path.lstrip('/')}"
        headers = {
            "User-Agent": "QuantumAnalyzer/2.0",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

        try:
            async with _httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                res = await client.get(url, params=params)
                if res.status_code >= 400:
                    logger.debug(
                        "HyperTracker API error on %s: status=%s body=%s",
                        path,
                        res.status_code,
                        res.text[:200],
                    )
                    return None
                return res.json()
        except Exception as exc:
            logger.debug("HyperTracker request failed for %s: %s", path, exc)
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # Public API Implementation
    # ──────────────────────────────────────────────────────────────────────────

    async def get_segments(
        self,
        limit: int = _LIMIT_DEFAULT,
        offset: int = 0,
        order: str = "asc",
        order_by: str | None = None,
    ) -> list[dict[str, Any]] | None:
        params: dict[str, Any] = {
            "limit": self._coerce_limit(limit),
            "offset": max(0, int(offset)),
            "order": self._coerce_order(order),
        }
        if order_by:
            params["orderBy"] = str(order_by).strip()

        data = await self._request("/api/external/segments", params=params)
        return data if isinstance(data, list) else None

    async def get_segment_summary(
        self, segment_id: int, position_age: str | None = None
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {}
        if position_age:
            params["positionAge"] = str(position_age).strip()
        return await self._request(
            f"/api/external/segments/{int(segment_id)}/summary", params=params
        )

    async def get_segment_bias_history(
        self,
        segment_id: int,
        start: str,
        end: str | None = None,
        limit: int = _LIMIT_DEFAULT,
        next_cursor: str | None = None,
    ) -> dict[str, Any] | None:
        if not str(start).strip():
            return None
        params: dict[str, Any] = {
            "start": str(start).strip(),
            "limit": self._coerce_limit(limit),
        }
        if end:
            params["end"] = str(end).strip()
        if next_cursor:
            params["nextCursor"] = str(next_cursor).strip()
        return await self._request(
            f"/api/external/segments/{int(segment_id)}/bias-history", params=params
        )

    async def get_positions(
        self,
        start: str,
        end: str | None = None,
        coin: str | list[str] | None = None,
        address: str | list[str] | None = None,
        open_only: bool | None = None,
        segment_id: int | None = None,
        limit: int = _LIMIT_DEFAULT,
        next_cursor: str | None = None,
        order: str = "desc",
        order_by: str | None = None,
    ) -> dict[str, Any] | None:
        if not str(start).strip():
            return None
        params: dict[str, Any] = {
            "start": str(start).strip(),
            "limit": self._coerce_limit(limit),
            "order": self._coerce_order(order),
        }
        if end:
            params["end"] = str(end).strip()
        if next_cursor:
            params["nextCursor"] = str(next_cursor).strip()
        if order_by:
            params["orderBy"] = str(order_by).strip()

        coins = self._coerce_list(coin)
        if coins:
            params["coin"] = coins
        addresses = self._coerce_list(address)
        if addresses:
            params["address"] = addresses
        if open_only is not None:
            params["open"] = bool(open_only)
        if segment_id is not None:
            params["segmentId"] = int(segment_id)

        return await self._request("/api/external/positions", params=params)

    async def get_positions_coins(self) -> list[dict[str, Any]] | None:
        return await self._request("/api/external/positions/coins")

    async def get_position_metrics_general(
        self,
        start: str,
        end: str | None = None,
        limit: int = _LIMIT_DEFAULT,
        next_cursor: str | None = None,
    ) -> dict[str, Any] | None:
        if not str(start).strip():
            return None
        params: dict[str, Any] = {
            "start": str(start).strip(),
            "limit": self._coerce_limit(limit),
        }
        if end:
            params["end"] = str(end).strip()
        if next_cursor:
            params["nextCursor"] = str(next_cursor).strip()
        return await self._request("/api/external/position-metrics/general", params=params)

    async def get_position_metrics_coin(
        self,
        coin: str,
        start: str,
        end: str | None = None,
        limit: int = _LIMIT_DEFAULT,
        next_cursor: str | None = None,
    ) -> dict[str, Any] | None:
        symbol = str(coin).strip().upper()
        if not symbol or not str(start).strip():
            return None
        params: dict[str, Any] = {
            "start": str(start).strip(),
            "limit": self._coerce_limit(limit),
        }
        if end:
            params["end"] = str(end).strip()
        if next_cursor:
            params["nextCursor"] = str(next_cursor).strip()
        return await self._request(f"/api/external/position-metrics/coin/{symbol}", params=params)

    async def get_perp_volume_metrics(
        self,
        start: str,
        end: str | None = None,
        limit: int = _LIMIT_DEFAULT,
        next_cursor: str | None = None,
    ) -> dict[str, Any] | None:
        if not str(start).strip():
            return None
        params: dict[str, Any] = {
            "start": str(start).strip(),
            "limit": self._coerce_limit(limit),
        }
        if end:
            params["end"] = str(end).strip()
        if next_cursor:
            params["nextCursor"] = str(next_cursor).strip()
        return await self._request("/api/external/metrics/perp-volume", params=params)

    async def get_perp_pnl_leaderboard(
        self,
        limit: int = 25,
        offset: int = 0,
        order: str = "desc",
        order_by: str = "pnlAllTime",
        rank_by: str = "pnlAllTime",
    ) -> dict[str, Any] | None:
        safe_limit = limit if limit in _LEADERBOARD_LIMIT_ALLOWED else 25
        safe_order_by = order_by if order_by in _PNL_FIELD_ALLOWED else "pnlAllTime"
        safe_rank_by = rank_by if rank_by in _PNL_FIELD_ALLOWED else "pnlAllTime"

        params: dict[str, Any] = {
            "limit": safe_limit,
            "offset": max(0, int(offset)),
            "order": self._coerce_order(order),
            "orderBy": safe_order_by,
            "rankBy": safe_rank_by,
        }
        return await self._request("/api/external/leaderboards/perp-pnl", params=params)

    async def get_hype_holders(
        self,
        limit: int = _LIMIT_DEFAULT,
        offset: int = 0,
        order: str = "desc",
        order_by: str | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {
            "limit": self._coerce_limit(limit),
            "offset": max(0, int(offset)),
            "order": self._coerce_order(order),
        }
        if order_by and str(order_by).strip() in _HYPE_ORDER_BY_ALLOWED:
            params["orderBy"] = str(order_by).strip()
        return await self._request("/api/external/hype/holders", params=params)

    async def get_fills(
        self,
        start: str,
        end: str | None = None,
        limit: int = _LIMIT_DEFAULT,
        coin: str | list[str] | None = None,
        address: str | list[str] | None = None,
        builder: str | list[str] | None = None,
        side: str | None = None,
        oid: str | None = None,
        next_cursor: str | None = None,
    ) -> dict[str, Any] | None:
        start_dt = self._parse_iso8601_utc(start)
        if start_dt is None:
            return None

        end_dt = self._parse_iso8601_utc(end) or start_dt.replace(
            hour=23, minute=59, second=59, microsecond=999000
        )

        if start_dt.date() != end_dt.date():
            logger.debug(
                "HyperTracker /fills requires same-day window (start=%s, end=%s)", start, end
            )
            return None

        params: dict[str, Any] = {
            "start": self._to_iso8601_utc(start_dt),
            "end": self._to_iso8601_utc(end_dt),
            "limit": self._coerce_limit(limit),
        }
        if next_cursor:
            params["nextCursor"] = str(next_cursor).strip()
        if oid:
            params["oid"] = str(oid).strip()

        side_text = str(side or "").strip().upper()
        if side_text in _FILL_SIDE_ALLOWED:
            params["side"] = side_text

        coins = self._coerce_list(coin)
        if coins:
            params["coin"] = coins
        addresses = self._coerce_list(address)
        if addresses:
            params["address"] = addresses
        builders = self._coerce_list(builder)
        if builders:
            params["builder"] = builders

        return await self._request("/api/external/fills", params=params)


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : hypertracker_fetcher.py
# Sub-capa         : Fetchers (Institutional Sentiment)
# Enfoque          : Cliente para CoinMarketMan (HT API).
# Cambio Crítico   : Conversión completa de requests -> httpx (Async).
# Integración      : Utiliza backend.config.settings (HYPERTRACKER_API_TOKEN).
# ─────────────────────────────────────────────────────────────────────
