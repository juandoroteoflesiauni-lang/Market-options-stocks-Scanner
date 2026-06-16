from __future__ import annotations
"""Shared confluence enums — bidirectional (LONG/SHORT) signal contracts.

Single source of truth for ConfluenceAction, ConfluenceConviction, WyckoffFase,
SpotVsZGL, VSAVannaSignal. Previously duplicated across tecnico/ and opciones_gex/
confluence_models with Long-Only mandate. This module enables bidirectional
trading by adding SELL/SELL_WATCH/CONFLICT actions and HIGH_BULL/HIGH_BEAR
conviction levels.
"""


from enum import Enum


class ConfluenceAction(str, Enum):
    """Acciones direccionales del orquestador de confluencia.

    Bidireccional: emite tanto LONG (BUY/BUY_WATCH) como SHORT (SELL/SELL_WATCH).
    SELL_BLOCKED conservado por compatibilidad con consumidores legacy; nuevos
    consumidores deben usar SELL/SELL_WATCH directamente.
    """

    BUY = "BUY"  # score >= +0.50  → largo con convicción
    BUY_WATCH = "BUY_WATCH"  # score >= +0.25  → largo cauteloso
    MONITOR_BUY = "MONITOR_BUY"  # alias legacy de BUY_WATCH (preservado)
    WAIT = "WAIT"  # score ∈ (-0.25, +0.25) → neutral
    SELL_WATCH = "SELL_WATCH"  # score <= -0.25  → corto cauteloso
    SELL = "SELL"  # score <= -0.50  → corto con convicción
    SELL_BLOCKED = "SELL_BLOCKED"  # legacy: bloqueo histórico Long-Only
    CASH = "CASH"  # sin estructura legible
    CONFLICT = "CONFLICT"  # engines en contradicción (audit D3)


class ConfluenceConviction(str, Enum):
    """Niveles de convicción institucional, simétricos por dirección."""

    HIGH_BULL = "HIGH_BULL"  # score >= +0.75
    MEDIUM_BULL = "MEDIUM_BULL"  # score >= +0.50
    LOW_BULL = "LOW_BULL"  # score >= +0.25
    HIGH = "HIGH"  # alias legacy direccional-agnóstico
    MEDIUM = "MEDIUM"  # alias legacy
    LOW = "LOW"  # alias legacy
    NEUTRAL = "NEUTRAL"
    LOW_BEAR = "LOW_BEAR"  # score <= -0.25
    MEDIUM_BEAR = "MEDIUM_BEAR"  # score <= -0.50
    HIGH_BEAR = "HIGH_BEAR"  # score <= -0.75


class WyckoffFase(str, Enum):
    """Fases canónicas de la metodología Wyckoff."""

    ACUMULACION = "ACUMULACION"
    MARKUP = "MARKUP"
    DISTRIBUCION = "DISTRIBUCION"
    MARKDOWN = "MARKDOWN"
    RANGO = "RANGO"
    UNKNOWN = "UNKNOWN"


class SpotVsZGL(str, Enum):
    """Posición relativa del precio respecto al Zero Gamma Level."""

    ABOVE = "ABOVE"
    BELOW = "BELOW"
    AT = "AT"


class VSAVannaSignal(str, Enum):
    """Presión de flujo Vanna/Charm."""

    BUY_PRESSURE = "BUY_PRESSURE"
    SELL_PRESSURE = "SELL_PRESSURE"
    NEUTRAL = "NEUTRAL"


class SignalDirection(str, Enum):
    """Dirección explícita de señal (independiente de magnitud)."""

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    CASH = "CASH"
    CONFLICT = "CONFLICT"


__all__ = [
    "ConfluenceAction",
    "ConfluenceConviction",
    "SignalDirection",
    "SpotVsZGL",
    "VSAVannaSignal",
    "WyckoffFase",
]
