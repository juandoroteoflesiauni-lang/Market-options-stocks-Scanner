from __future__ import annotations
"""Market-data routing contract for BingX bot symbols.

This module is intentionally pure: it chooses providers and symbols, but does
not fetch data. Callers can use the route to wire venue data, analysis data,
execution L2 and options/derivatives without spreading market-type conditionals
across routers and services.
"""


from dataclasses import asdict, dataclass

from backend.services.bingx_options_bridge import INDEX_OPTIONS_PROXIES
from backend.services.bingx_symbol_linker import classify_underlying, underlying_from_bingx_symbol


@dataclass(frozen=True)
class ProviderRoute:
    primary: str | None
    symbol: str | None = None
    fallbacks: tuple[str, ...] = ()
    purpose: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MarketDataRoute:
    venue_symbol: str
    underlying_symbol: str
    market_type: str
    analysis_ohlcv: ProviderRoute
    analysis_volume: ProviderRoute
    analysis_order_book: ProviderRoute
    analysis_trades: ProviderRoute
    execution_l2: ProviderRoute
    options: ProviderRoute
    crypto_derivatives: ProviderRoute

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_market_data_route(venue_symbol: str) -> MarketDataRoute:
    symbol = str(venue_symbol or "").strip().upper().replace("/", "-")
    underlying = underlying_from_bingx_symbol(symbol)
    market_type = classify_underlying(symbol)

    if market_type == "crypto_standard":
        return MarketDataRoute(
            venue_symbol=symbol,
            underlying_symbol=underlying,
            market_type=market_type,
            analysis_ohlcv=ProviderRoute("binance", symbol=symbol, fallbacks=("bingx",)),
            analysis_volume=ProviderRoute("binance", symbol=symbol, fallbacks=("bingx",)),
            analysis_order_book=ProviderRoute("binance", symbol=symbol),
            analysis_trades=ProviderRoute("binance", symbol=symbol),
            execution_l2=ProviderRoute("bingx", symbol=symbol),
            options=ProviderRoute("deribit", symbol=underlying),
            crypto_derivatives=ProviderRoute("deribit", symbol=underlying),
        )

    if market_type == "stock_perp":
        return MarketDataRoute(
            venue_symbol=symbol,
            underlying_symbol=underlying,
            market_type=market_type,
            analysis_ohlcv=ProviderRoute("bingx", symbol=symbol),
            analysis_volume=ProviderRoute("bingx", symbol=symbol),
            analysis_order_book=ProviderRoute("bingx", symbol=symbol),
            analysis_trades=ProviderRoute("bingx", symbol=symbol),
            execution_l2=ProviderRoute("bingx", symbol=symbol),
            options=ProviderRoute("equity_options", symbol=underlying),
            crypto_derivatives=ProviderRoute(None, symbol=None),
        )

    if market_type == "stock_index_perp":
        proxy = INDEX_OPTIONS_PROXIES.get(underlying)
        return MarketDataRoute(
            venue_symbol=symbol,
            underlying_symbol=underlying,
            market_type=market_type,
            analysis_ohlcv=ProviderRoute("bingx", symbol=symbol),
            analysis_volume=ProviderRoute("bingx", symbol=symbol),
            analysis_order_book=ProviderRoute("bingx", symbol=symbol),
            analysis_trades=ProviderRoute("bingx", symbol=symbol),
            execution_l2=ProviderRoute("bingx", symbol=symbol),
            options=ProviderRoute("equity_options_proxy" if proxy else None, symbol=proxy),
            crypto_derivatives=ProviderRoute(None, symbol=None),
        )

    return MarketDataRoute(
        venue_symbol=symbol,
        underlying_symbol=underlying,
        market_type=market_type,
        analysis_ohlcv=ProviderRoute(None, symbol=None),
        analysis_volume=ProviderRoute(None, symbol=None),
        analysis_order_book=ProviderRoute(None, symbol=None),
        analysis_trades=ProviderRoute(None, symbol=None),
        execution_l2=ProviderRoute(None, symbol=None),
        options=ProviderRoute(None, symbol=None),
        crypto_derivatives=ProviderRoute(None, symbol=None),
    )


__all__ = [
    "MarketDataRoute",
    "ProviderRoute",
    "build_market_data_route",
]
