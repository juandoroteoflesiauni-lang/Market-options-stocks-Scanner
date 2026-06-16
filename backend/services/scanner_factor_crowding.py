from __future__ import annotations
from typing import Any
"""Fase 3: factor crowding indices and conviction penalty (cross-section, no IO).

Computes universe-level crowding per factor family from loadings in the current scan,
then penalizes ``conviction_score`` when a row's top drivers sit in crowded factors.
"""


import json
import math
import os

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    FactorCrowdingIndex,
    MarketScannerRow,
    ScannerCrowdedFactor,
    ScannerCrowdingBreakdown,
)
from backend.services.scanner_factor_attribution import UNIFIED_FACTOR_FAMILIES

logger = get_logger(__name__)

WARN_INSUFFICIENT_UNIVERSE = "insufficient_universe"
WARN_FACTOR_CROWDED = "factor_crowded"
WARN_MULTI_FACTOR_CROWDED = "multi_factor_crowded"


def crowding_enabled() -> bool:
    raw = os.getenv("SCANNER_FACTOR_CROWDING", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _min_universe() -> int:
    return int(os.getenv("SCANNER_CROWDING_MIN_UNIVERSE", "8"))


def _warn_pct() -> float:
    return float(os.getenv("SCANNER_CROWDING_WARN_PCT", "85"))


def _max_penalty() -> float:
    return float(os.getenv("SCANNER_CROWDING_MAX_PENALTY", "25"))


def _row_loadings(row: MarketScannerRow) -> dict[str, float]:
    breakdown = row.conviction_breakdown
    if breakdown and breakdown.factor_contributions:
        return dict(breakdown.factor_contributions)
    if row.barra_exposure and row.barra_exposure.factors:
        return dict(row.barra_exposure.factors)
    if row.factor_loadings:
        return dict(row.factor_loadings)
    return {}


def _collect_factor_matrix(
    rows: list[MarketScannerRow],
) -> tuple[list[str], dict[str, list[float]]]:
    """Build factor_key -> loadings list across universe."""
    matrix: dict[str, list[float]] = {}
    for row in rows:
        loadings = _row_loadings(row)
        for key, val in loadings.items():
            if key not in UNIFIED_FACTOR_FAMILIES and not key.startswith("sector_"):
                continue
            matrix.setdefault(key, []).append(float(val))
    active = [k for k, vals in matrix.items() if len(vals) >= 2]
    return active, {k: matrix[k] for k in active}


def _herfindahl(abs_loadings: list[float]) -> float:
    total = sum(abs_loadings)
    if total < 1e-9:
        return 0.0
    shares = [abs(v) / total for v in abs_loadings]
    return sum(s * s for s in shares)


def _coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if abs(mean) < 1e-9:
        var = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(var)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var) / abs(mean)


