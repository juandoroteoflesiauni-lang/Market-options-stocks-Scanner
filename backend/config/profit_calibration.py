"""Calibración Fase C — modo profit vs verificación. # [PD-8][TH]"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

SessionMode = Literal["verification", "profit"]

# Umbrales modo profit (más selectivos que verificación)
PROFIT_ALPACA_PROB_FLOOR: float = 0.55
PROFIT_ALPACA_MIN_VOLUME_Z: float = 0.65
PROFIT_ALPACA_MIN_CLOSE_POSITION: float = 0.55
PROFIT_BINGX_MIN_DECISION_SCORE: float = 0.55
PROFIT_BINGX_MIN_PREDICTIVE_CONF: float = 0.50
PROFIT_ALPACA_NOTIONAL_USD: float = 1_500.0
PROFIT_BINGX_NOTIONAL_USDT: float = 350.0
PROFIT_EXECUTION_COOLDOWN_MINUTES: float = 8.0
PROFIT_ROLLING_PF_WINDOW: int = 30
PROFIT_ROLLING_PF_MIN_SAMPLE: int = 10
PROFIT_ROLLING_PF_MIN: float = 1.15
VERIFICATION_ROLLING_PF_MIN: float = 0.85
PROFIT_KELLY_FRACTION: float = 0.25
PROFIT_KELLY_MIN_SCALAR: float = 0.35
PROFIT_KELLY_MAX_SCALAR: float = 1.0
PROFIT_KELLY_MIN_SAMPLE: int = 12


@dataclass(frozen=True)
class ProfitCalibrationPolicy:
    """Política de calibración rolling PF + Kelly fraccional."""

    session_mode: SessionMode = "verification"
    rolling_pf_enabled: bool = True
    rolling_pf_window: int = PROFIT_ROLLING_PF_WINDOW
    rolling_pf_min_sample: int = PROFIT_ROLLING_PF_MIN_SAMPLE
    rolling_pf_min_profit: float = PROFIT_ROLLING_PF_MIN
    rolling_pf_min_verification: float = VERIFICATION_ROLLING_PF_MIN
    kelly_enabled: bool = True
    kelly_fraction: float = PROFIT_KELLY_FRACTION
    kelly_min_scalar: float = PROFIT_KELLY_MIN_SCALAR
    kelly_max_scalar: float = PROFIT_KELLY_MAX_SCALAR
    kelly_min_sample: int = PROFIT_KELLY_MIN_SAMPLE
    journal_db_path: str = "data/quantum_analyzer.duckdb"

    @classmethod
    def from_env(cls) -> ProfitCalibrationPolicy:
        mode_raw = os.getenv("BOT_SESSION_MODE", "verification").strip().lower()
        mode: SessionMode = "profit" if mode_raw == "profit" else "verification"

        def _bool(name: str, default: bool) -> bool:
            raw = os.getenv(name, "").strip().lower()
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
            return default

        def _float(name: str, default: float) -> float:
            raw = os.getenv(name, "").strip()
            try:
                return float(raw)
            except (ValueError, TypeError):
                return default

        def _int(name: str, default: int) -> int:
            raw = os.getenv(name, "").strip()
            try:
                return max(1, int(raw))
            except (ValueError, TypeError):
                return default

        return cls(
            session_mode=mode,
            rolling_pf_enabled=_bool("PROFIT_ROLLING_PF_GATE_ENABLED", True),
            rolling_pf_window=_int("PROFIT_ROLLING_PF_WINDOW", PROFIT_ROLLING_PF_WINDOW),
            rolling_pf_min_sample=_int(
                "PROFIT_ROLLING_PF_MIN_SAMPLE", PROFIT_ROLLING_PF_MIN_SAMPLE
            ),
            rolling_pf_min_profit=_float("PROFIT_ROLLING_PF_MIN", PROFIT_ROLLING_PF_MIN),
            rolling_pf_min_verification=_float(
                "VERIFICATION_ROLLING_PF_MIN", VERIFICATION_ROLLING_PF_MIN
            ),
            kelly_enabled=_bool("PROFIT_KELLY_SIZING_ENABLED", mode == "profit"),
            kelly_fraction=_float("PROFIT_KELLY_FRACTION", PROFIT_KELLY_FRACTION),
            kelly_min_scalar=_float("PROFIT_KELLY_MIN_SCALAR", PROFIT_KELLY_MIN_SCALAR),
            kelly_max_scalar=_float("PROFIT_KELLY_MAX_SCALAR", PROFIT_KELLY_MAX_SCALAR),
            kelly_min_sample=_int("PROFIT_KELLY_MIN_SAMPLE", PROFIT_KELLY_MIN_SAMPLE),
            journal_db_path=os.getenv("TRADE_JOURNAL_DB", "data/quantum_analyzer.duckdb"),
        )

    @property
    def rolling_pf_floor(self) -> float:
        if self.session_mode == "profit":
            return self.rolling_pf_min_profit
        return self.rolling_pf_min_verification


def is_profit_mode() -> bool:
    return ProfitCalibrationPolicy.from_env().session_mode == "profit"


def profit_calibration_env_flags() -> dict[str, str]:
    """Env para modo profit — umbrales estrictos + PF gate + Kelly."""
    from backend.config.execution_policy import execution_phase_b_env_flags

    phase_b = execution_phase_b_env_flags()
    phase_b.update(
        {
            "EXECUTION_REPEATED_MAX_PER_SYMBOL": "3",
            "EXECUTION_PRICE_COLLAR_PCT": "0.005",
            "BOT_EXECUTION_COOLDOWN_MINUTES": str(PROFIT_EXECUTION_COOLDOWN_MINUTES),
        }
    )
    return {
        "BOT_SESSION_MODE": "profit",
        "ALPACA_MIN_VOLUME_Z": str(PROFIT_ALPACA_MIN_VOLUME_Z),
        "ALPACA_MIN_CLOSE_POSITION": str(PROFIT_ALPACA_MIN_CLOSE_POSITION),
        "ALPACA_PROB_FLOOR": str(PROFIT_ALPACA_PROB_FLOOR),
        "ALPACA_SIZE_DOWN_BAND": "0.08",
        "ALPACA_R2_MIN_SCORE": "48.0",
        "ALPACA_R2_GATE_VETO_THRESHOLD": "0.12",
        "ALPACA_R2_CONFLUENCE_MIN_ENGINES": "2",
        "BINGX_MIN_DECISION_SCORE": str(PROFIT_BINGX_MIN_DECISION_SCORE),
        "BINGX_MIN_PREDICTIVE_CONFIDENCE": str(PROFIT_BINGX_MIN_PREDICTIVE_CONF),
        "ALPACA_NOTIONAL_PER_TRADE_USD": str(PROFIT_ALPACA_NOTIONAL_USD),
        "BINGX_NOTIONAL_PER_TRADE_USDT": str(PROFIT_BINGX_NOTIONAL_USDT),
        "BINGX_USE_STATIC_NOTIONAL": "true",
        "PROFIT_ROLLING_PF_GATE_ENABLED": "true",
        "PROFIT_ROLLING_PF_WINDOW": str(PROFIT_ROLLING_PF_WINDOW),
        "PROFIT_ROLLING_PF_MIN_SAMPLE": str(PROFIT_ROLLING_PF_MIN_SAMPLE),
        "PROFIT_ROLLING_PF_MIN": str(PROFIT_ROLLING_PF_MIN),
        "PROFIT_KELLY_SIZING_ENABLED": "true",
        "PROFIT_KELLY_FRACTION": str(PROFIT_KELLY_FRACTION),
        "PROFIT_KELLY_MIN_SCALAR": str(PROFIT_KELLY_MIN_SCALAR),
        "PROFIT_KELLY_MAX_SCALAR": str(PROFIT_KELLY_MAX_SCALAR),
        "PROFIT_KELLY_MIN_SAMPLE": str(PROFIT_KELLY_MIN_SAMPLE),
        "OPTIONS_STRATEGY_MIN_GLOBAL_CONFIDENCE": "0.42",
        "ALPACA_PREDICTIVE_GATE_DISABLED": "false",
        **phase_b,
    }


__all__ = [
    "PROFIT_ALPACA_NOTIONAL_USD",
    "PROFIT_BINGX_NOTIONAL_USDT",
    "PROFIT_ROLLING_PF_MIN",
    "ProfitCalibrationPolicy",
    "SessionMode",
    "is_profit_mode",
    "profit_calibration_env_flags",
]
