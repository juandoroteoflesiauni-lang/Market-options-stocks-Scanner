"""Motor ⑬ — Bayesian Kelly sizer from the trade journal. # [PD-2 n/a][TH][PD-6]

Closed-form Beta-Binomial posterior for the win rate plus an empirical payoff
ratio — no SciPy, no MCMC. The win-rate posterior mean is::

    p = (alpha + wins) / (alpha + beta + n)

and the Kelly fraction is the standard::

    f* = max(0, p - (1 - p) / b),   b = mean(wins) / |mean(losses)|

with an optional half-Kelly cap. The journal-reading entry point degrades to a
neutral ``1.0`` whenever the estimate cannot be trusted.

This module does NOT touch ``kelly_session_sizer`` (the bot-cycle Kelly) — the
route-bucket / pnl helpers are duplicated locally on purpose to keep the two
sizers independent.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import bayesian_kelly_calibration as bk_cal
from backend.config.bingx_risk_sizing_v2_calibration import bayesian_kelly_ops_min
from backend.config.logger_setup import get_logger
from backend.config.profit_calibration import ProfitCalibrationPolicy

logger = get_logger(__name__)


@dataclass(frozen=True)
class BayesianKellyDecideResult:
    """Bayesian Kelly result tailored for decide()/risk-sizing consumption.

    ``active`` distinguishes a genuine estimate from a degraded/neutral state
    (which both used to collapse to a 1.0 scalar). ``fraction`` is the raw
    Bayesian Kelly fraction when active, else ``None``. ``multiplier`` is the
    operational sizing factor in ``[ops_min, 1.0]`` (1.0 when degraded).
    """

    multiplier: float
    fraction: float | None
    active: bool


def _route_bucket(trade: dict[str, Any]) -> str:
    """Map a journal row to its route bucket (duplicated, not imported)."""
    route = str(trade.get("route") or "").strip().upper()
    if route:
        return route
    symbol = str(trade.get("symbol") or "")
    if symbol.endswith("-USDT") or "USDT" in symbol.upper():
        return "BINGX"
    return "ALPACA"


def _pnl_value(trade: dict[str, Any]) -> float | None:
    raw = trade.get("realized_pnl")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def compute_bayesian_kelly_fraction(
    pnls: list[float],
    *,
    prior_alpha: float = 1.0,
    prior_beta: float = 1.0,
    half_kelly: bool = True,
    min_sample: int = 12,
) -> float:
    """Bayesian Kelly fraction in [0, 1] from realized PnLs (closed-form).

    Returns 0.0 when the sample is too small or one-sided (no payoff ratio).
    """
    if len(pnls) < min_sample:
        return 0.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    if not wins or not losses:
        return 0.0

    n = len(pnls)
    p = (prior_alpha + len(wins)) / (prior_alpha + prior_beta + n)

    avg_win = statistics.mean(wins)
    avg_loss = abs(statistics.mean(losses))
    if avg_loss <= 0:
        return 0.0
    b = avg_win / avg_loss
    if b <= 0:
        return 0.0

    fraction = p - (1.0 - p) / b
    fraction = max(0.0, fraction)
    if half_kelly:
        fraction *= 0.5
    return max(0.0, min(1.0, fraction))


def _resolve_journal_fraction(
    *,
    route: str | None,
    policy: ProfitCalibrationPolicy | None,
    db_path: str | Path | None,
) -> tuple[bool, float]:
    """Read the journal and return ``(active, fraction)``.

    ``active`` is False (and ``fraction`` 0.0) whenever the estimate must
    degrade to neutral: Kelly disabled, not profit mode, journal missing, or
    route sample below ``min_sample``.
    """
    cfg = policy or ProfitCalibrationPolicy.from_env()
    if not cfg.kelly_enabled or cfg.session_mode != "profit":
        return False, 0.0

    path = Path(db_path or cfg.journal_db_path)
    if not path.exists():
        return False, 0.0

    sample_floor = bk_cal.min_sample()

    from backend.services.trade_journal_service import list_trades

    bucket = route.upper() if route else None
    trades = list_trades(path, limit=max(sample_floor * 4, 80))
    pnls: list[float] = []
    for trade in trades:
        if bucket and _route_bucket(trade) != bucket:
            continue
        pnl = _pnl_value(trade)
        if pnl is not None:
            pnls.append(pnl)

    if len(pnls) < sample_floor:
        return False, 0.0

    fraction = compute_bayesian_kelly_fraction(
        pnls,
        prior_alpha=bk_cal.prior_alpha(),
        prior_beta=bk_cal.prior_beta(),
        half_kelly=bk_cal.half_kelly(),
        min_sample=sample_floor,
    )
    logger.debug(
        "bayesian_kelly_sizer route=%s sample=%d fraction=%.4f",
        bucket or "ALL",
        len(pnls),
        fraction,
    )
    return True, fraction


def bayesian_kelly_scalar(
    *,
    route: str | None = None,
    policy: ProfitCalibrationPolicy | None = None,
    db_path: str | Path | None = None,
) -> float:
    """Journal-driven Bayesian Kelly scalar in (0, 1], or 1.0 (neutral).

    Degrades to ``1.0`` when: Kelly disabled, not in profit mode, the journal
    is missing/empty, or the route sample is below ``min_sample``.
    """
    active, fraction = _resolve_journal_fraction(route=route, policy=policy, db_path=db_path)
    if not active:
        return 1.0
    return max(bk_cal.min_fraction(), min(bk_cal.max_fraction(), fraction))


def bayesian_kelly_for_decide(
    *,
    route: str = "BINGX",
    policy: ProfitCalibrationPolicy | None = None,
    db_path: str | Path | None = None,
) -> BayesianKellyDecideResult:
    """Bayesian Kelly for decide()/risk-sizing — distinguishes active vs neutral.

    ``active=False`` → ``multiplier=1.0`` and ``fraction=None`` (degraded). When
    active, the raw fraction maps operationally to ``[ops_min, 1.0]`` via
    ``ops_min + fraction * (1 - ops_min)``.
    """
    active, fraction = _resolve_journal_fraction(route=route, policy=policy, db_path=db_path)
    if not active:
        return BayesianKellyDecideResult(multiplier=1.0, fraction=None, active=False)
    ops_min = bayesian_kelly_ops_min()
    multiplier = ops_min + fraction * (1.0 - ops_min)
    return BayesianKellyDecideResult(
        multiplier=round(multiplier, 4),
        fraction=round(fraction, 4),
        active=True,
    )


__all__ = [
    "BayesianKellyDecideResult",
    "bayesian_kelly_for_decide",
    "bayesian_kelly_scalar",
    "compute_bayesian_kelly_fraction",
]
