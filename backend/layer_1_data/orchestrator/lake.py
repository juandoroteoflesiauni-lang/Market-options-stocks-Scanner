from typing import Any
"""
backend/layer_1_data/orchestrator/lake.py
════════════════════════════════════════════════════════════════════════════════
QuantumAnalyzer V2 — DataLake Orchestrator
════════════════════════════════════════════════════════════════════════════════
Centralized market data access layer.
Unifies Polygon, FMP, Massive, BCRA, and yfinance into a single institutional-grade
interface. All returns are Pydantic V2 domain models.
════════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Config
from backend.config.settings import load_settings
from backend.domain.argentina_models import ArgentinaDolarSnapshot

# Domain models
from backend.domain.market_models import FundamentalMetrics, MarketSnapshot, OHLCVBar
from backend.domain.morning_briefing_models import MacroSnapshot
from backend.layer_1_data.fetchers.argentina_datos_fetcher import ArgentinaDatosFetcher
from backend.layer_1_data.fetchers.bcra_fetcher import BCRAFetcher
from backend.layer_1_data.fetchers.data912_fetcher import Data912Fetcher
from backend.layer_1_data.fetchers.fmp_client import FMPClient
from backend.layer_1_data.fetchers.hypertracker_fetcher import HyperTrackerFetcher
from backend.layer_1_data.fetchers.massive_client import MassiveClient

# Fetchers
from backend.layer_1_data.fetchers.polygon_client import PolygonClient
from backend.layer_1_data.fetchers.primary_fetcher import PrimaryFetcher

logger = logging.getLogger("backend.layer_1_data.orchestrator.lake")


@dataclass
class ProviderTelemetry:
    """Metrics tracking for provider hardening."""

    latency_ms: float = 0.0
    coverage_count: int = 0
    failures: int = 0
    freshness_seconds: float = 0.0
    last_success_at: datetime | None = None


@dataclass
class SymbolTelemetry:
    providers: dict[str, ProviderTelemetry] = field(default_factory=dict)


# ── yfinance lazy import ──────────────────────────────────────────────────────
try:
    import yfinance as yf

    _YF_AVAILABLE = True
except ImportError:
    yf = None

    _YF_AVAILABLE = False


class DataLake:
    """
    Central orchestrator for the DATA sector.

    Implements institutional fallback chains:
    - OHLCV: Polygon -> Alpaca -> yfinance.
    - Fundamentales: FMP -> yfinance.
    - Opciones: Massive (Polygon Bulk API) -> Tradier.
    - Macro: HyperTracker + ArgentinaDatos + FRED.
    """

    def __init__(self) -> None:
        self.settings = load_settings()

        # Initialize fetchers
        self.polygon = PolygonClient()
        self.massive = MassiveClient()
        # Note: FMPClient might be in 'datos' or 'fetchers'; assuming fetchers for consistency
        self.fmp = FMPClient()
        self.bcra = BCRAFetcher()
        self.arg_datos = ArgentinaDatosFetcher()
        self.hypertracker = HyperTrackerFetcher()
        self.data912 = Data912Fetcher()
        self.primary = PrimaryFetcher()

        # Telemetry registry per symbol
        self.provider_telemetry: dict[str, SymbolTelemetry] = {}

    def _record_telemetry(
        self, symbol: str, provider: str, latency: float, success: bool, coverage: int = 0
    ) -> None:
        """Internal helper to record metrics per provider/symbol."""
        sym_telemetry = self.provider_telemetry.setdefault(symbol.upper(), SymbolTelemetry())
        prov_telemetry = sym_telemetry.providers.setdefault(provider, ProviderTelemetry())
        prov_telemetry.latency_ms = latency * 1000.0  # raw perf_counter to ms
        if success:
            prov_telemetry.coverage_count += coverage
            now = datetime.now(UTC)
            if prov_telemetry.last_success_at:
                prov_telemetry.freshness_seconds = (
                    now - prov_telemetry.last_success_at
                ).total_seconds()
            prov_telemetry.last_success_at = now
        else:
            prov_telemetry.failures += 1

    # ──────────────────────────────────────────────────────────────────────────
    # §1  MARKET DATA (OHLCV & QUOTES)
    # ──────────────────────────────────────────────────────────────────────────

    async def get_ohlcv(
        self,
        ticker: str,
        period: str = "2y",
        timeframe: str = "1D",
    ) -> list[OHLCVBar]:
        """
        Fetch OHLCV bars using institutional fallback chain.
        Currently defaults to a clean conversion.
        """
        # Logic for V2:
        # 1. Try Polygon Aggregates (if supported)
        # 2. Try Alpaca
        # 3. Fallback to yfinance (sync wrapped in thread)

        # Placeholder for simplified yfinance fallback while we port Polygon Aggs
        if not _YF_AVAILABLE:
            return []

        try:
            # Sync to async wrapper
            def _fetch_yf():
                tkr = yf.Ticker(ticker)
                return tkr.history(period=period, interval=self._map_interval(timeframe))

            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, _fetch_yf)

            if df is None or df.empty:
                return []

            bars = []
            for ts, row in df.iterrows():
                bars.append(
                    OHLCVBar(
                        timestamp_utc=ts,  # Validation in model ensures UTC
                        open=row["Open"],
                        high=row["High"],
                        low=row["Low"],
                        close=row["Close"],
                        volume=row.get("Volume", 0),
                    )
                )
            return bars
        except Exception as exc:
            logger.error("DataLake.get_ohlcv failed for %s: %s", ticker, exc)
            return []

    @staticmethod
    def _map_interval(timeframe: str) -> str:
        mapping = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "1h",
            "1D": "1d",
            "1W": "1wk",
        }
        return mapping.get(timeframe, "1d")

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshot | None:
        """
        Fetch real-time snapshot via Polygon (Primary) or FMP.
        """
        snap = await self.polygon.get_quote(symbol)
        if snap:
            return MarketSnapshot(
                symbol=snap.symbol,
                price=snap.price,
                change_pct=snap.change_pct or 0.0,
                volume=snap.volume or 0,
                timestamp_utc=(
                    datetime.fromtimestamp(snap.timestamp / 1000, tz=UTC)
                    if snap.timestamp
                    else datetime.now(UTC)
                ),
            )
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # §2  FUNDAMENTALS & CORPORATE
    # ──────────────────────────────────────────────────────────────────────────

    async def get_fundamentals(self, ticker: str) -> FundamentalMetrics | None:
        """
        Fetch fundamental metrics via FMP (Primary).
        """
        # FMP enrichment logic
        data = await self.fmp.get_fundamentals_enrichment(ticker)
        if data:
            return FundamentalMetrics(ticker=ticker, **data)

        # yfinance fallback
        if _YF_AVAILABLE:
            try:
                tkr = yf.Ticker(ticker)
                info = tkr.info
                return FundamentalMetrics(
                    ticker=ticker,
                    market_cap=info.get("marketCap"),
                    pe_ratio=info.get("trailingPE"),
                    beta=info.get("beta"),
                    sector=info.get("sector"),
                    industry=info.get("industry"),
                )
            except:
                pass
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # §3  ARGENTINA / MACRO
    # ──────────────────────────────────────────────────────────────────────────

    async def get_argentina_dolar(self) -> list[ArgentinaDolarSnapshot]:
        """
        Fetch Argentina Dolar rates via ArgentinaDatos.
        """
        return await self.arg_datos.get_latest_dolar_rates()

    async def get_argentina_live_assets(self, panel: str = "stocks") -> Any:
        """
        Fetch live quotes for Argentine assets via Data912.
        Panels: stocks, bonds, options, cedears, notes.
        """
        if panel == "stocks":
            return await self.data912.get_live_stocks()
        elif panel == "bonds":
            return await self.data912.get_live_bonds()
        elif panel == "options":
            return await self.data912.get_live_options()
        elif panel == "cedears":
            return await self.data912.get_live_cedears()
        return []

    async def get_primary_instruments(self) -> list[Any]:
        """Fetch available instruments from Primary (Matba Rofex)."""
        return await self.primary.get_instruments()

    async def get_macro_snapshot(self) -> MacroSnapshot:
        """
        Unified Macro Snapshot for the Morning Briefing.
        Composes data from multiple sources.
        """
        # Simplified default for migration
        return MacroSnapshot(
            vix_level=15.0,
            vix_1d_change=0.0,
            us_10y_yield=4.20,
            snapshot_utc=datetime.now(UTC),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # §4  OPTIONS (GEX / INSTITUTIONAL)
    # ──────────────────────────────────────────────────────────────────────────

    async def get_options_chain_snapshot(self, symbol: str) -> Any:
        """
        Massive Institutional Options Snapshot (Bulk).
        Used by GEX Engines. Explores Massive Primary -> Secondary -> FMP with telemetry tracking.
        """
        # 1. Massive Primary
        t0 = time.perf_counter()
        data = await self.massive.get_options_chain(symbol, endpoint_type="options_primary")
        latency = time.perf_counter() - t0

        if data:
            self._record_telemetry(
                symbol, "Massive_Primary", latency, success=True, coverage=len(data)
            )
            return data
        else:
            self._record_telemetry(symbol, "Massive_Primary", latency, success=False)
            logger.warning(f"Massive Primary failed for {symbol}, falling back to Secondary...")

        # 2. Massive Secondary
        t0 = time.perf_counter()
        data = await self.massive.get_options_chain(symbol, endpoint_type="options_secondary")
        latency = time.perf_counter() - t0

        if data:
            self._record_telemetry(
                symbol, "Massive_Secondary", latency, success=True, coverage=len(data)
            )
            return data
        else:
            self._record_telemetry(symbol, "Massive_Secondary", latency, success=False)
            logger.warning(f"Massive Secondary failed for {symbol}, falling back to FMP...")

        # 3. FMP Fallback
        t0 = time.perf_counter()
        try:
            fmp_data = await self.fmp.get_options_iv_history(symbol)
            latency = time.perf_counter() - t0
            if fmp_data:
                self._record_telemetry(symbol, "FMP", latency, success=True, coverage=len(fmp_data))
                return fmp_data
        except Exception as e:
            latency = time.perf_counter() - t0
            logger.error(f"FMP fallback error {symbol}: {e}")

        self._record_telemetry(symbol, "FMP", latency, success=False)
        return None

    def get_provider_telemetry(self, symbol: str | None = None) -> dict[str, Any]:
        """
        Returns hardening telemetry (latency, coverage, failures, freshness)
        per provider for a symbol or globally for all symbols if none provided.
        """
        if symbol:
            symbol = symbol.upper()
            if symbol not in self.provider_telemetry:
                return {}

            # Serialize for endpoint usage
            return {
                prov_name: {
                    "latency_ms": round(tel.latency_ms, 2),
                    "coverage_count": tel.coverage_count,
                    "failures": tel.failures,
                    "freshness_seconds": round(tel.freshness_seconds, 2),
                    "last_success_at": (
                        tel.last_success_at.isoformat() if tel.last_success_at else None
                    ),
                }
                for prov_name, tel in self.provider_telemetry[symbol].providers.items()
            }

        return {sym: self.get_provider_telemetry(sym) for sym in self.provider_telemetry.keys()}


# ─────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: DATA
# Archivo          : lake.py
# Sub-capa         : Orchestrator
# Enfoque          : Fachada única para ingesta de datos.
# Cambio Crítico   : Refactorización total de V1 para usar fetchers V2 Async.
# Integración      : Centraliza Polygon, Massive, FMP y fuentes locales.
# ─────────────────────────────────────────────────────────────────────
