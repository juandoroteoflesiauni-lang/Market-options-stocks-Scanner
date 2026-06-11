"""Shared helpers for Market Scanner Phase B module signals."""

from __future__ import annotations

import math

from backend.domain.market_scanner_models import (
    ScannerModuleKey,
    ScannerModuleSignal,
    ScannerSignalLabel,
)


def label_for_module_score(score: float) -> ScannerSignalLabel:
    """Map a normalized 0-100 score into the scanner five-bucket label scale."""
    if score >= 80.0:
        return "strong_buy"
    if score >= 60.0:
        return "buy"
    if score > 40.0:
        return "neutral"
    if score > 20.0:
        return "sell"
    return "strong_sell"


def clamp_score(score: float) -> float:
    if not math.isfinite(score):
        return 50.0
    return max(0.0, min(100.0, score))


def neutral_module_signal(
    module: ScannerModuleKey,
    warning: str,
    *,
    engine_count: int = 0,
) -> ScannerModuleSignal:
    return ScannerModuleSignal(
        module=module,
        label="neutral",
        score=50.0,
        confidence=0.0,
        engine_count=engine_count,
        available_count=0,
        reasons=[],
        warnings=[warning],
    )


def build_module_signal(
    module: ScannerModuleKey,
    score: float,
    confidence: float,
    *,
    engine_count: int,
    available_count: int,
    reasons: list[str],
    warnings: list[str] | None = None,
) -> ScannerModuleSignal:
    normalized = round(clamp_score(score), 2)
    return ScannerModuleSignal(
        module=module,
        label=label_for_module_score(normalized),
        score=normalized,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        engine_count=engine_count,
        available_count=available_count,
        reasons=reasons[:6],
        warnings=(warnings or [])[:6],
    )
