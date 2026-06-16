from __future__ import annotations
"""Portfolio risk primitives (Layer 5) — sizing, Kelly-lite, simple historical VaR."""


import math
import statistics
from collections.abc import Iterable


def risk_layer_healthcheck() -> str:
    return "Capa 5 operativa: motor de riesgo portfolio_risk cargado."


def note_scanner_risk_hints() -> str:
    """Document bridge: Market Scanner `risk_hints` are pre-trade diagnostics only."""
    return "Use portfolio_risk engine for binding limits; scanner Kelly/VaR proxy is illustrative."


def fractional_kelly(
    win_prob: float,
    *,
    win_payoff: float = 1.0,
    loss_payoff: float = 1.0,
    shrink: float = 0.25,
    cap: float = 0.25,
) -> float:
    """Half/quarter Kelly style fraction with hard cap (institutional conservatism)."""
    p = max(0.0, min(1.0, win_prob))
    q = 1.0 - p
    b = max(win_payoff, 1e-9) / max(loss_payoff, 1e-9)
    raw = (p * b - q) / max(b, 1e-9)
    if raw <= 0:
        return 0.0
    return float(max(0.0, min(cap, raw * shrink)))


def historical_var_pct(returns_pct: Iterable[float], alpha: float = 0.05) -> float | None:
    """Left-tail VaR on simple historical P/L % (negative = loss)."""
    xs = sorted(float(x) for x in returns_pct if math.isfinite(float(x)))
    if len(xs) < max(10, int(1 / max(alpha, 0.01))):
        return None
    idx = max(0, min(len(xs) - 1, int(math.floor(alpha * len(xs))) - 1))
    return float(abs(xs[idx]))


def position_notional_from_risk(
    equity: float,
    risk_budget_pct: float,
    stop_distance_pct: float,
) -> float | None:
    """Risk-budget notional: equity * risk% / stop% (diagnostic)."""
    if equity <= 0 or risk_budget_pct <= 0 or stop_distance_pct <= 0:
        return None
    return float(equity * (risk_budget_pct / 100.0) / max(stop_distance_pct / 100.0, 1e-9))


def stress_loss_pct(
    mean_return_pct: float,
    vol_pct: float,
    shocks: tuple[float, ...] = (-2.0, -3.0),
) -> dict[str, float]:
    """Gaussian-style stress scenarios on daily % stats."""
    out: dict[str, float] = {}
    for z in shocks:
        key = f"z{z:.1f}_pct"
        out[key] = round(mean_return_pct + z * vol_pct, 4)
    return out


def portfolio_risk_summary(
    *,
    equity: float,
    win_prob: float | None,
    returns_pct: list[float] | None,
    risk_budget_pct: float = 0.75,
    stop_pct: float = 3.0,
) -> dict[str, float | str | None]:
    """Single-call bundle for UI / thesis risk strip."""
    kelly = fractional_kelly(win_prob or 0.5) if win_prob is not None else None
    var_p = historical_var_pct(returns_pct or [], alpha=0.05) if returns_pct else None
    notional = position_notional_from_risk(equity, risk_budget_pct, stop_pct)
    vol = statistics.pstdev(returns_pct) if returns_pct and len(returns_pct) > 2 else None
    mean = statistics.fmean(returns_pct) if returns_pct else None
    stress = stress_loss_pct(mean, vol) if mean is not None and vol is not None and vol > 0 else {}
    return {
        "fractional_kelly": kelly,
        "hist_var_95_pct": var_p,
        "risk_notional_hint": notional,
        "stress": stress,
    }
