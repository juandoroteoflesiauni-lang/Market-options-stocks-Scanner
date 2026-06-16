from __future__ import annotations
from typing import Any
"""Optional FinGPT-style NLP context provider for the Market Scanner.

The module is dependency-light at import time. Heavy NLP runtimes are imported
only when SCANNER_NLP_ENGINE enables FinGPT and FINGPT_MODEL_NAME is configured.
"""


import os
from functools import lru_cache

from backend.config.logger_setup import get_logger
from backend.services.scanner_external_contracts import clamp_score

logger = get_logger(__name__)

_FINGPT_SOURCE = "fingpt"
_MAX_NEWS_ITEMS = 12
_MAX_TEXT_CHARS = 1_500

_BULLISH_CATALYST_TERMS = {
    "upgrade": "ANALYST_UPGRADE",
    "raises guidance": "GUIDANCE_RAISE",
    "raise guidance": "GUIDANCE_RAISE",
    "beat": "EARNINGS_BEAT",
    "launch": "PRODUCT",
    "partnership": "PARTNERSHIP",
    "buyback": "CAPITAL_RETURN",
    "approval": "REGULATORY_APPROVAL",
}
_BEARISH_CATALYST_TERMS = {
    "downgrade": "ANALYST_DOWNGRADE",
    "cuts guidance": "GUIDANCE_CUT",
    "cut guidance": "GUIDANCE_CUT",
    "miss": "EARNINGS_MISS",
    "probe": "REGULATORY_PROBE",
    "lawsuit": "LEGAL_RISK",
    "default": "CREDIT_RISK",
    "sec": "REGULATORY_PROBE",
}
_NEUTRAL_CATALYST_TERMS = {
    "earnings": "EARNINGS",
    "guidance": "GUIDANCE",
    "merger": "M_AND_A",
    "acquisition": "M_AND_A",
    "fed": "MACRO",
    "cpi": "MACRO",
}


