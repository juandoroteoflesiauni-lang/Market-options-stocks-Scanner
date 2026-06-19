"""Kelly fraccional desde journal reciente (Fase C). # [TH]"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.profit_calibration import ProfitCalibrationPolicy

logger = get_logger(__name__)


def _route_bucket(trade: dict[str, Any]) -> str:
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


def compute_fractional_kelly(
    pnls: list[float],
    *,
    fraction: float,
) -> float:
    """Kelly fraccional en [0, 1] a partir de PnL realizados."""
    if len(pnls) < 2:
        return 0.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    if not wins or not losses:
        return 0.0
    win_rate = len(wins) / len(pnls)
    avg_win = statistics.mean(wins)
    avg_loss = abs(statistics.mean(losses))
    if avg_loss <= 0:
        return 0.0
    b = avg_win / avg_loss
    full_kelly = win_rate - (1.0 - win_rate) / b if b > 0 else 0.0
    return max(0.0, min(1.0, full_kelly * fraction))


def kelly_notional_scalar(
    *,
    route: str | None = None,
    policy: ProfitCalibrationPolicy | None = None,
    db_path: str | Path | None = None,
) -> float:
    """Escala notional 0.35-1.0 segun edge reciente del journal."""
    cfg = policy or ProfitCalibrationPolicy.from_env()
    if not cfg.kelly_enabled or cfg.session_mode != "profit":
        return 1.0

    from backend.services.trade_journal_service import list_trades

    path = Path(db_path or cfg.journal_db_path)
    if not path.exists():
        return 1.0

    bucket = route.upper() if route else None
    trades = list_trades(path, limit=max(cfg.kelly_min_sample * 4, 80))
    pnls: list[float] = []
    for trade in trades:
        if bucket and _route_bucket(trade) != bucket:
            continue
        pnl = _pnl_value(trade)
        if pnl is not None:
            pnls.append(pnl)
        if len(pnls) >= cfg.rolling_pf_window:
            break

    if len(pnls) < cfg.kelly_min_sample:
        return 1.0

    fractional = compute_fractional_kelly(pnls, fraction=cfg.kelly_fraction)
    # Mapear Kelly fraccional (tipico 0-0.25) a scalar operativo
    scalar = fractional / max(cfg.kelly_fraction, 1e-9)
    scalar = max(cfg.kelly_min_scalar, min(cfg.kelly_max_scalar, scalar))
    logger.debug(
        "kelly_session_sizer route=%s sample=%d fractional=%.4f scalar=%.4f",
        bucket or "ALL",
        len(pnls),
        fractional,
        scalar,
    )
    return scalar


__all__ = ["compute_fractional_kelly", "kelly_notional_scalar"]
