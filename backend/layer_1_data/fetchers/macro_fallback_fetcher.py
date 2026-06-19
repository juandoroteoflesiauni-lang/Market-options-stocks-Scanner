"""Fallback macro data cuando FMP MACRO devuelve 403 (FRED + Finnhub). # [PD-3][TH]"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import httpx

from backend.domain.fmp_models import FMPEconomicCalendarItem, FMPEconomicIndicator, FMPTreasuryRate
from backend.layer_1_data.fetchers.fred_fetcher import FredFetcher

logger = logging.getLogger(__name__)

FINNHUB_ECONOMIC_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/economic"
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

_FRED_INDICATOR_SERIES: dict[str, str] = {
    "GDP": "GDP",
    "CPI": "CPIAUCSL",
    "unemploymentRate": "UNRATE",
    "PCE": "PCEPI",
    "inflationRate": "CPIAUCSL",
}

_FRED_TREASURY_FIELDS: tuple[tuple[str, str], ...] = (
    ("year2", "DGS2"),
    ("year10", "DGS10"),
    ("year30", "DGS30"),
)


def macro_fallback_enabled() -> bool:
    """True si se debe intentar FRED/Finnhub cuando FMP MACRO falla."""
    return os.getenv("MACRO_FMP_FALLBACK_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _finnhub_api_key() -> str | None:
    raw = os.getenv("FINNHUB_API_KEY", "").strip()
    if raw:
        return raw
    try:
        from backend.config.settings import load_settings

        key = getattr(load_settings(), "finnhub_api_key", None)
        if isinstance(key, str) and key.strip():
            return key.strip()
    except Exception:
        pass
    return None


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _in_date_range(obs_date: str, start: date, end: date) -> bool:
    parsed = _parse_iso_date(obs_date)
    return parsed is not None and start <= parsed <= end


async def _fred_observations(
    series_id: str,
    *,
    observation_start: str,
    observation_end: str,
    limit: int = 400,
) -> list[dict[str, Any]]:
    fetcher = FredFetcher()
    api_key = await fetcher._get_api_key()
    if not api_key:
        return []

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
        "observation_start": observation_start,
        "observation_end": observation_end,
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(_FRED_BASE, params=params)
            if response.status_code != 200:
                logger.debug(
                    "FRED fallback failed series=%s status=%s",
                    series_id,
                    response.status_code,
                )
                return []
            payload = response.json()
    except Exception as exc:
        logger.debug("FRED fallback error series=%s exc=%s", series_id, exc)
        return []

    observations = payload.get("observations", []) if isinstance(payload, dict) else []
    if not isinstance(observations, list):
        return []
    return [row for row in observations if isinstance(row, dict)]


def _obs_value(row: dict[str, Any]) -> float | None:
    raw = row.get("value", ".")
    if raw in {".", None, ""}:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def fetch_treasury_rates_fred(from_date: str, to_date: str) -> list[FMPTreasuryRate]:
    """Serie diaria DGS2/DGS10/DGS30 fusionada al shape FMP treasury."""
    start = _parse_iso_date(from_date)
    end = _parse_iso_date(to_date)
    if start is None or end is None:
        return []

    merged: dict[str, dict[str, float | None]] = {}
    for field, series_id in _FRED_TREASURY_FIELDS:
        rows = await _fred_observations(
            series_id,
            observation_start=from_date,
            observation_end=to_date,
        )
        for row in rows:
            obs_date = str(row.get("date") or "")
            if not _in_date_range(obs_date, start, end):
                continue
            value = _obs_value(row)
            if value is None:
                continue
            bucket = merged.setdefault(obs_date, {})
            bucket[field] = value

    if not merged:
        return []

    items: list[FMPTreasuryRate] = []
    for obs_date in sorted(merged.keys(), reverse=True):
        fields = merged[obs_date]
        items.append(
            FMPTreasuryRate(
                date=obs_date,
                year2=fields.get("year2"),
                year10=fields.get("year10"),
                year30=fields.get("year30"),
            )
        )
    logger.info(
        "macro_fallback.treasury_fred rows=%s from=%s to=%s",
        len(items),
        from_date,
        to_date,
    )
    return items


async def fetch_economic_indicator_fred(name: str) -> list[FMPEconomicIndicator]:
    """Indicadores macro desde FRED (GDP, CPI, desempleo)."""
    series_id = _FRED_INDICATOR_SERIES.get(name)
    if not series_id:
        return []

    end = date.today()
    start = date(end.year - 2, end.month, min(end.day, 28))
    rows = await _fred_observations(
        series_id,
        observation_start=start.isoformat(),
        observation_end=end.isoformat(),
        limit=60,
    )
    items: list[FMPEconomicIndicator] = []
    for row in rows:
        obs_date = str(row.get("date") or "")
        value = _obs_value(row)
        if not obs_date or value is None:
            continue
        items.append(FMPEconomicIndicator(date=obs_date, value=value))

    if items:
        logger.info(
            "macro_fallback.indicator_fred name=%s series=%s rows=%s",
            name,
            series_id,
            len(items),
        )
    return items


def _map_finnhub_impact(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in {"3", "high"}:
        return "High"
    if text in {"2", "medium"}:
        return "Medium"
    if text in {"1", "low"}:
        return "Low"
    return str(raw)


async def fetch_economic_calendar_finnhub(
    date_from: str,
    date_to: str,
) -> list[FMPEconomicCalendarItem]:
    """Calendario macro US desde Finnhub ``/calendar/economic``."""
    api_key = _finnhub_api_key()
    if not api_key:
        logger.debug("macro_fallback.finnhub_calendar skipped: no FINNHUB_API_KEY")
        return []

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(
                FINNHUB_ECONOMIC_CALENDAR_URL,
                params={"from": date_from, "to": date_to, "token": api_key},
            )
            if response.status_code != 200:
                logger.debug(
                    "macro_fallback.finnhub_calendar failed status=%s",
                    response.status_code,
                )
                return []
            payload = response.json()
    except Exception as exc:
        logger.debug("macro_fallback.finnhub_calendar error=%s", exc)
        return []

    calendar = payload.get("economicCalendar") if isinstance(payload, dict) else None
    if not isinstance(calendar, list):
        return []

    items: list[FMPEconomicCalendarItem] = []
    for row in calendar:
        if not isinstance(row, dict):
            continue
        event_time = str(row.get("time") or row.get("date") or "")
        event_date = event_time[:10] if event_time else None
        country = str(row.get("country") or "").strip() or None
        if country and country.upper() not in {"US", "USA", "UNITED STATES"}:
            continue
        event_name = str(row.get("event") or "").strip() or None
        if not event_date or not event_name:
            continue
        items.append(
            FMPEconomicCalendarItem(
                date=event_date,
                event=event_name,
                country=country or "US",
                impact=_map_finnhub_impact(row.get("impact")),
            )
        )

    if items:
        logger.info(
            "macro_fallback.calendar_finnhub rows=%s from=%s to=%s",
            len(items),
            date_from,
            date_to,
        )
    return items


__all__ = [
    "fetch_economic_calendar_finnhub",
    "fetch_economic_indicator_fred",
    "fetch_treasury_rates_fred",
    "macro_fallback_enabled",
]
