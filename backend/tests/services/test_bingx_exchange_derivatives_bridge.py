from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.services.bingx_exchange_derivatives_bridge import (
    BingXExchangeDerivativesResult,
    build_exchange_derivatives_bridge,
)


@dataclass(frozen=True)
class _FakeSnapshot:
    status: str
    symbol_root: str
    providers: tuple[dict[str, object], ...]
    quality_score: float
    source: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "symbol_root": self.symbol_root,
            "providers": list(self.providers),
            "quality_score": self.quality_score,
            "source": self.source,
            "reason": self.reason,
        }


class _FakeClient:
    def __init__(self, snapshot: _FakeSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    async def fetch_snapshot(self, symbol_root: str) -> _FakeSnapshot:
        self.calls.append(symbol_root)
        return self.snapshot


async def test_build_exchange_derivatives_bridge_crypto_collects_sources() -> None:
    client = _FakeClient(
        _FakeSnapshot(
            status="available",
            symbol_root="BTC",
            source="exchange_derivatives_public",
            quality_score=0.83,
            providers=(
                {
                    "provider": "binance",
                    "status": "available",
                    "source": "binance_public_derivatives",
                    "funding_rate": 0.0001,
                    "open_interest": 12345.0,
                    "option_greeks_count": 2,
                    "avg_mark_iv": 0.52,
                },
                {
                    "provider": "deribit",
                    "status": "available",
                    "source": "deribit_public_derivatives",
                    "funding_rate": 0.0002,
                    "option_greeks_count": 3,
                    "net_gamma_proxy": 0.21,
                },
            ),
        )
    )

    result = await build_exchange_derivatives_bridge(
        "BTC-USDT",
        market_type="crypto_standard",
        client=client,
    )

    assert isinstance(result, BingXExchangeDerivativesResult)
    assert client.calls == ["BTC"]
    assert result.status == "available"
    assert result.source == "exchange_derivatives_public"
    assert result.quality_score == pytest.approx(0.83)
    assert result.metrics is not None
    assert result.metrics["provider_count"] == 2
    assert result.metrics["available_provider_count"] == 2
    assert result.metrics["funding_rates"]["binance"] == pytest.approx(0.0001)
    assert result.data_sources == (
        "binance_public_derivatives",
        "deribit_public_derivatives",
    )
    payload = result.to_dict()
    assert payload["metrics"]["avg_mark_iv"] == pytest.approx(0.52)  # type: ignore[index]


async def test_build_exchange_derivatives_bridge_non_crypto_unavailable_without_fetch() -> None:
    client = _FakeClient(
        _FakeSnapshot(
            status="available",
            symbol_root="AAPL",
            source="exchange_derivatives_public",
            quality_score=1.0,
            providers=(),
        )
    )

    result = await build_exchange_derivatives_bridge(
        "AAPL-USDT",
        market_type="stock_perp",
        client=client,
    )

    assert client.calls == []
    assert result.status == "unavailable"
    assert result.source == "none"
    assert result.reason == "exchange_derivatives_only_for_crypto"
    assert result.metrics is None
    assert result.data_sources == ()


async def test_build_exchange_derivatives_bridge_provider_exception_degrades() -> None:
    class FailingClient:
        async def fetch_snapshot(self, symbol_root: str) -> object:
            raise RuntimeError(f"{symbol_root}: timeout")

    result = await build_exchange_derivatives_bridge(
        "ETH-USDT",
        market_type="crypto_standard",
        client=FailingClient(),
    )

    assert result.status == "unavailable"
    assert result.source == "exchange_derivatives_public"
    assert result.reason == "exchange_derivatives_fetch_failed"
    assert result.metrics is None
    assert result.data_sources == ()
