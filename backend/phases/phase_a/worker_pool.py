import asyncio
import logging

from backend.hub.market_data_hub import MarketDataHub
from backend.models.market_snapshot import MarketSnapshot
from backend.phases.phase_a.divergence_checker import DivergenceChecker

logger = logging.getLogger(__name__)


class ApiKeyPool:
    """Manages API key rotation across concurrent workers.

    Args:
        api_keys: List of valid API keys from environment.
    """

    def __init__(self, api_keys: list[str]) -> None:
        self._keys: list[str] = api_keys
        self._index: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire_key(self) -> str:
        async with self._lock:
            if not self._keys:
                raise ValueError("No API keys available in the pool")
            key = self._keys[self._index % len(self._keys)]
            self._index += 1
            return key


async def scan_ticker_batch(
    ticker_batch: list[str],
    hub: MarketDataHub,
    key_pool: ApiKeyPool,
) -> list[MarketSnapshot]:
    """Scans a batch of tickers and returns validated snapshots.

    Pipeline por ticker:
      1. Fetch MarketSnapshot via hub (FMP quote + daily_change_pct)
      2. Run divergence check (15m vs 1D) — 1 call extra a FMP intraday
      3. Si hay contradicción, descartar inmediatamente

    Args:
        ticker_batch: Subset of tickers assigned to this worker.
        hub: The MarketDataHub instance for data fetching.
        key_pool: Shared API key pool for rate-limit management.

    Returns:
        List of valid MarketSnapshot objects. Invalid tickers are discarded.
    """
    _ = await key_pool.acquire_key()

    results: list[MarketSnapshot] = []

    for ticker in ticker_batch:
        result = await hub.get_market_snapshot(ticker=ticker)
        if not result.is_success:
            logger.warning(
                "Phase A: Discarding invalid ticker [PD-1]",
                extra={"ticker": ticker, "reason": result.reason},
            )
            continue

        snapshot = result.unwrap()

        # ── Divergence check (15m vs 1D) ─────────────────────────────────
        veto = await DivergenceChecker.check(hub, snapshot)
        if veto.vetoed:
            logger.info(
                "Phase A: %s VETOED [%s] — %s",
                snapshot.ticker,
                veto.veto_type,
                veto.reason,
            )
            continue

        results.append(snapshot)

    return results
