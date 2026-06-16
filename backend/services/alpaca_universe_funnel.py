"""Embudo cuantitativo de pre-filtro para acciones (Alpaca). # [PD-3][IM][TH]

Reduce el Universo Extendido (~1000) a las mejores 50-70 acciones mediante un
score compuesto: liquidez/volumen, ATR (volatilidad operable), Relative
Strength (vs. benchmark) y momentum MACD. Usa los helpers puros de
``backend.domain.volatility``; no contiene estado mutable global.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from backend.config.logger_setup import get_logger
from backend.domain.volatility import compute_atr, compute_macd, compute_relative_strength

logger = get_logger(__name__)

_MIN_BARS = 35


@dataclass(frozen=True)
class SymbolBars:
    """Series OHLCV de un símbolo para el embudo."""

    symbol: str
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]
    volumes: tuple[float, ...]


class FunnelConfig(BaseModel):
    """Pesos y umbrales del embudo (sin números mágicos dispersos)."""

    model_config = ConfigDict(frozen=True)

    top_n: int = 60
    min_avg_volume: float = 500_000.0
    min_atr_pct: float = 0.5
    max_atr_pct: float = 12.0
    weight_rs: float = 0.40
    weight_macd: float = 0.25
    weight_volume: float = 0.20
    weight_atr: float = 0.15


@dataclass(frozen=True)
class _Scored:
    symbol: str
    score: float


def _avg_volume(volumes: Sequence[float]) -> float:
    return sum(volumes) / len(volumes) if volumes else 0.0


def _atr_pct(atr: float, last_close: float) -> float:
    return (atr / last_close) * 100.0 if last_close > 0 else 0.0


def _passes_hard_filter(bars: SymbolBars, atr_pct: float, config: FunnelConfig) -> bool:
    """Filtro duro de liquidez y volatilidad operable."""
    if _avg_volume(bars.volumes) < config.min_avg_volume:
        return False
    return config.min_atr_pct <= atr_pct <= config.max_atr_pct


def _composite_score(
    rs: float, macd_hist: float, avg_vol: float, atr_pct: float, config: FunnelConfig
) -> float:
    """Score ponderado normalizado de las cuatro señales."""
    rs_norm = max(0.0, min(1.0, (rs + 10.0) / 20.0))
    macd_norm = 1.0 if macd_hist > 0.0 else 0.0
    vol_norm = max(0.0, min(1.0, avg_vol / (config.min_avg_volume * 20.0)))
    atr_norm = max(0.0, min(1.0, atr_pct / config.max_atr_pct))
    return (
        config.weight_rs * rs_norm
        + config.weight_macd * macd_norm
        + config.weight_volume * vol_norm
        + config.weight_atr * atr_norm
    )


def _score_symbol(
    bars: SymbolBars, benchmark_closes: Sequence[float], config: FunnelConfig
) -> _Scored | None:
    """Aplica filtro duro y calcula el score compuesto de un símbolo."""
    if len(bars.closes) < _MIN_BARS:
        return None
    atr = compute_atr(bars.highs, bars.lows, bars.closes)
    macd = compute_macd(bars.closes)
    rs = compute_relative_strength(bars.closes, benchmark_closes)
    if atr is None or macd is None or rs is None:
        return None
    atr_pct = _atr_pct(atr, bars.closes[-1])
    if not _passes_hard_filter(bars, atr_pct, config):
        return None
    score = _composite_score(rs, macd.histogram, _avg_volume(bars.volumes), atr_pct, config)
    return _Scored(symbol=bars.symbol, score=score)


def run_funnel(
    candidates: Sequence[SymbolBars],
    benchmark_closes: Sequence[float],
    config: FunnelConfig | None = None,
) -> list[str]:
    """Reduce los candidatos a las mejores ``top_n`` acciones por score."""
    cfg = config or FunnelConfig()
    scored = [
        s for s in (_score_symbol(bars, benchmark_closes, cfg) for bars in candidates) if s
    ]
    scored.sort(key=lambda s: s.score, reverse=True)
    selected = [s.symbol for s in scored[: cfg.top_n]]
    logger.info("alpaca_funnel.selected in=%d out=%d", len(candidates), len(selected))
    return selected
