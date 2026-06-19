"""Gate de promoción del meta-learner al router en producción (F6). # [PD-3][TH]"""

from __future__ import annotations

import os
from typing import Any

_REAL_SOURCES: frozenset[str] = frozenset(
    {
        "prediction_logger",
        "prediction_logger_real",
    }
)
_SYNTHETIC_SOURCES: frozenset[str] = frozenset({"synthetic_yfinance"})


def meta_learner_promote_synthetic_allowed() -> bool:
    """Permite copiar modelos entrenados con yfinance al router live."""
    return os.getenv("META_LEARNER_PROMOTE_SYNTHETIC", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def meta_learner_source(metrics: dict[str, Any]) -> str:
    """Normaliza la fuente reportada por ``train_for_symbol``."""
    return str(metrics.get("source") or "").strip().lower()


def should_promote_meta_learner_to_router(metrics: dict[str, Any]) -> bool:
    """True solo si el modelo es apto para reemplazar ``meta_learner.joblib``."""
    if metrics.get("error"):
        return False
    if metrics.get("saved") is False:
        return False

    source = meta_learner_source(metrics)
    if source in _REAL_SOURCES:
        return True
    if source in _SYNTHETIC_SOURCES:
        return meta_learner_promote_synthetic_allowed()
    return False


def promotion_skip_reason(metrics: dict[str, Any]) -> str | None:
    """Razón legible cuando no se promueve el modelo al router."""
    if should_promote_meta_learner_to_router(metrics):
        return None
    source = meta_learner_source(metrics) or "unknown"
    if source in _SYNTHETIC_SOURCES and not meta_learner_promote_synthetic_allowed():
        return "synthetic_source_blocked"
    if metrics.get("error"):
        return "training_error"
    if metrics.get("saved") is False:
        return "model_not_saved"
    return "source_not_eligible"


__all__ = [
    "meta_learner_promote_synthetic_allowed",
    "meta_learner_source",
    "promotion_skip_reason",
    "should_promote_meta_learner_to_router",
]
