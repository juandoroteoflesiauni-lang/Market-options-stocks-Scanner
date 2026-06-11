"""Lazy exports for probabilistic engines.

Some engines depend on optional ML stacks. Import concrete modules directly, or
request these names from the package when those optional dependencies are present.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "BacktestCallable": ".parametric_optimizer",
    "CMMath": ".cm_math",
    "CalibrationProfile": ".quantum_alpha",
    "MultimodalPredictiveEngine": ".multimodal_predictive",
    "OptimizationAnalyzer": ".parametric_optimizer",
    "ParameterSpaceExpander": ".parametric_optimizer",
    "ParametricOptimizerEngine": ".parametric_optimizer",
    "ParticleFilter": ".probabilistic_engine",
    "QuantumAlphaEngine": ".quantum_alpha",
    "QuantumAlphaLSTM": ".quantum_alpha",
    "SelfAttention": ".quantum_alpha",
    "SentimentEngine": ".sentiment_engine",
    "SentimentReputationEngine": ".sentiment_engine",
    "VSAForecastEngine": ".vsa_forecast_engine",
    "VSAForecastResult": ".vsa_forecast_engine",
    "apply_macro_anchoring": ".probabilistic_engine",
    "calculate_kelly_sizing": ".probabilistic_engine",
    "calculate_probabilistic_gex_gating": ".cm_math",
    "calibrate_heston_vov": ".probabilistic_engine",
    "compute_charm_price_bias": ".cm_math",
    "compute_etv": ".probabilistic_engine",
    "compute_vanna_vol_drift": ".cm_math",
    "estimate_mjd_params": ".probabilistic_engine",
    "estimate_payoff_ratio": ".probabilistic_engine",
    "fit_gpd": ".probabilistic_engine",
    "load_pretrained_weights": ".quantum_alpha",
    "particle_filter_volatility": ".probabilistic_engine",
    "project_trajectories": ".probabilistic_engine",
    "run_particle_filter": ".probabilistic_engine",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load an engine export only when requested."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports to introspection."""
    return sorted(set(globals()) | set(__all__))
