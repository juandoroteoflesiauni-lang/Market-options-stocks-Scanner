"""Implementation Shortfall (Perold) — métricas TCA por ejecución. # [PD-2][TH]"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

Side = Literal["BUY", "SELL", "buy", "sell"]


@dataclass(frozen=True)
class TcaExecutionMetrics:
    """Métricas TCA de un fill completo o parcial."""

    route: str
    decision_price: float
    fill_price: float
    quantity: float
    fill_rate: float
    implementation_shortfall_bps: float
    slippage_usd: float
    delay_ms: int
    decision_timestamp: str
    execution_timestamp: str


def _parse_ts(raw: str | datetime | None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    token = str(raw).strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _delay_ms(
    decision_timestamp: str | datetime | None,
    execution_timestamp: str | datetime | None,
) -> int:
    start = _parse_ts(decision_timestamp)
    end = _parse_ts(execution_timestamp)
    if start is None or end is None:
        return 0
    delta = (end - start).total_seconds() * 1000.0
    return max(0, int(round(delta)))


def _normalize_side(side: Side) -> str:
    return str(side).upper()


def compute_implementation_shortfall(
    *,
    route: str,
    side: Side,
    quantity: float,
    decision_price: float,
    fill_price: float,
    decision_timestamp: str | datetime | None = None,
    execution_timestamp: str | datetime | None = None,
    fill_rate: float = 1.0,
) -> TcaExecutionMetrics:
    """Calcula IS firmado: positivo = coste adverso (peor que arrival).

    Para BUY: slippage = (fill - decision) * qty.
    Para SELL: slippage = (decision - fill) * qty.
    """
    if decision_price <= 0:
        raise ValueError(f"decision_price must be > 0, got {decision_price}")
    if quantity <= 0:
        raise ValueError(f"quantity must be > 0, got {quantity}")

    qty = quantity * max(0.0, min(1.0, fill_rate))
    side_norm = _normalize_side(side)
    if side_norm == "BUY":
        adverse_per_unit = fill_price - decision_price
    else:
        adverse_per_unit = decision_price - fill_price

    slippage_usd = adverse_per_unit * qty
    is_bps = (adverse_per_unit / decision_price) * 10_000.0

    exec_ts = execution_timestamp or datetime.now(tz=UTC).isoformat()
    dec_ts = decision_timestamp or exec_ts

    return TcaExecutionMetrics(
        route=route.strip().upper(),
        decision_price=round(decision_price, 6),
        fill_price=round(fill_price, 6),
        quantity=round(quantity, 8),
        fill_rate=round(fill_rate, 4),
        implementation_shortfall_bps=round(is_bps, 2),
        slippage_usd=round(slippage_usd, 4),
        delay_ms=_delay_ms(dec_ts, exec_ts),
        decision_timestamp=str(dec_ts),
        execution_timestamp=str(exec_ts),
    )


__all__ = ["TcaExecutionMetrics", "compute_implementation_shortfall"]
