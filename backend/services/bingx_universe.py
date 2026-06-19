from __future__ import annotations

from typing import Any, Literal, Protocol

"""Dynamic BingX universe discovery and liquidity filtering.

This service sits outside Layer 1 because it combines exchange metadata with
application-level filters and optional equity enrichment availability checks.
"""


import asyncio
import os
from collections.abc import Awaitable
from dataclasses import asdict, dataclass

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_client import BingXClient, is_perp_symbol

logger = get_logger(__name__)
DEFAULT_OPEN_INTEREST_CONCURRENCY = 20

# ── Universe policy ────────────────────────────────────────────────────────────
# Stablecoins: always excluded regardless of liquidity.
_STABLECOIN_ROOTS: frozenset[str] = frozenset(
    {
        "USDT",
        "USDC",
        "SUSDS",
        "USD1",
        "BUSD",
        "DAI",
        "USDE",
        "FDUSD",
        "TUSD",
        "PYUSD",
        "USDP",
        "FRAX",
        "LUSD",
        "USDD",
        "CRVUSD",
        "ALUSD",
        "SUSD",
        "HUSD",
        "GUSD",
        "EURS",
        "USDJ",
        "USDX",
    }
)

# Commodities / metals / energy raw-material synthetics — excluded.
_COMMODITY_ROOTS: frozenset[str] = frozenset(
    {
        "GOLD",
        "XAU",
        "XAUUSD",
        "SILVER",
        "XAG",
        "OIL",
        "WTI",
        "BRENT",
        "GAS",
        "NG",
        "WHEAT",
        "CORN",
        "COFFEE",
        "COTTON",
        "SUGAR",
        "COPPER",
        "PLATINUM",
        "PALLADIUM",
        "CRUDE",
    }
)

# FX / forex synthetic perps — excluded.
_FX_ROOTS: frozenset[str] = frozenset(
    {
        "EUR",
        "GBP",
        "JPY",
        "AUD",
        "CAD",
        "CHF",
        "NZD",
        "CNH",
        "CNY",
        "MXN",
        "SGD",
        "HKD",
        "KRW",
        "TRY",
        "BRL",
        "INR",
        "EURUSD",
        "GBPUSD",
        "USDJPY",
    }
)

# Stock-index perpetuals — classified separately from individual equity perps.
_STOCK_INDEX_ROOTS: frozenset[str] = frozenset(
    {
        "SPX",
        "SPY",
        "QQQ",
        "NDX",
        "DJI",
        "IWM",
        "VIX",
        "NQ",
        "ES",
        "YM",
        "RTY",
        "US30",
        "US500",
        "US100",
    }
)

# Top-cap crypto allowed for analysis (stablecoins already excluded above).
# Exactly 10 symbols — the production maximum. Configurable via BINGX_TOP_CRYPTO_ROOTS
# (comma-separated root list). BINGX_TOP_CRYPTO_LIMIT caps the count to [5, 10].
_DEFAULT_TOP_CRYPTO_ROOTS = "BTC,ETH,BNB,SOL,XRP,DOGE,ADA,TRX,AVAX,LINK"

_BINGX_TOP_CRYPTO_LIMIT_MIN = 5
_BINGX_TOP_CRYPTO_LIMIT_MAX = 10

MarketType = Literal["crypto_standard", "stock_perp", "stock_index_perp", "excluded"]


def is_stock_index_root(root: str) -> bool:
    """Return True when ``root`` is a supported stock-index perp underlying."""
    return root.strip().upper() in _STOCK_INDEX_ROOTS


def _top_crypto_roots_from_env() -> frozenset[str]:
    raw = os.getenv("BINGX_TOP_CRYPTO_ROOTS", _DEFAULT_TOP_CRYPTO_ROOTS)
    roots = [item.strip().upper() for item in raw.split(",") if item.strip()]
    raw_limit = _env_int("BINGX_TOP_CRYPTO_LIMIT", _BINGX_TOP_CRYPTO_LIMIT_MAX)
    limit = max(_BINGX_TOP_CRYPTO_LIMIT_MIN, min(_BINGX_TOP_CRYPTO_LIMIT_MAX, raw_limit))
    if raw_limit != limit:
        logger.warning(
            "BINGX_TOP_CRYPTO_LIMIT=%d out of [%d, %d]; clamped to %d",
            raw_limit,
            _BINGX_TOP_CRYPTO_LIMIT_MIN,
            _BINGX_TOP_CRYPTO_LIMIT_MAX,
            limit,
        )
    return frozenset(roots[:limit])


