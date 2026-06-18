"""Política de salidas BingX: buckets por ticker + confluencia (PnL apalancado). # [PD-8][TH]"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PositionSide = Literal["LONG", "SHORT"]

# Umbrales de confluencia (slow-cycle cache).
CONFLUENCE_HEALTHY_FLOOR: float = 0.45
CONFLUENCE_WEAK_CEILING: float = 0.35
CONFLUENCE_DROP_WEAK_DELTA: float = 0.25
EARLY_SL_LEVERAGED_PCT: float = -3.0

_BULLISH_SIGNALS = frozenset({"BUY", "BULLISH", "LONG"})
_BEARISH_SIGNALS = frozenset({"SELL", "BEARISH", "SHORT", "WAIT"})
_OPPOSE_LONG = frozenset({"SELL", "BEARISH", "SHORT"})
_OPPOSE_SHORT = frozenset({"BUY", "BULLISH", "LONG"})


@dataclass(frozen=True)
class BingXExitBucketThresholds:
    """Umbrales en % PnL apalancado (lo que muestra BingX)."""

    bucket: str
    tp1_leveraged_pct: float
    tp1_trim_ratio: float
    tp2_leveraged_pct: float
    tp2_trim_ratio: float
    sl_def_leveraged_pct: float
    sl_def_trim_ratio: float


@dataclass(frozen=True)
class BingXConfluenceCacheEntry:
    """Snapshot de confluencia del último slow-cycle."""

    symbol: str
    underlying: str
    confluence_score: float | None
    confluence_signal: str | None
    gamma_flip: float | None
    speed_instability: bool
    tail_risk_severity: str
    updated_at_iso: str


_INDEX = BingXExitBucketThresholds(
    bucket="INDEX",
    tp1_leveraged_pct=4.0,
    tp1_trim_ratio=0.25,
    tp2_leveraged_pct=8.0,
    tp2_trim_ratio=0.20,
    sl_def_leveraged_pct=-5.0,
    sl_def_trim_ratio=0.30,
)
_MEGA = BingXExitBucketThresholds(
    bucket="MEGA",
    tp1_leveraged_pct=4.5,
    tp1_trim_ratio=0.28,
    tp2_leveraged_pct=9.0,
    tp2_trim_ratio=0.22,
    sl_def_leveraged_pct=-5.5,
    sl_def_trim_ratio=0.30,
)
_SEMIS = BingXExitBucketThresholds(
    bucket="SEMIS",
    tp1_leveraged_pct=5.0,
    tp1_trim_ratio=0.30,
    tp2_leveraged_pct=10.0,
    tp2_trim_ratio=0.22,
    sl_def_leveraged_pct=-6.0,
    sl_def_trim_ratio=0.30,
)
_HIGH_BETA = BingXExitBucketThresholds(
    bucket="HIGH_BETA",
    tp1_leveraged_pct=6.0,
    tp1_trim_ratio=0.25,
    tp2_leveraged_pct=11.0,
    tp2_trim_ratio=0.20,
    sl_def_leveraged_pct=-7.0,
    sl_def_trim_ratio=0.35,
)
_DEFENSIVE = BingXExitBucketThresholds(
    bucket="DEFENSIVE",
    tp1_leveraged_pct=4.0,
    tp1_trim_ratio=0.25,
    tp2_leveraged_pct=8.0,
    tp2_trim_ratio=0.20,
    sl_def_leveraged_pct=-5.0,
    sl_def_trim_ratio=0.25,
)

_BUCKET_BY_ROOT: dict[str, BingXExitBucketThresholds] = {
    "SPY": _INDEX,
    "QQQ": _INDEX,
    "AAPL": _MEGA,
    "MSFT": _MEGA,
    "GOOGL": _MEGA,
    "AMZN": _MEGA,
    "META": _MEGA,
    "NVDA": _MEGA,
    "AMD": _SEMIS,
    "INTC": _SEMIS,
    "MU": _SEMIS,
    "AVGO": _SEMIS,
    "TSLA": _HIGH_BETA,
    "COIN": _HIGH_BETA,
    "PLTR": _HIGH_BETA,
    "HOOD": _HIGH_BETA,
    "CRWV": _HIGH_BETA,
    "IREN": _HIGH_BETA,
    "JPM": _DEFENSIVE,
}


def resolve_exit_bucket(underlying_root: str) -> BingXExitBucketThresholds:
    """Devuelve umbrales del bucket; default MEGA si root desconocido."""
    return _BUCKET_BY_ROOT.get(underlying_root.upper().strip(), _MEGA)


def adapt_thresholds_for_leverage(
    thresholds: BingXExitBucketThresholds,
    leverage: float,
) -> BingXExitBucketThresholds:
    """Ajuste leve: leverage alto → recorte TP1 un poco mayor."""
    lev = max(1.0, leverage)
    if lev <= 5.0:
        return thresholds
    boost = min(0.05, (lev - 5.0) * 0.01)
    return BingXExitBucketThresholds(
        bucket=thresholds.bucket,
        tp1_leveraged_pct=thresholds.tp1_leveraged_pct,
        tp1_trim_ratio=min(0.35, thresholds.tp1_trim_ratio + boost),
        tp2_leveraged_pct=thresholds.tp2_leveraged_pct,
        tp2_trim_ratio=thresholds.tp2_trim_ratio,
        sl_def_leveraged_pct=thresholds.sl_def_leveraged_pct,
        sl_def_trim_ratio=thresholds.sl_def_trim_ratio,
    )


def _normalize_signal(signal: str | None) -> str:
    return str(signal or "").upper().strip()


def _gamma_regime_ok(side: PositionSide, spot: float | None, gamma_flip: float | None) -> bool:
    if spot is None or gamma_flip is None:
        return True
    if side == "LONG":
        return spot >= gamma_flip
    return spot <= gamma_flip


def _signal_opposes(side: PositionSide, signal: str | None) -> bool:
    sig = _normalize_signal(signal)
    if not sig:
        return False
    if side == "LONG":
        return sig in _OPPOSE_LONG
    return sig in _OPPOSE_SHORT


def confluence_is_healthy(
    entry: BingXConfluenceCacheEntry | None,
    *,
    side: PositionSide,
    spot: float | None,
) -> bool:
    """Confluencia sana: score alto + régimen gamma + señal no opuesta."""
    if entry is None or entry.confluence_score is None:
        return False
    if entry.confluence_score < CONFLUENCE_HEALTHY_FLOOR:
        return False
    if _signal_opposes(side, entry.confluence_signal):
        return False
    if not _gamma_regime_ok(side, spot, entry.gamma_flip):
        return False
    if entry.speed_instability and entry.confluence_score < 0.5:
        return False
    return not (entry.tail_risk_severity in {"HIGH", "CRITICAL"} and entry.confluence_score < 0.5)


def confluence_is_weakened(
    entry: BingXConfluenceCacheEntry | None,
    *,
    side: PositionSide,
    spot: float | None,
    entry_score_at_open: float | None = None,
) -> bool:
    """True si la tesis se deterioró (habilita SL defensivo)."""
    if entry is None:
        return False
    score = entry.confluence_score
    if score is not None and score < CONFLUENCE_WEAK_CEILING:
        return True
    if (
        score is not None
        and entry_score_at_open is not None
        and (entry_score_at_open - score) >= CONFLUENCE_DROP_WEAK_DELTA
    ):
        return True
    if _signal_opposes(side, entry.confluence_signal):
        return True
    if not _gamma_regime_ok(side, spot, entry.gamma_flip):
        return True
    return (entry.speed_instability and (score is None or score < 0.5)) or (
        entry.tail_risk_severity in {"HIGH", "CRITICAL"} and (score is None or score < 0.5)
    )


def confluence_broken_for_tp(
    entry: BingXConfluenceCacheEntry | None,
    *,
    side: PositionSide,
    spot: float | None,
) -> bool:
    """True si no debemos tomar TP1 (señal rota / régimen en contra)."""
    if entry is None:
        return False
    if entry.confluence_score is not None and entry.confluence_score < CONFLUENCE_WEAK_CEILING:
        return True
    if _signal_opposes(side, entry.confluence_signal):
        return True
    return not _gamma_regime_ok(side, spot, entry.gamma_flip)


def leveraged_pnl_pct(
    *,
    side: PositionSide,
    entry_price: float,
    mark_price: float,
    leverage: float,
) -> float | None:
    """PnL % apalancado (alineado con la UI de BingX)."""
    if entry_price <= 0 or mark_price <= 0:
        return None
    if side == "LONG":
        price_pct = ((mark_price - entry_price) / entry_price) * 100.0
    else:
        price_pct = ((entry_price - mark_price) / entry_price) * 100.0
    return price_pct * max(1.0, leverage)


__all__ = [
    "CONFLUENCE_HEALTHY_FLOOR",
    "EARLY_SL_LEVERAGED_PCT",
    "BingXConfluenceCacheEntry",
    "BingXExitBucketThresholds",
    "adapt_thresholds_for_leverage",
    "confluence_broken_for_tp",
    "confluence_is_healthy",
    "confluence_is_weakened",
    "leveraged_pnl_pct",
    "resolve_exit_bucket",
]
