"""Gate de profit factor rolling por ruta (Fase C). # [PD-3][TH]"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.config.profit_calibration import ProfitCalibrationPolicy

logger = get_logger(__name__)

REASON_ROLLING_PF_LOW = "rolling_profit_factor_below_minimum"


@dataclass(frozen=True)
class RollingPFVerdict:
    """Resultado del gate PF rolling."""

    allowed: bool
    profit_factor: float | None
    sample_size: int
    route: str
    reason_code: str | None = None
    floor: float = 0.0


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


def _profit_factor_from_pnls(pnls: list[float]) -> float | None:
    if not pnls:
        return None
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses <= 0:
        return 99.0 if wins > 0 else None
    return min(wins / losses, 99.0)


def evaluate_rolling_pf_gate(
    *,
    route: str | None = None,
    policy: ProfitCalibrationPolicy | None = None,
    db_path: str | Path | None = None,
) -> RollingPFVerdict:
    """Bloquea entradas si el PF rolling de la ruta cae bajo el piso de sesión."""
    cfg = policy or ProfitCalibrationPolicy.from_env()
    bucket = (route or "PORTFOLIO").upper()
    floor = cfg.rolling_pf_floor

    if not cfg.rolling_pf_enabled:
        return RollingPFVerdict(
            allowed=True,
            profit_factor=None,
            sample_size=0,
            route=bucket,
            floor=floor,
        )

    from backend.services.trade_journal_service import list_trades

    path = Path(db_path or cfg.journal_db_path)
    if not path.exists():
        return RollingPFVerdict(
            allowed=True,
            profit_factor=None,
            sample_size=0,
            route=bucket,
            floor=floor,
        )

    trades = list_trades(path, limit=max(cfg.rolling_pf_window * 3, 100))
    filtered: list[float] = []
    for trade in trades:
        if route and _route_bucket(trade) != bucket:
            continue
        pnl = _pnl_value(trade)
        if pnl is not None:
            filtered.append(pnl)
        if len(filtered) >= cfg.rolling_pf_window:
            break

    sample = len(filtered)
    if sample < cfg.rolling_pf_min_sample:
        return RollingPFVerdict(
            allowed=True,
            profit_factor=_profit_factor_from_pnls(filtered),
            sample_size=sample,
            route=bucket,
            floor=floor,
        )

    pf = _profit_factor_from_pnls(filtered)
    allowed = pf is None or pf >= floor
    if not allowed:
        logger.warning(
            "rolling_pf_gate.block route=%s pf=%.3f floor=%.3f sample=%d mode=%s",
            bucket,
            pf or 0.0,
            floor,
            sample,
            cfg.session_mode,
        )
    return RollingPFVerdict(
        allowed=allowed,
        profit_factor=round(pf, 4) if pf is not None else None,
        sample_size=sample,
        route=bucket,
        reason_code=None if allowed else REASON_ROLLING_PF_LOW,
        floor=floor,
    )


def build_profit_calibration_eod_report(
    *,
    db_path: str | Path | None = None,
    policy: ProfitCalibrationPolicy | None = None,
) -> dict[str, Any]:
    """Reporte EOD: PF rolling + Kelly scalar por ruta."""
    cfg = policy or ProfitCalibrationPolicy.from_env()
    from backend.services.calibration.kelly_session_sizer import kelly_notional_scalar

    routes = ("ALPACA", "BINGX", "R1", "R2", "PORTFOLIO")
    by_route: dict[str, Any] = {}
    for route in routes:
        pf = evaluate_rolling_pf_gate(route=None if route == "PORTFOLIO" else route, policy=cfg)
        kelly = kelly_notional_scalar(
            route=None if route == "PORTFOLIO" else route,
            policy=cfg,
            db_path=db_path,
        )
        by_route[route] = {
            "rolling_pf": pf.profit_factor,
            "pf_floor": pf.floor,
            "pf_gate_allowed": pf.allowed,
            "sample_size": pf.sample_size,
            "kelly_scalar": round(kelly, 4),
        }

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "session_mode": cfg.session_mode,
        "rolling_pf_enabled": cfg.rolling_pf_enabled,
        "kelly_enabled": cfg.kelly_enabled,
        "by_route": by_route,
    }


__all__ = [
    "REASON_ROLLING_PF_LOW",
    "RollingPFVerdict",
    "build_profit_calibration_eod_report",
    "evaluate_rolling_pf_gate",
]