def score_news(symbol: str, news_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Score scanner news sentiment with an optional FinGPT-compatible classifier."""
    runtime = _runtime_status()
    if runtime is not None:
        return _unavailable_sentiment(runtime)
    texts = _news_texts(news_items)
    if not texts:
        return _unavailable_sentiment("insufficient_news")

    try:
        classifier = _load_text_classifier(_model_name(), _device())
        raw_results = classifier(texts, truncation=True)
    except Exception as exc:
        logger.warning("scanner.fingpt.score_news_unavailable symbol=%s error=%s", symbol, exc)
        return _unavailable_sentiment(_runtime_error_reason(exc))

    normalized = [_normalize_classifier_result(result) for result in _flatten_results(raw_results)]
    normalized = [item for item in normalized if item is not None]
    if not normalized:
        return _unavailable_sentiment("empty_model_output")

    score = sum(item["score"] for item in normalized) / len(normalized)
    confidence = sum(item["confidence"] for item in normalized) / len(normalized)
    return {
        "status": "available",
        "score": round(clamp_score(score, 0.0, 1.0), 4),
        "label": _label_from_score(score),
        "source": _FINGPT_SOURCE,
        "confidence": round(clamp_score(confidence, 0.0, 1.0), 4),
        "sample_size": len(normalized),
        "model": _model_name(),
    }


def extract_catalysts(symbol: str, news_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract FinGPT-style catalyst hints from scanner news without issuing trade decisions."""
    runtime = _runtime_status()
    if runtime is not None:
        return _unavailable_catalysts(runtime)
    texts = _news_texts(news_items)
    if not texts:
        return _unavailable_catalysts("insufficient_news")

    try:
        _load_text_classifier(_model_name(), _device())
    except Exception as exc:
        logger.warning(
            "scanner.fingpt.extract_catalysts_unavailable symbol=%s error=%s", symbol, exc
        )
        return _unavailable_catalysts(_runtime_error_reason(exc))

    joined = " ".join(texts).lower()
    catalysts = _catalyst_events(joined)
    bullish_hits = sum(1 for term in _BULLISH_CATALYST_TERMS if term in joined)
    bearish_hits = sum(1 for term in _BEARISH_CATALYST_TERMS if term in joined)
    risk_hits = (
        bullish_hits + bearish_hits + sum(1 for term in _NEUTRAL_CATALYST_TERMS if term in joined)
    )
    risk = clamp_score(0.12 + 0.14 * risk_hits + 0.08 * bearish_hits, 0.0, 1.0)
    tone = (
        "BULLISH"
        if bullish_hits > bearish_hits
        else "BEARISH" if bearish_hits > bullish_hits else "NEUTRAL"
    )
    confidence = clamp_score(0.35 + 0.08 * min(len(texts), 5) + 0.06 * risk_hits, 0.0, 0.9)

    return {
        "status": "available",
        "event_risk_score": round(risk, 4),
        "tone": tone,
        "tone_confidence": round(confidence, 4),
        "news_count": len(texts),
        "upcoming_catalysts": catalysts[:8],
        "source": _FINGPT_SOURCE,
        "confidence": round(confidence, 4),
        "model": _model_name(),
    }


def _runtime_status() -> str | None:
    engine = os.getenv("SCANNER_NLP_ENGINE", "fmp").strip().lower()
    if engine not in {"fingpt", "hybrid"}:
        return "disabled"
    if not _model_name():
        return "model_not_configured"
    return None


def _model_name() -> str:
    return os.getenv("FINGPT_MODEL_NAME", "").strip()


def _device() -> str:
    raw = os.getenv("FINGPT_DEVICE", "cpu").strip().lower()
    return raw if raw in {"cpu", "cuda"} else "cpu"


@lru_cache(maxsize=4)
def _load_text_classifier(model_name: str, device: str) -> Any:
    from transformers import pipeline


    device_id = 0 if device == "cuda" else -1
    return pipeline(
        "text-classification",
        model=model_name,
        tokenizer=model_name,
        device=device_id,
    )


def _news_texts(news_items: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for item in news_items[:_MAX_NEWS_ITEMS]:
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or item.get("text") or "").strip()
        text = f"{title}. {summary}".strip(" .")
        if text:
            texts.append(text[:_MAX_TEXT_CHARS])
    return texts


def _flatten_results(raw_results: Any) -> list[dict[str, Any]]:
    if isinstance(raw_results, dict):
        return [raw_results]
    if not isinstance(raw_results, list):
        return []
    flattened: list[dict[str, Any]] = []
    for result in raw_results:
        if isinstance(result, dict):
            flattened.append(result)
        elif isinstance(result, list):
            flattened.extend(item for item in result if isinstance(item, dict))
    return flattened


def _normalize_classifier_result(result: dict[str, Any]) -> dict[str, float] | None:
    label = str(result.get("label") or "").lower()
    confidence = clamp_score(result.get("score"), 0.0, 1.0)
    if not label:
        return None
    if any(token in label for token in ("positive", "bull", "buy")):
        score = 0.5 + 0.5 * confidence
    elif any(token in label for token in ("negative", "bear", "sell")):
        score = 0.5 - 0.5 * confidence
    else:
        score = 0.5
        confidence = min(confidence, 0.65)
    return {"score": score, "confidence": confidence}


def _label_from_score(score: float) -> str:
    if score >= 0.58:
        return "bullish"
    if score <= 0.42:
        return "bearish"
    return "neutral"


def _catalyst_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for terms in (_BULLISH_CATALYST_TERMS, _BEARISH_CATALYST_TERMS, _NEUTRAL_CATALYST_TERMS):
        for term, event_type in terms.items():
            if term not in text or event_type in seen:
                continue
            seen.add(event_type)
            events.append(
                {
                    "event_type": event_type,
                    "date": None,
                    "days_until": None,
                    "label": term,
                    "source": _FINGPT_SOURCE,
                }
            )
    return events


def _runtime_error_reason(exc: Exception) -> str:
    if isinstance(exc, ImportError | ModuleNotFoundError):
        return "runtime_not_installed"
    return "model_unavailable"


def _unavailable_sentiment(reason: str) -> dict[str, Any]:
    return {
        "status": "source unavailable",
        "score": None,
        "label": "source unavailable",
        "source": _FINGPT_SOURCE,
        "confidence": 0.0,
        "reason": reason,
    }


def _unavailable_catalysts(reason: str) -> dict[str, Any]:
    return {
        "status": "source unavailable",
        "event_risk_score": None,
        "tone": "UNAVAILABLE",
        "source": _FINGPT_SOURCE,
        "confidence": 0.0,
        "reason": reason,
        "upcoming_catalysts": [],
    }


__all__ = ["extract_catalysts", "score_news"]
