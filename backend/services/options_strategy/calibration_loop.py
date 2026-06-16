"""Calibration loop offline del módulo Options Strategy (Fase 7). # [PD-3][TH]"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.config.logger_setup import get_logger
from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
    load_options_strategy_config,
)
from backend.models.options_strategy import (
    OptionsStrategyAuditLog,
    OptionsStrategyCalibrationReport,
    PlaybookCalibrationStats,
    StrategyDecision,
)
from backend.quant_engine.math.predictive.factor_calibration import FactorCalibrationEngine
from backend.services.options_strategy.audit_store import OptionsStrategyAuditStore

logger = get_logger(__name__)

_LAYER_FACTORS = ("technical", "predictive", "options")
_MIN_OBSERVATIONS = 30
_WEIGHT_BLEND = 0.30
_CONFIDENCE_STEP = 0.02
_HIGH_EXECUTE_RATE = 0.30
_LOW_EXECUTE_RATE = 0.05


def _blend_weights(
    current: dict[str, float],
    suggested: dict[str, float],
) -> dict[str, float]:
    keys = set(current) | set(suggested)
    blended = {
        key: (1.0 - _WEIGHT_BLEND) * current.get(key, 0.0)
        + _WEIGHT_BLEND * suggested.get(key, 0.0)
        for key in keys
    }
    total = sum(blended.values())
    if total <= 0:
        return dict(current)
    return {key: value / total for key, value in blended.items()}


def _suggest_min_confidence(current: float, execute_rate: float) -> float:
    if execute_rate > _HIGH_EXECUTE_RATE:
        return min(current + _CONFIDENCE_STEP, 0.85)
    if execute_rate < _LOW_EXECUTE_RATE:
        return max(current - _CONFIDENCE_STEP, 0.55)
    return current


def _extract_matrix(
    logs: list[OptionsStrategyAuditLog],
    win_map: dict[str, bool] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Construye matriz de features y target.

    Si ``win_map`` trae outcomes realizados, aprende del PnL real (win/loss) y
    usa solo los trades con resultado conocido. Sin outcomes, cae al target
    basado en la decisión EXECUTE (comportamiento previo).
    """
    outcome_driven = bool(win_map)
    rows: list[list[float]] = []
    targets: list[float] = []
    for log in logs:
        features = log.features
        if features is None:
            continue
        if outcome_driven:
            win = win_map.get(log.audit_id)
            if win is None:
                continue
            target = 1.0 if win else 0.0
        else:
            target = (
                1.0
                if log.playbook_decision.decision == StrategyDecision.EXECUTE
                and log.playbook_decision.execution_ready
                else 0.0
            )
        rows.append(
            [
                features.technical_direction_bias,
                features.predictive_direction_bias,
                features.options_direction_bias,
            ]
        )
        targets.append(target)
    if not rows:
        return np.empty((0, 3)), np.empty(0)
    return np.asarray(rows, dtype=np.float64), np.asarray(targets, dtype=np.float64)


def _playbook_stats(logs: list[OptionsStrategyAuditLog]) -> tuple[PlaybookCalibrationStats, ...]:
    buckets: dict[str, dict[str, float]] = {}
    for log in logs:
        family = log.playbook_decision.playbook_family or "unassigned"
        bucket = buckets.setdefault(
            family,
            {"total": 0.0, "execute": 0.0, "no_trade": 0.0, "veto": 0.0, "conf_sum": 0.0},
        )
        bucket["total"] += 1.0
        if log.playbook_decision.decision == StrategyDecision.EXECUTE:
            bucket["execute"] += 1.0
            bucket["conf_sum"] += log.playbook_decision.confidence
        else:
            bucket["no_trade"] += 1.0
        if log.playbook_decision.veto_triggered:
            bucket["veto"] += 1.0

    stats: list[PlaybookCalibrationStats] = []
    for family, bucket in sorted(buckets.items()):
        total = int(bucket["total"])
        execute_count = int(bucket["execute"])
        stats.append(
            PlaybookCalibrationStats(
                playbook_family=family,
                total_signals=total,
                execute_count=execute_count,
                no_trade_count=int(bucket["no_trade"]),
                avg_confidence_on_execute=(
                    bucket["conf_sum"] / execute_count if execute_count > 0 else 0.0
                ),
                veto_rate=bucket["veto"] / total if total > 0 else 0.0,
            )
        )
    return tuple(stats)


