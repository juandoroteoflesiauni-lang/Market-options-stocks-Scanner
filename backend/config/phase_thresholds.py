from __future__ import annotations
"""Singleton de configuración activa de pesos estratégicos del funnel.

Provee el punto único de acceso a los StrategyWeights actualmente activos.
Puede ser modificado en caliente vía API REST (POST /api/strategy/weights)
y es consultado por todas las fases del pipeline.
"""


import threading

from backend.models.strategy_weights import StrategyWeights

_lock = threading.RLock()
_active_weights: StrategyWeights = StrategyWeights.DEFAULT


def get_active_weights() -> StrategyWeights:
    """Retorna los pesos activos (thread-safe)."""
    with _lock:
        return _active_weights


def set_active_weights(weights: StrategyWeights) -> None:
    """Actualiza los pesos activos (thread-safe).

    Args:
        weights: Nueva configuración StrategyWeights validada.
    """
    global _active_weights
    with _lock:
        _active_weights = weights


def update_weight(path: str, value: float) -> bool:
    """Actualiza un peso individual por ruta de acceso (thread-safe).

    Args:
        path: Ruta punto-separada, ej. "phase_c.gex_score"
        value: Nuevo valor numérico.

    Returns:
        True si se actualizó, False si la ruta no existe.
    """
    current = get_active_weights()
    flat = current.to_flat_dict()
    if path not in flat:
        return False

    # Reconstruir desde flat dict con el nuevo valor
    flat[path] = value
    _rebuild_from_flat(flat)
    return True


