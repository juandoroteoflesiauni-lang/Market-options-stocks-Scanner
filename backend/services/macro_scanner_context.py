from __future__ import annotations
from typing import Any
"""Macro context for Market Scanner Phase B (Layer 4 → Layer 1 only)."""


import asyncio
from datetime import date, timedelta

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


def _high_impact(ev: object) -> bool:
    raw = getattr(ev, "impact", None)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"high", "3"}


async def fetch_macro_scanner_context() -> dict[str, Any]:
    """Best-effort macro snapshot: FRED rates + FMP economic calendar (14d).

    Missing keys or provider failures degrade gracefully; desk summary stays deterministic.
    """
    sources: dict[str, bool] = {"fred": False, "fmp_calendar": False}
    limitations: list[str] = []
    fred_flat: dict[str, float | None] = {}
    vix: float | None = None
    calendar: dict[str, Any] = {
        "events_14d": 0,
        "high_impact_14d": 0,
        "sample_events": [],
    }

    async def _fred() -> None:
        nonlocal fred_flat, vix, sources
        try:
            from backend.layer_1_data.fetchers.fred_fetcher import FredFetcher

            ff = FredFetcher()
            snap, vix_obj = await asyncio.gather(
                ff.get_macro_snapshot(),
                ff.get_vix_close(),
            )
            fred_flat = dict(snap) if isinstance(snap, dict) else {}
            if isinstance(vix_obj, dict):
                raw_v = vix_obj.get("value")
                if isinstance(raw_v, int | float):
                    vix = float(raw_v)
            if any(v is not None for v in fred_flat.values()) or vix is not None:
                sources["fred"] = True
        except Exception as exc:
            limitations.append(f"fred_context_failed:{str(exc)[:80]}")

    async def _fmp_cal() -> None:
        nonlocal calendar, sources
        try:
            from backend.layer_1_data.fetchers.fmp_client import FMPClient

            today = date.today()
            d0 = today.isoformat()
            d1 = (today + timedelta(days=14)).isoformat()
            client = FMPClient()
            events = await client.get_economic_calendar(d0, d1)
            if not events:
                limitations.append("fmp_calendar_empty")
                return
            sources["fmp_calendar"] = True
            hi = sum(1 for e in events if _high_impact(e))
            calendar["events_14d"] = len(events)
            calendar["high_impact_14d"] = hi
            sample: list[str] = []
            for ev in events[:6]:
                evs = str(getattr(ev, "event", "") or getattr(ev, "country", "") or "event")
                imp = str(getattr(ev, "impact", "") or "")
                dt = str(getattr(ev, "date", "") or "")
                sample.append(f"{dt} {evs} ({imp})".strip())
            calendar["sample_events"] = sample
        except Exception as exc:
            limitations.append(f"fmp_calendar_failed:{str(exc)[:80]}")

    await asyncio.gather(_fred(), _fmp_cal())
    return {
        "sources": sources,
        "fred": fred_flat,
        "vix": vix,
        "calendar": calendar,
        "limitations": limitations,
        "as_of": date.today().isoformat(),
    }
