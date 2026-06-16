from __future__ import annotations
from typing import Any
"""Phase C — Scoring module for derivatives engine.

All scoring functions are stateless and receive configuration via
parameters rather than importing singletons. This keeps them pure,
testable, and easy to reason about.

Engine scores are normalised to [0, 100].
"""


from decimal import Decimal

from backend.config.phase_thresholds import get_active_weights
from backend.models.enriched_snapshot import EnrichedSnapshot
from backend.models.option_contract import OptionContract
from backend.phases.phase_c.engine_models import QuantEngineResults


def gex_score(options_result: Any) -> float:
    """Score basado en GEX/VEX/CEX del OptionsEngine."""
    if options_result is None:
        return 50.0
    mic = getattr(options_result, "options_mic_score", 0.0)
    return min(max(mic, 0.0), 100.0)


def gamma_flip_score(report: Any, spot: float) -> float:
    """Score basado en la distancia al gamma flip point."""
    if report is None:
        return 50.0
    flip_point = getattr(report, "flip_point", None)
    if flip_point is None:
        return 50.0
    distance_pct = abs(spot - flip_point) / max(spot, 1.0) * 100.0
    if distance_pct < 2.0:
        return 90.0
    elif distance_pct < 5.0:
        return 75.0
    elif distance_pct < 10.0:
        return 60.0
    else:
        return 40.0


def dex_score(dex_report: Any) -> float:
    """Score basado en exposición delta MM."""
    if dex_report is None:
        return 50.0
    dex_val = getattr(dex_report, "dex_as_pct_adtv", None)
    if dex_val is None:
        return 50.0
    return min(max(float(dex_val) * 10, 0.0), 100.0)


def flow_score(flow_signal: Any) -> float:
    """Score basado en flujo institucional."""
    if flow_signal is None:
        return 50.0
    directional = getattr(flow_signal, "directional_score", 0.0)
    confidence = getattr(flow_signal, "confidence", 0.0)
    base_score = (directional + 1.0) * 50.0
    return base_score * confidence + 50.0 * (1.0 - confidence)


def zero_day_score(zero_day_report: Any) -> float:
    """Score basado en análisis 0DTE."""
    if zero_day_report is None:
        return 50.0
    pin_prob = getattr(zero_day_report, "pinning_prob", 0.0)
    alerts = getattr(zero_day_report, "alerts", [])
    alert_count = len(alerts) if alerts else 0
    alert_score = min(alert_count * 10, 50.0)
    pin_score = pin_prob * 50.0
    return alert_score + pin_score


def shadow_delta_score(shadow_report: Any) -> float:
    """Score basado en shadow delta gap."""
    if shadow_report is None:
        return 50.0
    net_portfolio = getattr(shadow_report, "net_portfolio", None)
    if net_portfolio is None:
        return 50.0
    delta_gap = float(abs(getattr(net_portfolio, "total_delta_gap", 0.0)))
    return min(delta_gap * 5.0 + 50.0, 100.0)


def delta_flow_score(delta_flow: Any) -> float:
    """Score basado en capitulación por delta flow."""
    if delta_flow is None:
        return 50.0
    z_score = getattr(delta_flow, "z_score", None)
    signal = getattr(delta_flow, "signal", None)
    if z_score is None:
        return 50.0
    signal_str: str
    if signal and hasattr(signal, "value"):
        signal_str = signal.value
    else:
        signal_str = str(signal) if signal else "NEUTRAL"
    if "EXHAUSTION" in signal_str:
        return 85.0
    elif "LONG_SETUP" in signal_str:
        return 90.0
    elif "HOLD" in signal_str:
        return 65.0
    else:
        return 50.0


def phase_b_momentum_score(candidate: EnrichedSnapshot) -> float:
    """Score de momentum basado en datos de Phase B."""
    ofi = abs(candidate.ofi_score) * 100
    smc = 50.0
    if candidate.smc_direction == "BULLISH":
        smc = 80.0
    elif candidate.smc_direction == "BEARISH":
        smc = 70.0
    return min((ofi + smc) / 2, 100.0)


