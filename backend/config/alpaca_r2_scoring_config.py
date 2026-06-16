"""Configuración del scoring técnico L1 para Ruta 2 Alpaca. # [PD-8][IM]"""

from __future__ import annotations

import os

# Motores L1 elegibles (excluye lob_dynamics — requiere L2 real).
R2_L1_ENGINE_KEYS: tuple[str, ...] = (
    "candle_geometry",
    "market_structure",
    "order_flow_delta",
    "vsa",
    "volume_profile",
    "volume_nodes",
    "vwap_advanced",
    "delta_volume",
    "vpoc_migration",
    "ofi",
    "fvg",
    "tpo_skewness",
    "single_prints",
    "hmm_regime",
    "vsa_footprint",
)

R2_CONFLUENCE_CORE_ENGINES: tuple[str, ...] = (
    "market_structure",
    "fvg",
    "vsa",
    "tpo_skewness",
    "volume_profile",
    "hmm_regime",
)

R2_VOLUME_GATE_ENGINES: tuple[str, ...] = ("vsa", "volume_profile", "ofi")
R2_STRUCTURE_GATE_ENGINES: tuple[str, ...] = (
    "market_structure",
    "fvg",
    "single_prints",
    "candle_geometry",
)

R2_CONFLUENCE_MIN_ENGINES: int = int(os.getenv("ALPACA_R2_CONFLUENCE_MIN_ENGINES", "4"))
R2_MIN_SCORE: float = float(os.getenv("ALPACA_R2_MIN_SCORE", "65"))
R2_HMM_BULLISH_ONLY: bool = os.getenv("ALPACA_R2_HMM_BULLISH_ONLY", "true").lower() in {
    "1",
    "true",
    "yes",
}
R2_VSA_VOLUME_GATE: bool = os.getenv("ALPACA_R2_VSA_VOLUME_GATE", "true").lower() in {
    "1",
    "true",
    "yes",
}
R2_GATE_VETO_THRESHOLD: float = float(os.getenv("ALPACA_R2_GATE_VETO_THRESHOLD", "0.3"))
R2_CLASSIC_WEIGHT: float = float(os.getenv("ALPACA_R2_CLASSIC_WEIGHT", "0.6"))
R2_TECH_WEIGHT: float = float(os.getenv("ALPACA_R2_TECH_WEIGHT", "0.4"))
R2_S1_MIN_ENGINES: int = int(os.getenv("ALPACA_R2_S1_MIN_ENGINES", "2"))
R2_S2_MIN_ENGINES: int = int(os.getenv("ALPACA_R2_S2_MIN_ENGINES", "4"))
R2_S3_MIN_ENGINES: int = int(os.getenv("ALPACA_R2_S3_MIN_ENGINES", "6"))


def r2_confluence_min_engines() -> int:
    return int(os.getenv("ALPACA_R2_CONFLUENCE_MIN_ENGINES", str(R2_CONFLUENCE_MIN_ENGINES)))


def r2_min_score() -> float:
    return float(os.getenv("ALPACA_R2_MIN_SCORE", str(R2_MIN_SCORE)))


def r2_gate_veto_threshold() -> float:
    return float(os.getenv("ALPACA_R2_GATE_VETO_THRESHOLD", str(R2_GATE_VETO_THRESHOLD)))


def r2_hmm_bullish_only() -> bool:
    return os.getenv("ALPACA_R2_HMM_BULLISH_ONLY", "true").lower() in {"1", "true", "yes"}


def r2_vsa_volume_gate() -> bool:
    return os.getenv("ALPACA_R2_VSA_VOLUME_GATE", "true").lower() in {"1", "true", "yes"}

__all__ = [
    "R2_CLASSIC_WEIGHT",
    "R2_CONFLUENCE_CORE_ENGINES",
    "R2_CONFLUENCE_MIN_ENGINES",
    "R2_GATE_VETO_THRESHOLD",
    "R2_HMM_BULLISH_ONLY",
    "R2_L1_ENGINE_KEYS",
    "R2_MIN_SCORE",
    "R2_S1_MIN_ENGINES",
    "R2_S2_MIN_ENGINES",
    "R2_S3_MIN_ENGINES",
    "R2_STRUCTURE_GATE_ENGINES",
    "R2_TECH_WEIGHT",
    "R2_VOLUME_GATE_ENGINES",
    "R2_VSA_VOLUME_GATE",
    "r2_confluence_min_engines",
    "r2_gate_veto_threshold",
    "r2_hmm_bullish_only",
    "r2_min_score",
    "r2_vsa_volume_gate",
]
