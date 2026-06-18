"""Calibration for hybrid motors in BingX institutional consensus."""

from __future__ import annotations

# Rebalanced venue weights (sum 0.76) — classic 16-engine stack
VENUE_ENGINE_WEIGHTS: dict[str, float] = {
    "hmm_regime": 0.10,
    "ofi": 0.085,
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

# Hybrid motors (sum 0.24) — price + options confluence layer
HYBRID_MOTOR_WEIGHTS: dict[str, float] = {
    "hybrid_wavetrend": 0.04,
    "hybrid_divergences": 0.035,
    "hybrid_vsa": 0.03,
    "hybrid_elliott": 0.03,
    "hybrid_exhaustion": 0.03,
    "hybrid_shadow_macd": 0.035,
    "hybrid_delta_profile": 0.035,
}

TECHNICAL_WEIGHT_MATRIX: dict[str, float] = {
    **VENUE_ENGINE_WEIGHTS,
    **HYBRID_MOTOR_WEIGHTS,
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