def _rebuild_from_flat(flat: dict[str, float]) -> None:
    """Reconstruye StrategyWeights desde un dict plano."""
    from backend.models.strategy_weights import (
        PhaseAWeights,
        PhaseBWeights,
        PhaseCContractFilters,
        PhaseCContractScoreWeights,
        PhaseCEngineWeights,
        PhaseCWeights,
        PhaseDWeights,
        StrategyWeights,
    )

    new = StrategyWeights(
        phase_a=PhaseAWeights(
            phase_weight=flat.get("phase_a.phase_weight", 0.10),
            validation_strictness=flat.get("phase_a.validation_strictness", 0.85),
            min_price=flat.get("phase_a.min_price", 0.50),
            min_volume=int(flat.get("phase_a.min_volume", 10_000)),
            max_spread_pct=flat.get("phase_a.max_spread_pct", 0.20),
            ema_cluster_min_score=flat.get("phase_a.ema_cluster_min_score", 50.0),
            ema_cluster_min_aligned=int(flat.get("phase_a.ema_cluster_min_aligned", 3)),
            atr_gate_min_score=flat.get("phase_a.atr_gate_min_score", 50.0),
            min_atr_pct=flat.get("phase_a.min_atr_pct", 0.003),
            max_atr_pct=flat.get("phase_a.max_atr_pct", 0.05),
            rsi_extreme_min_score=flat.get("phase_a.rsi_extreme_min_score", 50.0),
            rsi_oversold_threshold=flat.get("phase_a.rsi_oversold_threshold", 15.0),
            rsi_overbought_threshold=flat.get("phase_a.rsi_overbought_threshold", 85.0),
            vwap_zscore_min_score=flat.get("phase_a.vwap_zscore_min_score", 50.0),
            vwap_max_zscore=flat.get("phase_a.vwap_max_zscore", 3.0),
            entropy_min_score=flat.get("phase_a.entropy_min_score", 50.0),
            max_entropy=flat.get("phase_a.max_entropy", 3.5),
            supertrend_min_score=flat.get("phase_a.supertrend_min_score", 50.0),
            supertrend_period=int(flat.get("phase_a.supertrend_period", 10)),
            supertrend_multiplier=flat.get("phase_a.supertrend_multiplier", 3.0),
            supertrend_max_changes=int(flat.get("phase_a.supertrend_max_changes", 2)),
        ),
        phase_b=PhaseBWeights(
            phase_weight=flat.get("phase_b.phase_weight", 0.25),
            ofi_weight=flat.get("phase_b.ofi_weight", 0.45),
            smc_weight=flat.get("phase_b.smc_weight", 0.35),
            vpin_weight=flat.get("phase_b.vpin_weight", 0.20),
            ofi_sensitivity=flat.get("phase_b.ofi_sensitivity", 1.0),
            smc_lookback_periods=int(flat.get("phase_b.smc_lookback_periods", 20)),
            vpin_buckets=int(flat.get("phase_b.vpin_buckets", 50)),
        ),
        phase_c=PhaseCWeights(
            phase_weight=flat.get("phase_c.phase_weight", 0.45),
            engine_weights=PhaseCEngineWeights(
                gex_score=flat.get("phase_c.gex_score", 0.20),
                gamma_flip=flat.get("phase_c.gamma_flip", 0.12),
                dex_exposure=flat.get("phase_c.dex_exposure", 0.15),
                flow_signal=flat.get("phase_c.flow_signal", 0.12),
                zero_day=flat.get("phase_c.zero_day", 0.10),
                shadow_delta=flat.get("phase_c.shadow_delta", 0.10),
                delta_flow=flat.get("phase_c.delta_flow", 0.08),
                phase_b_momentum=flat.get("phase_c.phase_b_momentum", 0.13),
            ),
            contract_score_weights=PhaseCContractScoreWeights(
                basic_metrics=flat.get("phase_c.basic_metrics_weight", 0.40),
                engine_average=flat.get("phase_c.engine_average_weight", 0.60),
            ),
            contract_filters=PhaseCContractFilters(
                min_volume=int(flat.get("phase_c.min_volume", 100)),
                min_open_interest=int(flat.get("phase_c.min_open_interest", 500)),
                max_spread_pct=flat.get("phase_c.max_spread_pct", 0.15),
                min_dte=int(flat.get("phase_c.min_dte", 14)),
                max_dte=int(flat.get("phase_c.max_dte", 60)),
                delta_target_call=flat.get("phase_c.delta_target_call", 0.35),
                delta_target_put=-abs(flat.get("phase_c.delta_target_call", 0.35)),
                min_composite_score=flat.get("phase_c.min_composite_score", 40.0),
                iv_min=flat.get("phase_c.iv_min", 0.10),
                iv_max=flat.get("phase_c.iv_max", 0.40),
            ),
            top_n_tickers=int(flat.get("phase_c.top_n_tickers", 5)),
            top_n_contracts=int(flat.get("phase_c.top_n_contracts", 5)),
        ),
        phase_d=PhaseDWeights(
            phase_weight=flat.get("phase_d.phase_weight", 0.20),
            momentum_weight=flat.get("phase_d.momentum_weight", 0.35),
            volatility_weight=flat.get("phase_d.volatility_weight", 0.25),
            volume_spike_weight=flat.get("phase_d.volume_spike_weight", 0.20),
            vwap_weight=flat.get("phase_d.vwap_weight", 0.10),
            phase_c_confluence_weight=flat.get("phase_d.phase_c_confluence_weight", 0.10),
            entry_momentum_threshold=flat.get("phase_d.entry_momentum_threshold", 0.003),
            volume_spike_multiplier=flat.get("phase_d.volume_spike_multiplier", 2.5),
            min_confidence=flat.get("phase_d.min_confidence", 0.60),
            cooldown_seconds=int(flat.get("phase_d.cooldown_seconds", 30)),
            stop_loss_pct=flat.get("phase_d.stop_loss_pct", 0.02),
            take_profit_pct=flat.get("phase_d.take_profit_pct", 0.04),
            momentum_window=int(flat.get("phase_d.momentum_window", 20)),
            volatility_window=int(flat.get("phase_d.volatility_window", 30)),
        ),
    )
    set_active_weights(new)


def reset_to_defaults() -> None:
    """Resetea todos los pesos a los valores por defecto."""
    global _active_weights
    with _lock:
        _active_weights = StrategyWeights.DEFAULT
