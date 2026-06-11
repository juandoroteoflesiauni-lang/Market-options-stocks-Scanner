"""Options/GEX synthesis for Market Scanner Phase B candidates."""

from __future__ import annotations

import math

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerRow,
    ScannerCustomization,
    ScannerIndicatorDefinition,
    ScannerModuleSignal,
)
from backend.services.market_scanner_cmf_iv import CmfIvScannerResult, apply_cmf_iv_score_adjustment
from backend.services.market_scanner_institutional_scoring import (
    institutional_scoring_enabled,
    weight_scale_factor,
)
from backend.services.market_scanner_mfi_flow import (
    MfiFlowScannerResult,
    apply_mfi_flow_score_adjustment,
    apply_obv_mfi_conviction_adjustment,
)
from backend.services.market_scanner_module_signals import (
    build_module_signal,
    neutral_module_signal,
)
from backend.services.market_scanner_obv_oi import ObvOiScannerResult, apply_obv_oi_score_adjustment

logger = get_logger(__name__)

OPTIONABLE_SUFFIXES = ("USD",)


def _detect_confluence_oblock_put_wall(
    smc_order_blocks: list | None,
    put_wall: float | None,
    spot_price: float | None,
    markov_regime: str | None,
    tolerance_pct: float = 2.5,  # OB zone + 2.5% = "coincide"
) -> dict[str, object]:
    """
    Detecta confluencia entre:
    1. BULLISH Order Block (SMC)
    2. Put Wall (GEX)
    3. Markov BULL_QUIET régimen

    INPUTS:
    - smc_order_blocks: list[OrderBlock] o None
      OrderBlock tiene: .direction, .low, .high, .entry_zone, .strength
    - put_wall: float | None (precio del put wall)
    - spot_price: float | None (spot actual)
    - markov_regime: str ("BULL_QUIET", "BEAR_VOLATILE", "CHAOTIC", None)
    - tolerance_pct: distancia máxima entre put_wall y OB zone

    OUTPUTS dict:
    {
        "confluence_detected": bool,  # Las 3 condiciones se cumplen
        "markov_state": str,
        "active_ob_count": int,       # OBs BULLISH en rango
        "confidence_multiplier": float,  # [0.6, 1.0, 1.2]
        "score_adjustment": float,    # [-8, 0, +30]
        "reasons": list[str],
        "confluence_strength": float, # 0-1, cuán cercana está put_wall a OB
    }
    """
    if not smc_order_blocks or not put_wall or not spot_price:
        return {
            "confluence_detected": False,
            "markov_state": "UNKNOWN",
            "active_ob_count": 0,
            "confidence_multiplier": 1.0,
            "score_adjustment": 0.0,
            "reasons": [],
            "confluence_strength": 0.0,
        }

    bullish_obs = [
        ob
        for ob in smc_order_blocks
        if getattr(ob, "direction", "") == "BULLISH"
        or (isinstance(ob, dict) and ob.get("direction") == "BULLISH")
    ]
    if not bullish_obs:
        return {
            "confluence_detected": False,
            "markov_state": "UNKNOWN",
            "active_ob_count": 0,
            "confidence_multiplier": 1.0,
            "score_adjustment": 0.0,
            "reasons": [],
            "confluence_strength": 0.0,
        }

    confluence_score = 0.0
    for ob in bullish_obs:
        low = getattr(ob, "low", 0.0) if hasattr(ob, "low") else ob.get("low", 0.0)
        high = getattr(ob, "high", 0.0) if hasattr(ob, "high") else ob.get("high", 0.0)
        entry_zone = (
            getattr(ob, "entry_zone", 0.0)
            if hasattr(ob, "entry_zone")
            else ob.get("entry_zone", 0.0)
        )

        dist_to_low = abs(put_wall - low) / max(abs(low), 1e-9)
        dist_to_high = abs(put_wall - high) / max(abs(high), 1e-9)
        dist_to_entry = abs(put_wall - entry_zone) / max(abs(entry_zone), 1e-9)

        min_dist = min(dist_to_low, dist_to_high, dist_to_entry)

        if min_dist <= tolerance_pct / 100.0:
            confluence_score = max(confluence_score, 1.0 - min_dist)

    if markov_regime == "BULL_QUIET":
        if confluence_score > 0.7:
            return {
                "confluence_detected": True,
                "markov_state": "BULL_QUIET",
                "active_ob_count": len(bullish_obs),
                "confidence_multiplier": 1.2,
                "score_adjustment": 30.0,
                "reasons": [
                    f"STRONG CONFLUENCE: {len(bullish_obs)} BULLISH Order Block(s) align with Put Wall",
                    "Markov regime BULL_QUIET validates confluence",
                ],
                "confluence_strength": confluence_score,
            }
    elif markov_regime == "BEAR_VOLATILE":
        if confluence_score > 0.7:
            return {
                "confluence_detected": False,
                "markov_state": "BEAR_VOLATILE",
                "active_ob_count": len(bullish_obs),
                "confidence_multiplier": 0.6,
                "score_adjustment": -8.0,
                "reasons": [
                    "Order Block + Put Wall confluence detected",
                    "BUT Markov in BEAR_VOLATILE: reduced conviction",
                ],
                "confluence_strength": confluence_score,
            }
    elif markov_regime == "CHAOTIC":
        return {
            "confluence_detected": False,
            "markov_state": "CHAOTIC",
            "active_ob_count": 0,
            "confidence_multiplier": 0.6,
            "score_adjustment": -3.0,
            "reasons": ["OB/Put Wall found but CHAOTIC regime reduces confidence"],
            "confluence_strength": confluence_score,
        }
    else:
        return {
            "confluence_detected": False,
            "markov_state": "UNKNOWN",
            "active_ob_count": len(bullish_obs) if confluence_score > 0 else 0,
            "confidence_multiplier": 0.8 if confluence_score > 0.7 else 1.0,
            "score_adjustment": 0.0,
            "reasons": (
                ["OB/Put Wall confluence found but Markov regime unavailable"]
                if confluence_score > 0.7
                else []
            ),
            "confluence_strength": confluence_score,
        }

    return {
        "confluence_detected": False,
        "markov_state": markov_regime,
        "active_ob_count": 0,
        "confidence_multiplier": 1.0,
        "score_adjustment": 0.0,
        "reasons": [],
        "confluence_strength": 0.0,
    }