def _pearson(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    ax, bx = a[:n], b[:n]
    ma = sum(ax) / n
    mb = sum(bx) / n
    num = sum((ax[i] - ma) * (bx[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in ax))
    db = math.sqrt(sum((x - mb) ** 2 for x in bx))
    if da < 1e-9 or db < 1e-9:
        return 0.0
    return max(-1.0, min(1.0, num / (da * db)))


def _percentile_rank(values: dict[str, float], key: str) -> float | None:
    if key not in values:
        return None
    sorted_vals = sorted(values.values())
    if not sorted_vals:
        return None
    target = values[key]
    below = sum(1 for v in sorted_vals if v < target)
    equal = sum(1 for v in sorted_vals if v == target)
    return ((below + 0.5 * equal) / len(sorted_vals)) * 100.0


def compute_universe_factor_crowding(
    rows: list[MarketScannerRow],
) -> list[FactorCrowdingIndex]:
    """Compute crowding indices for all active factors in the scan universe."""
    if len(rows) < _min_universe():
        return []

    active_keys, matrix = _collect_factor_matrix(rows)
    if not active_keys:
        return []

    # Align rows: build per-symbol vector for correlation (symbols with any loading)
    symbols = [r.symbol for r in rows]
    sym_index = {s: i for i, s in enumerate(symbols)}
    aligned: dict[str, list[float]] = {}
    for row in rows:
        loadings = _row_loadings(row)
        for key in active_keys:
            aligned.setdefault(key, [0.0] * len(symbols))
            if row.symbol in sym_index:
                aligned[key][sym_index[row.symbol]] = float(loadings.get(key, 0.0))

    crowding_raw: dict[str, float] = {}
    components: dict[str, dict[str, float]] = {}

    for key in active_keys:
        vals = matrix[key]
        abs_vals = [abs(v) for v in vals]
        hhi = _herfindahl(abs_vals)
        cv = _coefficient_of_variation(vals)
        dispersion_norm = max(0.0, min(1.0, 1.0 - min(cv, 2.0) / 2.0))

        corr_sum = 0.0
        corr_n = 0
        vec = aligned.get(key, [])
        for other in active_keys:
            if other == key:
                continue
            c = _pearson(vec, aligned.get(other, []))
            corr_sum += abs(c)
            corr_n += 1
        corr_mean = corr_sum / corr_n if corr_n else 0.0

        raw = 0.5 * hhi + 0.3 * corr_mean + 0.2 * (1.0 - dispersion_norm)
        crowding_raw[key] = raw
        components[key] = {
            "concentration_score": round(hhi, 4),
            "loading_dispersion": round(cv, 4),
            "pairwise_corr_mean": round(corr_mean, 4),
        }

    indices: list[FactorCrowdingIndex] = []
    for key in active_keys:
        pct = _percentile_rank(crowding_raw, key)
        comp = components[key]
        tier: str = "real" if len(matrix[key]) >= _min_universe() else "proxy"
        indices.append(
            FactorCrowdingIndex(
                factor_key=key,
                crowding_percentile=round(pct, 2) if pct is not None else None,
                concentration_score=comp["concentration_score"],
                loading_dispersion=comp["loading_dispersion"],
                pairwise_corr_mean=comp["pairwise_corr_mean"],
                data_tier=tier,

            )
        )
    indices.sort(
        key=lambda x: x.crowding_percentile if x.crowding_percentile is not None else -1.0,
        reverse=True,
    )
    return indices


def _index_by_factor(indices: list[FactorCrowdingIndex]) -> dict[str, FactorCrowdingIndex]:
    return {i.factor_key: i for i in indices}


def apply_crowding_penalty_to_rows(
    rows: list[MarketScannerRow],
    indices: list[FactorCrowdingIndex],
) -> None:
    """Attach crowding breakdown and reduce conviction_score in place."""
    if len(rows) < _min_universe() or not indices:
        for row in rows:
            row.crowding_breakdown = ScannerCrowdingBreakdown(
                crowding_penalty=0.0,
                warnings=[WARN_INSUFFICIENT_UNIVERSE],
            )
        return

    by_factor = _index_by_factor(indices)
    warn_pct = _warn_pct()
    max_pen = _max_penalty()

    for row in rows:
        breakdown = row.conviction_breakdown
        if not breakdown:
            row.crowding_breakdown = ScannerCrowdingBreakdown(
                crowding_penalty=0.0,
                warnings=["no_conviction_breakdown"],
            )
            continue

        crowded: list[ScannerCrowdedFactor] = []
        penalty = 0.0
        drivers = breakdown.top_drivers[:3]

        for driver in drivers:
            idx = by_factor.get(driver.factor_key)
            if idx is None or idx.crowding_percentile is None:
                continue
            pct = idx.crowding_percentile
            if pct >= warn_pct:
                crowded.append(
                    ScannerCrowdedFactor(factor_key=driver.factor_key, crowding_percentile=pct)
                )
            excess = max(0.0, pct - 50.0)
            weight = driver.contribution_pct / 100.0
            penalty += excess * weight * 0.5

        penalty = min(max_pen, round(penalty, 2))
        warnings: list[str] = []
        if crowded:
            warnings.append(WARN_FACTOR_CROWDED)
        if len(crowded) >= 2:
            warnings.append(WARN_MULTI_FACTOR_CROWDED)

        raw_conviction = breakdown.conviction_score
        if breakdown.conviction_score_raw is None:
            breakdown.conviction_score_raw = raw_conviction
        breakdown.crowding_penalty_applied = penalty
        new_conviction = max(0.0, round(raw_conviction - penalty, 2))
        breakdown.conviction_score = new_conviction
        row.conviction_score = new_conviction

        row.crowding_breakdown = ScannerCrowdingBreakdown(
            crowding_penalty=penalty,
            crowded_factors=crowded,
            warnings=warnings,
        )

    logger.info(
        "Applied crowding penalty to %d rows (%d crowded universe factors)",
        len(rows),
        sum(1 for i in indices if (i.crowding_percentile or 0) >= warn_pct),
    )


def persist_crowding_snapshots(
    scan_id: str,
    indices: list[FactorCrowdingIndex],
    rows: list[MarketScannerRow],
) -> None:
    """Persist universe and row crowding snapshots (best-effort)."""
    try:
        from backend.services.scanner_crowding_history_store import (
            append_row_snapshots,
            append_universe_snapshots,
        )

        append_universe_snapshots(indices)
        append_row_snapshots(scan_id, rows)
    except Exception as exc:
        logger.warning("crowding.persist_failed error=%s", str(exc)[:200])


def components_json(indices: list[FactorCrowdingIndex]) -> str:
    """Serialize indices for SQLite audit column."""
    payload: list[dict[str, Any]] = [
        {
            "factor_key": i.factor_key,
            "crowding_percentile": i.crowding_percentile,
            "concentration_score": i.concentration_score,
            "loading_dispersion": i.loading_dispersion,
            "pairwise_corr_mean": i.pairwise_corr_mean,
        }
        for i in indices
    ]
    return json.dumps(payload)