def classify_instrument(
    root: str,
    asset_class: Literal["crypto", "synthetic_stock"],
) -> tuple[MarketType, bool, str | None]:
    """Return ``(market_type, execution_allowed, exclusion_reason)``.

    Pure function — no I/O. Enforces the BingX universe policy:
    - Stablecoins, commodities, FX: always excluded.
    - Synthetic-stock perps: ``stock_perp`` (individual) or ``stock_index_perp`` — executable.
    - Crypto roots: excluded. The BingX Bot operates synthetic stocks only.
    - Everything else: excluded.
    """
    r = root.upper()

    if r in _STABLECOIN_ROOTS:
        return "excluded", False, "stablecoin"
    if r in _COMMODITY_ROOTS:
        return "excluded", False, "commodity"
    if r in _FX_ROOTS:
        return "excluded", False, "fx"

    if asset_class == "synthetic_stock":
        if r in _STOCK_INDEX_ROOTS:
            return "stock_index_perp", True, None
        return "stock_perp", True, None

    return "excluded", False, "crypto_disabled"


class FMPQuoteClient(Protocol):
    def get_quote(self, symbol: str) -> Awaitable[object | None]: ...


class MassiveOptionsClient(Protocol):
    def get_options_chain(self, ticker: str) -> Awaitable[list[dict[str, Any]] | None]: ...


@dataclass(frozen=True)
class BingXInstrument:
    symbol: str
    display_name: str
    asset_class: Literal["crypto", "synthetic_stock"]
    volume_24h_usdt: float
    open_interest: float | None
    last_price: float
    max_leverage: int
    is_tradeable: bool
    fmp_symbol: str | None
    massive_available: bool
    # ── Universe policy fields (defaulted for backward compatibility) ──────────
    venue_symbol: str = ""
    underlying_symbol: str = ""
    market_type: MarketType = "excluded"
    analysis_allowed: bool = False  # True for crypto_standard, stock_perp, stock_index_perp
    execution_allowed: bool = False  # True only for stock_perp and stock_index_perp
    exclusion_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _UniverseCandidate:
    display: str
    api_symbol: str
    root: str
    asset_class: Literal["crypto", "synthetic_stock"]
    volume_24h_usdt: float
    last_price: float
    max_leverage: int
    is_tradeable: bool


@dataclass(frozen=True)
class LiquidityFilter:
    min_crypto_volume_24h: float = 10_000_000.0
    min_stock_volume_24h: float = 500_000.0
    min_open_interest: float = 1_000_000.0
    min_stock_open_interest: float = 500_000.0
    require_fmp_for_stocks: bool = True

    @classmethod
    def from_env(cls: type[LiquidityFilter]) -> LiquidityFilter:
        return cls(
            min_crypto_volume_24h=_env_float("BINGX_MIN_CRYPTO_VOLUME_24H", 10_000_000.0),
            min_stock_volume_24h=_env_float("BINGX_MIN_STOCK_VOLUME_24H", 500_000.0),
            min_open_interest=_env_float("BINGX_MIN_OPEN_INTEREST", 1_000_000.0),
            min_stock_open_interest=_env_float("BINGX_MIN_STOCK_OPEN_INTEREST", 500_000.0),
            require_fmp_for_stocks=_env_bool("BINGX_REQUIRE_FMP_FOR_STOCKS", True),
        )


