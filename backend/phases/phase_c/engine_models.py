"""Shared models for Phase C engines.

This module exists to break the circular import between
derivatives_engine.py and scoring.py.
"""

from __future__ import annotations

from typing import Any


class QuantEngineResults:
    """Contenedor de resultados de los motores de backend.quant_engine."""

    __slots__ = (
        "delta_flow_snapshot",
        "dex_report",
        "flow_signal",
        "gamma_flip_report",
        "options_result",
        "shadow_delta_report",
        "zero_day_report",
    )

    def __init__(self) -> None:
        self.options_result: Any = None
        self.gamma_flip_report: Any = None
        self.dex_report: Any = None
        self.flow_signal: Any = None
        self.zero_day_report: Any = None
        self.shadow_delta_report: Any = None
        self.delta_flow_snapshot: Any = None
