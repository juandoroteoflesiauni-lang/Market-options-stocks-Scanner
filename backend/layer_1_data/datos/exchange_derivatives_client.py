from __future__ import annotations
from typing import Literal, Any
"""Public derivatives market-data client for crypto cross-venue enrichment.

The client is read-only and uses unauthenticated public endpoints from
Binance, Deribit, and OKX. It intentionally returns normalized dictionaries
instead of exchange-native payloads so upper layers can consume provenance,
funding, open interest, and options volatility/greeks without coupling to a
single venue schema.
"""


import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import httpx

from backend.config.logger_setup import get_logger

SourceStatus = Literal["available", "unavailable"]

logger = get_logger(__name__)

BINANCE_SOURCE = "binance_public_derivatives"
DERIBIT_SOURCE = "deribit_public_derivatives"
OKX_SOURCE = "okx_public_derivatives"
AGGREGATED_SOURCE = "exchange_derivatives_public"


@dataclass(frozen=True)
class ExchangeDerivativesProviderSnapshot:
    """Normalized public derivatives snapshot from one exchange."""

    provider: str
    status: SourceStatus
    source: str
    funding_rate: float | None = None
    next_funding_time_ms: int | None = None
    open_interest: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    option_greeks_count: int = 0
    avg_mark_iv: float | None = None
    net_gamma_proxy: float | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExchangeDerivativesSnapshot:
    """Aggregated cross-venue derivatives snapshot for a crypto root."""

    status: SourceStatus
    symbol_root: str
    source: str
    providers: tuple[ExchangeDerivativesProviderSnapshot, ...]
    quality_score: float
    reason: str | None = None
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["providers"] = [provider.to_dict() for provider in self.providers]
        return payload


