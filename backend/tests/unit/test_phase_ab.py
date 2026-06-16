from __future__ import annotations
"""Tests unitarios para Phase A (ApiKeyPool) y Phase B (MicrostructureEngine).

Cubre:
- ApiKeyPool: rotación de claves
- MicrostructureEngine: enrich_single con/sin OHLCV, enrich_batch
- Legacy QuantitativeEngine wrapper
"""


from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.engine.quantitative_engine import QuantitativeEngine
from backend.models.enriched_snapshot import EnrichedSnapshot
from backend.models.market_snapshot import DataLineage, MarketSnapshot, OHLCVBar
from backend.phases.phase_a.worker_pool import ApiKeyPool
from backend.phases.phase_b.microstructure_engine import MicrostructureEngine


class DummyEventBus:
    async def publish(self, event: object) -> None:
        pass


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=5, raw_field_count=10)


def _make_snapshot(
    ticker: str = "AAPL",
    price: str = "150.00",
    volume: int = 1_000_000,
    ohlcv: tuple[OHLCVBar, ...] | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker,
        exchange="NASDAQ",
        price=Decimal(price),
        volume=volume,
        exchange_timestamp=datetime.now(UTC),
        data_lineage=_make_lineage(),
        ohlcv=ohlcv or (),
    )


def _make_bars(n: int = 30, uptrend: bool = True) -> tuple[OHLCVBar, ...]:
    """Generate N OHLCV bars with a clear directional bias."""
    base = 150.0
    bars: list[OHLCVBar] = []
    for i in range(n):
        if uptrend:
            c = base + i * 0.5 + (i % 3) * 0.1
        else:
            c = base - i * 0.5 - (i % 3) * 0.1
        bars.append(
            OHLCVBar(
                time=f"2025-01-01T09:{i:02d}:00Z",
                open=c - 0.2,
                high=c + 0.5,
                low=c - 0.5,
                close=c,
                volume=float(100_000 + i * 1_000),
            )
        )
    return tuple(bars)


# ── Phase A: ApiKeyPool ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_key_pool():
    pool = ApiKeyPool(["key1", "key2"])
    assert await pool.acquire_key() == "key1"
    assert await pool.acquire_key() == "key2"
    assert await pool.acquire_key() == "key1"


@pytest.mark.asyncio
async def test_api_key_pool_empty():
    pool = ApiKeyPool([])
    with pytest.raises(ValueError, match="No API keys available"):
        await pool.acquire_key()


# ── Phase B: MicrostructureEngine (sin OHLCV) ────────────────────────────────


@pytest.mark.asyncio
async def test_microstructure_engine_empty_batch():
    engine = MicrostructureEngine(max_workers=1)
    result = await engine.enrich_batch([])
    assert result == []
    engine.shutdown()


@pytest.mark.asyncio
async def test_microstructure_engine_no_ohlcv():
    """Snapshot without OHLCV passes through with zeroed fields."""
    engine = MicrostructureEngine(max_workers=1)
    snapshot = _make_snapshot()
    enriched = await engine.enrich_single(snapshot)

    assert enriched.ticker == "AAPL"
    assert enriched.price == Decimal("150.00")
    assert enriched.ofi_score == 0.0
    assert enriched.smc_direction is None
    assert enriched.smc_weight == 0.0
    engine.shutdown()


# ── Phase B: MicrostructureEngine (con OHLCV - uptrend) ──────────────────────


@pytest.mark.asyncio
async def test_microstructure_engine_with_ohlcv():
    """Snapshot with uptrend OHLCV should produce non-zero metrics."""
    engine = MicrostructureEngine(max_workers=1)
    bars = _make_bars(n=30, uptrend=True)
    snapshot = _make_snapshot(ohlcv=bars)
    enriched = await engine.enrich_single(snapshot)

    assert enriched.ticker == "AAPL"
    assert isinstance(enriched.ofi_score, float)
    assert enriched.smc_direction in ("BULLISH", "BEARISH", None)
    assert 0.0 <= enriched.smc_weight <= 1.0
    engine.shutdown()


# ── Phase B: enrich_batch ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_microstructure_engine_batch():
    engine = MicrostructureEngine(max_workers=2)
    snapshots = [
        _make_snapshot(ticker="AAPL", ohlcv=_make_bars(n=20, uptrend=True)),
        _make_snapshot(ticker="MSFT", ohlcv=_make_bars(n=20, uptrend=False)),
        _make_snapshot(ticker="GOOGL"),
    ]
    results = await engine.enrich_batch(snapshots)

    assert len(results) == 3
    tickers = [r.ticker for r in results]
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert "GOOGL" in tickers

    for r in results:
        assert isinstance(r, EnrichedSnapshot)
        assert isinstance(r.ofi_score, float)
    engine.shutdown()


# ── Legacy: QuantitativeEngine wrapper ───────────────────────────────────────


@pytest.mark.asyncio
async def test_quantitative_engine():
    bus = DummyEventBus()
    engine = QuantitativeEngine(event_bus=bus, max_workers=1)
    bars = _make_bars(n=25, uptrend=True)
    snapshot = _make_snapshot(ohlcv=bars)

    result = await engine.process_snapshot(snapshot)
    assert result.is_success
    enriched = result.unwrap()
    assert enriched.ticker == "AAPL"
    assert enriched.price == Decimal("150.00")
    assert isinstance(enriched.ofi_score, float)
    assert enriched.smc_direction in ("BULLISH", "BEARISH", None)

    engine.shutdown()
