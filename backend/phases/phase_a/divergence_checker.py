from __future__ import annotations
from typing import Any
"""Divergence Checker — detección de divergencia multi-timeframe para Phase A.

Evalúa si la dirección del precio en 15m contradice la tendencia primaria
diaria (VETO_COMPLETE_CONTRADICTION). Sin esta inercia direccional el ticker
se descarta antes de los filtros pesados.

Resoluciones usadas:
  - 15m: últimas 15 velas de 1min agregadas → dirección short-term
  - 1D:  daily_change_pct desde el quote FMP (sin costo extra)
"""


import logging

from backend.hub.market_data_hub import MarketDataHub
from backend.models.hard_veto import HardVetoResult, VetoType
from backend.models.market_snapshot import MarketSnapshot

logger = logging.getLogger(__name__)

# Umbral: si la dirección 15m es opuesta a la dirección 1D → veto
_CONTRADICTION_BARS = 15

# Mínimo cambio porcentual diario para considerar una tendencia definida
_MIN_DAILY_TREND_PCT = 0.5

# Fracción mínima de velas 1min en la misma dirección para definir la tendencia 15m
_MIN_SHORT_TERM_CONSENSUS = 0.66


def _resolve_short_term_direction(bars: list[dict[str, Any]]) -> str | None:
    """Determina la dirección de los últimos ~15 minutos.

    Toma las últimas N velas de 1min y calcula cuántas son alcistas/bajistas
    basado en close > open (alcista) o close < open (bajista).

    Returns:
        "BULL", "BEAR", o None si no hay consenso.
    """
    if len(bars) < 2:
        return None

    relevant = bars[-_CONTRADICTION_BARS:] if len(bars) >= _CONTRADICTION_BARS else bars
    bullish = sum(1 for b in relevant if b.get("close", 0) > b.get("open", 0))
    bearish = len(relevant) - bullish

    total = len(relevant)
    if total == 0:
        return None

    bull_ratio = bullish / total
    if bull_ratio >= _MIN_SHORT_TERM_CONSENSUS:
        return "BULL"
    if bearish / total >= _MIN_SHORT_TERM_CONSENSUS:
        return "BEAR"
    return None


def _resolve_daily_direction(daily_change_pct: float) -> str | None:
    """Determina la dirección primaria basada en el cambio diario.

    Returns:
        "BULL" si el cambio es positivo y supera el umbral,
        "BEAR" si es negativo,
        None si el cambio es neutro.
    """
    if daily_change_pct >= _MIN_DAILY_TREND_PCT:
        return "BULL"
    if daily_change_pct <= -_MIN_DAILY_TREND_PCT:
        return "BEAR"
    return None


class DivergenceChecker:
    """Chequeo de divergencia multi-timeframe para un ticker.

    Uso:
        result = await DivergenceChecker.check(hub, snapshot)
        if result.vetoed:
            # ticker descartado por contradicción direccional
    """

    @staticmethod
    async def check(
        hub: MarketDataHub,
        snapshot: MarketSnapshot,
    ) -> HardVetoResult:
        """Evalúa si hay contradicción entre tendencia 15m y 1D.

        Si el snapshot tiene daily_change_pct suficiente para definir una
        tendencia diaria, y la dirección de las últimas velas 1min es
        opuesta, se emite VETO_COMPLETE_CONTRADICTION.

        Si no hay suficientes datos o la tendencia es neutra, se retorna
        HardVetoResult.passed() (no veto).
        """
        ticker = snapshot.ticker

        # ── Dirección diaria (sin costo — ya viene en el snapshot) ──────
        daily_dir = _resolve_daily_direction(snapshot.daily_change_pct)
        if daily_dir is None:
            # Cambio diario muy pequeño o neutro — no hay tendencia clara
            return HardVetoResult.passed()

        # ── Dirección 15m (1 call a FMP por ticker) ─────────────────────
        candles_result = await hub.get_intraday_candles(ticker, limit=_CONTRADICTION_BARS + 5)
        if candles_result.is_failure:
            logger.debug(
                "DivergenceChecker: intraday fetch failed for %s — %s",
                ticker,
                candles_result.reason,
            )
            return HardVetoResult.passed()

        short_dir = _resolve_short_term_direction(candles_result.unwrap())
        if short_dir is None:
            return HardVetoResult.passed()

        # ── ¿Contradicción? ─────────────────────────────────────────────
        if short_dir != daily_dir:
            return HardVetoResult.veto(
                VetoType.VETO_COMPLETE_CONTRADICTION,
                f"{ticker}: daily={daily_dir} ({snapshot.daily_change_pct:+.2f}%) "
                f"vs 15m={short_dir} — direcciones opuestas",
            )

        return HardVetoResult.passed()
