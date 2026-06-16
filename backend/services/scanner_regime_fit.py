from __future__ import annotations
"""Fase 2: edge-regime mapping — score how well a row's edge fits the regime.

Given the active desk regime and a row's top conviction factors, this module
computes ``regime_fit_score`` (0-100) as the contribution-weighted blend of each
factor's historical performance in that regime. Cold start (no actionable
history) yields a neutral ~50 with an ``insufficient_history`` reason rather
than a fabricated edge.

Survival framing: a low ``regime_fit_score`` means the row's edge has *not*
historically worked in the current regime — a reason for the desk to size down,
never a green light. This score never authorizes risk by itself.
"""


from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    DeskRegimeLabel,
    DeskRegimeSnapshot,
    FactorRegimeStat,
    MarketScannerRow,
    ScannerRegimeFitBreakdown,
    ScannerRegimeFitLine,
)
from backend.services.scanner_factor_regime_performance import get_stats_for_factors

logger = get_logger(__name__)

NEUTRAL_FIT = 50.0
# Number of top conviction drivers considered for the fit.
_TOP_DRIVERS = 5

REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_DRIVERS = "no_conviction_drivers"
REASON_NO_DESK_REGIME = "no_desk_regime"


def _stat_to_fit_score(stat: FactorRegimeStat) -> float:
    """Map a factor-regime stat to a 0-100 fit score.

    base 50 ± win-rate tilt (±30) ± Sharpe tilt (±20). Clamped to [0, 100].
    """
    win_component = (stat.win_rate - 0.5) * 60.0
    sharpe_clamped = max(-2.0, min(2.0, stat.sharpe_annualized))
    sharpe_component = sharpe_clamped * 10.0
    return max(0.0, min(100.0, NEUTRAL_FIT + win_component + sharpe_component))


def _stress_line(stat: FactorRegimeStat, lookback_days: int) -> str:
    months = max(1, round(lookback_days / 30.0))
    return (
        f"In {stat.regime}, {stat.factor_key} had Sharpe {stat.sharpe_annualized:.2f} "
        f"(win {stat.win_rate * 100:.0f}%) over last {months}m (n={stat.sample_count})"
    )


def compute_regime_fit(
    row: MarketScannerRow,
    regime: DeskRegimeLabel,
    stats: dict[str, FactorRegimeStat],
) -> ScannerRegimeFitBreakdown:
    """Build the regime-fit breakdown for one row given precomputed stats."""
    warnings: list[str] = []
    lines: list[ScannerRegimeFitLine] = []
    stress_overlay: list[str] = []

    breakdown = row.conviction_breakdown
    drivers = breakdown.top_drivers[:_TOP_DRIVERS] if breakdown else []
    if not drivers:
        warnings.append(REASON_NO_DRIVERS)
        return ScannerRegimeFitBreakdown(
            regime=regime,
            regime_fit_score=NEUTRAL_FIT,
            lines=[],
            stress_overlay=[],
            warnings=warnings,
        )

    weighted_sum = 0.0
    weight_total = 0.0
    sufficient_seen = False
    for driver in drivers:
        stat = stats.get(driver.factor_key)
        contribution = max(0.0, driver.contribution_pct)
        if stat is None or not stat.sufficient:
            note = REASON_INSUFFICIENT_HISTORY
            fit_score = NEUTRAL_FIT
            sample_count = stat.sample_count if stat else 0
            win_rate = stat.win_rate if stat else None
            avg_ret = stat.avg_forward_return if stat else None
            sharpe = stat.sharpe_annualized if stat else None
        else:
            sufficient_seen = True
            fit_score = _stat_to_fit_score(stat)
            note = "ok"
            sample_count = stat.sample_count
            win_rate = stat.win_rate
            avg_ret = stat.avg_forward_return
            sharpe = stat.sharpe_annualized
            stress_overlay.append(_stress_line(stat, stat.lookback_days))

        lines.append(
            ScannerRegimeFitLine(
                factor_key=driver.factor_key,
                regime=regime,
                contribution_pct=round(min(100.0, contribution), 2),
                fit_score=round(fit_score, 2),
                sample_count=sample_count,
                win_rate=win_rate,
                avg_forward_return=avg_ret,
                sharpe_annualized=sharpe,
                note=note,
            )
        )
        # Weight neutral (insufficient) lines too, so cold start trends to ~50.
        weighted_sum += fit_score * contribution
        weight_total += contribution

    regime_fit_score = weighted_sum / weight_total if weight_total > 0 else NEUTRAL_FIT
    if not sufficient_seen:
        warnings.append(REASON_INSUFFICIENT_HISTORY)

    return ScannerRegimeFitBreakdown(
        regime=regime,
        regime_fit_score=round(max(0.0, min(100.0, regime_fit_score)), 2),
        lines=lines,
        stress_overlay=stress_overlay,
        warnings=warnings,
    )


def attach_regime_fit(
    rows: list[MarketScannerRow],
    desk_regime: DeskRegimeSnapshot | None,
) -> None:
    """Attach ``regime_fit_score`` + ``regime_fit_breakdown`` to rows in place.

    With no desk regime, every row gets a neutral breakdown flagged
    ``no_desk_regime`` (never silently skipped).
    """
    if desk_regime is None:
        for row in rows:
            row.regime_fit_score = NEUTRAL_FIT
            row.regime_fit_breakdown = ScannerRegimeFitBreakdown(
                regime="TRANSITION",
                regime_fit_score=NEUTRAL_FIT,
                lines=[],
                stress_overlay=[],
                warnings=[REASON_NO_DESK_REGIME],
            )
        return

    regime = desk_regime.label
    # Collect the union of factor keys across rows for a single batched stat query.
    factor_keys: set[str] = set()
    for row in rows:
        if row.conviction_breakdown:
            for driver in row.conviction_breakdown.top_drivers[:_TOP_DRIVERS]:
                factor_keys.add(driver.factor_key)

    stats = get_stats_for_factors(sorted(factor_keys), regime) if factor_keys else {}

    enriched = 0
    for row in rows:
        breakdown = compute_regime_fit(row, regime, stats)
        row.regime_fit_breakdown = breakdown
        row.regime_fit_score = breakdown.regime_fit_score
        enriched += 1

    logger.info(
        "regime_fit.attached rows=%d regime=%s factors=%d",
        enriched,
        regime,
        len(factor_keys),
    )


def build_regime_factor_rows(rows: list[MarketScannerRow]) -> list[dict[str, object]]:
    """Build persistence payload (one entry per top conviction driver per row)."""
    factor_rows: list[dict[str, object]] = []
    for row in rows:
        breakdown = row.conviction_breakdown
        if not breakdown:
            continue
        for driver in breakdown.top_drivers[:_TOP_DRIVERS]:
            factor_rows.append(
                {
                    "symbol": row.symbol,
                    "factor_key": driver.factor_key,
                    "loading": driver.loading,
                    "contribution_pct": driver.contribution_pct,
                    "price_at_scan": row.price,
                }
            )
    return factor_rows
