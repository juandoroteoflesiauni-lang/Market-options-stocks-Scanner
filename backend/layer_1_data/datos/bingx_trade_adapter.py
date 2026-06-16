from __future__ import annotations
from typing import Literal, Any
"""BingX trade tape + L2 → institutional microstructure bundle (Layer 1)."""


from dataclasses import asdict, dataclass, field

from backend.config.logger_setup import get_logger
from backend.layer_1_data.datos.bingx_l2_adapter import (
    BingXL2AdapterResult,
    build_l2_snapshot_from_bingx_depth,
)
from backend.quant_engine.math.technical.vpin_from_trades import (
    compute_cvd_from_trades,
    compute_vpin_from_signed_volume,
)

logger = get_logger(__name__)

TradeSide = Literal["buy", "sell", "unknown"]


@dataclass(frozen=True)
class BingXTradeTick:
    price: float
    quantity: float
    side: TradeSide
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class BingXMicrostructureBundle:
    """Normalized real microstructure for scanner / overlay."""

    symbol: str
    venue_symbol: str
    ok: bool
    reason: str
    source: str = "bingx_trade_l2_v1"
    vpin: float | None = None
    volume_imbalance: float | None = None
    cvd: float | None = None
    period_delta: float | None = None
    poc_price: float | None = None
    vah_price: float | None = None
    val_price: float | None = None
    l2_spread: float | None = None
    l2_imbalance: float | None = None
    trade_count: int = 0
    method_vpin: str = "vpin_trade_tape_v1"
    order_book: dict[str, Any] | None = None
    fallback_used: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_bingx_trades(raw_trades: list[dict[str, Any]]) -> list[BingXTradeTick]:
    """Parse BingX spot/perp trade list into normalized ticks."""
    ticks: list[BingXTradeTick] = []
    for row in raw_trades:
        if not isinstance(row, dict):
            continue
        price = _float(row.get("price") or row.get("p"))
        qty = _float(row.get("qty") or row.get("quantity") or row.get("q") or row.get("volume"))
        if price is None or qty is None or qty <= 0:
            continue
        side_raw = row.get("isBuyerMaker")
        if side_raw is None:
            side_raw = row.get("side") or row.get("S")
        side: TradeSide = "unknown"
        if side_raw is True or str(side_raw).lower() in {"sell", "s", "1"}:
            side = "sell"
        elif side_raw is False or str(side_raw).lower() in {"buy", "b", "0"}:
            side = "buy"
        ts = row.get("time") or row.get("T") or row.get("timestamp")
        ts_ms: int | None = None
        if ts is not None:
            try:
                ts_f = float(ts)
                ts_ms = int(ts_f if ts_f > 1e12 else ts_f * 1000)
            except (TypeError, ValueError):
                ts_ms = None
        ticks.append(
            BingXTradeTick(
                price=price,
                quantity=qty,
                side=side,
                timestamp_ms=ts_ms,
            )
        )
    return ticks


def signed_volumes_from_ticks(ticks: list[BingXTradeTick]) -> list[float]:
    out: list[float] = []
    for tick in ticks:
        sign = 1.0 if tick.side == "buy" else -1.0 if tick.side == "sell" else 0.0
        if sign == 0.0:
            continue
        out.append(sign * tick.quantity)
    return out


def bucket_buy_sell_volumes(
    ticks: list[BingXTradeTick],
    *,
    bucket_size: int = 20,
) -> tuple[list[float], list[float]]:
    """Aggregate trades into fixed-count buckets for VPIN."""
    if not ticks:
        return [], []
    buy_buckets: list[float] = []
    sell_buckets: list[float] = []
    chunk = max(1, bucket_size)
    for i in range(0, len(ticks), chunk):
        slice_ticks = ticks[i : i + chunk]
        buy = sum(t.quantity for t in slice_ticks if t.side == "buy")
        sell = sum(t.quantity for t in slice_ticks if t.side == "sell")
        unknown = [t for t in slice_ticks if t.side == "unknown"]
        if unknown:
            half = sum(t.quantity for t in unknown) / 2.0
            buy += half
            sell += half
        buy_buckets.append(buy)
        sell_buckets.append(sell)
    return buy_buckets, sell_buckets


