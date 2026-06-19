"""Resilient data facade over existing FMP/Massive clients. # [PD-3][TH]"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from backend.domain.agentic_models import MacroDataSnapshot, OptionsContractContext
from backend.domain.fmp_models import FMPEconomicCalendarItem, FMPEconomicIndicator, FMPTreasuryRate
from backend.hub.api_consumption_monitor import ApiCallStatus, api_consumption_monitor
from backend.hub.circuit_breaker import CircuitBreaker
from backend.hub.rate_limiter import rate_limiter
from backend.models.option_contract import OptionChainSnapshot, OptionContract
from backend.models.result import Result
from backend.services.ai_core.llm_context_cache import get_llm_context_cache

logger = logging.getLogger(__name__)

_INFLATION_INDICATOR_NAMES: tuple[str, ...] = ("CPI", "PCE", "inflationRate")


class _FMPMacroClient(Protocol):
    async def get_economic_calendar(
        self, date_from: str, date_to: str
    ) -> list[FMPEconomicCalendarItem]: ...

    async def get_treasury_rates(self, from_date: str, to_date: str) -> list[FMPTreasuryRate]: ...

    async def get_economic_indicator(self, name: str) -> list[FMPEconomicIndicator]: ...


class _OptionsHub(Protocol):
    async def get_options_chain(self, ticker: str) -> Result[OptionChainSnapshot]: ...


def contract_context_from_option(contract: OptionContract) -> OptionsContractContext:
    """Build a compact agent-facing view from a full OptionContract."""
    return OptionsContractContext(
        contract_symbol=contract.contract_symbol,
        underlying_ticker=contract.underlying_ticker,
        option_type=contract.option_type,
        strike=str(contract.strike),
        implied_volatility=contract.implied_volatility,
        delta=contract.delta,
        gamma=contract.gamma,
        open_interest=contract.open_interest,
        volume=contract.volume,
        composite_score=contract.composite_score,
    )


def _serialize_calendar(items: list[FMPEconomicCalendarItem]) -> tuple[dict[str, str | None], ...]:
    return tuple(
        {"date": item.date, "event": item.event, "country": item.country, "impact": item.impact}
        for item in items
    )


def _serialize_treasury(items: list[FMPTreasuryRate]) -> tuple[dict[str, str | float | None], ...]:
    return tuple(item.model_dump(mode="json") for item in items)


def _serialize_indicators(
    items: list[FMPEconomicIndicator],
) -> tuple[dict[str, str | float | None], ...]:
    return tuple(item.model_dump(mode="json") for item in items)


class AgenticDataFacade:
    """Async facade wrapping FMP macro mixins and MarketDataHub options."""

    def __init__(
        self,
        fmp_client: _FMPMacroClient,
        *,
        options_hub: _OptionsHub | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._fmp = fmp_client
        self._options_hub = options_hub
        self._breaker = circuit_breaker or CircuitBreaker(provider_name="fmp")

    async def fetch_macro_snapshot(
        self,
        *,
        horizon_days: int = 7,
        symbol: str = "MACRO",
        use_cache: bool = True,
    ) -> Result[MacroDataSnapshot]:
        """Fetch macro calendar, treasury yields and inflation indicators."""
        if not self._breaker.can_execute():
            return Result.failure(reason="FMP circuit open")

        if use_cache:
            cache = get_llm_context_cache()

            async def _fetch() -> MacroDataSnapshot:
                result = await self._fetch_macro_snapshot_uncached(horizon_days=horizon_days)
                if result.is_failure:
                    raise RuntimeError(result.reason)
                return result.unwrap()

            try:
                raw, hit = await cache.get_or_fetch(
                    feature="macro_snapshot",
                    symbol=symbol,
                    source="fmp",
                    fetcher=_fetch,
                    serialize=lambda s: s.model_dump(mode="json"),
                    estimated_cost_usd=Decimal("0.01"),
                )
                if hit:
                    from backend.domain.agentic_models import CachedContextEntry

                    if isinstance(raw, CachedContextEntry):
                        snapshot = MacroDataSnapshot.model_validate(raw.payload)
                        return Result.success(snapshot)
                return Result.success(raw)  # type: ignore[arg-type]
            except Exception as exc:
                logger.warning("agentic_data_facade.cache_fallback error=%s", exc)

        return await self._fetch_macro_snapshot_uncached(horizon_days=horizon_days)

    async def _fetch_macro_snapshot_uncached(
        self,
        *,
        horizon_days: int = 7,
    ) -> Result[MacroDataSnapshot]:
        """Uncached macro fetch (FMP via circuit breaker)."""
        if not self._breaker.can_execute():
            return Result.failure(reason="FMP circuit open")

        today = datetime.now(tz=UTC).date()
        date_from = today.isoformat()
        date_to = (today + timedelta(days=horizon_days)).isoformat()
        start = time.perf_counter()

        try:
            await rate_limiter.acquire("fmp")
            calendar = await self._fmp.get_economic_calendar(date_from, date_to)
            treasury = await self._fmp.get_treasury_rates(date_from, date_to)

            inflation_rows: list[FMPEconomicIndicator] = []
            for name in _INFLATION_INDICATOR_NAMES:
                rows = await self._fmp.get_economic_indicator(name)
                inflation_rows.extend(rows[:3])

            duration = time.perf_counter() - start
            await api_consumption_monitor.record(
                provider="fmp",
                endpoint="/agentic/macro_snapshot",
                api_key_label="macro",
                status=ApiCallStatus.SUCCESS,
                duration_seconds=duration,
                bytes_received=0,
            )
            self._breaker.record_success()

            snapshot = MacroDataSnapshot(
                calendar_events=_serialize_calendar(calendar),
                treasury_yields=_serialize_treasury(treasury),
                inflation_indicators=_serialize_indicators(inflation_rows),
                fetched_at=datetime.now(tz=UTC),
            )
            return Result.success(snapshot)
        except Exception as exc:
            self._breaker.record_failure()
            duration = time.perf_counter() - start
            await api_consumption_monitor.record(
                provider="fmp",
                endpoint="/agentic/macro_snapshot",
                api_key_label="macro",
                status=ApiCallStatus.ERROR,
                duration_seconds=duration,
                bytes_received=0,
                error_message=str(exc),
            )
            logger.warning("agentic_data_facade.macro_failed error=%s", exc)
            return Result.failure(reason=str(exc))

    async def get_options_chain(self, ticker: str) -> Result[OptionChainSnapshot]:
        """Delegate options chain fetch to MarketDataHub when configured."""
        if self._options_hub is None:
            return Result.failure(reason="Options hub not configured")
        return await self._options_hub.get_options_chain(ticker)

    def top_contracts(
        self,
        chain: OptionChainSnapshot,
        *,
        limit: int = 5,
    ) -> list[OptionsContractContext]:
        """Return top-N contracts by composite score for agent evaluation."""
        ranked = sorted(chain.contracts, key=lambda c: c.composite_score, reverse=True)
        return [contract_context_from_option(c) for c in ranked[:limit]]


__all__ = [
    "AgenticDataFacade",
    "contract_context_from_option",
]
