from __future__ import annotations
from typing import Any
"""
PillarScorer - Reward-first scoring engine with 5 pillars.

Philosophy:
- Missing data -> neutral 5.0
- Weak signal  -> 3.0-5.0
- Strong signal -> 7.0-10.0
- Non-binary by default; only systemic shock can hard-force CASH

Final scale: 0.0-10.0
- >= 7.5 -> SNIPER LONG
- >= 6.5 -> LONG
- >= 5.0 -> WATCH
- <  5.0 -> CASH
"""


import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger("backend.quant_engine.engines.fundamental.pillar_scorer")

from ..predictive.sentiment_engine import SentimentEngine


@dataclass
class PillarWeights:
    technical: float = 0.30
    options: float = 0.25
    news: float = 0.20
    macro: float = 0.15
    fundamentals: float = 0.10

    def validate(self) -> None:
        total = self.technical + self.options + self.news + self.macro + self.fundamentals
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Pillar weights must sum to 1.0, got {total:.3f}")


WEIGHTS_DEFAULT = PillarWeights(0.30, 0.25, 0.20, 0.15, 0.10)
WEIGHTS_ARG_CEDEAR = PillarWeights(0.28, 0.18, 0.18, 0.26, 0.10)
WEIGHTS_TECH_STOCK = PillarWeights(0.28, 0.25, 0.18, 0.12, 0.17)
WEIGHTS_ETF = PillarWeights(0.32, 0.28, 0.20, 0.18, 0.02)
WEIGHTS_NO_OPTIONS = PillarWeights(0.40, 0.00, 0.25, 0.20, 0.15)


ARG_FINANCIAL_TICKERS = {
    "GGAL",
    "BMA",
    "SUPV",
    "EDN",
    "PAM",
    "YPF",
    "CEPU",
    "VIST",
    "PAMP",
    "TGS",
    "CREY",
}


@dataclass
class PillarScores:
    technical: float = 5.0
    options: float = 5.0
    news: float = 5.0
    macro: float = 5.0
    fundamentals: float = 5.0
    composite: float = 5.0

    detail: dict[str, Any] = field(default_factory=dict)

    data_quality: float = 0.0
    shock_override: bool = False


_NEUTRAL = 5.0


def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


