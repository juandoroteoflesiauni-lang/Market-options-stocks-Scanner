"""Utilidades de scoring para capas Options Strategy. # [TH]"""

from __future__ import annotations

from backend.domain.alpaca_options_models import OptionsConfluence
from backend.models.options_strategy import BreakoutState, RegimeClass


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def clamp11(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


def smc_sesgo_to_bias(sesgo: str) -> float:
    token = str(sesgo or "neutral").lower()
    if token in {"bullish", "bull"}:
        return 1.0
    if token in {"bullish_watch"}:
        return 0.5
    if token in {"bearish", "bear"}:
        return -1.0
    if token in {"bearish_watch"}:
        return -0.5
    return 0.0


def market_regime_to_bias(regime: str) -> float:
    token = str(regime or "").lower()
    if "bull" in token:
        return 0.8
    if "bear" in token:
        return -0.8
    return 0.0


def ofi_regime_to_bias(regime: str) -> float:
    token = str(regime or "")
    if "Accumulation" in token or token.endswith("ACCUMULATION"):
        return 0.7
    if "Distribution" in token or token.endswith("DISTRIBUTION"):
        return -0.7
    return 0.0


def fear_greed_to_bias(score: float) -> float:
    return clamp11((float(score) - 50.0) / 50.0)


def markov_label_to_regime(label: str, signal: str) -> RegimeClass:
    state = str(label or "").upper()
    sig = str(signal or "").upper()
    if state == "BULL_QUIET" and sig == "STABLE":
        return "trend"
    if state == "BEAR_VOLATILE":
        return "volatile"
    if state == "CHAOTIC" or sig == "CRITICAL":
        return "dislocated"
    if sig == "SHIFTING":
        return "mean_reversion"
    return "unknown"


def infer_breakout_state(
    *,
    mss_count: int,
    sweep_count: int,
    ofi_bias: float,
    range_pct: float,
) -> BreakoutState:
    if mss_count > 0:
        return "confirmed"
    if sweep_count >= 2 and abs(ofi_bias) < 0.2:
        return "failed"
    if abs(ofi_bias) >= 0.5:
        return "arming"
    if range_pct < 0.01:
        return "compressed"
    return "unknown"


def confluence_to_bias(confluence: OptionsConfluence) -> float:
    """Mapea confluencia híbrida R1 a bias direccional [-1, 1]."""
    sign = (
        1.0
        if confluence.dominant_direction == "BULL"
        else -1.0
        if confluence.dominant_direction == "BEAR"
        else 0.0
    )
    return clamp11(sign * float(confluence.score))


def l2_ofi_bias_from_microstructure(micro: dict[str, object]) -> float:
    """OFI real desde bundle BingX L2 (imbalance o best bid/ask)."""
    imb = micro.get("l2_imbalance")
    if imb is not None:
        try:
            return clamp11(float(imb))
        except (TypeError, ValueError):
            pass
    order_book = micro.get("order_book")
    if not isinstance(order_book, dict):
        return 0.0
    bids = order_book.get("bids") or []
    asks = order_book.get("asks") or []
    if not bids or not asks:
        return 0.0
    try:
        bid_sz = float(bids[0][1] if isinstance(bids[0], (list, tuple)) else bids[0].get("size", 0))
        ask_sz = float(asks[0][1] if isinstance(asks[0], (list, tuple)) else asks[0].get("size", 0))
    except (TypeError, ValueError, IndexError):
        return 0.0
    total = bid_sz + ask_sz
    if total <= 0:
        return 0.0
    return clamp11((bid_sz - ask_sz) / total)


def l2_microstructure_score_from_bundle(micro: dict[str, object]) -> float:
    """Score [0,1] para fusión: imbalance + vpin inverso."""
    ofi_strength = abs(l2_ofi_bias_from_microstructure(micro))
    vpin = micro.get("vpin")
    vpin_penalty = 0.0
    if vpin is not None:
        try:
            vpin_penalty = clamp01(float(vpin))
        except (TypeError, ValueError):
            vpin_penalty = 0.0
    return clamp01(ofi_strength * 0.7 + (1.0 - vpin_penalty) * 0.3)
