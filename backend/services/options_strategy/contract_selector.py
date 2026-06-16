"""Selector de contratos para estructuras MVP. # [PD-3][TH]"""

from __future__ import annotations

from datetime import date

from backend.config.logger_setup import get_logger
from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.models.options_strategy import (
    OptionsStrategyInput,
    OptionsStructure,
    SelectedOptionContract,
)
from backend.services.options_strategy._bars import resolve_spot_price
from backend.services.options_strategy._chain import (
    chain_rows,
    dte_from_expiry,
    leg_has_liquidity,
    leg_is_tradeable,
    parse_expiry_date,
    resolve_chain_leg_mark,
)

logger = get_logger(__name__)

DEFAULT_DELTA_BUY = 0.38
DEFAULT_DELTA_SELL = 0.20
DEFAULT_DELTA_SHORT_PUT = 0.25
DEFAULT_DELTA_CREDIT_SHORT = 0.28
DEFAULT_DELTA_CREDIT_LONG = 0.12
_MIN_CREDIT_SPREAD_WIDTH = 5.0
_BUTTERFLY_WING_WIDTH = 5.0


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _pick_expiry(
    rows: list[dict],
    *,
    as_of_date: date,
    dte_min: int,
    dte_max: int,
    min_daily_volume: int = 25,
) -> date | None:
    best: tuple[int, int, date] | None = None
    for item in rows:
        expiry_raw = item.get("expiration") or item.get("expiry")
        expiry = parse_expiry_date(str(expiry_raw) if expiry_raw else None)
        if expiry is None:
            continue
        dte = dte_from_expiry(expiry, as_of=as_of_date)
        if dte < dte_min or dte > dte_max:
            continue
        oi = int(
            _safe_float(item.get("total_oi") or item.get("call_oi"))
            + _safe_float(item.get("put_oi"))
        )
        vol = int(_safe_float(item.get("call_volume")) + _safe_float(item.get("put_volume")))
        liquidity = oi + vol
        if liquidity <= 0:
            has_leg = any(
                leg_is_tradeable(item, prefix=prefix, min_daily_volume=min_daily_volume)
                for prefix in ("call", "put")
            )
            if not has_leg:
                continue
            liquidity = 1
        candidate = (liquidity, -abs(dte - ((dte_min + dte_max) // 2)), expiry)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


def _leg_mark(item: dict, prefix: str) -> float:
    """Mark operativo: mark/mid → NBBO → last → day close."""
    return resolve_chain_leg_mark(item, prefix)


def _contract_from_row(
    inp: OptionsStrategyInput,
    item: dict,
    *,
    right: str,
    side: str,
    expiry: date,
    ratio: int = 1,
) -> SelectedOptionContract | None:
    prefix = "call" if right == "call" else "put"
    strike = _safe_float(item.get("strike"))
    if strike <= 0:
        return None
    oi = int(_safe_float(item.get(f"{prefix}_oi")))
    mark = _leg_mark(item, prefix)
    if mark <= 0:
        return None
    delta = _safe_float(item.get(f"{prefix}_delta"))
    iv = _safe_float(item.get(f"{prefix}_iv"), 0.0) or None
    contract_symbol = item.get(f"{prefix}_contract_ticker")
    dte = dte_from_expiry(expiry, as_of=inp.as_of)
    return SelectedOptionContract(
        underlying=inp.symbol,
        expiry=expiry,
        strike=strike,
        right=right,  # type: ignore[arg-type]
        side=side,  # type: ignore[arg-type]
        delta=delta if delta != 0 else None,
        open_interest=oi,
        mark=mark,
        iv=iv,
        dte=dte,
        contract_symbol=str(contract_symbol) if contract_symbol else None,
        ratio=ratio,
    )


class ContractSelector:
    """Selecciona strikes/expiry para las 4 estructuras MVP."""

    @classmethod
    def select(
        cls,
        inp: OptionsStrategyInput,
        structure: OptionsStructure,
        *,
        config: OptionsStrategyConfigBundle | None = None,
        delta_buy: float = DEFAULT_DELTA_BUY,
        delta_sell: float = DEFAULT_DELTA_SELL,
    ) -> tuple[SelectedOptionContract, ...]:
        active = config or get_options_strategy_config()
        min_vol = active.universe.min_daily_volume
        rows = chain_rows(inp)
        if not rows or structure == OptionsStructure.NO_TRADE:
            return ()

        expiry = _pick_expiry(
            rows,
            as_of_date=inp.as_of.date(),
            dte_min=active.universe.dte_min,
            dte_max=active.universe.dte_max,
            min_daily_volume=active.universe.min_daily_volume,
        )
        if expiry is None:
            return ()

        spot = resolve_spot_price(inp, None)
        same_expiry = [
            row
            for row in rows
            if parse_expiry_date(str(row.get("expiration") or row.get("expiry") or "")) == expiry
        ]
        if not same_expiry:
            return ()

        if structure == OptionsStructure.LONG_CALL:
            pick = cls._closest_delta(
                same_expiry, right="call", target=delta_buy, min_daily_volume=min_vol
            )
            if pick is None:
                return ()
            leg = _contract_from_row(inp, pick.row, right="call", side="long", expiry=expiry)
            return (leg,) if leg else ()

        if structure == OptionsStructure.LONG_PUT:
            pick = cls._closest_delta(
                same_expiry, right="put", target=-delta_buy, min_daily_volume=min_vol
            )
            if pick is None:
                return ()
            leg = _contract_from_row(inp, pick.row, right="put", side="long", expiry=expiry)
            return (leg,) if leg else ()

        if structure in {
            OptionsStructure.CALL_DEBIT_SPREAD,
            OptionsStructure.BULL_CALL_SPREAD,
        }:
            long_pick = cls._closest_delta(
                same_expiry, right="call", target=delta_buy, min_daily_volume=min_vol
            )
            short_pick = cls._closest_delta(
                same_expiry, right="call", target=delta_sell, min_daily_volume=min_vol
            )
            if long_pick is None or short_pick is None:
                return ()
            long_strike = _safe_float(long_pick.row.get("strike"))
            short_strike = _safe_float(short_pick.row.get("strike"))
            if long_strike >= short_strike:
                return ()
            return (
                _contract_from_row(inp, long_pick.row, right="call", side="long", expiry=expiry),
                _contract_from_row(inp, short_pick.row, right="call", side="short", expiry=expiry),
            )

        if structure == OptionsStructure.PUT_DEBIT_SPREAD:
            long_pick = cls._closest_delta(
                same_expiry, right="put", target=-delta_buy, min_daily_volume=min_vol
            )
            short_pick = cls._closest_delta(
                same_expiry, right="put", target=-delta_sell, min_daily_volume=min_vol
            )
            if long_pick is None or short_pick is None:
                return ()
            long_strike = _safe_float(long_pick.row.get("strike"))
            short_strike = _safe_float(short_pick.row.get("strike"))
            if long_strike <= short_strike:
                return ()
            return (
                _contract_from_row(inp, long_pick.row, right="put", side="long", expiry=expiry),
                _contract_from_row(inp, short_pick.row, right="put", side="short", expiry=expiry),
            )

        if structure == OptionsStructure.SHORT_PUT:
            pick = cls._closest_delta(
                same_expiry,
                right="put",
                target=-DEFAULT_DELTA_SHORT_PUT,
                min_daily_volume=min_vol,
            )
            if pick is None:
                return ()
            leg = _contract_from_row(inp, pick.row, right="put", side="short", expiry=expiry)
            return (leg,) if leg else ()

        if structure == OptionsStructure.PUT_CREDIT_SPREAD:
            spot = resolve_spot_price(inp, None)
            eligible = sorted(
                [
                    row
                    for row in same_expiry
                    if _safe_float(row.get("strike")) < spot
                    and leg_has_liquidity(row, prefix="put", min_daily_volume=min_vol)
                    and resolve_chain_leg_mark(row, "put") > 0
                ],
                key=lambda row: _safe_float(row.get("strike")),
                reverse=True,
            )
            if len(eligible) < 2:
                return ()
            short_row = eligible[0]
            long_row = eligible[-1]
            for row in eligible[1:]:
                width = _safe_float(short_row.get("strike")) - _safe_float(row.get("strike"))
                if width >= _MIN_CREDIT_SPREAD_WIDTH:
                    long_row = row
                    break
            short_strike = _safe_float(short_row.get("strike"))
            long_strike = _safe_float(long_row.get("strike"))
            if short_strike <= long_strike:
                return ()
            return (
                _contract_from_row(inp, short_row, right="put", side="short", expiry=expiry),
                _contract_from_row(inp, long_row, right="put", side="long", expiry=expiry),
            )

        if structure == OptionsStructure.CALL_BUTTERFLY:
            spot = resolve_spot_price(inp, None)
            strikes_sorted = sorted(
                same_expiry,
                key=lambda row: _safe_float(row.get("strike")),
            )
            if len(strikes_sorted) < 3:
                return ()
            interior = strikes_sorted[1:-1]
            body_row = min(
                interior,
                key=lambda row: (
                    abs(_safe_float(row.get("strike")) - spot),
                    _safe_float(row.get("strike")),
                ),
            )
            body_idx = strikes_sorted.index(body_row)
            lower_row = strikes_sorted[body_idx - 1]
            upper_row = strikes_sorted[body_idx + 1]
            lower_strike = _safe_float(lower_row.get("strike"))
            body_strike = _safe_float(body_row.get("strike"))
            upper_strike = _safe_float(upper_row.get("strike"))
            if not (lower_strike < body_strike < upper_strike):
                return ()
            lower_leg = _contract_from_row(
                inp, lower_row, right="call", side="long", expiry=expiry
            )
            body_leg = _contract_from_row(
                inp, body_row, right="call", side="short", expiry=expiry, ratio=2
            )
            upper_leg = _contract_from_row(
                inp, upper_row, right="call", side="long", expiry=expiry
            )
            if lower_leg is None or body_leg is None or upper_leg is None:
                return ()
            return (lower_leg, body_leg, upper_leg)

        if structure == OptionsStructure.CALL_CREDIT_SPREAD:
            short_pick = cls._closest_delta(
                same_expiry,
                right="call",
                target=DEFAULT_DELTA_CREDIT_SHORT,
                min_daily_volume=min_vol,
            )
            long_pick = cls._closest_delta(
                same_expiry,
                right="call",
                target=DEFAULT_DELTA_CREDIT_LONG,
                min_daily_volume=min_vol,
            )
            if short_pick is None or long_pick is None:
                return ()
            short_strike = _safe_float(short_pick.row.get("strike"))
            long_strike = _safe_float(long_pick.row.get("strike"))
            if short_strike >= long_strike:
                return ()
            return (
                _contract_from_row(inp, short_pick.row, right="call", side="short", expiry=expiry),
                _contract_from_row(inp, long_pick.row, right="call", side="long", expiry=expiry),
            )

        return ()

    @staticmethod
    def _closest_delta(
        rows: list[dict],
        *,
        right: str,
        target: float,
        min_daily_volume: int = 25,
    ) -> "_DeltaPick | None":
        prefix = "call" if right == "call" else "put"
        best: tuple[float, dict] | None = None
        for item in rows:
            if not leg_is_tradeable(
                item, prefix=prefix, min_daily_volume=min_daily_volume
            ):
                continue
            delta = _safe_float(item.get(f"{prefix}_delta"))
            distance = abs(delta - target)
            candidate = (distance, item)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:
            return None
        return _DeltaPick(row=best[1])


class _DeltaPick:
    __slots__ = ("row",)

    def __init__(self, row: dict) -> None:
        self.row = row


__all__ = [
    "ContractSelector",
    "DEFAULT_DELTA_BUY",
    "DEFAULT_DELTA_SELL",
    "DEFAULT_DELTA_SHORT_PUT",
    "DEFAULT_DELTA_CREDIT_SHORT",
    "DEFAULT_DELTA_CREDIT_LONG",
]
