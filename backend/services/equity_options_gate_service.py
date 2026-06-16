"""Gate GEX/predictivo de opciones — solo Ruta 1 (equities). # [PD-3][TH][IM]"""

from __future__ import annotations

from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaDecision
from backend.domain.probabilistic_models import PredictiveOptionsBundleReport

logger = get_logger(__name__)

REASON_TAIL_RISK_BLOCK = "equity_tail_risk_critical"
REASON_GAMMA_NEGATIVE = "equity_gamma_negative_regime"
REASON_SHADOW_DELTA_BLOCK = "equity_shadow_delta_block"
REASON_SPEED_INSTABILITY = "equity_speed_instability_size_down"
REASON_PINNING_RISK = "equity_pinning_risk_size_down"


def apply_equity_options_gate(
    decision: AlpacaDecision,
    bundle: PredictiveOptionsBundleReport | None,
    *,
    dte: int | None = None,
) -> AlpacaDecision:
    """Aplica reglas GEX al veredicto Alpaca (LONG-only). Sin bundle → sin cambio."""
    import os

    if os.getenv("EQUITY_OPTIONS_GATE_RELAXED", "").lower() in {"1", "true", "yes"}:
        return decision
    if bundle is None or decision.decision in {"BLOCK", "INSUFFICIENT_DATA"}:
        return decision
    if decision.direction != "LONG" and decision.decision != "SIZE_DOWN":
        return decision

    reasons = list(decision.reason_codes)

    if bundle.tail_risk_severity == "CRITICAL":
        return _block(decision, REASON_TAIL_RISK_BLOCK, reasons)

    if bundle.is_gamma_negative_regime:
        return _block(decision, REASON_GAMMA_NEGATIVE, reasons)

    if bundle.shadow_delta_imbalance < -0.8:
        return _block(decision, REASON_SHADOW_DELTA_BLOCK, reasons)

    if bundle.speed_instability_warning and decision.decision == "ALLOW":
        reasons.append(REASON_SPEED_INSTABILITY)
        return decision.model_copy(
            update={"decision": "SIZE_DOWN", "reason_codes": tuple(reasons)}
        )

    pin_prob = bundle.pinning_probability or 0.0
    if dte is not None and dte <= 1 and pin_prob > 0.70 and decision.decision == "ALLOW":
        reasons.append(REASON_PINNING_RISK)
        return decision.model_copy(
            update={"decision": "SIZE_DOWN", "reason_codes": tuple(reasons)}
        )

    return decision


def _block(
    decision: AlpacaDecision,
    reason: str,
    reasons: list[str],
) -> AlpacaDecision:
    if reason not in reasons:
        reasons.append(reason)
    logger.info(
        "equity_options_gate.block symbol=%s reason=%s",
        decision.symbol,
        reason,
    )
    return decision.model_copy(
        update={
            "decision": "BLOCK",
            "direction": "FLAT",
            "reason_codes": tuple(reasons),
        }
    )


__all__ = [
    "apply_equity_options_gate",
    "REASON_GAMMA_NEGATIVE",
    "REASON_TAIL_RISK_BLOCK",
]
