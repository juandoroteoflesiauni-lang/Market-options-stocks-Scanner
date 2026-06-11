"""
Orchestrator for automated motor reputation updates based on logged performance.

This service bridge the gap between PredictionLogger (where outcomes are stored)
and RegimeWeightingEngine (where reputation-based weights are adjusted).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from backend.config.logger_setup import get_logger
from backend.layer_3_specialists.ia_probabilistico.engines.regime_weights import (
    ALL_MOTORS,
    update_weights_from_performance,
)
from backend.services.prediction_logger import PredictionLogger

logger = get_logger(__name__)


def _get_motor_direction(score: float | None) -> str:
    if score is None:
        return "NEUTRAL"
    if score > 50.5:
        return "UP"
    if score < 49.5:
        return "DOWN"
    return "NEUTRAL"


def sync_all_motors_reputation(
    pred_logger: PredictionLogger,
    last_n_days: int = 30,
    min_samples: int = 20,
) -> dict[str, Any]:
    """
    Fetch all logged predictions with outcomes from the last N days,
    calculate per-motor accuracy by regime, and update weights.
    """
    logger.info("Starting reputation sync for last %d days", last_n_days)

    # We use a broad query to get all symbols or we could iterate over symbols.
    # For simplicity, let's assume we want to update based on ALL data in the log.

    # PredictionLogger.get_predictions_for_retraining requires a symbol.
    # Let's add a method to PredictionLogger to get all data if needed,
    # or just use the DB directly here.

    cutoff = (datetime.now() - timedelta(days=last_n_days)).isoformat()

    with pred_logger._lock, pred_logger._connect() as conn:
        rows = conn.execute(
            """
            SELECT p.regime, p.motor_signals, o.outcome_return_5d
            FROM predictions p
            INNER JOIN outcomes o ON p.prediction_id = o.prediction_id
            WHERE p.timestamp >= ?
              AND o.outcome_return_5d IS NOT NULL
            """,
            (cutoff,),
        ).fetchall()

    if not rows:
        logger.info("No predictions with outcomes found in the last %d days.", last_n_days)
        return {"status": "no_data", "samples": 0}

    # Data structure: regime -> motor -> [is_correct, ...]
    performance: dict[str, dict[str, list[int]]] = {}

    total_samples = len(rows)
    for row in rows:
        regime = row["regime"]
        if not regime:
            continue

        try:
            motors = json.loads(row["motor_signals"]) if row["motor_signals"] else {}
        except (TypeError, ValueError):
            continue

        ret = float(row["outcome_return_5d"])
        realised_dir = "UP" if ret > 0.005 else ("DOWN" if ret < -0.005 else "NEUTRAL")

        if regime not in performance:
            performance[regime] = {m: [] for m in ALL_MOTORS}

        for motor_name in ALL_MOTORS:
            # Check if motor exists in logged signals
            # Some motors might have slightly different names in the log
            # We try to match them.
            score = motors.get(motor_name)
            if score is None:
                # Try common aliases or prefixes
                score = motors.get(f"{motor_name}_score")

            if score is not None:
                m_dir = _get_motor_direction(float(score))
                is_correct = 1 if m_dir == realised_dir else 0
                performance[regime][motor_name].append(is_correct)

    updates_count = 0
    results: dict[str, Any] = {}

    for regime, motors_perf in performance.items():
        regime_results = {}
        for motor_name, hits in motors_perf.items():
            if len(hits) < min_samples:
                continue

            accuracy = float(np.mean(hits))
            try:
                update_weights_from_performance(
                    motor_name=motor_name, recent_accuracy=accuracy, regime=regime
                )
                updates_count += 1
                regime_results[motor_name] = round(accuracy, 3)
            except Exception as exc:
                logger.error(
                    "Failed to update reputation for %s in %s: %s", motor_name, regime, exc
                )

        if regime_results:
            results[regime] = regime_results

    logger.info("Reputation sync completed. Total updates: %d", updates_count)
    return {
        "status": "success",
        "total_samples": total_samples,
        "updates": updates_count,
        "details": results,
    }
