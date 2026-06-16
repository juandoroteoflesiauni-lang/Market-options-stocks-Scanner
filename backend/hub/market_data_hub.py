from typing import Any
import logging
import time

import httpx

from backend.bus.event_bus import EventBus
from backend.config.settings import MarketDataSettings
from backend.hub.api_consumption_monitor import ApiCallStatus, api_consumption_monitor
from backend.hub.backoff import exponential_backoff
from backend.hub.circuit_breaker import CircuitBreaker
from backend.hub.normalizers.alpaca_normalizer import AlpacaNormalizer
from backend.hub.normalizers.fmp_normalizer import FmpNormalizer
from backend.hub.normalizers.massive_normalizer import MassiveNormalizer
from backend.hub.normalizers.massive_options_normalizer import MassiveOptionsNormalizer
from backend.hub.rate_limiter import rate_limiter
from backend.models.market_snapshot import MarketSnapshot
from backend.models.option_contract import OptionChainSnapshot
from backend.models.result import Result
from backend.services.alpaca_universe_fetcher import get_universe_type_for_ticker

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
        self._massive_options_normalizer = MassiveOptionsNormalizer()

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
        start = time.perf_counter()

        await rate_limiter.acquire("fmp")

        response = await self._client.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        duration = time.perf_counter() - start
        content_length = int(response.headers.get("content-length", 0))
        status = ApiCallStatus.SUCCESS

        if not isinstance(data, list) or len(data) == 0:
            status = ApiCallStatus.ERROR
            await api_consumption_monitor.record(
                provider="fmp",
                endpoint="/api/v3/quote/{symbol}",
                api_key_label="primary",
                status=status,
                duration_seconds=duration,
                bytes_received=content_length,
                error_message="Empty response",
            )
            raise ValueError(f"Ticker {ticker} not found or invalid format from FMP")

        await api_consumption_monitor.record(
            provider="fmp",
            endpoint="/api/v3/quote/{symbol}",
            api_key_label="primary",
            status=status,
            duration_seconds=duration,
            bytes_received=content_length,
        )

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
        start = time.perf_counter()

        await rate_limiter.acquire("alpaca")

        response = await self._client.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        duration = time.perf_counter() - start
        content_length = int(response.headers.get("content-length", 0))

        bar = data.get("bar")
        if not bar:
            await api_consumption_monitor.record(
                provider="alpaca",
                endpoint="/v2/stocks/{symbol}/bars/latest",
                api_key_label="primary",
                status=ApiCallStatus.ERROR,
                duration_seconds=duration,
                bytes_received=content_length,
                error_message="No bar data",
            )
            raise ValueError(f"No bar data returned from Alpaca for ticker {ticker}")

        await api_consumption_monitor.record(
            provider="alpaca",
            endpoint="/v2/stocks/{symbol}/bars/latest",
            api_key_label="primary",
            status=ApiCallStatus.SUCCESS,
            duration_seconds=duration,
            bytes_received=content_length,
        )

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
                snapshot = snapshot.model_copy(update={"universe_type": get_universe_type_for_ticker(ticker)})
                return Result.success(snapshot)
            except Exception as exc:
                self._fmp_breaker.record_failure()
                await api_consumption_monitor.record(
                    provider="fmp",
                    endpoint="/api/v3/quote/{symbol}",
                    api_key_label="primary",
                    status=ApiCallStatus.ERROR,
                    duration_seconds=0.0,
                    error_message=str(exc)[:200],
                )
                logger.warning("FMP fetch failed for %s: %s", ticker, exc)

        if self._alpaca_breaker.can_execute():
            try:
                raw_data = await self._fetch_alpaca(ticker)
                self._alpaca_breaker.record_success()

                snapshot = self._alpaca_normalizer.normalize(raw_data, start_ns)
                snapshot = snapshot.model_copy(update={"universe_type": get_universe_type_for_ticker(ticker)})
                return Result.success(snapshot)
            except Exception as exc:
                self._alpaca_breaker.record_failure()
                await api_consumption_monitor.record(
                    provider="alpaca",
                    endpoint="/v2/stocks/{symbol}/bars/latest",
                    api_key_label="primary",
                    status=ApiCallStatus.ERROR,
                    duration_seconds=0.0,
                    error_message=str(exc)[:200],
                )
                logger.warning("Alpaca fetch failed for %s: %s", ticker, exc)

        return Result.failure(reason="All providers exhausted or circuits open")

    async def get_vix_level(self) -> Result[float]:
        """Fetches the current VIX level via FMP.

        Uses the existing _fetch_fmp pipeline (circuit-breaker, rate-limiter,
        exponential backoff) with ticker "^VIX".

        Returns:
            Result.success(vix_price) or Result.failure(reason).
        """
        try:
            raw = await self._fetch_fmp("^VIX")
        except Exception as exc:
            return Result.failure(reason=f"VIX fetch failed: {exc}")

        vix = raw.get("price")
        if vix is None or not isinstance(vix, int | float) or vix <= 0:
            return Result.failure(reason=f"Invalid VIX price from FMP: {vix}")

        self._fmp_breaker.record_success()
        return Result.success(float(vix))

    async def get_raw_quote(self, ticker: str) -> Result[dict[str, Any]]:
        """Fetches the raw quote dictionary (useful for previousClose etc)."""
        if self._fmp_breaker.can_execute():
            try:
                raw_data = await self._fetch_fmp(ticker)
                return Result.success(raw_data)
            except Exception as exc:
                logger.warning("FMP raw quote failed for %s: %s", ticker, exc)

        return Result.failure(reason="FMP raw quote failed")

    async def get_after_market_quotes(self, tickers: list[str]) -> Result[dict[str, Any]]:
        """Fetches batch after-market quotes for the given tickers from FMP."""
        if not tickers:
            return Result.success({})

        if self._fmp_breaker.can_execute():
            try:
                start = time.perf_counter()
                tickers_str = ",".join([t.upper() for t in tickers])
                url = f"https://financialmodelingprep.com/api/v4/batch-pre-post-market-trade/{tickers_str}"
                params = {"apikey": self._settings.fmp_api_key.get_secret_value()}

                await rate_limiter.acquire("fmp")
                response = await self._client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                duration = time.perf_counter() - start
                content_length = int(response.headers.get("content-length", 0))

                await api_consumption_monitor.record(
                    provider="fmp",
                    endpoint="/api/v4/batch-pre-post-market-trade/{tickers}",
                    api_key_label="primary",
                    status=ApiCallStatus.SUCCESS,
                    duration_seconds=duration,
                    bytes_received=content_length,
                )

                result = {}
                if isinstance(data, list):
                    for item in data:
                        sym = item.get("symbol")
                        if sym:
                            result[sym] = item
                return Result.success(result)
            except Exception as exc:
                logger.warning("FMP after-market quote failed: %s", exc)

        return Result.failure(reason="FMP after-market quote failed")

    async def get_intraday_candles(
        self, ticker: str, limit: int = 60
    ) -> Result[list[dict[str, Any]]]:
        """Fetches historical 1-minute candles for the given ticker from FMP.

        Returns the last `limit` candles in ascending chronological order.
        """
        if self._fmp_breaker.can_execute():
            try:
                url = f"https://financialmodelingprep.com/api/v3/historical-chart/1min/{ticker.upper()}"
                params = {"apikey": self._settings.fmp_api_key.get_secret_value()}

                response = await self._client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()

                if isinstance(data, list):
                    # FMP returns descending order (newest first).
                    # We want ascending order (oldest first) for charting.
                    # Slice the first `limit` items, then reverse.
                    sliced_data = data[:limit]
                    sliced_data.reverse()

                    # Convert FMP format to OHLCV format expected by lightweight-charts
                    from datetime import datetime
                    from zoneinfo import ZoneInfo

                    ny_tz = ZoneInfo("America/New_York")
                    candles = []
                    for bar in sliced_data:
                        try:
                            # FMP format: '2026-06-05 09:30:00' (EST/EDT)
                            dt = datetime.strptime(bar["date"], "%Y-%m-%d %H:%M:%S")
                            # Convert to Unix timestamp based on New York time
                            dt = dt.replace(tzinfo=ny_tz)
                            timestamp = int(dt.timestamp())

                            candles.append(
                                {
                                    "time": timestamp,
                                    "open": float(bar["open"]),
                                    "high": float(bar["high"]),
                                    "low": float(bar["low"]),
                                    "close": float(bar["close"]),
                                    "volume": int(bar["volume"]),
                                }
                            )
                        except (ValueError, KeyError):
                            continue

                    return Result.success(candles)
                return Result.success([])
            except Exception as exc:
                logger.warning("FMP intraday candles failed for %s: %s", ticker, exc)

        return Result.failure(reason="FMP intraday candles failed")

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

    @exponential_backoff(max_retries=3)
    async def _fetch_massive_options(self, ticker: str) -> dict[str, Any]:
        """Fetches options chain data from Massive API.

        Args:
            ticker: Símbolo del underlying a consultar.

        Returns:
            Massive response dict containing options chain data.
        """
        url = f"https://api.massive.com/v1/options/chain/{ticker.upper()}"
        headers = {
            "Authorization": f"Bearer {self._settings.massive_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        start = time.perf_counter()

        await rate_limiter.acquire("massive")
        response = await self._client.get(url, headers=headers, timeout=15.0)
        response.raise_for_status()
        data = response.json()

        duration = time.perf_counter() - start
        content_length = int(response.headers.get("content-length", 0))

        if not isinstance(data, dict):
            await api_consumption_monitor.record(
                provider="massive",
                endpoint="/v1/options/chain/{symbol}",
                api_key_label="primary",
                status=ApiCallStatus.ERROR,
                duration_seconds=duration,
                bytes_received=content_length,
                error_message="Invalid format",
            )
            raise ValueError(f"Invalid options response format from Massive for {ticker}")

        await api_consumption_monitor.record(
            provider="massive",
            endpoint="/v1/options/chain/{symbol}",
            api_key_label="primary",
            status=ApiCallStatus.SUCCESS,
            duration_seconds=duration,
            bytes_received=content_length,
        )

        return data

    async def get_options_chain(self, ticker: str) -> Result[OptionChainSnapshot]:
        """Fetches and normalizes an options chain from Massive.

        Args:
            ticker: Símbolo del underlying.

        Returns:
            Result con OptionChainSnapshot o razón de fallo.
        """
        start_ns = time.time_ns()

        try:
            raw_data = await self._fetch_massive_options(ticker)

            spot_price = float(raw_data.get("spot_price", 0))
            raw_contracts = raw_data.get("contracts", [])

            if not raw_contracts:
                return Result.failure(reason=f"No options contracts returned for {ticker}")

            chain = self._massive_options_normalizer.normalize_chain(
                ticker=ticker,
                spot_price=spot_price,
                raw_contracts=raw_contracts,
                ingestion_start_ns=start_ns,
            )

            if not chain.has_data:
                return Result.failure(reason=f"No valid options parsed for {ticker}")

            logger.info(
                "Massive options: %s — %d contracts, PC ratio: %.2f",
                ticker,
                len(chain.contracts),
                chain.put_call_ratio_volume,
            )

            return Result.success(chain)

        except Exception as exc:
            logger.warning("Massive options fetch failed for %s: %s", ticker, exc)
            return Result.failure(reason=f"Massive options failed: {exc}")
