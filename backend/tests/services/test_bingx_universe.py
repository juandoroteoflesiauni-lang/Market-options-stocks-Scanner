from __future__ import annotations
from typing import Any

from dataclasses import dataclass

import pytest

from backend.services.bingx_universe import (
    _BINGX_TOP_CRYPTO_LIMIT_MAX,
    _BINGX_TOP_CRYPTO_LIMIT_MIN,
    _DEFAULT_TOP_CRYPTO_ROOTS,
    BingXUniverseService,
    LiquidityFilter,
    classify_instrument,
)


class FakeClient:
    def __init__(self) -> None:
        self.oi_symbols: list[str] = []

    async def fetch_perp_symbol_map(self) -> dict[str, str]:
        return {
            "BTC-USDT": "BTC-USDT",
            "DOGE-USDT": "DOGE-USDT",
            "AAPL-USDT": "NCSKAAPL2USD-USDT",
            "MSFT-USDT": "NCSKMSFT2USD-USDT",
        }

    async def fetch_perp_contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "displayName": "BTC-USDT",
                "symbol": "BTC-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "50",
            },
            {
                "displayName": "DOGE-USDT",
                "symbol": "DOGE-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "20",
            },
            {
                "displayName": "AAPL-USDT",
                "symbol": "NCSKAAPL2USD-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "5",
            },
            {
                "displayName": "MSFT-USDT",
                "symbol": "NCSKMSFT2USD-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "5",
            },
        ]

    async def fetch_all_tickers_perp(self) -> list[dict[str, Any]]:
        return [
            {"symbol": "BTC-USDT", "lastPrice": "50000", "quoteVolume": "25000000"},
            {"symbol": "DOGE-USDT", "lastPrice": "0.1", "quoteVolume": "1000"},
            {"symbol": "NCSKAAPL2USD-USDT", "lastPrice": "190", "quoteVolume": "750000"},
            {"symbol": "NCSKMSFT2USD-USDT", "lastPrice": "420", "quoteVolume": "900000"},
        ]

    async def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        self.oi_symbols.append(symbol)
        return {
            "BTC-USDT": {"openInterest": "3000000"},
            "DOGE-USDT": {"openInterest": "2000"},
            "AAPL-USDT": {"openInterest": "1500000"},
            "MSFT-USDT": {"openInterest": "2000000"},
        }[symbol]


@dataclass
class FakeQuote:
    symbol: str
    price: float


class FakeFMP:
    async def get_quote(self, symbol: str) -> FakeQuote | None:
        return FakeQuote(symbol, 100.0) if symbol == "AAPL" else None


class FakeMassive:
    async def get_options_chain(self, symbol: str) -> list[dict[str, Any]] | None:
        return [{"contract": "ok"}] if symbol == "MSFT" else None


@pytest.mark.asyncio
async def test_universe_filters_liquidity_and_keeps_enrichable_stocks() -> None:
    service = BingXUniverseService(
        client=FakeClient(),
        fmp_client=FakeFMP(),
        massive_client=FakeMassive(),
    )

    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
        )
    )

    assert [item.symbol for item in instruments] == ["MSFT-USDT", "AAPL-USDT"]
    assert all(item.asset_class == "synthetic_stock" for item in instruments)
    assert instruments[0].massive_available is True
    assert instruments[1].fmp_symbol == "AAPL"


@pytest.mark.asyncio
async def test_universe_does_not_require_unconfigured_enrichment_clients_for_stocks() -> None:
    service = BingXUniverseService(client=FakeClient())

    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
            require_fmp_for_stocks=True,
        )
    )

    assert [item.symbol for item in instruments] == ["MSFT-USDT", "AAPL-USDT"]
    assert all(
        item.asset_class != "synthetic_stock" or item.fmp_symbol in {"MSFT", "AAPL"}
        for item in instruments
    )


@pytest.mark.asyncio
async def test_universe_prefilters_before_open_interest_calls() -> None:
    client = FakeClient()
    service = BingXUniverseService(
        client=client,
        fmp_client=FakeFMP(),
        massive_client=FakeMassive(),
    )

    await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
        )
    )

    assert "DOGE-USDT" not in client.oi_symbols


class SlowOpenInterestClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.in_flight = 0
        self.max_in_flight = 0

    async def fetch_perp_contracts(self) -> list[dict[str, Any]]:
        roots = ("AAPL", "MSFT", "TSLA", "PLTR", "NVDA", "META")
        return [
            {
                "displayName": f"{root}-USDT",
                "symbol": f"NCSK{root}2USD-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "20",
            }
            for root in roots
        ]

    async def fetch_all_tickers_perp(self) -> list[dict[str, Any]]:
        roots = ("AAPL", "MSFT", "TSLA", "PLTR", "NVDA", "META")
        return [
            {
                "symbol": f"NCSK{root}2USD-USDT",
                "lastPrice": "1",
                "quoteVolume": "25000000",
            }
            for root in roots
        ]

    async def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        import asyncio

        self.oi_symbols.append(symbol)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return {"openInterest": "3000000"}


@pytest.mark.asyncio
async def test_universe_fetches_open_interest_concurrently() -> None:
    client = SlowOpenInterestClient()
    service = BingXUniverseService(client=client)

    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
        )
    )

    assert len(instruments) == 6
    assert client.max_in_flight > 1


class StockOpenInterestClient(FakeClient):
    async def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        self.oi_symbols.append(symbol)
        return {
            "BTC-USDT": {"openInterest": "3000000"},
            "DOGE-USDT": {"openInterest": "2000"},
            "AAPL-USDT": {"openInterest": "900000"},
            "MSFT-USDT": {"openInterest": "950000"},
        }[symbol]


@pytest.mark.asyncio
async def test_universe_uses_stock_specific_open_interest_threshold() -> None:
    service = BingXUniverseService(client=StockOpenInterestClient())

    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
            min_stock_open_interest=500_000,
            require_fmp_for_stocks=True,
        )
    )

    assert [item.symbol for item in instruments] == ["MSFT-USDT", "AAPL-USDT"]


class ExcludedHighLiquidityClient(FakeClient):
    async def fetch_perp_symbol_map(self) -> dict[str, str]:
        return {}

    async def fetch_perp_contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "displayName": "BTC-USDT",
                "symbol": "BTC-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "50",
            },
            {
                "displayName": "SHIB-USDT",
                "symbol": "SHIB-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "20",
            },
            {
                "displayName": "USDC-USDT",
                "symbol": "USDC-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "20",
            },
        ]

    async def fetch_all_tickers_perp(self) -> list[dict[str, Any]]:
        return [
            {"symbol": "BTC-USDT", "lastPrice": "50000", "quoteVolume": "25000000"},
            {"symbol": "SHIB-USDT", "lastPrice": "0.00001", "quoteVolume": "30000000"},
            {"symbol": "USDC-USDT", "lastPrice": "1", "quoteVolume": "40000000"},
        ]

    async def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        self.oi_symbols.append(symbol)
        return {"openInterest": "3000000"}


@pytest.mark.asyncio
async def test_universe_excludes_policy_blocked_crypto_before_open_interest() -> None:
    client = ExcludedHighLiquidityClient()
    service = BingXUniverseService(client=client)

    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
        )
    )

    assert instruments == []
    assert "BTC-USDT" not in client.oi_symbols
    assert "SHIB-USDT" not in client.oi_symbols
    assert "USDC-USDT" not in client.oi_symbols


class StockIndexClient(FakeClient):
    async def fetch_perp_contracts(self) -> list[dict[str, Any]]:
        return [
            {
                "displayName": "SPX-USDT",
                "symbol": "SPX-USDT",
                "apiStateOpen": "true",
                "maxLeverage": "10",
            }
        ]

    async def fetch_all_tickers_perp(self) -> list[dict[str, Any]]:
        return [{"symbol": "SPX-USDT", "lastPrice": "5200", "quoteVolume": "1000000"}]

    async def fetch_open_interest(self, symbol: str) -> dict[str, Any]:
        self.oi_symbols.append(symbol)
        return {"openInterest": "1000000"}


@pytest.mark.asyncio
async def test_discover_universe_classifies_stock_indices_as_operable_perps() -> None:
    service = BingXUniverseService(client=StockIndexClient())

    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
            min_stock_open_interest=500_000,
        )
    )

    assert len(instruments) == 1
    assert instruments[0].symbol == "SPX-USDT"
    assert instruments[0].market_type == "stock_index_perp"
    assert instruments[0].analysis_allowed is True
    assert instruments[0].execution_allowed is True


