from __future__ import annotations
from typing import Any
"""Pure Greek-flow snapshot calculations for normalized option chains."""


import math

CONTRACT_MULTIPLIER = 100.0
REQUIRED_COMPONENTS = (
    "strike",
    "expiry",
    "option_type",
    "gamma",
    "delta",
    "vanna",
    "charm",
    "implied_volatility",
    "open_interest",
)


def compute_greek_flow_snapshot(
    chain_rows: list[dict[str, Any]], spot: float | None
) -> dict[str, Any]:
    """Compute institutional Greek-flow exposure from normalized option-chain rows.

    The function is intentionally pure and provider-agnostic: callers pass already-normalized
    option rows, and the returned dict is safe to serialize or fold into scanner features.
    """
    spot_value = _float(spot)
    rows = [row for row in chain_rows if isinstance(row, dict)]
    missing_components = _missing_components(rows)

    strike_buckets: dict[float, dict[str, float]] = {}
    call_wall_candidates: dict[float, float] = {}
    put_wall_candidates: dict[float, float] = {}

    net_gamma = 0.0
    net_delta = 0.0
    net_vanna = 0.0
    net_charm = 0.0
    zero_dte_gamma_pressure = 0.0
    usable_rows = 0

    for row in rows:
        strike = _float(row.get("strike"))
        option_type = _option_type(row)
        open_interest = _non_negative_float(row.get("open_interest"))
        if strike is None or option_type is None or open_interest is None:
            continue

        usable_rows += 1
        sign = 1.0 if option_type == "call" else -1.0
        gamma = _float(row.get("gamma")) or 0.0
        delta = _signed_greek(row.get("delta"), sign)
        vanna = _signed_greek(row.get("vanna"), sign)
        charm = _signed_greek(row.get("charm"), sign)

        gamma_exposure = _gamma_exposure(gamma, open_interest, sign, spot_value)
        delta_exposure = _linear_exposure(delta, open_interest, spot_value)
        vanna_exposure = _linear_exposure(vanna, open_interest, spot_value)
        charm_exposure = _linear_exposure(charm, open_interest, spot_value)

        net_gamma += gamma_exposure
        net_delta += delta_exposure
        net_vanna += vanna_exposure
        net_charm += charm_exposure

        bucket = strike_buckets.setdefault(
            strike,
            {
                "net_gamma_exposure": 0.0,
                "call_gamma_exposure": 0.0,
                "put_gamma_exposure": 0.0,
                "open_interest": 0.0,
            },
        )
        bucket["net_gamma_exposure"] += gamma_exposure
        bucket["open_interest"] += open_interest
        if option_type == "call":
            bucket["call_gamma_exposure"] += gamma_exposure
            call_wall_candidates[strike] = call_wall_candidates.get(strike, 0.0) + open_interest
        else:
            bucket["put_gamma_exposure"] += gamma_exposure
            put_wall_candidates[strike] = put_wall_candidates.get(strike, 0.0) + open_interest

        if _is_zero_dte(row.get("expiry")):
            zero_dte_gamma_pressure += abs(gamma_exposure)

    pressure_by_strike = _pressure_by_strike(strike_buckets)
    gamma_flip = _gamma_flip(pressure_by_strike)
    source_tier = "full_chain_greeks" if not missing_components and usable_rows else "degraded"
    data_quality_score = _data_quality_score(
        rows=rows,
        usable_rows=usable_rows,
        missing_components=missing_components,
        spot=spot_value,
    )

    return {
        "source_tier": source_tier,
        "data_quality_score": data_quality_score,
        "missing_components": missing_components,
        "row_count": len(rows),
        "usable_row_count": usable_rows,
        "net_gamma_exposure": round(net_gamma, 4),
        "net_delta_exposure": round(net_delta, 4),
        "net_vanna_exposure": round(net_vanna, 4),
        "net_charm_exposure": round(net_charm, 4),
        "call_wall": _wall(call_wall_candidates),
        "put_wall": _wall(put_wall_candidates),
        "gamma_flip": gamma_flip,
        "zero_gamma_distance_pct": _distance_pct(spot_value, gamma_flip),
        "zero_dte_gamma_pressure": round(zero_dte_gamma_pressure, 4),
        "pressure_by_strike": pressure_by_strike,
    }