class BingXUniverseService:
    """Discover tradeable BingX instruments and apply liquidity/enrichment gates."""

    def __init__(
        self,
        *,
        client: BingXClient,
        fmp_client: FMPQuoteClient | None = None,
        massive_client: MassiveOptionsClient | None = None,
        priority_stocks: tuple[str, ...] | None = None,
    ) -> None:
        self._client = client
        self._fmp = fmp_client
        self._massive = massive_client
        self._priority_stocks = priority_stocks or _priority_stocks_from_env()
        self._cached: tuple[BingXInstrument, ...] | None = None

    async def discover_universe(
        self,
        liquidity_filter: LiquidityFilter | None = None,
    ) -> list[BingXInstrument]:
        effective_filter = liquidity_filter or LiquidityFilter.from_env()
        from backend.config.dual_bot_core_universe import dual_bot_fixed_universe_enabled

        if dual_bot_fixed_universe_enabled():
            return await self._discover_fixed_core_universe(effective_filter)

        contracts, tickers = await asyncio.gather(
            self._client.fetch_perp_contracts(),
            self._client.fetch_all_tickers_perp(),
        )
        ticker_by_symbol = _ticker_lookup(tickers)
        candidates: list[_UniverseCandidate] = []

        for contract in contracts:
            display = str(contract.get("displayName") or contract.get("symbol") or "").strip()
            api_symbol = str(contract.get("symbol") or display).strip()
            if not display:
                continue
            ticker = ticker_by_symbol.get(api_symbol) or ticker_by_symbol.get(display)
            if ticker is None:
                continue
            root = _symbol_root(display)
            asset_class: Literal["crypto", "synthetic_stock"] = (
                "synthetic_stock" if self._is_synthetic_stock(display, api_symbol) else "crypto"
            )
            is_tradeable = str(contract.get("apiStateOpen", "true")).lower() == "true"
            volume = _first_float(ticker, "quoteVolume", "quoteVolume24h", "volume", "vol")
            price = _first_float(ticker, "lastPrice", "price", "close")
            max_leverage = int(_first_float(contract, "maxLeverage", "maxLongLeverage") or 1)
            candidate = _UniverseCandidate(
                display=display,
                api_symbol=api_symbol,
                root=root,
                asset_class=asset_class,
                volume_24h_usdt=volume,
                last_price=price,
                max_leverage=max_leverage,
                is_tradeable=is_tradeable,
            )
            if _passes_prefilter(candidate, effective_filter):
                candidates.append(candidate)

        semaphore = asyncio.Semaphore(DEFAULT_OPEN_INTEREST_CONCURRENCY)
        discovered = await asyncio.gather(
            *(
                self._build_instrument(candidate, effective_filter, semaphore)
                for candidate in candidates
            )
        )
        instruments = [instrument for instrument in discovered if instrument is not None]

        instruments.sort(key=lambda item: item.volume_24h_usdt, reverse=True)
        self._cached = tuple(instruments)
        logger.info("bingx_universe.discovered count=%d", len(instruments))
        return instruments

    async def _discover_fixed_core_universe(
        self,
        liquidity_filter: LiquidityFilter,
    ) -> list[BingXInstrument]:
        """Solo los tickers de ``DUAL_BOT_CORE_UNIVERSE`` — sin discovery dinámico."""
        from backend.config.dual_bot_core_universe import DUAL_BOT_CORE_UNIVERSE

        core_filter = LiquidityFilter(
            min_crypto_volume_24h=liquidity_filter.min_crypto_volume_24h,
            min_stock_volume_24h=liquidity_filter.min_stock_volume_24h,
            min_open_interest=liquidity_filter.min_open_interest,
            min_stock_open_interest=liquidity_filter.min_stock_open_interest,
            require_fmp_for_stocks=False,
        )
        await self._client.fetch_perp_symbol_map()
        instruments: list[BingXInstrument] = []
        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(2.0)
            instruments = await self._scan_fixed_core_candidates(core_filter)
            if len(instruments) >= len(DUAL_BOT_CORE_UNIVERSE):
                break
            logger.warning(
                "bingx_universe.fixed_core_retry attempt=%d found=%d expected=%d",
                attempt + 1,
                len(instruments),
                len(DUAL_BOT_CORE_UNIVERSE),
            )
        self._cached = tuple(instruments)
        logger.info(
            "bingx_universe.fixed_core_discovered count=%d requested=%d",
            len(instruments),
            len(DUAL_BOT_CORE_UNIVERSE),
        )
        return instruments

    async def _scan_fixed_core_candidates(
        self,
        core_filter: LiquidityFilter,
    ) -> list[BingXInstrument]:
        """Single pass: match core equity roots to BingX perp contracts."""
        from backend.config.dual_bot_core_universe import DUAL_BOT_CORE_UNIVERSE
        from backend.services.bingx_symbol_linker import underlying_from_bingx_symbol

        contracts, tickers = await asyncio.gather(
            self._client.fetch_perp_contracts(),
            self._client.fetch_all_tickers_perp(),
        )
        ticker_by_symbol = _ticker_lookup(tickers)
        root_to_candidate: dict[str, _UniverseCandidate] = {}

        for contract in contracts:
            display = str(contract.get("displayName") or contract.get("symbol") or "").strip()
            api_symbol = str(contract.get("symbol") or display).strip()
            if not display:
                continue
            root = underlying_from_bingx_symbol(display or api_symbol)
            if root not in DUAL_BOT_CORE_UNIVERSE:
                continue
            ticker = ticker_by_symbol.get(api_symbol) or ticker_by_symbol.get(display) or {}
            asset_class: Literal["crypto", "synthetic_stock"] = (
                "synthetic_stock" if self._is_synthetic_stock(display, api_symbol) else "crypto"
            )
            if asset_class != "synthetic_stock":
                continue
            volume = _first_float(ticker, "quoteVolume", "quoteVolume24h", "volume", "vol")
            price = _first_float(ticker, "lastPrice", "price", "close")
            is_tradeable = _core_contract_tradeable(contract, volume=volume, price=price)
            max_leverage = int(_first_float(contract, "maxLeverage", "maxLongLeverage") or 1)
            candidate = _UniverseCandidate(
                display=display,
                api_symbol=api_symbol,
                root=root,
                asset_class=asset_class,
                volume_24h_usdt=volume,
                last_price=price,
                max_leverage=max_leverage,
                is_tradeable=is_tradeable,
            )
            if _passes_core_prefilter(candidate, core_filter):
                root_to_candidate[root] = candidate

        missing = [root for root in DUAL_BOT_CORE_UNIVERSE if root not in root_to_candidate]
        if missing:
            logger.warning("bingx_universe.fixed_core_missing roots=%s", ",".join(missing))

        ordered = [
            root_to_candidate[root] for root in DUAL_BOT_CORE_UNIVERSE if root in root_to_candidate
        ]
        semaphore = asyncio.Semaphore(DEFAULT_OPEN_INTEREST_CONCURRENCY)
        discovered = await asyncio.gather(
            *(self._build_instrument(candidate, core_filter, semaphore) for candidate in ordered)
        )
        return [instrument for instrument in discovered if instrument is not None]

    async def get_filtered_universe(
        self,
        liquidity_filter: LiquidityFilter | None = None,
    ) -> list[str]:
        instruments = await self.discover_universe(liquidity_filter)
        return [item.symbol for item in instruments]

    async def get_cached_or_discover(self) -> list[BingXInstrument]:
        if self._cached is not None:
            return list(self._cached)
        return await self.discover_universe()

    async def refresh(self) -> list[BingXInstrument]:
        self._cached = None
        return await self.discover_universe()

    async def _fetch_open_interest(self, symbol: str) -> float | None:
        try:
            payload = await self._client.fetch_open_interest(symbol)
        except Exception as exc:
            logger.debug("bingx_universe.open_interest_failed symbol=%s error=%s", symbol, exc)
            return None
        return _first_float(payload, "openInterest", "openInterestValue", "sumOpenInterestValue")

    async def _build_instrument(
        self,
        candidate: _UniverseCandidate,
        liquidity_filter: LiquidityFilter,
        semaphore: asyncio.Semaphore,
    ) -> BingXInstrument | None:
        async with semaphore:
            oi_task = self._fetch_open_interest(candidate.api_symbol or candidate.display)
            enrichment_task = self._enrichment_available(
                candidate.root,
                candidate.asset_class,
                liquidity_filter.require_fmp_for_stocks,
            )
            oi, enrichment = await asyncio.gather(oi_task, enrichment_task)
        fmp_available, massive_available = enrichment
        fmp_symbol = candidate.root if candidate.asset_class == "synthetic_stock" else None
        market_type, execution_allowed, exclusion_reason = classify_instrument(
            candidate.root, candidate.asset_class
        )
        analysis_allowed = market_type != "excluded"
        instrument = BingXInstrument(
            symbol=candidate.display,
            display_name=candidate.display,
            asset_class=candidate.asset_class,
            volume_24h_usdt=candidate.volume_24h_usdt,
            open_interest=oi,
            last_price=candidate.last_price,
            max_leverage=candidate.max_leverage,
            is_tradeable=candidate.is_tradeable,
            fmp_symbol=fmp_symbol,
            massive_available=massive_available,
            venue_symbol=candidate.display,
            underlying_symbol=candidate.root,
            market_type=market_type,
            analysis_allowed=analysis_allowed,
            execution_allowed=execution_allowed,
            exclusion_reason=exclusion_reason,
        )
        if _passes_filter(instrument, liquidity_filter, fmp_available):
            return instrument
        return None

    def _is_synthetic_stock(self, display: str, api_symbol: str) -> bool:
        root = _symbol_root(display)
        if is_perp_symbol(display):
            return True
        return (
            is_stock_index_root(root)
            or root in self._priority_stocks
            or api_symbol.upper().startswith("NCSK")
        )

    async def _enrichment_available(
        self,
        root: str,
        asset_class: str,
        require_fmp_for_stocks: bool,
    ) -> tuple[bool, bool]:
        if asset_class != "synthetic_stock":
            return True, False
        if self._fmp is None and self._massive is None:
            return True, False
        fmp_task = self._has_fmp_quote(root)
        massive_task = self._has_massive_options(root)
        fmp_available, massive_available = await asyncio.gather(fmp_task, massive_task)
        if not require_fmp_for_stocks:
            return True, massive_available
        return fmp_available or massive_available, massive_available

    async def _has_fmp_quote(self, root: str) -> bool:
        if self._fmp is None:
            return False
        try:
            return await self._fmp.get_quote(root) is not None
        except Exception as exc:
            logger.debug("bingx_universe.fmp_check_failed symbol=%s error=%s", root, exc)
            return False

    async def _has_massive_options(self, root: str) -> bool:
        try:
            from backend.services.alpaca_r1_options_context import load_route1_options_context

            ctx = load_route1_options_context(root.upper().strip())
            if ctx is not None and ctx.available:
                return True
        except Exception as exc:
            logger.debug("bingx_universe.sqlite_options_check_failed symbol=%s error=%s", root, exc)
        if self._massive is None:
            return False
        try:
            chain = await self._massive.get_options_chain(root)
        except Exception as exc:
            logger.debug("bingx_universe.massive_check_failed symbol=%s error=%s", root, exc)
            return False
        return bool(chain)


