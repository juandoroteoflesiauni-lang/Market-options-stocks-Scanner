"""Enriquecimiento Ruta 1: opciones GEX + puente predictivo. # [PD-3][TH]"""

from __future__ import annotations

from typing import Any

from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaDecision
from backend.domain.probabilistic_models import PredictiveOptionsBundleReport
from backend.services.bingx_predictive_bridge import build_predictive_bridge

logger = get_logger(__name__)

REASON_PREDICTIVE_BEARISH = "equity_predictive_bearish_block"
REASON_PREDICTIVE_LOW_CONF = "equity_predictive_low_confidence_size_down"


async def fetch_route1_options_report(
    symbol: str,
) -> PredictiveOptionsBundleReport | None:
    """Obtiene el bundle GEX/predictivo para un equity de Ruta 1."""
    try:
        from backend.services.alpaca_r1_options_context import fetch_route1_options_bundle

        bundle = await fetch_route1_options_bundle(symbol)
        return bundle.report
    except Exception as exc:
        logger.warning(
            "route1_context.options_failed symbol=%s error=%s",
            symbol,
            str(exc)[:120],
        )
        return None


async def fetch_route1_predictive_meta(symbol: str) -> dict[str, Any]:
    """Metadatos del puente predictivo para gate ligero en Ruta 1."""
    try:
        bridge = await build_predictive_bridge(symbol, market_type="stock")
        if bridge.status != "available" or bridge.signal is None:
            return {}
        sig = bridge.signal
        return {
            "directional_bias": sig.directional_bias,
            "confidence": sig.confidence,
            "reason_codes": tuple(sig.reason_codes),
            "probability_long": sig.probability_long,
        }
    except Exception as exc:
        logger.warning(
            "route1_context.predictive_failed symbol=%s error=%s",
            symbol,
            str(exc)[:120],
        )
        return {}


def apply_predictive_gate(
    decision: AlpacaDecision,
    meta: dict[str, Any],
) -> AlpacaDecision:
    """Gate predictivo ligero sobre veredicto ya filtrado por opciones/L2."""
    import os

    if os.getenv("ALPACA_PREDICTIVE_GATE_DISABLED", "").lower() in {"1", "true", "yes"}:
        return decision
    if not meta or decision.decision in {"BLOCK", "INSUFFICIENT_DATA"}:
        return decision

    reasons = list(decision.reason_codes)
    bias = str(meta.get("directional_bias") or "").upper()
    confidence = float(meta.get("confidence") or 0.0)

    if bias == "BEARISH" and decision.direction == "LONG":
        reasons.append(REASON_PREDICTIVE_BEARISH)
        return decision.model_copy(
            update={
                "decision": "BLOCK",
                "direction": "FLAT",
                "reason_codes": tuple(reasons),
            }
        )

    if confidence < 0.35 and decision.decision == "ALLOW":
        reasons.append(REASON_PREDICTIVE_LOW_CONF)
        return decision.model_copy(
            update={"decision": "SIZE_DOWN", "reason_codes": tuple(reasons)}
        )

    return decision


__all__ = [
    "apply_predictive_gate",
    "fetch_route1_options_report",
    "fetch_route1_predictive_meta",
]
