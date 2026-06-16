from __future__ import annotations
"""Tiingo market data provider for Layer 1 ingestion.

Tiingo's IEX feed is useful for OHLCV, intraday bars and top-of-book quotes.
It is intentionally not marked as an L2 provider: the public API exposes best
bid/ask fields, not multi-level order-book ladders.
"""


import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Self

import httpx

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_DEFAULT_BASE_URL: Final[str] = "https://api.tiingo.com"
_DEFAULT_TIMEOUT: Final[float] = 20.0
_SUPPORTED_INTERVALS: Final[dict[str, str]] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1hour",
    "4h": "4hour",
    "1d": "daily",
}


@dataclass(frozen=True)
class TiingoCapabilities:
    """Explicit feature flags so upper layers never mistake Tiingo for L2."""

    supports_ohlcv: bool = True
    supports_intraday: bool = True
    supports_realtime_l1: bool = True
    supports_news: bool = True
    supports_fundamentals: bool = True
    supports_crypto_top_of_book: bool = True
    supports_l2: bool = False
    depth_levels: int = 1
    l2_reason: str = "Tiingo IEX exposes top-of-book bid/ask, not L2 depth."

    def to_dict(self: Self) -> dict[str, bool | int | str]:
        """JSON-safe capabilities payload."""
        return asdict(self)


@dataclass(frozen=True)
class TiingoTopOfBookQuote:
    """Normalized IEX quote/top-of-book response."""

    ticker: str
    timestamp: str | None
    last: float | None
    last_size: float | None
    bid_price: float | None
    bid_size: float | None
    ask_price: float | None
    ask_size: float | None
    mid: float | None
    volume: float | None
    source: str = "tiingo_iex_top_of_book"
    supports_l2: bool = False

    def to_dict(self: Self) -> dict[str, object]:
        """JSON-safe quote payload."""
        return asdict(self)


def _finite_positive(value: object) -> float | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _finite_non_negative(value: object) -> float | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _parse_iso_ms(value: object) -> int:
    if not isinstance(value, str) or not value:
        return 0
    try:
        normalized = value.replace("Z", "+00:00")
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        return 0


def _parse_tiingo_bar(row: dict[str, object]) -> dict[str, float | int] | None:
    open_price = _finite_positive(row.get("open"))
    high = _finite_positive(row.get("high"))
    low = _finite_positive(row.get("low"))
    close = _finite_positive(row.get("close"))
    if open_price is None or high is None or low is None or close is None:
        return None
    timestamp = _parse_iso_ms(row.get("date") or row.get("timestamp"))
    if timestamp <= 0:
        return None
    volume = _finite_non_negative(row.get("volume")) or 0.0
    return {
        "t": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class TiingoMarketDataProvider:
    """Small REST client for Tiingo IEX, fundamentals and news-capable data."""

    capabilities = TiingoCapabilities()

    def __init__(
        self: Self,
        api_key: str | None,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def is_configured(self: Self) -> bool:
        """Whether the provider has an API key available."""
        return bool(self.api_key)

    def capabilities_payload(self: Self) -> dict[str, bool | int | str]:
        """Return provider capability flags."""
        return self.capabilities.to_dict()

    def get_iex_top_of_book(self: Self, symbol: str) -> TiingoTopOfBookQuote | None:
        """Fetch latest IEX top-of-book/last-sale fields for one ticker."""
        if not self.is_configured:
            logger.debug("tiingo: API key missing; top-of-book disabled")
            return None

        ticker = symbol.upper().strip()
        data = self._get_json("/iex", {"tickers": ticker})
        if isinstance(data, list):
            row = data[0] if data and isinstance(data[0], dict) else None
        else:
            row = data if isinstance(data, dict) else None
        if not row:
            return None

        return TiingoTopOfBookQuote(
            ticker=str(row.get("ticker") or ticker),
            timestamp=row.get("timestamp") if isinstance(row.get("timestamp"), str) else None,
            last=_finite_positive(row.get("last")) or _finite_positive(row.get("tngoLast")),
            last_size=_finite_non_negative(row.get("lastSize")),
            bid_price=_finite_positive(row.get("bidPrice")),
            bid_size=_finite_non_negative(row.get("bidSize")),
            ask_price=_finite_positive(row.get("askPrice")),
            ask_size=_finite_non_negative(row.get("askSize")),
            mid=_finite_positive(row.get("mid")),
            volume=_finite_non_negative(row.get("volume")),
        )

    def get_intraday_bars(
        self: Self,
        symbol: str,
        interval: str,
        *,
        max_bars: int,
        lookback_days: int,
    ) -> list[dict[str, float | int]] | None:
        """Fetch Tiingo OHLCV bars normalized to the local intraday contract."""
        if not self.is_configured:
            logger.debug("tiingo: API key missing; intraday bars disabled")
            return None

        resample_freq = _SUPPORTED_INTERVALS.get(interval)
        if not resample_freq:
            return None

        end_dt = datetime.now(tz=UTC)
        start_dt = end_dt - timedelta(days=max(1, lookback_days))
        data = self._get_json(
            f"/iex/{symbol.upper().strip()}/prices",
            {
                "resampleFreq": resample_freq,
                "startDate": start_dt.date().isoformat(),
                "endDate": end_dt.date().isoformat(),
                "format": "json",
            },
        )
        if not isinstance(data, list):
            return None

        bars = [
            parsed
            for row in data
            if isinstance(row, dict) and (parsed := _parse_tiingo_bar(row)) is not None
        ]
        if not bars:
            return None
        bars.sort(key=lambda item: int(item["t"]))
        bar_limit = max(1, min(int(max_bars), 100_000))
        return bars[-bar_limit:] if len(bars) > bar_limit else bars

    def _get_json(self: Self, path: str, params: dict[str, object]) -> object:
        merged: dict[str, object] = {"token": self.api_key}
        merged.update(params)
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, params=merged)
            if response.status_code != 200:
                logger.debug("tiingo: HTTP %s for %s", response.status_code, path)
                return None
            return response.json()
        except Exception as exc:
            logger.debug("tiingo: exception for %s: %s", path, exc)
            return None


__all__ = [
    "TiingoCapabilities",
    "TiingoMarketDataProvider",
    "TiingoTopOfBookQuote",
]
