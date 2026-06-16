"""Scorer y gate moderado de confluencia de opciones R1. # [PD-3][TH][IM]"""

from __future__ import annotations

from backend.config.alpaca_r1_options_scoring_config import (
    R1_FAMILY_ENGINES,
    R1_MODERATE_BEARISH_FAMILIES,
    R1_MODERATE_CONFLUENCE_MAX,
    R1_MODERATE_FAMILY_BEAR_THRESHOLD,
    default_calibrator_path,
    get_r1_family_weights,
    REASON_OPTIONS_CONFLUENCE_BEAR,
    REASON_OPTIONS_CONFLUENCE_BULL,
    REASON_OPTIONS_CONFLUENCE_DISTRIBUTION,
    REASON_OPTIONS_CONFLUENCE_MOMENTUM,
    REASON_OPTIONS_CONFLUENCE_STRUCTURE,
    REASON_OPTIONS_CONFLUENCE_VOLUME,
)
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import AlpacaDecision
from backend.domain.alpaca_options_models import (
    OptionsConfluence,
    OptionsDirection,
    OptionsEngineSignal,
)
from backend.services.motor_calibrator import MotorCalibrator

logger = get_logger(__name__)

_calibrator_cache: MotorCalibrator | None = None

_FAMILY_REASON = {
    "momentum": REASON_OPTIONS_CONFLUENCE_MOMENTUM,
    "volume": REASON_OPTIONS_CONFLUENCE_VOLUME,
    "structure": REASON_OPTIONS_CONFLUENCE_STRUCTURE,
}


def _align_multiplier(direction: OptionsDirection) -> float:
    if direction == "BULL":
        return 1.0
    if direction == "NEUTRAL":
        return 0.5
    return 0.0


def _dominant_direction(signals: list[OptionsEngineSignal]) -> OptionsDirection:
    bull = sum(1 for s in signals if s.direction == "BULL")
    bear = sum(1 for s in signals if s.direction == "BEAR")
    if bull > bear:
        return "BULL"
    if bear > bull:
        return "BEAR"
    return "NEUTRAL"


def _get_calibrator() -> MotorCalibrator | None:
    global _calibrator_cache
    if _calibrator_cache is not None:
        return _calibrator_cache
    path = default_calibrator_path()
    if not path.exists():
        return None
    try:
        calibrator = MotorCalibrator()
        calibrator.load(str(path))
        _calibrator_cache = calibrator
        return calibrator
    except Exception as exc:
        logger.warning("r1_confluence.calibrator_load_failed error=%s", exc)
        return None


def _apply_calibrator(signals: list[OptionsEngineSignal]) -> list[OptionsEngineSignal]:
    calibrator = _get_calibrator()
    if calibrator is None:
        return signals
    out: list[OptionsEngineSignal] = []
    for sig in signals:
        calibrated = calibrator.transform(sig.engine, sig.score)
        out.append(sig.model_copy(update={"score": calibrated}))
    return out


class OptionsConfluenceScorer:
    """Agrega señales de motores → sub-score LONG-only por familia."""

    @classmethod
    def score(cls, signals: list[OptionsEngineSignal]) -> OptionsConfluence | None:
        return cls.score_with_weights(signals, family_weights=get_r1_family_weights())

    @classmethod
    def score_with_weights(
        cls,
        signals: list[OptionsEngineSignal],
        *,
        family_weights: dict[str, float],
    ) -> OptionsConfluence | None:
        if not signals:
            return None
        signals = _apply_calibrator(signals)
        by_engine = {s.engine: round(s.score * _align_multiplier(s.direction), 4) for s in signals}
        by_family: dict[str, float] = {}
        reason_codes: list[str] = []

        for family, engines in R1_FAMILY_ENGINES.items():
            family_signals = [s for s in signals if s.engine in engines]
            if not family_signals:
                by_family[family] = 0.0
                continue
            aligned = [
                s.score * _align_multiplier(s.direction) for s in family_signals
            ]
            by_family[family] = round(sum(aligned) / len(aligned), 4)
            if by_family[family] >= 0.55:
                reason_codes.append(_FAMILY_REASON[family])

        total_weight = sum(family_weights.get(f, 0.0) for f in R1_FAMILY_ENGINES)
        if total_weight <= 0:
            return None

        weighted = sum(
            (family_weights.get(family, 0.0) / total_weight) * by_family.get(family, 0.0)
            for family in R1_FAMILY_ENGINES
        )
        dominant = _dominant_direction(signals)
        if dominant == "BULL":
            reason_codes.append(REASON_OPTIONS_CONFLUENCE_BULL)
        elif dominant == "BEAR":
            reason_codes.append(REASON_OPTIONS_CONFLUENCE_BEAR)

        bearish_families = sum(
            1 for score in by_family.values() if score < R1_MODERATE_FAMILY_BEAR_THRESHOLD
        )
        moderate = (
            dominant == "BEAR" and weighted <= R1_MODERATE_CONFLUENCE_MAX
        ) or bearish_families >= R1_MODERATE_BEARISH_FAMILIES

        return OptionsConfluence(
            score=round(max(0.0, min(1.0, weighted)), 4),
            by_family=by_family,
            by_engine=by_engine,
            dominant_direction=dominant,
            critical=False,
            moderate=moderate,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )


def apply_equity_options_confluence_gate(
    decision: AlpacaDecision,
    confluence: OptionsConfluence | None,
) -> AlpacaDecision:
    """SIZE_DOWN moderado; BLOCK crítico queda en equity_options_gate_service."""
    if confluence is None or decision.decision in {"BLOCK", "INSUFFICIENT_DATA"}:
        return decision
    if decision.direction != "LONG" and decision.decision != "SIZE_DOWN":
        return decision
    if not confluence.moderate or decision.decision != "ALLOW":
        return decision

    reasons = list(decision.reason_codes)
    if REASON_OPTIONS_CONFLUENCE_DISTRIBUTION not in reasons:
        reasons.append(REASON_OPTIONS_CONFLUENCE_DISTRIBUTION)
    logger.info(
        "equity_options_confluence_gate.size_down symbol=%s score=%.3f dominant=%s",
        decision.symbol,
        confluence.score,
        confluence.dominant_direction,
    )
    return decision.model_copy(
        update={"decision": "SIZE_DOWN", "reason_codes": tuple(reasons)}
    )


__all__ = [
    "OptionsConfluenceScorer",
    "apply_equity_options_confluence_gate",
]
