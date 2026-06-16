"""Constantes y estado del bot Alpaca (equities). # [PD-2][IM][TH]

Sin imports de BingX, sin ``import *``. Los modelos de dominio (decisión,
intent, riesgo, ciclo) viven en ``backend.domain.alpaca_models``.
"""

from __future__ import annotations

import os

from dataclasses import dataclass

# ─── Universo reducido (núcleo de alta liquidez) ──────────────────────────────
REDUCED_UNIVERSE: tuple[str, ...] = (
    "MSFT",
    "AAPL",
    "GOOGL",
    "META",
    "SPY",
    "QQQ",
    "TSLA",
    "NVDA",
    "PLTR",
    "AMZN",
)

# Benchmark para Relative Strength.
BENCHMARK_SYMBOL: str = "SPY"

# ─── Defaults de escaneo (acciones) ───────────────────────────────────────────
DEFAULT_HORIZON: str = "1h"
DEFAULT_SCAN_INTERVAL: str = "5m"
DEFAULT_KLINES_PER_SYMBOL: int = 500
DEFAULT_MIN_BARS_FOR_SIGNAL: int = 40
DEFAULT_VOLUME_Z_THRESHOLD: float = 2.0
DEFAULT_FUNNEL_TOP_N: int = 60
DEFAULT_FUNNEL_MIN_AVG_VOLUME_5M: float = 50_000.0
DEFAULT_FUNNEL_MIN_ATR_PCT_5M: float = 0.05
DEFAULT_PREFILTER_POOL_SIZE: int = 100
DEFAULT_GATHER_CONCURRENCY: int = 8
EXECUTION_COOLDOWN_MINUTES: float = float(
    os.getenv("BOT_EXECUTION_COOLDOWN_MINUTES", "15.0")
)

# Posición en el rango para sesgo alcista (VSA).
LONG_RANGE_THRESHOLD: float = 0.60
RANGE_LOOKBACK_BARS: int = 20

# ─── Ladder de salida paramétrica ─────────────────────────────────────────────
PARAMETRIC_TP_TRIGGER_PCT: float = 3.0
PARAMETRIC_TP_STEP_PCT: float = 0.5
PARAMETRIC_HALF_EXIT_RATIO: float = 0.50


@dataclass
class _ParametricExitState:
    """Estado por símbolo del ladder de toma parcial de ganancias."""

    initial_size: float
    half_tp_done: bool = False
    last_adaptive_milestone: int = 0