class ExchangeDerivativesPublicClient:
    """Read-only client for Binance, Deribit, and OKX public derivatives data."""

    def __init__(self, *, timeout_s: float = 5.0) -> None:
        self.timeout_s = timeout_s

    async def fetch_snapshot(self, symbol_root: str) -> ExchangeDerivativesSnapshot:
        root = symbol_root.strip().upper()
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            results = await self._fetch_all(client, root)

        providers = tuple(results)
        available = tuple(provider for provider in providers if provider.status == "available")
        quality = _quality_score(available, total=len(providers))
        status: SourceStatus = "available" if available else "unavailable"
        reason = None if available else "all_exchange_derivatives_unavailable"
        return ExchangeDerivativesSnapshot(
            status=status,
            symbol_root=root,
            source=AGGREGATED_SOURCE,
            providers=providers,
            quality_score=quality,
            reason=reason,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def _fetch_all(
        self, client: httpx.AsyncClient, root: str
    ) -> list[ExchangeDerivativesProviderSnapshot]:
        results = await _gather_provider_snapshots(
            self._fetch_binance(client, root),
            self._fetch_deribit(client, root),
            self._fetch_okx(client, root),
        )
        return list(results)

    async def _fetch_binance(
        self, client: httpx.AsyncClient, root: str
    ) -> ExchangeDerivativesProviderSnapshot:
        symbol = f"{root}USDT"
        try:
            premium = await _get_json(
                client,
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": symbol},
            )
            interest = await _get_json(
                client,
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": symbol},
            )
            options = await _get_json(
                client,
                "https://eapi.binance.com/eapi/v1/mark",
            )
        except Exception as exc:
            logger.warning("exchange_derivatives.binance_failed root=%s error=%s", root, exc)
            return _provider_unavailable("binance", BINANCE_SOURCE, "binance_fetch_failed")

        option_summary = _summarize_options(
            _as_list(options),
            symbol_prefix=f"{root}-",
            iv_key="markIV",
            gamma_key="gamma",
        )
        return ExchangeDerivativesProviderSnapshot(
            provider="binance",
            status="available",
            source=BINANCE_SOURCE,
            funding_rate=_safe_float(_as_dict(premium).get("lastFundingRate")),
            next_funding_time_ms=_safe_int(_as_dict(premium).get("nextFundingTime")),
            open_interest=_safe_float(_as_dict(interest).get("openInterest")),
            mark_price=_safe_float(_as_dict(premium).get("markPrice")),
            index_price=_safe_float(_as_dict(premium).get("indexPrice")),
            option_greeks_count=option_summary["count"],
            avg_mark_iv=option_summary["avg_mark_iv"],
            net_gamma_proxy=option_summary["net_gamma_proxy"],
        )

    async def _fetch_deribit(
        self, client: httpx.AsyncClient, root: str
    ) -> ExchangeDerivativesProviderSnapshot:
        try:
            ticker = await _get_json(
                client,
                "https://www.deribit.com/api/v2/public/ticker",
                params={"instrument_name": f"{root}-PERPETUAL"},
            )
            options = await _get_json(
                client,
                "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                params={"currency": root, "kind": "option"},
            )
        except Exception as exc:
            logger.warning("exchange_derivatives.deribit_failed root=%s error=%s", root, exc)
            return _provider_unavailable("deribit", DERIBIT_SOURCE, "deribit_fetch_failed")

        ticker_result = _as_dict(_as_dict(ticker).get("result"))
        option_summary = _summarize_options(
            _as_list(_as_dict(options).get("result")),
            symbol_prefix=f"{root}-",
            iv_key="mark_iv",
            gamma_key="gamma",
        )
        return ExchangeDerivativesProviderSnapshot(
            provider="deribit",
            status="available",
            source=DERIBIT_SOURCE,
            funding_rate=_safe_float(ticker_result.get("funding_8h")),
            next_funding_time_ms=None,
            open_interest=_safe_float(ticker_result.get("open_interest")),
            mark_price=_safe_float(ticker_result.get("mark_price")),
            index_price=_safe_float(ticker_result.get("index_price")),
            option_greeks_count=option_summary["count"],
            avg_mark_iv=option_summary["avg_mark_iv"],
            net_gamma_proxy=option_summary["net_gamma_proxy"],
        )

    async def _fetch_okx(
        self, client: httpx.AsyncClient, root: str
    ) -> ExchangeDerivativesProviderSnapshot:
        uly_usdt = f"{root}-USDT"
        swap_id = f"{root}-USDT-SWAP"
        try:
            funding = await _get_json(
                client,
                "https://www.okx.com/api/v5/public/funding-rate",
                params={"instId": swap_id},
            )
            interest = await _get_json(
                client,
                "https://www.okx.com/api/v5/public/open-interest",
                params={"instType": "SWAP", "uly": uly_usdt},
            )
            mark = await _get_json(
                client,
                "https://www.okx.com/api/v5/public/mark-price",
                params={"instType": "SWAP", "instId": swap_id},
            )
            options = await _get_json(
                client,
                "https://www.okx.com/api/v5/public/opt-summary",
                params={"uly": f"{root}-USD"},
            )
        except Exception as exc:
            logger.warning("exchange_derivatives.okx_failed root=%s error=%s", root, exc)
            return _provider_unavailable("okx", OKX_SOURCE, "okx_fetch_failed")

        funding_row = _first_data_row(funding)
        interest_row = _first_data_row(interest)
        mark_row = _first_data_row(mark)
        option_summary = _summarize_options(
            _as_list(_as_dict(options).get("data")),
            symbol_prefix=f"{root}-",
            iv_key="markVol",
            gamma_key="gamma",
        )
        return ExchangeDerivativesProviderSnapshot(
            provider="okx",
            status="available",
            source=OKX_SOURCE,
            funding_rate=_safe_float(funding_row.get("fundingRate")),
            next_funding_time_ms=_safe_int(funding_row.get("nextFundingTime")),
            open_interest=_safe_float(interest_row.get("oi")),
            mark_price=_safe_float(mark_row.get("markPx")),
            index_price=_safe_float(mark_row.get("idxPx")),
            option_greeks_count=option_summary["count"],
            avg_mark_iv=option_summary["avg_mark_iv"],
            net_gamma_proxy=option_summary["net_gamma_proxy"],
        )


async def _gather_provider_snapshots(
    *coroutines: Any,
) -> tuple[ExchangeDerivativesProviderSnapshot, ...]:
    import asyncio

    raw = await asyncio.gather(*coroutines, return_exceptions=True)
    snapshots: list[ExchangeDerivativesProviderSnapshot] = []
    providers = (
        ("binance", BINANCE_SOURCE),
        ("deribit", DERIBIT_SOURCE),
        ("okx", OKX_SOURCE),
    )
    for idx, item in enumerate(raw):
        if isinstance(item, ExchangeDerivativesProviderSnapshot):
            snapshots.append(item)
        else:
            provider, source = providers[idx]
            logger.warning(
                "exchange_derivatives.provider_exception provider=%s error=%s",
                provider,
                item,
            )
            snapshots.append(_provider_unavailable(provider, source, f"{provider}_fetch_failed"))
    return tuple(snapshots)


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> Any:
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def _provider_unavailable(
    provider: str,
    source: str,
    reason: str,
) -> ExchangeDerivativesProviderSnapshot:
    return ExchangeDerivativesProviderSnapshot(
        provider=provider,
        status="unavailable",
        source=source,
        reason=reason,
    )


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_data_row(payload: object) -> dict[str, Any]:
    rows = _as_list(_as_dict(payload).get("data"))
    return _as_dict(rows[0]) if rows else {}


def _summarize_options(
    rows: list[Any],
    *,
    symbol_prefix: str,
    iv_key: str,
    gamma_key: str,
) -> dict[str, Any]:
    iv_values: list[float] = []
    gammas: list[float] = []
    count = 0
    prefix = symbol_prefix.upper()
    for raw in rows:
        row = _as_dict(raw)
        symbol = str(row.get("symbol") or row.get("instrument_name") or row.get("instId") or "")
        if symbol and not symbol.upper().startswith(prefix):
            continue
        count += 1
        iv = _safe_float(row.get(iv_key))
        if iv is not None and iv >= 0:
            iv_values.append(iv)
        gamma = _safe_float(row.get(gamma_key))
        if gamma is not None:
            gammas.append(gamma)

    avg_iv = sum(iv_values) / len(iv_values) if iv_values else None
    net_gamma = sum(gammas) if gammas else None
    return {
        "count": count,
        "avg_mark_iv": round(avg_iv, 8) if avg_iv is not None else None,
        "net_gamma_proxy": round(net_gamma, 12) if net_gamma is not None else None,
    }


def _quality_score(
    available: tuple[ExchangeDerivativesProviderSnapshot, ...],
    *,
    total: int,
) -> float:
    if total <= 0 or not available:
        return 0.0
    provider_component = len(available) / total
    funding_component = (
        sum(1 for provider in available if provider.funding_rate is not None) / total
    )
    options_component = sum(1 for provider in available if provider.option_greeks_count > 0) / total
    return round(
        (provider_component * 0.5) + (funding_component * 0.25) + (options_component * 0.25), 4
    )


__all__ = [
    "AGGREGATED_SOURCE",
    "BINANCE_SOURCE",
    "DERIBIT_SOURCE",
    "OKX_SOURCE",
    "ExchangeDerivativesProviderSnapshot",
    "ExchangeDerivativesPublicClient",
    "ExchangeDerivativesSnapshot",
]