def synthesize_options_gex_signal(
    row: MarketScannerRow,
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
    snapshot: object | None = None,
    *,
    obv_oi_result: ObvOiScannerResult | None = None,
    obv_oi_weight: float = 0.0,
    mfi_flow_result: MfiFlowScannerResult | None = None,
    mfi_flow_weight: float = 0.0,
    cmf_iv_result: CmfIvScannerResult | None = None,
    cmf_iv_weight: float = 0.0,
    effective_weights: dict[str, dict[str, float]] | None = None,
    primary_timeframe: str = "15m",
) -> ScannerModuleSignal:
    """Return cached-options synthesis when available, otherwise neutral degradation."""
    enabled = _enabled_indicators(customization, indicators)
    if not enabled:
        return neutral_module_signal("options_gex", "Options/GEX module disabled.")

    if row.symbol.endswith(OPTIONABLE_SUFFIXES):
        return neutral_module_signal(
            "options_gex",
            "Options/GEX skipped for non-equity or synthetic symbol.",
            engine_count=len(enabled),
        )

    payload = _snapshot_payload(snapshot)
    if not payload:
        return neutral_module_signal(
            "options_gex",
            "Options snapshot unavailable for scanner Phase B.",
            engine_count=len(enabled),
        )

    # ── SMC + Markov data extraction (NEW) ──────────────────────────────────
    smc_order_blocks = None
    markov_regime = None

    if snapshot and hasattr(snapshot, "smc_result"):
        smc_result = snapshot.smc_result
        smc_order_blocks = getattr(smc_result, "order_blocks", None)
    elif snapshot and isinstance(snapshot, dict):
        smc_data = snapshot.get("smc_result", {})
        if isinstance(smc_data, dict):
            smc_order_blocks = smc_data.get("order_blocks")
        elif hasattr(smc_data, "order_blocks"):
            smc_order_blocks = smc_data.order_blocks

    if snapshot and hasattr(snapshot, "markov_regime"):
        markov_regime = snapshot.markov_regime
    elif snapshot and isinstance(snapshot, dict):
        markov_regime = snapshot.get("markov_regime")

    gex_levels = _as_dict(payload.get("gex_levels"))
    confluence = _as_dict(payload.get("confluence"))
    flow_signal = _as_dict(payload.get("flow_signal"))
    dealer_bias = str(gex_levels.get("dealer_bias") or "NEUTRAL").upper()
    net_gex = _float_or_none(gex_levels.get("net_gex_total"))
    squeeze = _float_or_none(gex_levels.get("squeeze_probability")) or 0.0
    gamma_flip = _float_or_none(gex_levels.get("zero_gamma_level"))
    iv_surface = _as_dict(payload.get("iv_surface"))
    term_structure = _as_dict(iv_surface.get("term_structure"))
    spot = _float_or_none(payload.get("spot"))
    chain_analytics = _as_dict(payload.get("chain_analytics"))
    institutional_metrics = _as_dict(chain_analytics.get("institutional_metrics"))
    chain_history = _as_dict(payload.get("chain_analytics_history"))
    history_points = _as_list(chain_history.get("points"))
    latest_history = _as_dict(history_points[0]) if history_points else {}
    alert_kinds = {
        str(_as_dict(alert).get("kind") or "").lower()
        for alert in _as_list(chain_analytics.get("alerts"))
    }
    options_features = _as_dict(payload.get("options_gex_features"))

    score = 50.0
    reasons: list[str] = []
    warnings: list[str] = []
    if options_features:
        composite = _float_or_none(options_features.get("composite_directional_signal"))
        source_tier = str(options_features.get("source_tier") or "unknown")
        data_quality = _float_or_none(options_features.get("data_quality_score"))
        if composite is not None:
            score += max(-24.0, min(24.0, composite * 24.0))
            reasons.append(f"Canonical Options/GEX composite folded into score ({source_tier}).")
        if data_quality is not None and data_quality < 0.50:
            score -= 5.0
            warnings.append("Options/GEX data quality is below scanner confidence threshold.")
        if source_tier == "light_proxy":
            score -= 4.0
            warnings.append(
                "Options/GEX source is light_proxy; treating it as risk context, not confirmation."
            )
        elif source_tier == "full_chain_gex":
            score += 3.0
            reasons.append("Options/GEX source tier is full_chain_gex.")
    dealer_w = 3.0
    net_gex_w = 3.0
    if effective_weights:
        dealer_w = float(effective_weights.get("dealer_bias", {}).get(primary_timeframe, dealer_w))
        net_gex_w = float(effective_weights.get("net_gex", {}).get(primary_timeframe, net_gex_w))

    dealer_scale = weight_scale_factor(dealer_w) if institutional_scoring_enabled() else 1.0
    net_gex_scale = weight_scale_factor(net_gex_w) if institutional_scoring_enabled() else 1.0

    if dealer_bias == "BULLISH":
        score += 20.0 * dealer_scale
        reasons.append("Dealer bias BULLISH from options snapshot.")
    elif dealer_bias == "BEARISH":
        score -= 20.0 * dealer_scale
        reasons.append("Dealer bias BEARISH from options snapshot.")
    else:
        reasons.append("Dealer bias NEUTRAL from options snapshot.")

    if net_gex is not None:
        delta = 12.0 * net_gex_scale
        score += delta if net_gex > 0 else -delta if net_gex < 0 else 0.0
        reasons.append(
            f"Net GEX {'positive' if net_gex > 0 else 'negative' if net_gex < 0 else 'neutral'}."
        )
    else:
        warnings.append("Net GEX missing from options snapshot.")

    gamma_regime = str(institutional_metrics.get("gamma_regime") or "").upper()
    dealer_pressure = _float_or_none(institutional_metrics.get("dealer_pressure_score"))
    if gamma_regime == "POSITIVE_GAMMA":
        score += 5.0
        reasons.append("Institutional chain reports POSITIVE_GAMMA regime.")
    elif gamma_regime == "NEGATIVE_GAMMA":
        score -= 8.0
        warnings.append("Institutional chain reports NEGATIVE_GAMMA regime.")
    elif gamma_regime == "TRANSITION_GAMMA":
        score -= 3.0
        warnings.append("Institutional chain reports TRANSITION_GAMMA regime.")

    if dealer_pressure is not None:
        score += max(-6.0, min(6.0, dealer_pressure / 10.0))
        reasons.append("Dealer pressure score folded into Options/GEX synthesis.")

    call_wall_change = _float_or_none(latest_history.get("call_wall_change"))
    put_wall_change = _float_or_none(latest_history.get("put_wall_change"))
    zero_dte_change = _float_or_none(latest_history.get("zero_dte_gamma_share_change"))
    if call_wall_change is not None:
        if call_wall_change > 0:
            score += min(5.0, call_wall_change)
            reasons.append("Call wall shifted higher in persisted chain history.")
        elif call_wall_change < 0:
            score -= min(5.0, abs(call_wall_change))
            warnings.append("Call wall shifted lower in persisted chain history.")
    if put_wall_change is not None:
        if put_wall_change > 0:
            score += min(4.0, put_wall_change)
            reasons.append("Put wall shifted higher, raising structural support.")
        elif put_wall_change < 0:
            score -= min(4.0, abs(put_wall_change))
            warnings.append("Put wall shifted lower, reducing structural support.")
    if zero_dte_change is not None and zero_dte_change >= 10.0:
        score -= min(8.0, zero_dte_change / 2.0)
        warnings.append("0DTE gamma concentration surged in persisted chain history.")

    if bool(latest_history.get("gamma_regime_changed")) or "dealer_regime_flip" in alert_kinds:
        score -= 8.0
        warnings.append("Dealer gamma regime flip detected in Options/GEX history.")
    if (
        bool(latest_history.get("dominant_expiry_changed"))
        or "dominant_expiry_rotation" in alert_kinds
    ):
        score -= 3.0
        warnings.append("Dominant expiry rotation detected in Options/GEX history.")

    if squeeze >= 0.75:
        score -= 8.0
        warnings.append("High squeeze probability reduces directional confidence.")

    flow_label = str(flow_signal.get("label") or flow_signal.get("signal") or "").upper()
    if flow_label in {"BUY", "BULLISH"}:
        score += 6.0
        reasons.append("Options flow signal confirms bullish pressure.")
    elif flow_label in {"SELL", "BEARISH"}:
        score -= 6.0
        reasons.append("Options flow signal confirms bearish pressure.")

    flow_notional = _float_or_none(flow_signal.get("notional")) or _float_or_none(
        flow_signal.get("premium")
    )
    flow_score = _float_or_none(flow_signal.get("score"))
    if flow_notional is not None and flow_notional > 5_000_000:
        adj = min(6.0, math.log10(flow_notional + 1.0) - 6.0)
        if flow_label in {"BUY", "BULLISH"}:
            score += adj
            reasons.append("Large notional flow supports directional read.")
        elif flow_label in {"SELL", "BEARISH"}:
            score -= adj
            reasons.append("Large notional flow supports bearish read.")
    if flow_score is not None and abs(flow_score) > 0.01:
        score += max(-5.0, min(5.0, flow_score * 8.0))
        reasons.append("Flow score magnitude folded into GEX synthesis.")

    # Gamma Flip logic
    if gamma_flip is not None and gamma_flip > 0 and spot is not None and spot > 0:
        flip_dist_pct = (spot - gamma_flip) / gamma_flip
        if flip_dist_pct > 0.01:
            score += 4.0
            reasons.append("Price is safely above Gamma Flip (+Gamma Regime).")
        elif flip_dist_pct < -0.01:
            score -= 4.0
            warnings.append("Price is below Gamma Flip (-Gamma Regime).")

    call_wall = _float_or_none(gex_levels.get("call_wall"))
    put_wall = _float_or_none(gex_levels.get("put_wall"))
    if spot is not None and spot > 0:
        if call_wall is not None and call_wall > 0:
            dist_call = (call_wall - spot) / spot
            if 0.0 <= dist_call <= 0.03:
                score -= 5.0
                warnings.append("Spot within 3% of call wall (gamma pinning risk).")
            elif dist_call > 0.05:
                score += 2.0
                reasons.append("Call wall not immediate; cleaner upside path.")
        if put_wall is not None and put_wall > 0:
            dist_put = (spot - put_wall) / spot
            if 0.0 <= dist_put <= 0.03:
                score += 3.0
                reasons.append("Spot holding above nearby put wall (support).")
            elif dist_put < -0.01:
                score -= 4.0
                warnings.append("Spot below put wall (structure breakdown risk).")

    # ── NEW: SMC Order Block + Put Wall + Markov Regime Confluence ──────────
    confluence_result = _detect_confluence_oblock_put_wall(
        smc_order_blocks=smc_order_blocks,
        put_wall=put_wall,
        spot_price=spot,
        markov_regime=markov_regime,
        tolerance_pct=2.5,  # OB ± 2.5% rango de coincidencia
    )

    if confluence_result.get("confluence_detected"):
        score += float(confluence_result.get("score_adjustment", 0.0))
        reasons.append(
            "🔥 **STRONG BUY**: SMC Order Block + Put Wall + BULL_QUIET confluence detected"
        )
        # Log advice for scaling
        logger.info("High confidence confluence detected; validate risk params before scaling")

        for extra_reason in confluence_result.get("reasons", []):
            if extra_reason and len(reasons) < 6:
                reasons.append(str(extra_reason))
    else:
        adj = float(confluence_result.get("score_adjustment", 0.0))
        if adj < 0:
            score += adj
            for reason in confluence_result.get("reasons", []):
                if reason and len(warnings) < 3:
                    warnings.append(str(reason))

    confidence_mult = float(confluence_result.get("confidence_multiplier", 1.0))

    if obv_oi_weight > 0 and obv_oi_result is not None and obv_oi_result.ok:
        score, obv_reasons = apply_obv_oi_score_adjustment(
            score, obv_oi_result, weight=obv_oi_weight
        )
        reasons.extend(obv_reasons[:2])
    elif obv_oi_weight > 0 and obv_oi_result is not None and not obv_oi_result.ok:
        warnings.append(
            obv_oi_result.reasons[0] if obv_oi_result.reasons else "OBV-OI unavailable."
        )

    if mfi_flow_weight > 0 and mfi_flow_result is not None and mfi_flow_result.ok:
        score, mfi_reasons = apply_mfi_flow_score_adjustment(
            score, mfi_flow_result, weight=mfi_flow_weight
        )
        reasons.extend(mfi_reasons[:2])
    elif mfi_flow_weight > 0 and mfi_flow_result is not None and not mfi_flow_result.ok:
        warnings.append(
            mfi_flow_result.reasons[0] if mfi_flow_result.reasons else "MFI-Flow unavailable."
        )

    if cmf_iv_weight > 0 and cmf_iv_result is not None and cmf_iv_result.ok:
        score, cmf_reasons = apply_cmf_iv_score_adjustment(
            score, cmf_iv_result, weight=cmf_iv_weight
        )
        reasons.extend(cmf_reasons[:2])
        if cmf_iv_result.iv_crush_active:
            warnings.append("CMF-IV IV crush filter — extreme vol regime; reduce size.")
    elif cmf_iv_weight > 0 and cmf_iv_result is not None and not cmf_iv_result.ok:
        warnings.append(
            cmf_iv_result.reasons[0] if cmf_iv_result.reasons else "CMF-IV unavailable."
        )

    # Term Structure logic
    contango = term_structure.get("contango", False)
    backwardation = term_structure.get("backwardation", False)
    if contango:
        score += 3.0
        reasons.append("IV Term Structure in Contango (Normal volatility environment).")
    elif backwardation:
        score -= 5.0
        warnings.append("IV Term Structure in Backwardation (High near-term risk).")

    confidence = _float_or_none(confluence.get("confidence"))
    if confidence is None:
        confidence = 0.35 + min(0.4, abs(score - 50.0) / 100.0)

    conviction_scale = 1.0
    if institutional_scoring_enabled() and (obv_oi_weight > 0 or mfi_flow_weight > 0):
        conviction_scale = (
            weight_scale_factor(obv_oi_weight) + weight_scale_factor(mfi_flow_weight)
        ) / 2.0
    score, confidence, conviction_reasons = apply_obv_mfi_conviction_adjustment(
        score,
        float(confidence),
        obv_oi_result=obv_oi_result,
        mfi_flow_result=mfi_flow_result,
        conviction_weight_scale=conviction_scale,
    )
    reasons.extend(conviction_reasons[:2])

    # Apply Markov regime multiplier (NEW)
    confidence = float(confidence * confidence_mult)
    confidence = float(max(0.0, min(1.0, confidence)))  # Re-clamp [0, 1]

    score = float(max(0.0, min(100.0, score)))

    # Calculate actual available engines from snapshot fields
    available_engines = 0
    if "net_gex_total" in gex_levels:
        available_engines += 1
    if "dealer_bias" in gex_levels:
        available_engines += 1
    if "zero_gamma_level" in gex_levels:
        available_engines += 1
    if "squeeze_probability" in gex_levels:
        available_engines += 1
    if term_structure:
        available_engines += 1
    if flow_signal:
        available_engines += 1
    if obv_oi_weight > 0 and obv_oi_result is not None and obv_oi_result.ok:
        available_engines += 1
    if mfi_flow_weight > 0 and mfi_flow_result is not None and mfi_flow_result.ok:
        available_engines += 1
    if cmf_iv_weight > 0 and cmf_iv_result is not None and cmf_iv_result.ok:
        available_engines += 1
    if institutional_metrics:
        available_engines += 1
    if latest_history:
        available_engines += 1
    if options_features:
        available_engines += len(_as_list(options_features.get("active_engines")))

    # Log confluence result para debugging (NEW)
    if confluence_result:
        try:
            logger.debug(
                "Phase B confluence: ob_count=%d, markov=%s, confluence_strength=%.2f",
                confluence_result.get("active_ob_count", 0),
                confluence_result.get("markov_state", "UNKNOWN"),
                confluence_result.get("confluence_strength", 0.0),
            )
        except Exception:
            pass

    return build_module_signal(
        "options_gex",
        score,
        confidence,
        engine_count=len(enabled),
        available_count=max(available_engines, 1) if payload else 0,
        reasons=reasons,
        warnings=warnings,
    )


def _enabled_indicators(
    customization: ScannerCustomization,
    indicators: list[ScannerIndicatorDefinition],
) -> list[ScannerIndicatorDefinition]:
    requested = set(customization.enabled_indicators or [])
    out: list[ScannerIndicatorDefinition] = []
    for indicator in indicators:
        if indicator.module != "options_gex":
            continue
        if customization.enabled_indicators is None and not indicator.default_enabled:
            continue
        if customization.enabled_indicators is not None and indicator.key not in requested:
            continue
        out.append(indicator)
    return out


def _snapshot_payload(snapshot: object | None) -> dict[str, object]:
    if snapshot is None:
        return {}
    if hasattr(snapshot, "model_dump"):
        dumped = snapshot.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return snapshot if isinstance(snapshot, dict) else {}


def _as_dict(value: object) -> dict[str, object]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _float_or_none(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
