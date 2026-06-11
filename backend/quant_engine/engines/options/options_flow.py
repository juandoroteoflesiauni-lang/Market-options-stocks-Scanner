"""Options flow signal engine without scraping or paid external dependencies."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field


class OptionFlowEvent(BaseModel):
    """Normalized option-flow row derived from supplied chain or snapshot data."""

    model_config = ConfigDict(frozen=True)

    symbol: str | None = None
    underlying: str | None = None
    expiry: str | None = None
    strike: float = 0.0
    right: str = "unknown"
    side: str = "unknown"
    volume: float = 0.0
    open_interest: float = 0.0
    mark: float | None = None
    premium: float = 0.0
    spot: float | None = None
    dte: int | None = None
    moneyness: str = "unknown"
    trade_id: str | None = None
    directional_delta: float = 0.0

    @classmethod
    def from_row(cls: type[OptionFlowEvent], row: dict[str, object]) -> OptionFlowEvent:
        """Normalize one raw flow row without assuming a vendor schema."""
        right = cls._normalize_right(row.get("right", row.get("option_type", "unknown")))
        side = cls._normalize_side(
            row.get("side", row.get("aggressor", row.get("condition", "unknown")))
        )
        volume = _as_float(row.get("volume"), 0.0)
        open_interest = _as_float(row.get("open_interest", row.get("oi")), 0.0)
        mark = _optional_float(row.get("mark", row.get("price", row.get("last"))))
        premium = _optional_float(row.get("premium", row.get("notional")))
        if premium is None:
            premium = volume * (mark or 0.0) * 100.0
        strike = _as_float(row.get("strike"), 0.0)
        spot = _optional_float(row.get("spot", row.get("underlying_price")))
        return cls(
            symbol=_optional_str(row.get("symbol")),
            underlying=_optional_str(row.get("underlying", row.get("ticker"))),
            expiry=_optional_str(row.get("expiry", row.get("expiration"))),
            strike=strike,
            right=right,
            side=side,
            volume=volume,
            open_interest=open_interest,
            mark=mark,
            premium=float(premium),
            spot=spot,
            dte=_optional_int(row.get("dte", row.get("days_to_expiry"))),
            moneyness=cls._moneyness(right, strike, spot),
            trade_id=_optional_str(row.get("trade_id", row.get("combo_id", row.get("spread_id")))),
            directional_delta=cls._directional_delta(right, side),
        )

    @staticmethod
    def _normalize_right(value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"c", "call", "calls"}:
            return "call"
        if text in {"p", "put", "puts"}:
            return "put"
        return "unknown"

    @staticmethod
    def _normalize_side(value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"ask", "buy", "buyer", "bought", "at_ask", "above_ask"}:
            return "buy"
        if text in {"bid", "sell", "seller", "sold", "at_bid", "below_bid"}:
            return "sell"
        return "unknown"

    @staticmethod
    def _moneyness(right: str, strike: float, spot: float | None) -> str:
        if spot is None or spot <= 0 or strike <= 0 or right not in {"call", "put"}:
            return "unknown"
        distance = abs(strike - spot) / spot
        if distance <= 0.01:
            return "atm"
        if right == "call":
            return "itm" if strike < spot else "otm"
        return "itm" if strike > spot else "otm"

    @staticmethod
    def _directional_delta(right: str, side: str) -> float:
        if right == "call" and side == "buy":
            return 1.0
        if right == "call" and side == "sell":
            return -1.0
        if right == "put" and side == "buy":
            return -1.0
        if right == "put" and side == "sell":
            return 1.0
        return 0.0


class OptionsFlowSignal(BaseModel):
    """Aggregated options flow signal."""

    model_config = ConfigDict(frozen=True)

    call_put_volume_ratio: float | None = None
    call_put_oi_ratio: float | None = None
    total_volume: float = 0.0
    total_premium: float = 0.0
    premium_intensity: float = 0.0
    unusual_strikes: list[dict[str, float | str]] = Field(default_factory=list)
    max_oi_expiry: str | None = None
    dte_concentration: list[dict[str, float | str]] = Field(default_factory=list)
    moneyness_pressure: dict[str, float] = Field(default_factory=dict)
    strike_clusters: list[dict[str, float]] = Field(default_factory=list)
    ambiguity_flags: list[str] = Field(default_factory=list)
    directional_score: float = Field(0.0, ge=-1.0, le=1.0)
    institutional_signal: str = "unknown"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)


class OptionsFlowSignalEngine:
    """Computes simple flow aggregates from an available option chain."""

    def analyze(
        self: OptionsFlowSignalEngine,
        rows: list[dict[str, object]],
    ) -> OptionsFlowSignal:
        """Return call/put pressure and limitations when inputs are sparse."""
        if not rows:
            return OptionsFlowSignal(
                directional_score=0.0,
                confidence=0.0,
                limitations=["No option chain rows supplied."],
            )
        events = [OptionFlowEvent.from_row(row) for row in rows]
        calls = [event for event in events if event.right == "call"]
        puts = [event for event in events if event.right == "put"]
        call_vol = sum(event.volume for event in calls)
        put_vol = sum(event.volume for event in puts)
        call_oi = sum(event.open_interest for event in calls)
        put_oi = sum(event.open_interest for event in puts)
        total_volume = call_vol + put_vol
        total_premium = sum(event.premium for event in events)
        limitations: list[str] = []
        if call_vol + put_vol <= 0 or call_oi + put_oi <= 0:
            limitations.append(
                "Volume or open interest unavailable; flow signal is not actionable."
            )
        unknown_sides = sum(1 for event in events if event.side == "unknown" and event.volume > 0)
        if unknown_sides:
            limitations.append(
                "Aggressor side unavailable for at least one row; directional score is limited."
            )

        avg_oi = (call_oi + put_oi) / max(1, len(rows))
        unusual = []
        for event in events:
            if event.volume > 0 and avg_oi > 0 and event.volume > 2.0 * avg_oi:
                unusual.append(
                    {
                        "strike": event.strike,
                        "right": event.right,
                        "volume": event.volume,
                        "open_interest": event.open_interest,
                        "premium": round(event.premium, 8),
                    }
                )
        expiry_oi = _sum_by(events, lambda event: event.expiry or "", "open_interest")
        dte_concentration = self._dte_concentration(events, total_premium)
        moneyness_pressure = self._moneyness_pressure(events)
        strike_clusters = self._strike_clusters(events)
        ambiguity_flags = self._ambiguity_flags(events)
        signed_premium = sum(event.premium * event.directional_delta for event in events)
        directional_score = signed_premium / total_premium if total_premium > 0 else 0.0
        ratio_vol = call_vol / put_vol if put_vol > 0 else None
        ratio_oi = call_oi / put_oi if put_oi > 0 else None
        if ambiguity_flags and abs(directional_score) < 0.25:
            signal = "ambiguous"
        elif directional_score > 0.25:
            signal = (
                "call_pressure" if ratio_vol is not None and ratio_vol >= 1.0 else "bullish_flow"
            )
        elif directional_score < -0.25:
            signal = (
                "put_pressure" if ratio_vol is not None and ratio_vol <= 1.0 else "bearish_flow"
            )
        elif ratio_vol is None:
            signal = "unknown"
        elif ratio_vol > 1.5 and not unknown_sides:
            signal = "call_pressure"
        elif ratio_vol < 0.67 and not unknown_sides:
            signal = "put_pressure"
        else:
            signal = "balanced"
        return OptionsFlowSignal(
            call_put_volume_ratio=ratio_vol,
            call_put_oi_ratio=ratio_oi,
            total_volume=round(total_volume, 8),
            total_premium=round(total_premium, 8),
            premium_intensity=round(total_premium / total_volume, 8) if total_volume > 0 else 0.0,
            unusual_strikes=[
                {
                    "strike": item["strike"] if isinstance(item["strike"], float) else 0.0,
                    "premium": item["premium"] if isinstance(item["premium"], float) else 0.0,
                    "volume": item["volume"] if isinstance(item["volume"], float) else 0.0,
                    "right": str(item["right"]),
                }
                for item in unusual
            ],
            max_oi_expiry=(
                max(expiry_oi, key=lambda expiry: expiry_oi[expiry]) if expiry_oi else None
            ),
            dte_concentration=dte_concentration,
            moneyness_pressure=moneyness_pressure,
            strike_clusters=strike_clusters,
            ambiguity_flags=ambiguity_flags,
            directional_score=round(directional_score, 8),
            institutional_signal=signal,
            confidence=0.0 if limitations else max(0.2, min(0.85, abs(directional_score) + 0.35)),
            limitations=limitations,
        )

    def _dte_concentration(
        self: OptionsFlowSignalEngine,
        events: list[OptionFlowEvent],
        total_premium: float,
    ) -> list[dict[str, float | str]]:
        buckets = {
            "0-7": 0.0,
            "8-30": 0.0,
            "31-60": 0.0,
            "61+": 0.0,
            "unknown": 0.0,
        }
        for event in events:
            buckets[_dte_bucket(event.dte)] += event.premium
        return [
            {
                "bucket": bucket,
                "premium": round(premium, 8),
                "premium_share": round(premium / total_premium, 8) if total_premium > 0 else 0.0,
            }
            for bucket, premium in sorted(buckets.items(), key=lambda item: item[1], reverse=True)
            if premium > 0
        ]

    def _moneyness_pressure(
        self: OptionsFlowSignalEngine, events: list[OptionFlowEvent]
    ) -> dict[str, float]:
        pressure: dict[str, float] = defaultdict(float)
        for event in events:
            key = f"{event.moneyness}_{event.right}"
            pressure[key] += event.premium
        return {
            key: round(value, 8)
            for key, value in sorted(pressure.items())
            if key != "unknown_unknown"
        }

    def _strike_clusters(
        self: OptionsFlowSignalEngine, events: list[OptionFlowEvent]
    ) -> list[dict[str, float]]:
        by_strike: dict[float, float] = defaultdict(float)
        by_volume: dict[float, float] = defaultdict(float)
        for event in events:
            by_strike[event.strike] += event.premium
            by_volume[event.strike] += event.volume
        return [
            {"strike": strike, "premium": round(premium, 8), "volume": round(by_volume[strike], 8)}
            for strike, premium in sorted(by_strike.items(), key=lambda item: item[1], reverse=True)
            if premium > 0
        ]

    def _ambiguity_flags(self: OptionsFlowSignalEngine, events: list[OptionFlowEvent]) -> list[str]:
        flags: list[str] = []
        trades: dict[str, list[OptionFlowEvent]] = defaultdict(list)
        for event in events:
            if event.trade_id:
                trades[event.trade_id].append(event)
        for trade_id, legs in trades.items():
            rights = {event.right for event in legs}
            if len(legs) > 1 and {"call", "put"} <= rights:
                flags.append(
                    f"Trade {trade_id} looks spread/hedging-like; avoid single-leg directional read."
                )
        return flags


def _as_float(value: object, default: float) -> float:
    try:
        return float(value) if isinstance(value, (int, float, str, bytes, bytearray)) else default
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float, str, bytes, bytearray)):
            return float(value)
        return None
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value) if isinstance(value, (int, float, str, bytes, bytearray)) else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sum_by(
    events: list[OptionFlowEvent],
    key_fn: Callable[[OptionFlowEvent], str],
    field: str,
) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for event in events:
        key = key_fn(event)
        if key:
            totals[key] += float(getattr(event, field))
    return dict(totals)


def _dte_bucket(dte: int | None) -> str:
    if dte is None:
        return "unknown"
    if dte <= 7:
        return "0-7"
    if dte <= 30:
        return "8-30"
    if dte <= 60:
        return "31-60"
    return "61+"
