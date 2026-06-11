"""Bridge public crypto derivatives data into the BingX Bot analysis contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.exchange_derivatives_client import (
    AGGREGATED_SOURCE,
    ExchangeDerivativesPublicClient,
)
from backend.services.bingx_candidate_context import SourceStatus
from backend.services.bingx_symbol_linker import underlying_from_bingx_symbol

logger = get_logger(__name__)

REASON_ONLY_FOR_CRYPTO = "exchange_derivatives_only_for_crypto"
REASON_FETCH_FAILED = "exchange_derivatives_fetch_failed"


class ExchangeDerivativesClient(Protocol):
    async def fetch_snapshot(self, symbol_root: str) -> Any:
        """Return an object with ``status``, ``providers`` and ``to_dict``."""


@dataclass(frozen=True)
class BingXExchangeDerivativesResult:
    """JSON-safe result consumed by BingX Bot and candidate analysis."""

    status: SourceStatus
    source: str
    market_type: str
    underlying_symbol: str
    metrics: dict[str, Any] | None = None
    providers: tuple[dict[str, Any], ...] = ()
    data_sources: tuple[str, ...] = ()
    quality_score: float | None = None
    reason: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def build_exchange_derivatives_bridge(
    venue_symbol: str,
    *,
    market_type: str,
    client: ExchangeDerivativesClient | None = None,
) -> BingXExchangeDerivativesResult:
    """Fetch and normalize public crypto derivatives data for a BingX symbol.

    The bridge is intentionally crypto-only. Equity/index perps already use
    the existing institutional options bridge, so this block avoids mixing
    crypto exchange options/funding with equity option-chain semantics.
    """
    underlying = underlying_from_bingx_symbol(venue_symbol).upper()
    if market_type != "crypto_standard":
        return BingXExchangeDerivativesResult(
            status="unavailable",
            source="none",
            market_type=market_type,
            underlying_symbol=underlying,
            reason=REASON_ONLY_FOR_CRYPTO,
        )

    derivatives_client = client or ExchangeDerivativesPublicClient()
    try:
        snapshot = await derivatives_client.fetch_snapshot(underlying)
    except Exception as exc:
        logger.warning(
            "bingx_exchange_derivatives.fetch_failed symbol=%s underlying=%s error=%s",
            venue_symbol,
            underlying,
            exc,
        )
        return BingXExchangeDerivativesResult(
            status="unavailable",
            source=AGGREGATED_SOURCE,
            market_type=market_type,
            underlying_symbol=underlying,
            reason=REASON_FETCH_FAILED,
        )

    raw_payload = _snapshot_to_dict(snapshot)
    providers = tuple(_provider_to_dict(provider) for provider in _snapshot_providers(snapshot))
    available_providers = tuple(
        provider for provider in providers if provider.get("status") == "available"
    )
    data_sources = tuple(
        str(provider.get("source")) for provider in available_providers if provider.get("source")
    )
    status = _snapshot_status(snapshot)
    quality_score = _safe_float(raw_payload.get("quality_score"))

    if status != "available":
        return BingXExchangeDerivativesResult(
            status="unavailable",
            source=str(raw_payload.get("source") or AGGREGATED_SOURCE),
            market_type=market_type,
            underlying_symbol=underlying,
            providers=providers,
            data_sources=data_sources,
            quality_score=quality_score,
            reason=str(raw_payload.get("reason") or "exchange_derivatives_unavailable"),
            raw=raw_payload,
        )

    return BingXExchangeDerivativesResult(
        status="available",
        source=str(raw_payload.get("source") or AGGREGATED_SOURCE),
        market_type=market_type,
        underlying_symbol=underlying,
        metrics=_build_metrics(providers),
        providers=providers,
        data_sources=data_sources,
        quality_score=quality_score,
        raw=raw_payload,
    )


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    if hasattr(snapshot, "to_dict"):
        payload = snapshot.to_dict()
        return payload if isinstance(payload, dict) else {}
    return snapshot if isinstance(snapshot, dict) else {}


def _snapshot_status(snapshot: Any) -> SourceStatus:
    status = getattr(snapshot, "status", None)
    if isinstance(snapshot, dict):
        status = snapshot.get("status")
    return "available" if status == "available" else "unavailable"


def _snapshot_providers(snapshot: Any) -> tuple[Any, ...]:
    providers = getattr(snapshot, "providers", None)
    if isinstance(snapshot, dict):
        providers = snapshot.get("providers")
    if isinstance(providers, tuple):
        return providers
    if isinstance(providers, list):
        return tuple(providers)
    return ()


def _provider_to_dict(provider: Any) -> dict[str, Any]:
    if hasattr(provider, "to_dict"):
        payload = provider.to_dict()
        return payload if isinstance(payload, dict) else {}
    if isinstance(provider, dict):
        return dict(provider)
    if hasattr(provider, "__dict__"):
        return dict(provider.__dict__)
    return {}


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed


def _build_metrics(providers: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    available = tuple(provider for provider in providers if provider.get("status") == "available")
    funding_rates = _provider_float_map(available, "funding_rate")
    open_interest = _provider_float_map(available, "open_interest")
    mark_prices = _provider_float_map(available, "mark_price")
    index_prices = _provider_float_map(available, "index_price")
    avg_iv = _average(provider.get("avg_mark_iv") for provider in available)
    net_gamma = _sum_values(provider.get("net_gamma_proxy") for provider in available)
    return {
        "provider_count": len(providers),
        "available_provider_count": len(available),
        "funding_rates": funding_rates,
        "open_interest": open_interest,
        "mark_prices": mark_prices,
        "index_prices": index_prices,
        "option_greeks_count": sum(
            int(provider.get("option_greeks_count") or 0) for provider in available
        ),
        "avg_mark_iv": avg_iv,
        "net_gamma_proxy": net_gamma,
    }


def _provider_float_map(
    providers: tuple[dict[str, Any], ...],
    field: str,
) -> dict[str, float]:
    values: dict[str, float] = {}
    for provider in providers:
        name = provider.get("provider")
        value = _safe_float(provider.get(field))
        if isinstance(name, str) and value is not None:
            values[name] = value
    return values


def _average(values: Any) -> float | None:
    parsed = [value for raw in values if (value := _safe_float(raw)) is not None]
    return round(sum(parsed) / len(parsed), 8) if parsed else None


def _sum_values(values: Any) -> float | None:
    parsed = [value for raw in values if (value := _safe_float(raw)) is not None]
    return round(sum(parsed), 12) if parsed else None


__all__ = [
    "REASON_FETCH_FAILED",
    "REASON_ONLY_FOR_CRYPTO",
    "BingXExchangeDerivativesResult",
    "ExchangeDerivativesClient",
    "build_exchange_derivatives_bridge",
]