def _safe(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(f) else f


def score_technical(state: Any) -> tuple[float, dict[str, float]]:
    detail: dict[str, float] = {}
    has_data = False

    smc = getattr(state, "smc_result", None)
    if smc is not None:
        has_data = True
        bias = str(getattr(smc, "bias", "") or "")
        conf = _safe(getattr(smc, "confidence", 0.5), 0.5)
        if "LONG" in bias.upper():
            smc_pts = 1.0 + conf * 2.0
        elif "SHORT" in bias.upper() or "BEAR" in bias.upper():
            smc_pts = max(0.0, (1.0 - conf) * 0.8)
        else:
            smc_pts = 0.8 + conf * 0.4

        if getattr(smc, "wyckoff_accumulation", False):
            smc_pts = min(3.0, smc_pts + 0.5)

        ob = _safe(getattr(smc, "ob_count_active", 0), 0.0)
        fvg = _safe(getattr(smc, "fvg_count_active", 0), 0.0)
        smc_pts = min(3.0, smc_pts + min(0.5, ob * 0.15 + fvg * 0.10))
        detail["smc"] = round(smc_pts, 3)
    else:
        smc_pts = _NEUTRAL * 0.30
        detail["smc"] = -1.0

    vsa = getattr(state, "vsa_result", None)
    if vsa is not None:
        has_data = True
        vsa_pts = 0.0
        if getattr(vsa, "stopping_volume", False):
            vsa_pts += 1.0
        if getattr(vsa, "no_supply", False):
            vsa_pts += 0.8
        if getattr(vsa, "buy_absorption", False):
            vsa_pts += 0.4
        if getattr(vsa, "no_demand", False):
            vsa_pts -= 0.6
        if getattr(vsa, "selling_climax", False):
            vsa_pts += 0.3
        rvol = _safe(getattr(vsa, "rvol", 1.0), 1.0)
        if rvol > 1.5:
            vsa_pts = min(2.0, vsa_pts + 0.2)
        vsa_pts = _clamp(vsa_pts, 0.0, 2.0)
        detail["vsa"] = round(vsa_pts, 3)
    else:
        vsa_pts = _NEUTRAL * 0.20
        detail["vsa"] = -1.0

    alligator = getattr(state, "alligator_result", None)
    if alligator is not None:
        has_data = True
        if getattr(alligator, "is_bullish", False):
            jaw = _safe(getattr(alligator, "jaw", 0.0), 0.0)
            lips = _safe(getattr(alligator, "lips", 0.0), 0.0)
            spread_bonus = min(0.5, abs(lips - jaw) / max(jaw, 1e-9) * 5) if jaw > 0 else 0.0
            ali_pts = min(2.0, 1.5 + spread_bonus)
        elif getattr(alligator, "is_sleeping", True):
            ali_pts = 0.6
        else:
            ali_pts = 0.0
        detail["alligator"] = round(ali_pts, 3)
    else:
        ali_pts = _NEUTRAL * 0.20
        detail["alligator"] = -1.0

    fractal = getattr(state, "fractal_result", None)
    entropy = getattr(state, "entropy_result", None)
    frac_pts = 0.0

    if fractal is not None:
        has_data = True
        frac_bias = str(getattr(fractal, "bias", "") or "")
        if "LONG" in frac_bias.upper() or "BULLISH" in frac_bias.upper():
            frac_pts += 1.5
        elif "NEUTRAL" in frac_bias.upper():
            frac_pts += 0.7
        choch = _safe(getattr(fractal, "choch_count", 0), 0.0)
        frac_pts = min(2.0, frac_pts + min(0.5, choch * 0.25))

    if entropy is not None:
        has_data = True
        is_ordered = bool(getattr(entropy, "is_ordered", False))
        entropy_val = _safe(getattr(entropy, "value", 0.5), 0.5)
        if is_ordered:
            frac_pts += max(0.5, 1.0 - entropy_val)

    frac_pts = _clamp(frac_pts, 0.0, 3.0)
    detail["fractal_entropy"] = round(frac_pts, 3)

    if not has_data:
        return _NEUTRAL, {"smc": -1.0, "vsa": -1.0, "alligator": -1.0, "fractal_entropy": -1.0}

    score = _clamp(smc_pts + vsa_pts + ali_pts + frac_pts, 0.0, 10.0)
    logger.debug(
        "[Pillar.Technical] %.2f (smc=%.2f vsa=%.2f ali=%.2f frac=%.2f)",
        score,
        smc_pts,
        vsa_pts,
        ali_pts,
        frac_pts,
    )
    return round(score, 3), detail


def score_options(state: Any) -> tuple[float, dict[str, float | str]]:
    detail: dict[str, float | str] = {}

    gex = getattr(state, "gex_result", None)
    options = getattr(state, "options_result", None)

    if gex is None and options is None:
        return _NEUTRAL, {"note": "sin_datos"}

    pts = 0.0

    if gex is not None and getattr(gex, "has_options_data", False):
        bias = str(getattr(gex, "dealer_bias", "") or "")
        if "LONG" in bias.upper() or "BULL" in bias.upper():
            gex_pts = 3.0
        elif "SHORT" in bias.upper() or "BEAR" in bias.upper():
            gex_pts = 0.5
        else:
            gex_pts = 1.5
        if getattr(gex, "is_0dte_dominant", False):
            gex_pts = min(3.0, gex_pts + 0.3)
        detail["gex_regime"] = round(gex_pts, 3)
        pts += gex_pts
    else:
        detail["gex_regime"] = -1.0
        pts += _NEUTRAL * 0.30

    if gex is not None:
        net_vanna = _safe(getattr(gex, "net_vanna_flow", 0.0), 0.0)
        vanna_flip = bool(getattr(gex, "vanna_flip_active", False))
        if net_vanna > 0:
            vanna_pts = min(2.0, 1.0 + net_vanna * 0.1)
        elif vanna_flip:
            vanna_pts = 2.0
        else:
            vanna_pts = max(0.0, 1.0 + net_vanna * 0.08)
        detail["vanna_charm"] = round(vanna_pts, 3)
        pts += vanna_pts
    else:
        detail["vanna_charm"] = -1.0
        pts += _NEUTRAL * 0.20

    if gex is not None and getattr(gex, "has_options_data", False):
        call_gex = _safe(getattr(gex, "call_gex", 0.0), 0.0)
        put_gex = abs(_safe(getattr(gex, "put_gex", 0.0), 0.0))
        total = call_gex + put_gex
        if total > 0:
            pcr = put_gex / total
            oi_pts = _clamp(2.0 - pcr * 2.5, 0.0, 2.0)
        else:
            oi_pts = 1.0
        detail["pcr_oi"] = round(oi_pts, 3)
        pts += oi_pts
    else:
        detail["pcr_oi"] = -1.0
        pts += _NEUTRAL * 0.20

    if options is not None and getattr(options, "ok", False):
        raw_score = _safe(getattr(options, "options_mic_score", 5.0), 5.0)
        opt_pts = raw_score * 0.30
        pdf = getattr(options, "pdf_analytics", None)
        if pdf is not None:
            right_tail = _safe(getattr(pdf, "right_tail_prob", 0.5), 0.5)
            if right_tail > 0.55:
                opt_pts = min(3.0, opt_pts + (right_tail - 0.55) * 2.0)
        detail["options_research"] = round(opt_pts, 3)
        pts += opt_pts
    else:
        detail["options_research"] = -1.0
        pts += _NEUTRAL * 0.30

    score = _clamp(pts, 0.0, 10.0)
    logger.debug("[Pillar.Options] %.2f", score)
    return round(score, 3), detail


def score_news(state: Any) -> tuple[float, dict[str, float | str]]:
    detail: dict[str, float | str] = {}
    sentiment = getattr(state, "sentiment_result", None)  # LLM-based news sentiment
    social = getattr(state, "social_sentiment_result", None)  # Algorithmic social sentiment (v4)

    if sentiment is None and social is None:
        return _NEUTRAL, {"note": "sin_datos_sentiment"}

    # 1. News Sentiment (LLM) - 70% weight if both exist
    news_raw = _safe(getattr(sentiment, "sentiment_score", 0.5), 0.5)
    if -1.0 <= news_raw <= 1.0:
        news_score = (news_raw + 1.0) / 2.0 * 10.0
    else:
        news_score = _clamp(news_raw, 0.0, 10.0)

    # 2. Social Sentiment (Algorithmic)
    social_data = getattr(state, "social_sentiment_raw", None) or (
        state.get("social_sentiment_raw") if isinstance(state, dict) else None
    )
    if social_data is not None:
        sentiment_engine = SentimentEngine()
        social_signal = sentiment_engine.analyze_social(
            social_data, getattr(state, "symbol", "UNKNOWN")
        )
        social_buzz = social_signal.buzz_score if social_signal else 0.0
        social_sent = social_signal.sentiment_score if social_signal else 0.0
        # Combine buzz and sentiment for social pts (0-10)
        social_score = _clamp((social_sent + 1.0) * 4.0 + (social_buzz * 0.2), 0.0, 10.0)
        detail["social_buzz"] = round(social_buzz, 2)
        detail["social_sentiment"] = round(social_sent, 3)
    else:
        social_score = news_score  # Fallback to news if social missing

    detail["news_llm"] = round(news_score, 2)
    detail["social_algo"] = round(social_score, 2)

    # 3. Analyst Price Target Momentum (Phase 4)
    # If avg price target has risen >5% in last 30 days, bonus
    pt_momentum = 0.0
    v4 = (
        getattr(state, "intelligence_v4", {})
        if not isinstance(state, dict)
        else state.get("intelligence_v4", {})
    )
    pt_hist = v4.get("priceTargetHistory", [])
    if pt_hist and len(pt_hist) > 5:
        latest_pt = _safe(
            (
                pt_hist[0].get("priceTarget")
                if isinstance(pt_hist[0], dict)
                else getattr(pt_hist[0], "priceTarget", 0.0)
            ),
            0.0,
        )
        # Find a target from ~30 days ago
        past_target = latest_pt
        for h in pt_hist[5:20]:
            p = _safe(
                h.get("priceTarget") if isinstance(h, dict) else getattr(h, "priceTarget", 0.0), 0.0
            )
            if p > 0:
                past_target = p
                break
        if past_target > 0:
            delta = (latest_pt - past_target) / past_target
            if delta > 0.05:
                pt_momentum = 1.0  # Significant upgrade cycle
            elif delta < -0.05:
                pt_momentum = -1.0  # Downgrade cycle
    detail["analyst_momentum"] = pt_momentum

    # 4. Transcript AI Bonus (Phase 4 Ext)
    transcript_bonus = 0.0
    transcript_data = v4.get("transcript_analysis", {})
    if transcript_data:
        transcript_bonus = _safe(transcript_data.get("conviction_bonus", 0.0), 0.0)
    detail["transcript_bonus"] = transcript_bonus

    score = _clamp(
        news_score * 0.7 + social_score * 0.3 + pt_momentum + transcript_bonus, 0.0, 10.0
    )

    logger.debug(
        "[Pillar.News] %.2f (news=%.2f social=%.2f pt_mom=%.2f trans_bonus=%.2f)",
        score,
        news_score,
        social_score,
        pt_momentum,
        transcript_bonus,
    )
    return round(score, 3), detail


def score_macro(state: Any, is_arg_ticker: bool = False) -> tuple[float, dict[str, float]]:
    detail: dict[str, float] = {}
    macro = getattr(state, "macro_result", None)
    arg_macro = getattr(state, "arg_macro_result", None)

    if macro is None and arg_macro is None:
        return _NEUTRAL, {"note": -1.0}

    pts = 0.0

    if macro is not None:
        cr = macro.curve_regime
        cr_name = str(getattr(cr, "value", cr) or "")
        if cr_name == "RISK-ON":
            curve_pts = 3.0
        elif cr_name == "RISK-OFF":
            spread = _safe(getattr(macro, "t10y2y", 0.0), 0.0)
            curve_pts = max(0.0, 1.0 + spread)
        else:
            curve_pts = 1.5
        detail["yield_curve"] = round(curve_pts, 3)
        pts += curve_pts
    else:
        detail["yield_curve"] = -1.0
        pts += _NEUTRAL * 0.30

    if macro is not None:
        vix = _safe(getattr(macro, "vix_actual", 18.0), 18.0)
        if vix < 13:
            vix_pts = 3.0
        elif vix < 18:
            vix_pts = 2.5
        elif vix < 25:
            vix_pts = 2.0
        elif vix < 32:
            vix_pts = 1.0
        elif vix < 40:
            vix_pts = 0.3
        else:
            vix_pts = 0.0
        detail["vix"] = round(vix_pts, 3)
        pts += vix_pts
    else:
        detail["vix"] = -1.0
        pts += _NEUTRAL * 0.30

    if macro is not None:
        fed = _safe(getattr(macro, "fed_funds_rate", 4.0), 4.0)
        cpi = _safe(getattr(macro, "cpi_yoy", 3.0), 3.0)

        # Phase 4 Ext: v4 Macro Support
        v4 = (
            getattr(state, "intelligence_v4", {})
            if not isinstance(state, dict)
            else state.get("intelligence_v4", {})
        )
        macro_v4 = v4.get("macro", {})
        if macro_v4.get("cpi"):
            cpi = _safe(macro_v4["cpi"][0].get("value"), cpi)

        fed_score = max(0.0, 1.0 - (fed - 2.0) * 0.2)
        cpi_score = max(0.0, 1.0 - (cpi - 2.0) * 0.15)

        # ── Pricing Power Check ──
        # If inflation > 4% and gross margin trend is negative
        pricing_power_penalty = 0.0
        if cpi > 4.0:
            growth = (
                getattr(state, "crecimiento", [])
                if not isinstance(state, dict)
                else state.get("crecimiento", [])
            )
            if growth and len(growth) >= 2:
                # Assuming роста list of dicts with grossProfitMargin
                curr_gm = _safe(growth[0].get("grossProfitMargin"), 0.5)
                prev_gm = _safe(growth[1].get("grossProfitMargin"), 0.5)
                if curr_gm < prev_gm * 0.98:  # >2% drop in margin
                    pricing_power_penalty = -0.5

        fed_pts = _clamp(fed_score + cpi_score + pricing_power_penalty, 0.0, 2.0)
        detail["fed_cpi"] = round(fed_pts, 3)
        detail["pricing_power_penalty"] = pricing_power_penalty
        pts += fed_pts
    else:
        detail["fed_cpi"] = -1.0
        pts += _NEUTRAL * 0.20

    # GDP & Unemployment (Phase 4 Ext)
    v4 = (
        getattr(state, "intelligence_v4", {})
        if not isinstance(state, dict)
        else state.get("intelligence_v4", {})
    )
    macro_v4 = v4.get("macro", {})
    gdp_val = 2.0  # Default/Neutral
    if macro_v4.get("gdp"):
        gdp_val = _safe(macro_v4["gdp"][0].get("value"), 2.0)

    if gdp_val < 0:
        gdp_pts = 0.3  # Recessionary headwind
    elif gdp_val > 3.0:
        gdp_pts = 1.0  # Strong growth tailwind
    else:
        gdp_pts = 0.6  # Moderate

    detail["gdp_score"] = gdp_pts
    pts += gdp_pts

    if is_arg_ticker:
        if arg_macro is not None:
            embi = _safe(getattr(arg_macro, "embi_bps", 1500.0), 1500.0)
            if embi < 400:
                embi_pts = 2.0
            elif embi < 800:
                embi_pts = 1.5
            elif embi < 1200:
                embi_pts = 0.8
            elif embi < 2000:
                embi_pts = 0.3
            else:
                embi_pts = 0.0
            brecha = _safe(getattr(arg_macro, "brecha_pct", 50.0), 50.0)
            if brecha < 10:
                embi_pts = min(2.0, embi_pts + 0.4)
            detail["embi_arg"] = round(embi_pts, 3)
            pts += embi_pts
        else:
            detail["embi_arg"] = -1.0
            pts += 1.0
    else:
        detail["embi_arg"] = 0.0

    score = _clamp(pts, 0.0, 10.0)
    logger.debug(
        "[Pillar.Macro] %.2f (curve=%.2f vix=%.2f fed=%.2f embi=%s)",
        score,
        detail.get("yield_curve", 0.0),
        detail.get("vix", 0.0),
        detail.get("fed_cpi", 0.0),
        detail.get("embi_arg", "N/A"),
    )
    return round(score, 3), detail


def score_fundamentals(state: Any) -> tuple[float, dict[str, float | str]]:
    detail: dict[str, float | str] = {}
    forensic = getattr(state, "forensic_result", None)
    valuation = getattr(state, "valuation_result", None)

    if forensic is None and valuation is None:
        return _NEUTRAL, {"note": "sin_fundamentales"}

    pts = 0.0

    if forensic is not None:
        distress_prob = _safe(
            getattr(state, "forensic_distress_prob", None)
            or (0.0 if not forensic.is_distressed else 0.85),
            0.5,
        )
        health_pts = 3.5 * (1.0 - distress_prob)
        detail["salud_forense"] = round(health_pts, 3)
        pts += health_pts
    else:
        detail["salud_forense"] = -1.0
        pts += _NEUTRAL * 0.35

    if forensic is not None and forensic.f_score is not None:
        f_score = _safe(forensic.f_score, 4.0)
        f_pts = _clamp(f_score / 9.0 * 3.0, 0.0, 3.0)
        if f_score >= 7:
            f_pts = min(3.0, f_pts + 0.3)
        detail["piotroski"] = round(f_pts, 3)
        pts += f_pts
    else:
        detail["piotroski"] = -1.0
        pts += _NEUTRAL * 0.30

    if valuation is not None:
        mos = _safe(getattr(valuation, "margin_of_safety", 0.0), 0.0)
        if mos >= 0.35:
            mos_pts = 2.0
        elif mos >= 0.20:
            mos_pts = 1.7
        elif mos >= 0.10:
            mos_pts = 1.3
        elif mos >= 0.0:
            mos_pts = 0.8
        else:
            mos_pts = max(0.0, 0.5 + mos * 2)
        detail["margin_of_safety"] = round(mos_pts, 3)
        pts += mos_pts
    else:
        detail["margin_of_safety"] = -1.0
        pts += _NEUTRAL * 0.20

    if forensic is not None and getattr(forensic, "beneish_m", None) is not None:
        m = _safe(forensic.beneish_m, -2.5)
        if m < -2.99:
            ben_pts = 1.5
        elif m < -2.22:
            ben_pts = 1.0
        else:
            ben_pts = 0.0
        detail["beneish"] = round(ben_pts, 3)
        pts += ben_pts
    else:
        detail["beneish"] = -1.0
        pts += _NEUTRAL * 0.15

    # ── Phase 5: Financial Health Scores (v4) ──
    v4 = (
        getattr(state, "intelligence_v4", {})
        if not isinstance(state, dict)
        else state.get("intelligence_v4", {})
    )
    health = (v4.get("health_scores") if v4 else {}) or {}
    alt_z = _safe(health.get("altman_z"), 0.0)
    pio_s = _safe(health.get("piotroski"), 0)

    z_pts = 0.0
    if alt_z > 3.0:
        z_pts = 0.5  # Safe
    elif alt_z < 1.8:
        z_pts = -1.5  # Distress
    detail["altman_z"] = round(z_pts, 2)
    pts += z_pts

    pio_pts = 0.0
    if pio_s >= 8:
        pio_pts = 0.5  # Quality
    elif pio_s <= 3:
        pio_pts = -0.8  # Low quality
    detail["piotroski"] = round(pio_pts, 2)
    pts += pio_pts

    # ── Insider Conviction Bonus ──
    insider = getattr(state, "insider_result", None)
    if insider is not None:
        conviction = _safe(getattr(insider, "conviction_score", 0.0), 0.0)
        # Bonus up to 1.5 points for strong insider signal
        insider_bonus = conviction * 0.15
        detail["insider_bonus"] = round(insider_bonus, 2)
        pts += insider_bonus
    else:
        detail["insider_bonus"] = 0.0

    # ── Institutional Stability (Phase 4) ──
    v4 = (
        getattr(state, "intelligence_v4", {})
        if not isinstance(state, dict)
        else state.get("intelligence_v4", {})
    )
    own_hist = v4.get("ownershipHistory", [])
    stability_pts = 0.0
    if len(own_hist) >= 4:
        vals = [
            _safe(
                (
                    o.get("institutionalOwnershipPercentage")
                    if isinstance(o, dict)
                    else getattr(o, "institutionalOwnershipPercentage", 0.0)
                ),
                0.0,
            )
            for o in own_hist[:4]
        ]
        if all(v > 0 for v in vals):
            avg = sum(vals) / 4
            # If current is higher than 1y avg, and not volatile
            if vals[0] >= avg * 0.95:
                stability_pts = 0.5
    detail["inst_stability"] = stability_pts
    pts += stability_pts

    score = _clamp(pts, 0.0, 10.0)
    logger.debug("[Pillar.Fundamentals] %.2f (stability=%.2f)", score, stability_pts)
    return round(score, 3), detail


class PillarScorer:
    """Combines 5 pillar scores into a composite reward score (0-10)."""

    def __init__(self, weights: PillarWeights | None = None):
        self._weights = weights or WEIGHTS_DEFAULT
        self._weights.validate()

    @classmethod
    def for_ticker(cls, ticker: str, has_options: bool = True) -> PillarScorer:
        t = ticker.upper()
        is_arg = t.endswith(".BA") or t in ARG_FINANCIAL_TICKERS
        is_etf = t in {"SPY", "QQQ", "IWM", "GLD", "TLT", "VTI", "XLF", "XLK", "XLC", "XLV", "ARKK"}
        is_tech = t in {
            "AAPL",
            "MSFT",
            "GOOGL",
            "META",
            "AMZN",
            "NVDA",
            "AMD",
            "TSLA",
            "NFLX",
            "CRM",
            "ORCL",
        }

        if not has_options:
            w = WEIGHTS_NO_OPTIONS
        elif is_arg:
            w = WEIGHTS_ARG_CEDEAR
        elif is_etf:
            w = WEIGHTS_ETF
        elif is_tech:
            w = WEIGHTS_TECH_STOCK
        else:
            w = WEIGHTS_DEFAULT
        return cls(weights=w)

    def score(self, state: Any) -> PillarScores:
        w = self._weights
        is_arg = (
            getattr(state, "ticker", "").endswith(".BA")
            or getattr(state, "ticker", "").upper() in ARG_FINANCIAL_TICKERS
        )

        t_score, t_detail = score_technical(state)
        o_score, o_detail = score_options(state)
        n_score, n_detail = score_news(state)
        m_score, m_detail = score_macro(state, is_arg_ticker=is_arg)
        f_score, f_detail = score_fundamentals(state)

        composite = _clamp(
            t_score * w.technical
            + o_score * w.options
            + n_score * w.news
            + m_score * w.macro
            + f_score * w.fundamentals,
            0.0,
            10.0,
        )
        composite = round(composite, 3)

        real_pillars = sum(
            [
                getattr(state, "smc_result", None) is not None,
                getattr(state, "gex_result", None) is not None
                and getattr(state.gex_result, "has_options_data", False),
                getattr(state, "sentiment_result", None) is not None
                or getattr(state, "social_sentiment_result", None) is not None,
                getattr(state, "macro_result", None) is not None,
                getattr(state, "forensic_result", None) is not None,
            ]
        )
        data_quality = real_pillars / 5.0

        shock = False
        markov = getattr(state, "markov_result", None)
        if markov is not None:
            regime = str(getattr(markov, "current_regime", "") or "").lower()
            regime_prob = _safe(getattr(markov, "regime_probability", 0.5), 0.5)
            if ("shock" in regime or "crisis" in regime) and regime_prob > 0.80:
                shock = True
                logger.warning("[PillarScorer] SHOCK override - composite %.2f -> CASH", composite)

        detail: dict[str, Any] = {
            "technical": t_detail,
            "options": o_detail,
            "news": n_detail,
            "macro": m_detail,
            "fundamentals": f_detail,
            "weights": {
                "technical": w.technical,
                "options": w.options,
                "news": w.news,
                "macro": w.macro,
                "fundamentals": w.fundamentals,
            },
        }

        logger.info(
            "[PillarScorer] %s -> T=%.1f O=%.1f N=%.1f M=%.1f F=%.1f -> COMPOSITE=%.2f (quality=%.0f%%)",
            getattr(state, "ticker", "UNKNOWN"),
            t_score,
            o_score,
            n_score,
            m_score,
            f_score,
            composite,
            data_quality * 100,
        )

        return PillarScores(
            technical=t_score,
            options=o_score,
            news=n_score,
            macro=m_score,
            fundamentals=f_score,
            composite=composite,
            detail=detail,
            data_quality=data_quality,
            shock_override=shock,
        )


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: pillar_scorer.py
# ─────────────────────────────────────────────────
