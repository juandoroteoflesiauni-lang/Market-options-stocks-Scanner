"""Universo fijo compartido Alpaca + BingX — 20 tickers curados. # [PD-8][TH][IM]

Cuando ``DUAL_BOT_FIXED_UNIVERSE=true`` ambos bots operan solo sobre esta lista,
con stack cuantitativo completo (técnico + predictivo + opciones/GEX) precalentado.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Liquidez options + perp BingX verificada; sin GME/MCD/SPX directo ni símbolos raros.
DUAL_BOT_CORE_UNIVERSE: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "SPY",
    "QQQ",
    "AMD",
    "NFLX",
    "COIN",
    "PLTR",
    "HOOD",
    "INTC",
    "IREN",
    "CRWV",
    "MU",
    "AVGO",
    "JPM",
)

DUAL_BOT_CORE_UNIVERSE_SET: frozenset[str] = frozenset(DUAL_BOT_CORE_UNIVERSE)

_DEFAULT_GEX_WARMUP_CONCURRENCY = 3


def dual_bot_fixed_universe_enabled() -> bool:
    """True cuando ambos bots deben usar solo ``DUAL_BOT_CORE_UNIVERSE``."""
    raw = os.getenv("DUAL_BOT_FIXED_UNIVERSE", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def dual_bot_route2_enabled() -> bool:
    """Ruta 2 Alpaca (scan dinámico). Off por defecto con universo fijo."""
    raw = os.getenv("DUAL_BOT_ROUTE2_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def dual_bot_analysis_concurrency() -> int:
    """Paralelismo de análisis por ciclo (evita timeouts CPU en motores pesados)."""
    raw = os.getenv("DUAL_BOT_ANALYSIS_CONCURRENCY", "4").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def resolve_active_equity_universe() -> tuple[str, ...]:
    """Universo equity activo para Alpaca y warmup."""
    if dual_bot_fixed_universe_enabled():
        return DUAL_BOT_CORE_UNIVERSE
    from backend.config.settings import load_settings

    return tuple(load_settings().default_universe)


def core_to_bingx_venue_symbol(ticker: str) -> str:
    """Mapea ticker equity → símbolo venue BingX (respeta aliases como AMD→AMDUS)."""
    from backend.services.bingx_symbol_linker import equity_to_bingx_venue_symbol

    return equity_to_bingx_venue_symbol(ticker)


def core_bingx_venue_symbols() -> tuple[str, ...]:
    """Los 20 símbolos BingX en formato venue."""
    return tuple(core_to_bingx_venue_symbol(t) for t in DUAL_BOT_CORE_UNIVERSE)


def core_symbol_has_full_quant(symbol: str) -> bool:
    """True si el símbolo pertenece al core y debe recibir stack quant completo."""
    if not dual_bot_fixed_universe_enabled():
        return False
    from backend.config.shared_options_tier_policy import normalize_equity_root

    root = normalize_equity_root(symbol)
    return bool(root) and root in DUAL_BOT_CORE_UNIVERSE_SET


def dual_bot_core_env_flags() -> dict[str, str]:
    """Variables de entorno: universo fijo + motores técnicos/predictivos/opciones ON."""
    return {
        "DUAL_BOT_FIXED_UNIVERSE": "true",
        "DUAL_BOT_ROUTE2_ENABLED": "false",
        "DUAL_BOT_ANALYSIS_CONCURRENCY": "4",
        "BINGX_PRIORITY_STOCKS": ",".join(DUAL_BOT_CORE_UNIVERSE),
        "OPTIONS_GEX_INSTITUTIONAL_CAPTURE_ENABLED": "true",
        "SHARED_OPTIONS_TIER_ENABLED": "true",
        "BINGX_SKIP_OPTIONS_SNAPSHOT": "false",
        "BINGX_REQUIRE_FMP_FOR_STOCKS": "false",
        "ALPACA_PREDICTIVE_GATE_DISABLED": "false",
        "DATA_ENABLE_COMPOSITE_PRICE_REPO": "true",
        "TECHNICAL_ENABLE_VOLUME_ENGINES": "true",
        "TECHNICAL_ENABLE_FVG_ENGINE": "true",
        "TECHNICAL_ENABLE_STRUCTURE_ENGINES": "true",
        "TECHNICAL_ENABLE_ORDER_FLOW_DELTA": "true",
        "TECHNICAL_ENABLE_LOB_DYNAMICS": "true",
        "TECHNICAL_ENABLE_HMM_ENGINE": "true",
        "TECHNICAL_ENABLE_FOOTPRINT_ENGINE": "true",
        "TECHNICAL_CPU_TIMEOUT_SEC": "20",
        "MARKET_SCANNER_INSTITUTIONAL_SCORING": "true",
        "BINGX_OMIT_REDUCE_ONLY": "true",
    }


async def warmup_core_quant_stack(
    symbols: tuple[str, ...] | None = None,
    *,
    gex_concurrency: int = _DEFAULT_GEX_WARMUP_CONCURRENCY,
    risk_free_rate: float = 0.04,
) -> dict[str, Any]:
    """Precalienta GEX/options + intraday cache para el universo core."""
    target = symbols or DUAL_BOT_CORE_UNIVERSE
    gex_stats = await warmup_core_gex_snapshots(
        target,
        concurrency=gex_concurrency,
        risk_free_rate=risk_free_rate,
    )
    cache_stats: dict[str, int] = {"ok": 0, "failed": 0, "skipped": len(target)}
    try:
        from backend.bus.event_bus import EventBus
        from backend.config.settings import load_settings
        from backend.hub.market_data_hub import MarketDataHub
        from backend.hub.warmup import CacheWarmUp

        settings = load_settings()
        hub = MarketDataHub(settings=settings, event_bus=EventBus())
        cache_stats = await CacheWarmUp.warm(
            hub,
            list(target),
            periods=60,
            interval="1min",
            concurrency=3,
        )
    except Exception as exc:
        logger.warning("dual_bot_core.intraday_warmup_failed error=%s", exc)
    return {"gex": gex_stats, "intraday_cache": cache_stats}


async def warmup_core_gex_snapshots(
    symbols: tuple[str, ...] | None = None,
    *,
    concurrency: int = _DEFAULT_GEX_WARMUP_CONCURRENCY,
    risk_free_rate: float = 0.04,
) -> dict[str, int]:
    """Precalienta snapshots GEX/options para el universo core al arranque."""
    from backend.api.routes.options_router import options_snapshot_service

    target = symbols or DUAL_BOT_CORE_UNIVERSE
    if not target:
        return {"ok": 0, "failed": 0, "skipped": 0}

    sem = asyncio.Semaphore(max(1, concurrency))
    ok = 0
    failed = 0

    async def _one(symbol: str) -> None:
        nonlocal ok, failed
        async with sem:
            try:
                await options_snapshot_service(symbol, None, risk_free_rate)
                ok += 1
            except Exception as exc:
                failed += 1
                logger.warning("dual_bot_core.gex_warmup_failed symbol=%s error=%s", symbol, exc)

    await asyncio.gather(*[_one(sym) for sym in target])
    logger.info(
        "dual_bot_core.gex_warmup_complete ok=%d failed=%d total=%d",
        ok,
        failed,
        len(target),
    )
    return {"ok": ok, "failed": failed, "skipped": 0}


__all__ = [
    "DUAL_BOT_CORE_UNIVERSE",
    "DUAL_BOT_CORE_UNIVERSE_SET",
    "core_bingx_venue_symbols",
    "core_symbol_has_full_quant",
    "core_to_bingx_venue_symbol",
    "dual_bot_analysis_concurrency",
    "dual_bot_core_env_flags",
    "dual_bot_fixed_universe_enabled",
    "dual_bot_route2_enabled",
    "resolve_active_equity_universe",
    "warmup_core_gex_snapshots",
    "warmup_core_quant_stack",
]
