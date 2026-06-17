"""Tests fallback macro FRED + Finnhub cuando FMP MACRO falla. # [PD-6][TH]"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.layer_1_data.fetchers.macro_fallback_fetcher import (
    fetch_economic_calendar_finnhub,
    fetch_economic_indicator_fred,
    fetch_treasury_rates_fred,
)


@pytest.mark.asyncio
async def test_fetch_treasury_rates_fred_merges_series() -> None:
    async def _fake_obs(
        series_id: str,
        *,
        observation_start: str,
        observation_end: str,
        limit: int = 400,
    ) -> list[dict[str, str]]:
        del observation_start, observation_end, limit
        if series_id == "DGS2":
            return [{"date": "2026-06-16", "value": "4.10"}]
        if series_id == "DGS10":
            return [{"date": "2026-06-16", "value": "4.45"}]
        if series_id == "DGS30":
            return [{"date": "2026-06-16", "value": "4.80"}]
        return []

    with patch(
        "backend.layer_1_data.fetchers.macro_fallback_fetcher._fred_observations",
        new=AsyncMock(side_effect=_fake_obs),
    ):
        rows = await fetch_treasury_rates_fred("2026-06-01", "2026-06-17")

    assert len(rows) == 1
    assert rows[0].date == "2026-06-16"
    assert rows[0].year2 == 4.10
    assert rows[0].year10 == 4.45
    assert rows[0].year30 == 4.80


@pytest.mark.asyncio
async def test_fetch_economic_indicator_fred_maps_gdp() -> None:
    with patch(
        "backend.layer_1_data.fetchers.macro_fallback_fetcher._fred_observations",
        new=AsyncMock(
            return_value=[{"date": "2026-01-01", "value": "29000.5"}],
        ),
    ):
        rows = await fetch_economic_indicator_fred("GDP")

    assert len(rows) == 1
    assert rows[0].date == "2026-01-01"
    assert rows[0].value == 29000.5


@pytest.mark.asyncio
async def test_fetch_economic_calendar_finnhub_maps_us_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "economicCalendar": [
                    {
                        "time": "2026-06-17 13:30:00",
                        "country": "US",
                        "event": "CPI YoY",
                        "impact": "high",
                    },
                    {
                        "time": "2026-06-17 08:00:00",
                        "country": "EU",
                        "event": "ECB Rate",
                        "impact": "medium",
                    },
                ]
            }

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, params: dict[str, str]) -> _Resp:
            del url
            assert params["token"] == "test-key"
            return _Resp()

    with patch("httpx.AsyncClient", return_value=_Client()):
        rows = await fetch_economic_calendar_finnhub("2026-06-10", "2026-06-20")

    assert len(rows) == 1
    assert rows[0].event == "CPI YoY"
    assert rows[0].country == "US"
    assert rows[0].impact == "High"
