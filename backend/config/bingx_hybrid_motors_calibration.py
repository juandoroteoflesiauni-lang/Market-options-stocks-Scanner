"""Calibration for hybrid motors in BingX institutional consensus."""

from __future__ import annotations

from backend.config.bingx_flow_desk_calibration import FLOW_MFI_FLOW_WEIGHT, FLOW_OBV_OI_WEIGHT

# Rebalanced venue weights (sum 0.76) — classic 16-engine stack.
# ofi shaved by 0.025 to fund the flow desk (see FLOW_DESK_WEIGHTS).
VENUE_ENGINE_WEIGHTS: dict[str, float] = {
    "hmm_regime": 0.10,
    "ofi": 0.060,
    "volume_profile": 0.07,
    "vwap_advanced": 0.07,
    "lob_dynamics": 0.07,
    "vsa": 0.06,
    "fvg": 0.06,
    "order_flow_delta": 0.05,
    "delta_volume": 0.04,
    "vpoc_migration": 0.03,
    "tpo_skewness": 0.02,
    "single_prints": 0.015,
    "vsa_footprint": 0.015,
    "avwap_m13": 0.03,
    "avwap_m14": 0.01,
    "avwap_m15": 0.01,
    "avwap_m16": 0.01,
    "avwap_m17": 0.01,
    "avwap_m18": 0.01,
}

# Hybrid motors (sum 0.24) — price + options confluence layer.
# hybrid_wavetrend shaved by 0.025 to fund the flow desk.
HYBRID_MOTOR_WEIGHTS: dict[str, float] = {
    "hybrid_wavetrend": 0.015,
    "hybrid_divergences": 0.035,
    "hybrid_vsa": 0.03,
    "hybrid_elliott": 0.03,
    "hybrid_exhaustion": 0.03,
    "hybrid_shadow_macd": 0.035,
    "hybrid_delta_profile": 0.035,
}

# Flow desk (sum 0.05) — OBV-OI + MFI-Flow confluence, funded by the shaves
# above so the overall matrix still sums to 1.0.
FLOW_DESK_WEIGHTS: dict[str, float] = {
    "flow_obv_oi": FLOW_OBV_OI_WEIGHT,
    "flow_mfi_flow": FLOW_MFI_FLOW_WEIGHT,
}

TECHNICAL_WEIGHT_MATRIX: dict[str, float] = {
    **VENUE_ENGINE_WEIGHTS,
    **HYBRID_MOTOR_WEIGHTS,
    **FLOW_DESK_WEIGHTS,
}

# Minimum bars to warm incremental hybrid engines on FMP 5m feed
HYBRID_MIN_BARS: int = 60
HYBRID_MAX_BARS: int = 300

# Divergence adapter window (minutes / bars)
DIVERGENCE_SINCE_BARS: int = 15
DIVERGENCE_MIN_SCORE: float = 35.0

# VSA hybrid minimum quality score (0-100)
VSA_HYBRID_MIN_SCORE: float = 40.0

# Consensus thresholds (verification mode — aligned with bot_relaxed_thresholds)
HYBRID_CONSENSUS_LONG: float = 0.55
HYBRID_CONSENSUS_SHORT: float = -0.55

# Signal strength floor for directional vote from hybrid block
HYBRID_MIN_STRENGTH_VOTE: int = 1