def compute_engine_scores(
    engine_results: QuantEngineResults,
    chain: Any,
    candidate: EnrichedSnapshot,
) -> dict[str, float]:
    """Computa scores normalizados (0-100) de cada motor."""
    return {
        "gex_score": gex_score(engine_results.options_result),
        "gamma_flip": gamma_flip_score(engine_results.gamma_flip_report, float(chain.spot_price)),
        "dex_exposure": dex_score(engine_results.dex_report),
        "flow_signal": flow_score(engine_results.flow_signal),
        "zero_day": zero_day_score(engine_results.zero_day_report),
        "shadow_delta": shadow_delta_score(engine_results.shadow_delta_report),
        "delta_flow": delta_flow_score(engine_results.delta_flow_snapshot),
        "phase_b_momentum": phase_b_momentum_score(candidate),
    }


def liquidity_score(contract: OptionContract) -> float:
    volume_score = min(contract.volume / 1000, 1.0) * 40
    oi_score = min(contract.open_interest / 5000, 1.0) * 40
    spread_score = max(0, 1.0 - contract.spread_pct * 10) * 20
    return volume_score + oi_score + spread_score


def delta_score(contract: OptionContract) -> float:
    cf = get_active_weights().phase_c.contract_filters
    target = cf.delta_target_call if contract.is_call else cf.delta_target_put
    distance = abs(abs(contract.delta) - abs(target))
    return max(0, 100 - distance * 200)


def iv_score(contract: OptionContract) -> float:
    cf = get_active_weights().phase_c.contract_filters
    iv = contract.implied_volatility
    if iv < cf.iv_min:
        return 30.0
    elif iv < cf.iv_max:
        return 80.0
    elif iv < cf.iv_max * 1.6:
        return 60.0
    else:
        return 40.0


def dte_score(contract: OptionContract) -> float:
    cf = get_active_weights().phase_c.contract_filters
    dte = contract.dte
    if dte < cf.min_dte or dte > cf.max_dte:
        return 0.0
    distance = abs(dte - cf.optimal_dte)
    return max(0, 100 - distance * 3)


def weighted_engine_average(engine_scores: dict[str, float]) -> dict[str, float]:
    ew = get_active_weights().phase_c.engine_weights
    weighted: dict[str, float] = {}
    for key, score in engine_scores.items():
        weight = getattr(ew, key, None)
        if weight is not None:
            weighted[key] = score * weight
    return weighted


def score_contract(
    contract: OptionContract,
    spot: Decimal,
    candidate: EnrichedSnapshot,
    engine_scores: dict[str, float],
) -> float:
    """Score compuesto por contrato usando scores de motores + métricas básicas."""
    cw = get_active_weights().phase_c
    sw = cw.contract_score_weights
    raw_basic = (
        liquidity_score(contract) * sw.liquidity
        + delta_score(contract) * sw.delta
        + iv_score(contract) * sw.iv
        + dte_score(contract) * sw.dte
    )
    engine_scores_weighted = weighted_engine_average(engine_scores)
    engine_avg = sum(engine_scores_weighted.values())
    score = raw_basic * sw.basic_metrics + engine_avg * sw.engine_average
    return round(min(score, 100.0), 2)


def classify_regime(engine_scores: dict[str, float]) -> str:
    avg = sum(engine_scores.values()) / max(len(engine_scores), 1)
    if avg >= 65:
        return "BULLISH"
    elif avg >= 45:
        return "NEUTRAL"
    else:
        return "BEARISH"


def compute_confidence(
    engine_scores: dict[str, float],
    top_contracts: list[tuple[OptionContract, float]],
) -> float:
    if not top_contracts:
        return 0.0
    score_avg = sum(s for _, s in top_contracts) / len(top_contracts)
    engine_avg = sum(engine_scores.values()) / max(len(engine_scores), 1)
    confidence = (score_avg * 0.5 + engine_avg * 0.5) / 100
    return round(min(max(confidence, 0.0), 1.0), 4)
