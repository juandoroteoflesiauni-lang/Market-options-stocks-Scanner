"""Options / GEX module backtester (placeholder: signal vs returns)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from backend.backtesting.base import (
    BacktestConfig,
    BacktestResult,
    run_long_only_threshold_backtest,
)


def run_gex_backtest(
    returns_pct: Sequence[float],
    gex_bias_signal: Sequence[float],
    *,
    symbol: str,
    cost_config: BacktestConfig | None = None,
) -> BacktestResult:
    return run_long_only_threshold_backtest(
        returns_pct,
        gex_bias_signal,
        symbol=symbol,
        module="options_gex",
        threshold=0.0,
        cost_config=cost_config,
    )


def run_gex_event_calibration_backtest(
    events: Sequence[dict[str, Any]],
    *,
    threshold: float = 50.0,
) -> dict[str, Any]:
    """Evaluate Options/GEX event scores against binary realized outcomes.

    Expected event fields:
    - ``kind``: alert/state name, e.g. NEG_GAMMA_BREAKOUT or EXPIRY_PIN
    - ``score``: 0-100 model confidence/intensity
    - ``outcome``: 1 if the event realized, else 0
    """
    rows = [
        {
            "kind": str(item.get("kind") or "UNKNOWN"),
            "score": max(0.0, min(float(item.get("score") or 0.0), 100.0)),
            "outcome": 1 if int(item.get("outcome") or 0) else 0,
        }
        for item in events
    ]
    by_kind: dict[str, dict[str, float | int | None]] = {}
    brier_terms: list[float] = []
    for row in rows:
        pred = row["score"] >= threshold
        outcome = bool(row["outcome"])
        bucket = by_kind.setdefault(
            row["kind"], {"events": 0, "tp": 0, "fp": 0, "fn": 0, "precision": None, "recall": None}
        )
        bucket["events"] = int(bucket["events"] or 0) + 1
        if pred and outcome:
            bucket["tp"] = int(bucket["tp"] or 0) + 1
        elif pred and not outcome:
            bucket["fp"] = int(bucket["fp"] or 0) + 1
        elif not pred and outcome:
            bucket["fn"] = int(bucket["fn"] or 0) + 1
        prob = float(row["score"]) / 100.0
        brier_terms.append((prob - float(row["outcome"])) ** 2)
    for bucket in by_kind.values():
        tp = int(bucket["tp"] or 0)
        fp = int(bucket["fp"] or 0)
        fn = int(bucket["fn"] or 0)
        bucket["precision"] = round(tp / (tp + fp), 4) if tp + fp else None
        bucket["recall"] = round(tp / (tp + fn), 4) if tp + fn else None
    return {
        "events": len(rows),
        "threshold": threshold,
        "brier_score": round(sum(brier_terms) / len(brier_terms), 6) if brier_terms else None,
        "by_kind": by_kind,
    }