def volume_profile_from_trades_and_book(
    ticks: list[BingXTradeTick],
    l2: BingXL2AdapterResult | None,
    *,
    price_bin_pct: float = 0.001,
) -> tuple[float | None, float | None, float | None]:
    """POC / VAH / VAL from trade histogram + L2 mid anchor."""
    if not ticks:
        return None, None, None
    hist: dict[float, float] = {}
    for tick in ticks:
        if tick.price <= 0:
            continue
        bin_px = round(tick.price * (1.0 / max(price_bin_pct, 1e-6))) * max(price_bin_pct, 1e-6)
        hist[bin_px] = hist.get(bin_px, 0.0) + tick.quantity
    if not hist:
        return None, None, None
    poc = max(hist, key=hist.get)
    total_vol = sum(hist.values())
    target = total_vol * 0.7
    sorted_bins = sorted(hist.items(), key=lambda x: x[0])
    poc_idx = next(i for i, (px, _) in enumerate(sorted_bins) if px == poc)
    acc = hist[poc]
    lo = hi = poc_idx
    while acc < target and (lo > 0 or hi < len(sorted_bins) - 1):
        expand_lo = sorted_bins[lo - 1][1] if lo > 0 else -1.0
        expand_hi = sorted_bins[hi + 1][1] if hi < len(sorted_bins) - 1 else -1.0
        if expand_hi >= expand_lo:
            hi += 1
            acc += sorted_bins[hi][1]
        else:
            lo -= 1
            acc += sorted_bins[lo][1]
    val_price = sorted_bins[lo][0]
    vah_price = sorted_bins[hi][0]
    return poc, vah_price, val_price


def build_microstructure_bundle(
    *,
    symbol: str,
    venue_symbol: str,
    raw_trades: list[dict[str, Any]],
    depth_payload: dict[str, Any] | None,
    market_type: str | None = None,
) -> BingXMicrostructureBundle:
    """Combine trade tape + L2 depth into scanner-ready microstructure."""
    ticks = parse_bingx_trades(raw_trades)
    l2 = (
        build_l2_snapshot_from_bingx_depth(
            venue_symbol,
            depth_payload or {},
            market_type=market_type,
        )
        if depth_payload
        else None
    )

    if len(ticks) < 5:
        return BingXMicrostructureBundle(
            symbol=symbol,
            venue_symbol=venue_symbol,
            ok=False,
            reason="insufficient_trades",
            l2_spread=l2.metrics.spread if l2 and l2.ok else None,
            l2_imbalance=l2.metrics.imbalance if l2 and l2.ok else None,
            trade_count=len(ticks),
        )

    buy_b, sell_b = bucket_buy_sell_volumes(ticks)
    vpin_out = compute_vpin_from_signed_volume(buy_b, sell_b)
    signed = signed_volumes_from_ticks(ticks)
    cvd_out = compute_cvd_from_trades(signed)
    poc, vah, val = volume_profile_from_trades_and_book(ticks, l2)

    return BingXMicrostructureBundle(
        symbol=symbol,
        venue_symbol=venue_symbol,
        ok=True,
        reason="ok",
        vpin=vpin_out.get("vpin"),
        volume_imbalance=vpin_out.get("volume_imbalance"),
        cvd=cvd_out.get("cvd"),
        period_delta=cvd_out.get("period_delta"),
        poc_price=poc,
        vah_price=vah,
        val_price=val,
        l2_spread=l2.metrics.spread if l2 and l2.ok else None,
        l2_imbalance=l2.metrics.imbalance if l2 and l2.ok else None,
        trade_count=len(ticks),
        method_vpin=str(vpin_out.get("method") or "vpin_trade_tape_v1"),
        order_book=depth_payload if depth_payload else None,
        extra={
            "l2_ok": l2.ok if l2 else False,
            "l2_reason": l2.reason if l2 else "no_depth",
        },
    )


def _float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


__all__ = [
    "BingXMicrostructureBundle",
    "BingXTradeTick",
    "build_microstructure_bundle",
    "parse_bingx_trades",
]
