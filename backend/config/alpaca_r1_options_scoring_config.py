"""Configuración de confluencia de opciones para Ruta 1 Alpaca. # [PD-8][IM]"""

from __future__ import annotations

import os
from pathlib import Path

OptionsFamilyKey = str

R1_OPTIONS_ENGINE_KEYS: tuple[str, ...] = (
    "delta_rsi",
    "shadow_macd",
    "vidya_iv_gamma",
    "cvd_ndde_gamma",
    "volume_profile_oi",
    "bb_gex",
    "sma_gamma",
    "hybrid_ribbon",
)

R1_FAMILY_ENGINES: dict[OptionsFamilyKey, tuple[str, ...]] = {
    "momentum": ("delta_rsi", "shadow_macd", "vidya_iv_gamma"),
    "volume": ("cvd_ndde_gamma", "volume_profile_oi"),
    "structure": ("bb_gex", "sma_gamma", "hybrid_ribbon"),
}

R1_FAMILY_WEIGHTS: dict[OptionsFamilyKey, float] = {
    "momentum": float(os.getenv("ALPACA_R1_FAMILY_WEIGHT_MOMENTUM", str(1 / 3))),
    "volume": float(os.getenv("ALPACA_R1_FAMILY_WEIGHT_VOLUME", str(1 / 3))),
    "structure": float(os.getenv("ALPACA_R1_FAMILY_WEIGHT_STRUCTURE", str(1 / 3))),
}

R1_CLASSIC_WEIGHT: float = float(os.getenv("ALPACA_R1_CLASSIC_WEIGHT", "0.6"))
R1_OPTIONS_WEIGHT: float = float(os.getenv("ALPACA_R1_OPTIONS_WEIGHT", "0.4"))

R1_MODERATE_BEARISH_FAMILIES: int = int(os.getenv("ALPACA_R1_MODERATE_BEARISH_FAMILIES", "2"))
R1_MODERATE_FAMILY_BEAR_THRESHOLD: float = float(
    os.getenv("ALPACA_R1_MODERATE_FAMILY_BEAR_THRESHOLD", "0.35")
)
R1_MODERATE_CONFLUENCE_MAX: float = float(os.getenv("ALPACA_R1_MODERATE_CONFLUENCE_MAX", "0.40"))

REASON_OPTIONS_CONFLUENCE_BULL = "options_confluence_bull"
REASON_OPTIONS_CONFLUENCE_BEAR = "options_confluence_bear"
REASON_OPTIONS_CONFLUENCE_MOMENTUM = "options_confluence_momentum"
REASON_OPTIONS_CONFLUENCE_VOLUME = "options_confluence_volume"
REASON_OPTIONS_CONFLUENCE_STRUCTURE = "options_confluence_structure"
REASON_OPTIONS_CONFLUENCE_DISTRIBUTION = "options_confluence_distribution"
REASON_OPTIONS_OS_IMBALANCE = "options_os_imbalance_bullish"

_DEFAULT_CALIBRATION_PATH = Path(__file__).resolve().parent / "alpaca_r1_options_calibrated.json"


def default_calibration_path() -> Path:
    """Ruta del artefacto JSON de calibración C5."""
    return Path(os.getenv("ALPACA_R1_CALIBRATION_PATH", str(_DEFAULT_CALIBRATION_PATH)))


def default_calibrator_path() -> Path:
    """Ruta joblib de calibradores isotónicos por motor."""
    return Path(
        os.getenv(
            "ALPACA_R1_CALIBRATOR_PATH",
            str(
                Path(__file__).resolve().parent.parent
                / "data"
                / "alpaca_r1_engine_calibrators.joblib"
            ),
        )
    )


def _load_calibrated_overrides() -> dict[str, float] | None:
    path = default_calibration_path()
    if not path.exists():
        return None
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        fw = payload.get("family_weights") or {}
        weights = {
            "momentum": float(fw.get("momentum", 1 / 3)),
            "volume": float(fw.get("volume", 1 / 3)),
            "structure": float(fw.get("structure", 1 / 3)),
        }
        total = sum(weights.values())
        if total <= 0:
            return None
        return {k: v / total for k, v in weights.items()}
    except Exception:
        return None


_CALIBRATED_FAMILY_WEIGHTS = _load_calibrated_overrides()


def get_r1_family_weights() -> dict[OptionsFamilyKey, float]:
    """Pesos activos: calibrados si existen, si no equal-weight + env."""
    if _CALIBRATED_FAMILY_WEIGHTS is not None:
        return dict(_CALIBRATED_FAMILY_WEIGHTS)
    return dict(R1_FAMILY_WEIGHTS)


def get_r1_blend_weights() -> tuple[float, float]:
    """Classic/options blend; override desde JSON calibrado si existe."""
    path = default_calibration_path()
    if path.exists():
        try:
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
            classic = float(payload.get("classic_weight", R1_CLASSIC_WEIGHT))
            options = float(payload.get("options_weight", R1_OPTIONS_WEIGHT))
            total = classic + options
            if total > 0:
                return classic / total, options / total
        except Exception:
            pass
    total = R1_CLASSIC_WEIGHT + R1_OPTIONS_WEIGHT
    return R1_CLASSIC_WEIGHT / total, R1_OPTIONS_WEIGHT / total


__all__ = [
    "R1_CLASSIC_WEIGHT",
    "R1_FAMILY_ENGINES",
    "R1_FAMILY_WEIGHTS",
    "R1_MODERATE_BEARISH_FAMILIES",
    "R1_MODERATE_CONFLUENCE_MAX",
    "R1_MODERATE_FAMILY_BEAR_THRESHOLD",
    "R1_OPTIONS_ENGINE_KEYS",
    "R1_OPTIONS_WEIGHT",
    "REASON_OPTIONS_CONFLUENCE_BEAR",
    "REASON_OPTIONS_CONFLUENCE_BULL",
    "REASON_OPTIONS_CONFLUENCE_DISTRIBUTION",
    "REASON_OPTIONS_CONFLUENCE_MOMENTUM",
    "REASON_OPTIONS_CONFLUENCE_STRUCTURE",
    "REASON_OPTIONS_CONFLUENCE_VOLUME",
    "default_calibration_path",
    "default_calibrator_path",
    "get_r1_blend_weights",
    "get_r1_family_weights",
]
