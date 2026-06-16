from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import numpy as np
import pandas as pd

# MIGRATION: Dependencia de dominio interna
from ..domain.strategy_kpi_models import StrategyKPIResult, SystemHealthStatus

# ─────────────────────────────────────────────────────────────────────────────
# MIGRATION : Constantes Institucionales (Thresholds de Salud)
# ─────────────────────────────────────────────────────────────────────────────
KPI_MIN_TRADES_FOR_SIGNIFICANCE: Final[int] = 30
KPI_PROFIT_FACTOR_CRITICAL: Final[float] = 0.8
KPI_PROFIT_FACTOR_DEGRADED: Final[float] = 1.2
KPI_WIN_RATE_CRITICAL: Final[float] = 0.35
KPI_WIN_RATE_DEGRADED: Final[float] = 0.45
KPI_SLIPPAGE_DEGRADED_BPS: Final[float] = 5.0
KPI_SIGNAL_DECAY_WARN_MS: Final[float] = 500.0

_NS_TO_MS: Final[float] = 1_000_000.0
_BPS_FACTOR: Final[float] = 10_000.0


class StrategyKPIEngine:
    """Stateless evaluator of execution-health KPIs from trade history."""

    _REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
        {
            "trade_id",
            "pnl",
            "gross_pnl",
            "signal_price",
            "execution_price",
            "signal_timestamp_ns",
            "execution_timestamp_ns",
            "kelly_fraction_deployed",
            "total_capital",
        }
    )

    @staticmethod
    def evaluate_system_health(trades_history: pd.DataFrame) -> StrategyKPIResult | None:
        """Compute operational KPIs and return an immutable diagnostics envelope."""
        try:
            if not isinstance(trades_history, pd.DataFrame):
                return None

            missing_columns = StrategyKPIEngine._REQUIRED_COLUMNS - set(trades_history.columns)
            if missing_columns:
                return None

            df = trades_history.copy()
            if len(df) == 0:
                return None

            numeric_float_cols = (
                "pnl",
                "gross_pnl",
                "signal_price",
                "execution_price",
                "kelly_fraction_deployed",
                "total_capital",
            )
            numeric_int_cols = ("signal_timestamp_ns", "execution_timestamp_ns")

            for col in numeric_float_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(np.float64)
            for col in numeric_int_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(np.int64)

            n_trades = int(len(df))
            is_significant = n_trades >= KPI_MIN_TRADES_FOR_SIGNIFICANCE

            win_rate = StrategyKPIEngine._compute_win_rate(df)
            profit_factor = StrategyKPIEngine._compute_profit_factor(df)
            avg_slippage_bps = StrategyKPIEngine._compute_avg_slippage_bps(df)
            capital_utilization = StrategyKPIEngine._compute_capital_utilization(df)
            signal_decay_ms = StrategyKPIEngine._compute_signal_decay_ms(df)

            health_status, notes = StrategyKPIEngine._classify_system_health(
                win_rate=win_rate,
                profit_factor=profit_factor,
                avg_slippage_bps=avg_slippage_bps,
                signal_decay_ms=signal_decay_ms,
                is_significant=is_significant,
                n_trades=n_trades,
            )

            return StrategyKPIResult(
                kpi_win_rate=win_rate,
                kpi_profit_factor=profit_factor,
                kpi_avg_slippage_bps=avg_slippage_bps,
                kpi_capital_utilization=capital_utilization,
                kpi_signal_decay_ms=signal_decay_ms,
                system_health_status=health_status,
                is_statistically_significant=is_significant,
                trade_sample_size=n_trades,
                evaluation_timestamp=datetime.now(tz=UTC),
                diagnostic_notes=tuple(notes),
            )
        except Exception:
            return None

    @staticmethod
    def _compute_win_rate(df: pd.DataFrame) -> float:
        winning_mask = df["pnl"] > 0.0
        return float(winning_mask.mean())

    @staticmethod
    def _compute_profit_factor(df: pd.DataFrame) -> float:
        gross_pnl = df["gross_pnl"]
        gains_mask = gross_pnl > 0.0
        losses_mask = gross_pnl < 0.0

        total_gains = float(gross_pnl[gains_mask].sum())
        total_losses = float(gross_pnl[losses_mask].abs().sum())

        if total_losses == 0.0:
            return float("inf") if total_gains > 0.0 else 1.0
        if total_gains == 0.0:
            return 0.0
        return total_gains / total_losses

    @staticmethod
    def _compute_avg_slippage_bps(df: pd.DataFrame) -> float:
        signal_px = df["signal_price"]
        execution_px = df["execution_price"]
        valid_mask = signal_px != 0.0
        if not bool(valid_mask.any()):
            return 0.0

        slippage_bps = (
            (execution_px[valid_mask] - signal_px[valid_mask]) / signal_px[valid_mask] * _BPS_FACTOR
        )
        return float(slippage_bps.mean())

    @staticmethod
    def _compute_capital_utilization(df: pd.DataFrame) -> float:
        total_capital = df["total_capital"]
        valid_mask = total_capital > 0.0
        if not bool(valid_mask.any()):
            return 0.0
        utilization = df["kelly_fraction_deployed"][valid_mask]
        return float(utilization.mean())

    @staticmethod
    def _compute_signal_decay_ms(df: pd.DataFrame) -> float:
        execution_ts = df["execution_timestamp_ns"].astype(np.int64)
        signal_ts = df["signal_timestamp_ns"].astype(np.int64)
        decay_ns = execution_ts - signal_ts
        decay_ms = decay_ns / _NS_TO_MS
        return float(decay_ms.mean())

    @staticmethod
    def _classify_system_health(
        *,
        win_rate: float,
        profit_factor: float,
        avg_slippage_bps: float,
        signal_decay_ms: float,
        is_significant: bool,
        n_trades: int,
    ) -> tuple[SystemHealthStatus, list[str]]:
        notes: list[str] = []
        health = SystemHealthStatus.OPTIMAL

        pf_critical = not np.isinf(profit_factor) and profit_factor < KPI_PROFIT_FACTOR_CRITICAL
        wr_critical = win_rate < KPI_WIN_RATE_CRITICAL
        slippage_extreme_threshold = KPI_SLIPPAGE_DEGRADED_BPS * 3.0

        if (pf_critical and wr_critical) or (avg_slippage_bps > slippage_extreme_threshold):
            health = SystemHealthStatus.CRITICAL_FAILURE
            notes.append(
                "CRITICAL_FAILURE: destructive PF/WR combination or extreme slippage detected."
            )
        else:
            if avg_slippage_bps > KPI_SLIPPAGE_DEGRADED_BPS:
                health = SystemHealthStatus.DEGRADED
                notes.append("DEGRADED: average slippage exceeds warning threshold.")

            if (
                not np.isinf(profit_factor)
                and KPI_PROFIT_FACTOR_CRITICAL <= profit_factor < KPI_PROFIT_FACTOR_DEGRADED
            ):
                health = SystemHealthStatus.DEGRADED
                notes.append("DEGRADED: profit factor in warning zone.")

            if KPI_WIN_RATE_CRITICAL <= win_rate < KPI_WIN_RATE_DEGRADED:
                health = SystemHealthStatus.DEGRADED
                notes.append("DEGRADED: win rate in warning zone.")

        if signal_decay_ms > KPI_SIGNAL_DECAY_WARN_MS:
            notes.append("INFO: signal-to-execution latency is elevated.")

        if not is_significant:
            notes.append(f"INFO: low sample size ({n_trades} < {KPI_MIN_TRADES_FOR_SIGNIFICANCE}).")

        if health == SystemHealthStatus.OPTIMAL and not notes:
            notes.append("OPTIMAL: all operational KPIs are within calibrated ranges.")

        return health, notes


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : strategy_kpi.py
# Sub-capa        : Engines
# Solver/Optimizer: N/A
# Eliminado       : Import de quantumbeta constants (hardcoded thresholds).
# Preservado      : Lógica de diagnóstico de salud de algoritmos (Stateless).
# Pendientes      : Pruebas de integración con orquestador de backtest.
# ────────────────────────────────────────────────────────────────────
