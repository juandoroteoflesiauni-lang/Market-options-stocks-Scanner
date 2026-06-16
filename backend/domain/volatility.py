"""Capa de dominio genérica de volatilidad e indicadores. # [PD-2][TH][IM]

Helpers puros y agnósticos de mercado (ATR, MACD, Relative Strength) que
reutilizan el núcleo vectorizado ``TechnicalMath``. No contienen lógica de
ejecución, riesgo ni ruteo: son compartibles por cualquier módulo (Scanner,
BingX, Alpaca) sin acoplar fronteras.

Cada función devuelve el valor *más reciente* (escalar) o ``None`` cuando la
serie es demasiado corta, para alimentar scores cuantitativos aguas arriba.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.quant_engine.math.technical.technical import TechnicalMath

# ─── Constantes (sin números mágicos) ────────────────────────────────────────
DEFAULT_ATR_PERIOD: int = 14
DEFAULT_MACD_FAST: int = 12
DEFAULT_MACD_SLOW: int = 26
DEFAULT_MACD_SIGNAL: int = 9
DEFAULT_RS_LOOKBACK: int = 20
_MIN_RS_BARS: int = 2


@dataclass(frozen=True)
class MacdResult:
    """Valores más recientes de la tripleta MACD."""

    macd: float
    signal: float
    histogram: float


def _last_finite(values: np.ndarray[Any, Any]) -> float | None:
    """Devuelve el último valor finito del array, o ``None`` si no hay."""
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return float(finite[-1])


def compute_atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = DEFAULT_ATR_PERIOD,
) -> float | None:
    """ATR de Wilder (último valor). ``None`` si faltan barras."""
    if len(closes) <= period or not (len(highs) == len(lows) == len(closes)):
        return None
    atr_series = TechnicalMath.atr(
        np.asarray(closes, dtype=np.float64),
        np.asarray(highs, dtype=np.float64),
        np.asarray(lows, dtype=np.float64),
        period,
    )
    return _last_finite(atr_series)


def compute_macd(
    closes: Sequence[float],
    fast: int = DEFAULT_MACD_FAST,
    slow: int = DEFAULT_MACD_SLOW,
    signal: int = DEFAULT_MACD_SIGNAL,
) -> MacdResult | None:
    """MACD (línea, señal, histograma) más reciente. ``None`` si es corta."""
    if len(closes) < slow + signal:
        return None
    macd_line, signal_line, histogram = TechnicalMath.macd(
        np.asarray(closes, dtype=np.float64), fast, slow, signal
    )
    macd_v = _last_finite(macd_line)
    signal_v = _last_finite(signal_line)
    hist_v = _last_finite(histogram)
    if macd_v is None or signal_v is None or hist_v is None:
        return None
    return MacdResult(macd=macd_v, signal=signal_v, histogram=hist_v)


def compute_relative_strength(
    symbol_closes: Sequence[float],
    benchmark_closes: Sequence[float],
    lookback: int = DEFAULT_RS_LOOKBACK,
) -> float | None:
    """Fuerza relativa: exceso de retorno del símbolo vs. benchmark (%).

    Positivo = el símbolo supera al benchmark en la ventana ``lookback``.
    """
    window = min(lookback, len(symbol_closes) - 1, len(benchmark_closes) - 1)
    if window < _MIN_RS_BARS:
        return None
    sym_now, sym_past = symbol_closes[-1], symbol_closes[-1 - window]
    bench_now, bench_past = benchmark_closes[-1], benchmark_closes[-1 - window]
    if sym_past <= 0 or bench_past <= 0:
        return None
    sym_return = (sym_now / sym_past) - 1.0
    bench_return = (bench_now / bench_past) - 1.0
    return float((sym_return - bench_return) * 100.0)