# ── classify_instrument unit tests ────────────────────────────────────────────


def test_classify_googl_is_stock_perp() -> None:
    market_type, execution_allowed, exclusion_reason = classify_instrument(
        "GOOGL", "synthetic_stock"
    )
    assert market_type == "stock_perp"
    assert execution_allowed is True
    assert exclusion_reason is None


def test_classify_gold_is_excluded_commodity() -> None:
    market_type, execution_allowed, exclusion_reason = classify_instrument("GOLD", "crypto")
    assert market_type == "excluded"
    assert execution_allowed is False
    assert exclusion_reason == "commodity"


def test_classify_xau_is_excluded_commodity() -> None:
    market_type, execution_allowed, exclusion_reason = classify_instrument("XAU", "crypto")
    assert market_type == "excluded"
    assert execution_allowed is False
    assert exclusion_reason == "commodity"


def test_classify_btc_is_excluded_when_bot_is_synthetic_stocks_only() -> None:
    """Crypto roots are excluded from the BingX Bot universe."""
    market_type, execution_allowed, exclusion_reason = classify_instrument("BTC", "crypto")
    assert market_type == "excluded"
    assert execution_allowed is False
    assert exclusion_reason == "crypto_disabled"


@pytest.mark.parametrize("root", ["USDT", "USDC", "SUSDS", "USD1"])
def test_classify_stablecoins_are_excluded(root: str) -> None:
    market_type, execution_allowed, exclusion_reason = classify_instrument(root, "crypto")
    assert market_type == "excluded"
    assert execution_allowed is False
    assert exclusion_reason == "stablecoin"


def test_classify_stock_index_perp() -> None:
    market_type, execution_allowed, exclusion_reason = classify_instrument("SPX", "synthetic_stock")
    assert market_type == "stock_index_perp"
    assert execution_allowed is True
    assert exclusion_reason is None


def test_classify_obscure_crypto_excluded() -> None:
    market_type, execution_allowed, _ = classify_instrument("SHIB", "crypto")
    assert market_type == "excluded"
    assert execution_allowed is False


def test_classify_fx_excluded() -> None:
    market_type, execution_allowed, exclusion_reason = classify_instrument("EUR", "crypto")
    assert market_type == "excluded"
    assert execution_allowed is False
    assert exclusion_reason == "fx"


@pytest.mark.asyncio
async def test_discover_universe_populates_policy_fields() -> None:
    """Instruments returned by discover_universe carry venue/underlying/market_type fields."""
    service = BingXUniverseService(
        client=FakeClient(),
        fmp_client=FakeFMP(),
        massive_client=FakeMassive(),
    )
    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
        )
    )
    aapl = next(i for i in instruments if i.symbol == "AAPL-USDT")
    assert aapl.venue_symbol == "AAPL-USDT"
    assert aapl.underlying_symbol == "AAPL"
    assert aapl.market_type == "stock_perp"
    assert aapl.analysis_allowed is True
    assert aapl.execution_allowed is True


# ── Universe policy: default top-crypto list ──────────────────────────────────


def test_default_top_crypto_roots_has_exactly_10_items() -> None:
    """The default list is pinned to exactly 10 — the production maximum cap."""
    roots = [r.strip() for r in _DEFAULT_TOP_CRYPTO_ROOTS.split(",") if r.strip()]
    assert len(roots) == _BINGX_TOP_CRYPTO_LIMIT_MAX


def test_default_top_crypto_roots_contains_required_symbols() -> None:
    """Core production symbols must always be present in the default list."""
    roots = {r.strip().upper() for r in _DEFAULT_TOP_CRYPTO_ROOTS.split(",") if r.strip()}
    for expected in ("BTC", "ETH", "SOL", "XRP"):
        assert expected in roots, f"{expected} missing from _DEFAULT_TOP_CRYPTO_ROOTS"