def _veto_counts(logs: list[OptionsStrategyAuditLog]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for log in logs:
        veto = log.playbook_decision.veto_triggered
        if not veto:
            continue
        counts[veto] = counts.get(veto, 0) + 1
    return counts


class OptionsStrategyCalibrationLoop:
    """Recalibra pesos y umbrales usando historial de auditoría (sin red)."""

    @classmethod
    def run(
        cls,
        *,
        config: OptionsStrategyConfigBundle | None = None,
        audit_store: OptionsStrategyAuditStore | None = None,
        outcome_store: "OptionsStrategyOutcomeStore | None" = None,
        use_outcomes: bool = True,
        limit: int = 500,
    ) -> OptionsStrategyCalibrationReport:
        active = config or get_options_strategy_config()
        store = audit_store or OptionsStrategyAuditStore()
        logs = store.load_recent_logs(limit=limit)
        current_weights = dict(active.omni_engine.weights)
        current_conf = active.omni_engine.min_global_confidence

        win_map: dict[str, bool] = {}
        if use_outcomes:
            from backend.services.options_strategy.outcome_store import (
                OptionsStrategyOutcomeStore,
            )

            o_store = outcome_store or OptionsStrategyOutcomeStore(db_path=store.db_path)
            win_map = o_store.load_win_map(limit=limit)

        if len(logs) < _MIN_OBSERVATIONS:
            return OptionsStrategyCalibrationReport(
                observation_count=len(logs),
                current_weights=current_weights,
                suggested_weights=current_weights,
                current_min_global_confidence=current_conf,
                suggested_min_global_confidence=current_conf,
                limitations=(
                    f"insufficient_observations_need_{_MIN_OBSERVATIONS}",
                ),
            )

        matrix, targets = _extract_matrix(logs, win_map or None)
        if matrix.shape[0] < _MIN_OBSERVATIONS:
            limitation = (
                "insufficient_outcome_rows" if win_map else "insufficient_feature_rows"
            )
            return OptionsStrategyCalibrationReport(
                observation_count=matrix.shape[0],
                current_weights=current_weights,
                suggested_weights=current_weights,
                current_min_global_confidence=current_conf,
                suggested_min_global_confidence=current_conf,
                limitations=(limitation,),
            )

        engine = FactorCalibrationEngine()
        factor_report = engine.get_calibration_report(
            matrix,
            targets,
            list(_LAYER_FACTORS),
        )
        if factor_report.is_failure:
            return OptionsStrategyCalibrationReport(
                observation_count=len(logs),
                current_weights=current_weights,
                suggested_weights=current_weights,
                current_min_global_confidence=current_conf,
                suggested_min_global_confidence=current_conf,
                playbook_stats=_playbook_stats(logs),
                veto_counts=_veto_counts(logs),
                limitations=(factor_report.reason,),
            )

        report = factor_report.unwrap()
        optimized = report.optimized_weights
        suggested_weights = _blend_weights(current_weights, optimized)
        execute_count = int(np.sum(targets))
        execute_rate = execute_count / len(targets)
        suggested_conf = _suggest_min_confidence(current_conf, execute_rate)

        recommendations = list(report.recommendations)
        if win_map:
            recommendations.append("calibrated_from_realized_outcomes")
        if execute_rate > _HIGH_EXECUTE_RATE:
            recommendations.append("raise_min_global_confidence_to_reduce_overtrading")
        if execute_rate < _LOW_EXECUTE_RATE:
            recommendations.append("lower_min_global_confidence_to_capture_more_edge")

        veto_map = _veto_counts(logs)
        top_veto = max(veto_map, key=veto_map.get) if veto_map else None
        if top_veto:
            recommendations.append(f"review_veto_rule:{top_veto}")

        return OptionsStrategyCalibrationReport(
            observation_count=len(logs),
            current_weights=current_weights,
            suggested_weights=suggested_weights,
            current_min_global_confidence=current_conf,
            suggested_min_global_confidence=suggested_conf,
            playbook_stats=_playbook_stats(logs),
            veto_counts=veto_map,
            execute_rate=execute_rate,
            factor_report=report.model_dump(mode="json"),
            recommendations=tuple(recommendations),
        )

    @classmethod
    def write_calibrated_config(
        cls,
        report: OptionsStrategyCalibrationReport,
        output_path: Path | str,
    ) -> Path:
        """Escribe sugerencias de calibración a YAML (no sobrescribe producción)."""
        import yaml

        destination = Path(output_path)
        payload = {
            "omni_engine": {
                "weights": report.suggested_weights,
                "min_global_confidence": report.suggested_min_global_confidence,
                "calibration_meta": {
                    "calibration_id": report.calibration_id,
                    "observation_count": report.observation_count,
                    "execute_rate": report.execute_rate,
                    "calibrated_at": report.created_at.isoformat(),
                },
            }
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        logger.info("options_strategy_calibration.wrote_config path=%s", destination)
        return destination


def load_calibrated_config_bundle(
    calibrated_path: Path | str,
    *,
    base_config: OptionsStrategyConfigBundle | None = None,
) -> OptionsStrategyConfigBundle:
    """Fusiona ``omni_engine_calibrated.yaml`` sobre la config base."""
    import yaml

    base = base_config or load_options_strategy_config()
    raw = yaml.safe_load(Path(calibrated_path).read_text(encoding="utf-8")) or {}
    omni_raw = raw.get("omni_engine") or {}
    omni = base.omni_engine.model_copy(
        update={
            key: value
            for key, value in omni_raw.items()
            if key in {"weights", "min_global_confidence", "disagreement_penalty"}
        }
    )
    return base.model_copy(update={"omni_engine": omni})


__all__ = [
    "OptionsStrategyCalibrationLoop",
    "load_calibrated_config_bundle",
]
