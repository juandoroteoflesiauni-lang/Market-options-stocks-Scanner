import logging
import time
from typing import Any

import httpx

from backend.bus.event_bus import EventBus
from backend.config.settings import MarketDataSettings
from backend.hub.backoff import exponential_backoff
from backend.hub.circuit_breaker import CircuitBreaker
from backend.hub.normalizers.alpaca_normalizer import AlpacaNormalizer
from backend.hub.normalizers.fmp_normalizer import FmpNormalizer
from backend.hub.normalizers.massive_normalizer import MassiveNormalizer
from backend.models.market_snapshot import MarketSnapshot
from backend.models.result import Result

logger = logging.getLogger(__name__)


class MarketDataHub:
    """The Anti-Corruption Layer for all external market data APIs."""

    def __init__(self, settings: MarketDataSettings, event_bus: EventBus) -> None:
        self._settings = settings
        self._bus = event_bus
        self._client = httpx.AsyncClient()

        self._fmp_breaker = CircuitBreaker(provider_name="fmp")
        self._alpaca_breaker = CircuitBreaker(provider_name="alpaca")

        self._fmp_normalizer = FmpNormalizer()
        self._alpaca_normalizer = AlpacaNormalizer()
        self._massive_normalizer = MassiveNormalizer()

        self._validate_connectivity()

    def _validate_connectivity(self) -> None:
        """Verifies that all required secrets are present."""
        logger.info("MarketDataHub initialized. Providers: FMP, Massive, Alpaca.")

    async def close(self) -> None:
        """Closes the HTTP client connection pool dynamically."""
        await self._client.aclose()
        logger.info("MarketDataHub HTTP client closed.")

    @exponential_backoff(max_retries=3)
    async def _fetch_fmp(self, ticker: str) -> dict[str, Any]:
        """Fetches ticker data from Financial Modeling Prep (FMP) API.

        Args:
            ticker: Símbolo del ticker a consultar.

        Returns:
            FMP response dict containing price, volume, and metadata.
        """
        url = f"https://financialmodelingprep.com/api/v3/quote/{ticker.upper()}"
        params = {"apikey": self._settings.fmp_api_key.get_secret_value()}

        response = await self._client.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list) or len(data) == 0:
            raise ValueError(f"Ticker {ticker} not found or invalid format from FMP")

        # FMP returns a list of quotes; extract the first matched quote dict
        quote: dict[str, Any] = data[0]
        return quote

    @exponential_backoff(max_retries=3)
    async def _fetch_alpaca(self, ticker: str) -> dict[str, Any]:
        """Fetches ticker data from Alpaca Stock Data API v2.

        Args:
            ticker: Símbolo del ticker a consultar.

        Returns:
            Normalized flat dict containing symbol, close, volume, and timestamp.
        """
        url = f"https://data.alpaca.markets/v2/stocks/{ticker.upper()}/bars/latest"
        headers = {
            "APCA-API-KEY-ID": self._settings.alpaca_api_key.get_secret_value(),
            "APCA-API-SECRET-KEY": self._settings.alpaca_api_secret.get_secret_value(),
        }

        response = await self._client.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        bar = data.get("bar")
        if not bar:
            raise ValueError(f"No bar data returned from Alpaca for ticker {ticker}")

        # Map Alpaca response to normalized format expected by AlpacaNormalizer
        return {
            "symbol": ticker.upper(),
            "close": bar["c"],
            "volume": bar["v"],
            "timestamp": bar["t"],
        }

    async def get_market_snapshot(self, ticker: str) -> Result[MarketSnapshot]:
        """Fetches and normalizes a snapshot, with failover."""
        start_ns = time.time_ns()

        if self._fmp_breaker.can_execute():
            try:
                raw_data = await self._fetch_fmp(ticker)
                self._fmp_breaker.record_success()

                snapshot = self._fmp_normalizer.normalize(raw_data, start_ns)
                return Result.success(snapshot)
            except Exception as exc:
                self._fmp_breaker.record_failure()
                logger.warning("FMP fetch failed for %s: %s", ticker, exc)

        if self._alpaca_breaker.can_execute():
            try:
                raw_data = await self._fetch_alpaca(ticker)
                self._alpaca_breaker.record_success()

                snapshot = self._alpaca_normalizer.normalize(raw_data, start_ns)
                return Result.success(snapshot)
            except Exception as exc:
                self._alpaca_breaker.record_failure()
                logger.warning("Alpaca fetch failed for %s: %s", ticker, exc)

        return Result.failure(reason="All providers exhausted or circuits open")

    async def ingest_ticker(self, ticker: str) -> Result[MarketSnapshot]:
        """Fetches a ticker's snapshot and publishes it to the event bus.

        Args:
            ticker: The market ticker symbol to ingest.

        Returns:
            The Result wrapper containing either the snapshot or failure reason.
        """
        result = await self.get_market_snapshot(ticker)

        if result.is_success:
            snapshot = result.unwrap()
            await self._bus.publish(snapshot)

        return result