def test_default_top_crypto_excludes_stablecoins() -> None:
    """No stablecoin may appear in the default list (guards against copy-paste errors)."""
    roots = {r.strip().upper() for r in _DEFAULT_TOP_CRYPTO_ROOTS.split(",") if r.strip()}
    for stable in ("USDT", "USDC", "SUSDS", "USD1", "BUSD", "DAI"):
        assert stable not in roots, f"Stablecoin {stable} found in _DEFAULT_TOP_CRYPTO_ROOTS"


# ── Universe policy: BINGX_TOP_CRYPTO_LIMIT clamping ─────────────────────────


def test_top_crypto_limit_clamps_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """BINGX_TOP_CRYPTO_LIMIT above 10 is capped at 10; position-11 symbol is excluded."""
    roots_13 = "BTC,ETH,BNB,SOL,XRP,DOGE,ADA,TRX,AVAX,LINK,TON,ARB,OP"
    monkeypatch.setenv("BINGX_TOP_CRYPTO_ROOTS", roots_13)
    monkeypatch.setenv("BINGX_TOP_CRYPTO_LIMIT", "13")
    mt_link, _, reason_link = classify_instrument("LINK", "crypto")  # position 10 → disabled
    mt_ton, _, _ = classify_instrument("TON", "crypto")  # position 11 → excluded
    assert mt_link == "excluded"
    assert reason_link == "crypto_disabled"
    assert mt_ton == "excluded"


def test_top_crypto_limit_clamps_to_min(monkeypatch: pytest.MonkeyPatch) -> None:
    """BINGX_TOP_CRYPTO_LIMIT below 5 is raised to 5; position-5 symbol stays included."""
    roots_6 = "BTC,ETH,BNB,SOL,XRP,DOGE"
    monkeypatch.setenv("BINGX_TOP_CRYPTO_ROOTS", roots_6)
    monkeypatch.setenv("BINGX_TOP_CRYPTO_LIMIT", "2")
    mt_xrp, _, reason_xrp = classify_instrument("XRP", "crypto")  # position 5 → disabled
    mt_doge, _, _ = classify_instrument("DOGE", "crypto")  # position 6 → excluded
    assert mt_xrp == "excluded"
    assert reason_xrp == "crypto_disabled"
    assert mt_doge == "excluded"


def test_top_crypto_limit_constants_are_5_and_10() -> None:
    """Policy constants must not drift from the agreed production range."""
    assert _BINGX_TOP_CRYPTO_LIMIT_MIN == 5
    assert _BINGX_TOP_CRYPTO_LIMIT_MAX == 10


# ── Universe policy: analysis_allowed vs execution_allowed separation ─────────


def test_classify_stock_perp_has_both_flags() -> None:
    """Stock perps are fully operational: analysis and execution both enabled."""
    mt, execution, reason = classify_instrument("AAPL", "synthetic_stock")
    assert mt == "stock_perp"
    assert execution is True
    assert reason is None


def test_classify_stock_index_perp_has_both_flags() -> None:
    """Stock index perps are fully operational: analysis and execution both enabled."""
    mt, execution, reason = classify_instrument("SPX", "synthetic_stock")
    assert mt == "stock_index_perp"
    assert execution is True
    assert reason is None


def test_classify_crypto_standard_is_not_executable() -> None:
    """Top-cap crypto: analysable but NOT executable via the equity execution pipeline."""
    mt, execution, reason = classify_instrument("ETH", "crypto")
    assert mt == "excluded"
    assert execution is False
    assert reason == "crypto_disabled"


@pytest.mark.asyncio
async def test_discover_universe_analysis_allowed_set_correctly() -> None:
    """analysis_allowed=True for crypto_standard and stock_perp; False for excluded."""
    service = BingXUniverseService(
        client=FakeClient(),
        fmp_client=FakeFMP(),
        massive_client=FakeMassive(),
    )
    instruments = await service.discover_universe(
        LiquidityFilter(
            min_crypto_volume_24h=10_000_000,
            min_stock_volume_24h=500_000,
            min_open_interest=1_000_000,
        )
    )
    by_symbol = {i.symbol: i for i in instruments}
    assert by_symbol["AAPL-USDT"].analysis_allowed is True
    assert by_symbol["AAPL-USDT"].execution_allowed is True
    assert by_symbol["MSFT-USDT"].analysis_allowed is True
    assert by_symbol["MSFT-USDT"].execution_allowed is True