def _passes_filter(
    instrument: BingXInstrument,
    liquidity_filter: LiquidityFilter,
    enrichment_available: bool,
) -> bool:
    if not instrument.is_tradeable:
        return False
    if not instrument.analysis_allowed:
        return False
    if instrument.last_price <= 0:
        return False
    min_open_interest = (
        liquidity_filter.min_stock_open_interest
        if instrument.asset_class == "synthetic_stock"
        else liquidity_filter.min_open_interest
    )
    if instrument.open_interest is None or instrument.open_interest < min_open_interest:
        return False
    if instrument.asset_class == "synthetic_stock":
        return (
            instrument.volume_24h_usdt >= liquidity_filter.min_stock_volume_24h
            and enrichment_available
        )
    return instrument.volume_24h_usdt >= liquidity_filter.min_crypto_volume_24h


def _core_contract_tradeable(
    contract: dict[str, Any],
    *,
    volume: float,
    price: float,
) -> bool:
    """BingX VST marks many stock perps ``apiStateOpen=false`` while still quoting."""
    if str(contract.get("apiStateOpen", "true")).strip().lower() == "true":
        return True
    return volume > 0 and price > 0


def _passes_core_prefilter(
    candidate: _UniverseCandidate,
    liquidity_filter: LiquidityFilter,
) -> bool:
    """Prefilter for fixed core universe (same gates as stock prefilter)."""
    return _passes_prefilter(candidate, liquidity_filter)


