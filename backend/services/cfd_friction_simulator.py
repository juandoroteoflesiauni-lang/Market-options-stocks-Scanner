from __future__ import annotations
"""CFD spread, slippage and overnight-financing friction simulator."""


import math

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

SYMBOL_SPREAD_MAP: dict[str, float] = {
    "GOOGL": 0.0005,
    "AAPL": 0.0005,
    "TSLA": 0.0005,
    "XAUUSD": 0.0020,
    "XAGUSD": 0.0030,
    "US100.CASH": 0.0008,
    "BTC/USDT": 0.0010,
}

_SYMBOL_ALIASES: dict[str, str] = {
    "GOOG": "GOOGL",
    "US100": "US100.CASH",
    "US100CASH": "US100.CASH",
    "US100.CASH": "US100.CASH",
    "BTCUSDT": "BTC/USDT",
    "BTCUSD": "BTC/USDT",
    "BTC-USD": "BTC/USDT",
}

DEFAULT_SPREAD_PCT = 0.0010
SLIPPAGE_SPREAD_MULTIPLIER = 1.5
LARGE_POSITION_THRESHOLD_USD = 100_000.0
LARGE_POSITION_EXTRA_SLIPPAGE_PER_100K = 0.0005
OVERNIGHT_FINANCING_PER_HOUR_PCT = 0.00005


def apply_cfd_friction(
    symbol: str,
    entry_price: float,
    exit_price: float,
    direction: str,
    holding_duration_hours: float = 0.0,
    position_size_usd: float | None = None,
) -> dict[str, float]:
    """Apply CFD spread, slippage and overnight financing to one trade return.

    INPUTS:
        symbol: Core Funding Lab symbol. Known symbols use ``SYMBOL_SPREAD_MAP``;
            unknown symbols fall back to 0.10%.
        entry_price: Positive trade entry/reference price.
        exit_price: Positive trade exit/reference price.
        direction: ``LONG`` or ``SHORT``. Short returns are directionally inverted.
        holding_duration_hours: Holding time in hours. Intraday holds up to 24h
            do not pay overnight financing; longer holds pay 0.005% per hour.
        position_size_usd: Optional notional. Size above USD 100k adds slippage.

    OUTPUTS:
        dict with spread, slippage, overnight, total friction and adjusted return,
        all expressed as decimal return units. Example: 0.00125 means 0.125%.
    """

    entry = _positive_finite(entry_price, "entry_price")
    exit_ = _positive_finite(exit_price, "exit_price")
    direction_mult = _direction_multiplier(direction)
    spread_pct = SYMBOL_SPREAD_MAP.get(_normalize_symbol(symbol), DEFAULT_SPREAD_PCT)
    slippage_pct = spread_pct * SLIPPAGE_SPREAD_MULTIPLIER

    if position_size_usd is not None and math.isfinite(float(position_size_usd)):
        excess_notional = max(0.0, float(position_size_usd) - LARGE_POSITION_THRESHOLD_USD)
        slippage_pct += (
            excess_notional / LARGE_POSITION_THRESHOLD_USD * LARGE_POSITION_EXTRA_SLIPPAGE_PER_100K
        )

    duration_hours = max(0.0, float(holding_duration_hours))
    overnight_financing_pct = (
        duration_hours * OVERNIGHT_FINANCING_PER_HOUR_PCT if duration_hours > 24.0 else 0.0
    )
    original_return_pct = direction_mult * (exit_ - entry) / entry
    total_friction_pct = spread_pct + slippage_pct + overnight_financing_pct
    adjusted_return_pct = original_return_pct - total_friction_pct

    result = {
        "spread_cost_pct": spread_pct,
        "slippage_cost_pct": slippage_pct,
        "overnight_financing_cost_pct": overnight_financing_pct,
        "total_friction_pct": total_friction_pct,
        "adjusted_return_pct": adjusted_return_pct,
    }
    logger.info(
        "cfd_friction_applied symbol=%s direction=%s spread=%s slippage=%s overnight=%s total=%s adjusted_return=%s",
        _normalize_symbol(symbol),
        str(direction).upper().strip(),
        round(spread_pct, 8),
        round(slippage_pct, 8),
        round(overnight_financing_pct, 8),
        round(total_friction_pct, 8),
        round(adjusted_return_pct, 8),
    )
    return result


def _normalize_symbol(symbol: str) -> str:
    cleaned = str(symbol).upper().strip()
    return _SYMBOL_ALIASES.get(cleaned, cleaned)


def _direction_multiplier(direction: str) -> float:
    cleaned = str(direction).upper().strip()
    if cleaned == "LONG":
        return 1.0
    if cleaned == "SHORT":
        return -1.0
    raise ValueError("direction must be LONG or SHORT")


def _positive_finite(value: float, field_name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{field_name} must be a positive finite number")
    return number
