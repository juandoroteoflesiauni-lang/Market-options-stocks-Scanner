"""Re-export CM kernels from Layer 2 (single source of truth for GEX/DAGEX/Kelly)."""

from __future__ import annotations

from backend.layer_2_quant_engine.math_core.cm_math import (
    CMMath,
    calculate_probabilistic_gex_gating,
    compute_charm_price_bias,
    compute_vanna_vol_drift,
)

__all__ = [
    "CMMath",
    "calculate_probabilistic_gex_gating",
    "compute_charm_price_bias",
    "compute_vanna_vol_drift",
]