def _passes_prefilter(
    candidate: _UniverseCandidate,
    liquidity_filter: LiquidityFilter,
) -> bool:
    if not candidate.is_tradeable:
        return False
    if candidate.last_price <= 0:
        return False
    market_type, _execution_allowed, _exclusion_reason = classify_instrument(
        candidate.root,
        candidate.asset_class,
    )
    if market_type == "excluded":
        return False
    if candidate.asset_class == "synthetic_stock":
        return candidate.volume_24h_usdt >= liquidity_filter.min_stock_volume_24h
    return candidate.volume_24h_usdt >= liquidity_filter.min_crypto_volume_24h


def _ticker_lookup(tickers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        for key in ("symbol", "displayName"):
            value = str(ticker.get(key) or "").strip()
            if value:
                out[value] = ticker
    return out


def _symbol_root(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("/", "-")
    if normalized.endswith("-USDT"):
        normalized = normalized[: -len("-USDT")]
    elif normalized.endswith("USDT"):
        normalized = normalized[: -len("USDT")]
    return normalized.rstrip("-")


def _first_float(payload: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _priority_stocks_from_env() -> tuple[str, ...]:
    from backend.config.dual_bot_core_universe import (
        DUAL_BOT_CORE_UNIVERSE,
        dual_bot_fixed_universe_enabled,
    )

    if dual_bot_fixed_universe_enabled():
        return DUAL_BOT_CORE_UNIVERSE
    raw = os.getenv(
        "BINGX_PRIORITY_STOCKS",
        "AMZN,AAPL,TSLA,GOOGL,META,MSFT,NVDA,PLTR",
    )
    return tuple(item.strip().upper() for item in raw.split(",") if item.strip())