def _missing_components(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return list(REQUIRED_COMPONENTS)

    missing: list[str] = []
    for component in REQUIRED_COMPONENTS:
        if component == "option_type":
            if not any(_option_type(row) is not None for row in rows):
                missing.append(component)
            continue
        aliases = _aliases(component)
        if not any(_has_value(row, aliases) for row in rows):
            missing.append(component)
    return missing


def _aliases(component: str) -> tuple[str, ...]:
    if component == "option_type":
        return ("option_type", "call_put", "type")
    if component == "implied_volatility":
        return ("implied_volatility", "iv")
    return (component,)


def _has_value(row: dict[str, Any], aliases: tuple[str, ...]) -> bool:
    return any(row.get(alias) is not None and row.get(alias) != "" for alias in aliases)


def _option_type(row: dict[str, Any]) -> str | None:
    raw = row.get("option_type")
    if raw is None:
        raw = row.get("call_put")
    if raw is None:
        raw = row.get("type")
    value = str(raw or "").strip().lower()
    if value in {"call", "c"}:
        return "call"
    if value in {"put", "p"}:
        return "put"
    return None


def _gamma_exposure(
    gamma: float,
    open_interest: float,
    option_sign: float,
    spot: float | None,
) -> float:
    spot_factor = spot if spot is not None and spot > 0 else 1.0
    return abs(gamma) * option_sign * open_interest * CONTRACT_MULTIPLIER * spot_factor**2 * 0.01


def _linear_exposure(greek: float, open_interest: float, spot: float | None) -> float:
    spot_factor = spot if spot is not None and spot > 0 else 1.0
    return greek * open_interest * CONTRACT_MULTIPLIER * spot_factor


def _signed_greek(value: object, option_sign: float) -> float:
    number = _float(value)
    if number is None:
        return 0.0
    if option_sign < 0 and number > 0:
        return -number
    if option_sign > 0 and number < 0:
        return abs(number)
    return number


def _pressure_by_strike(strike_buckets: dict[float, dict[str, float]]) -> list[dict[str, float]]:
    pressure = [
        {
            "strike": strike,
            "net_gamma_exposure": round(bucket["net_gamma_exposure"], 4),
            "call_gamma_exposure": round(bucket["call_gamma_exposure"], 4),
            "put_gamma_exposure": round(bucket["put_gamma_exposure"], 4),
            "open_interest": round(bucket["open_interest"], 4),
        }
        for strike, bucket in strike_buckets.items()
    ]
    pressure.sort(key=lambda item: abs(item["net_gamma_exposure"]), reverse=True)
    pressure = pressure[:64]
    pressure.sort(key=lambda item: item["strike"])
    return pressure


def _gamma_flip(pressure_by_strike: list[dict[str, float]]) -> float | None:
    if not pressure_by_strike:
        return None

    cumulative = 0.0
    previous_strike: float | None = None
    previous_cumulative: float | None = None
    closest_strike = pressure_by_strike[0]["strike"]
    closest_abs = math.inf

    for item in pressure_by_strike:
        strike = item["strike"]
        cumulative += item["net_gamma_exposure"]
        abs_cumulative = abs(cumulative)
        if abs_cumulative < closest_abs:
            closest_abs = abs_cumulative
            closest_strike = strike
        if (
            previous_cumulative is not None
            and previous_strike is not None
            and previous_cumulative * cumulative < 0
        ):
            distance = strike - previous_strike
            denominator = abs(previous_cumulative) + abs(cumulative)
            if denominator <= 0:
                return round(strike, 4)
            return round(previous_strike + distance * abs(previous_cumulative) / denominator, 4)
        if cumulative == 0.0:
            return round(strike, 4)
        previous_strike = strike
        previous_cumulative = cumulative

    return round(closest_strike, 4)


def _wall(candidates: dict[float, float]) -> float | None:
    if not candidates:
        return None
    return round(max(candidates.items(), key=lambda item: (item[1], -abs(item[0])))[0], 4)


def _distance_pct(spot: float | None, level: float | None) -> float | None:
    if spot is None or level is None or spot <= 0 or level <= 0:
        return None
    return round((spot - level) / spot * 100.0, 4)


def _data_quality_score(
    *,
    rows: list[dict[str, Any]],
    usable_rows: int,
    missing_components: list[str],
    spot: float | None,
) -> float:
    if not rows:
        return 0.0

    component_score = 1.0 - (len(missing_components) / len(REQUIRED_COMPONENTS))
    usable_score = usable_rows / max(len(rows), 1)
    spot_score = 1.0 if spot is not None and spot > 0 else 0.65
    score = component_score * 0.85 + usable_score * 0.10 + spot_score * 0.05
    return round(max(0.0, min(1.0, score)), 4)


def _is_zero_dte(expiry: object) -> bool:
    if expiry is None:
        return False
    if isinstance(expiry, int | float):
        return expiry <= 0
    raw = str(expiry).strip().lower()
    return raw in {"0", "0d", "0dte", "zero_dte", "same_day"}


def _non_negative_float(value: object) -> float | None:
    number = _float(value)
    if number is None:
        return None
    return max(0.0, number)


def _float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
