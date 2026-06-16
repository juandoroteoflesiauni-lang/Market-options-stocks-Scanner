from __future__ import annotations
from typing import Any
"""
backend/routers/massive_router.py
════════════════════════════════════════════════════════════════════════════════
Massive Data Router — Institutional-grade historical chart fetching.
════════════════════════════════════════════════════════════════════════════════
"""


import asyncio

from fastapi import APIRouter, Query

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.intraday_bars_fetcher import fetch_intraday_bars

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/massive", tags=["massive"])


@router.get("/chart/{symbol}")
async def get_massive_chart(
    symbol: str,
    timeframe: str = Query("5m", description="Timeframe: 1s, 1m, 5m, 15m, 30m, 1h, 4h, 1d"),
    limit: int = Query(5000, description="Max bars to return"),
    lookback_days: int = Query(90, description="How many days to look back"),
    date_from: str | None = Query(
        None, description="Start date YYYY-MM-DD (overrides lookback_days)"
    ),
    date_to: str | None = Query(None, description="End date YYYY-MM-DD"),
) -> dict[str, Any]:
    """
    Institutional chart endpoint. Fetches raw OHLCV bars from Massive/Polygon/Alpaca.
    Used by the Technical Advanced Terminal for deep historical analysis.

    NOTE: The frontend sends timeframes like '1D' (uppercase). We normalise to lowercase
    before forwarding so the Polygon interval map resolves correctly.
    Frontend expects candles with 'time' field (Unix ms); backends return 't' (Polygon
    convention), so we remap here to avoid silent empty-chart issues.
    """
    sym = symbol.upper().strip()
    # Normalise timeframe: frontend may send '1D', backend needs '1d'
    tf_norm = timeframe.lower()
    logger.info(
        "massive_chart: request sym=%s tf=%s limit=%d lookback=%d",
        sym,
        tf_norm,
        limit,
        lookback_days,
    )

    # fetch_intraday_bars uses blocking httpx.Client — run in thread to avoid blocking event loop
    result = await asyncio.to_thread(
        fetch_intraday_bars,
        sym,
        tf_norm,
        max_bars=limit,
        lookback_days=lookback_days,
    )

    # Remap each bar: backend uses {'t': unix_ms, 'open', 'high', 'low', 'close', 'volume'}
    # Frontend MassiveChartCandle expects {'time': unix_ms, 'open', 'high', 'low', 'close', 'volume'}
    raw_bars: list[dict[str, Any]] = result.get("bars") or []
    candles: list[dict[str, Any]] = []
    for bar in raw_bars:
        t = bar.get("t") or bar.get("time")
        if t is None:
            continue
        candles.append(
            {
                "time": int(t),
                "open": float(bar.get("open", 0)),
                "high": float(bar.get("high", 0)),
                "low": float(bar.get("low", 0)),
                "close": float(bar.get("close", 0)),
                "volume": float(bar.get("volume", 0)),
            }
        )

    # Basic institutional data quality check: detect large gaps
    gaps_detected = 0
    if len(candles) > 1:
        expected_interval_ms = {
            "1s": 1000,
            "1m": 60_000,
            "5m": 300_000,
            "15m": 900_000,
            "30m": 1_800_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
        }.get(tf_norm, 300_000)
        for i in range(1, len(candles)):
            gap = candles[i]["time"] - candles[i - 1]["time"]
            if gap > expected_interval_ms * 3:
                gaps_detected += 1

    return {
        "ok": len(candles) > 0,
        "symbol": sym,
        "timeframe": tf_norm,
        "candles": candles,
        "count": len(candles),
        "meta": {
            "source": result.get("source", ""),
            "error": result.get("error"),
            "data_quality": {
                "gaps_detected": gaps_detected,
                "has_gaps": gaps_detected > 0,
            },
        },
    }
