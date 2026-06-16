from decimal import Decimal
from typing import Any

from backend.models.global_context_snapshot import GlobalContextSnapshot


class GlobalContextEngine:
    """Evaluates macro context data to generate a GlobalContextSnapshot."""

    def __init__(self) -> None:
        self.vix_meltdown = 35.0
        self.vix_risk_off = 25.0

    def evaluate(self, context_data: dict[str, Any]) -> GlobalContextSnapshot:
        """Evaluate raw context data to compute factors."""
        vix = float(context_data.get("vix", 0.0))
        spy = context_data.get("spy")
        qqq = context_data.get("qqq")
        eem = context_data.get("eem")
        iwm = context_data.get("iwm")
        fear_greed_val = context_data.get("fear_greed_index")

        fear_greed = int(fear_greed_val) if fear_greed_val is not None else None

        is_valid = True
        if vix == 0.0 or spy is None or qqq is None:
            is_valid = False

        spy_trend = "NEUTRAL"
        if spy:
            if spy.daily_change_pct > 0.0:
                spy_trend = "BULL"
            elif spy.daily_change_pct < 0.0:
                spy_trend = "BEAR"

        qqq_trend = "NEUTRAL"
        if qqq:
            if qqq.daily_change_pct > 0.0:
                qqq_trend = "BULL"
            elif qqq.daily_change_pct < 0.0:
                qqq_trend = "BEAR"

        spy_eem_trend = "NEUTRAL"
        if spy and eem:
            diff = float(spy.daily_change_pct) - float(eem.daily_change_pct)
            if diff > 0.005:
                spy_eem_trend = "SPY_OUTPERFORM"
            elif diff < -0.005:
                spy_eem_trend = "EEM_OUTPERFORM"

        qqq_iwm_trend = "NEUTRAL"
        if qqq and iwm:
            diff = float(qqq.daily_change_pct) - float(iwm.daily_change_pct)
            if diff > 0.005:
                qqq_iwm_trend = "QQQ_OUTPERFORM"
            elif diff < -0.005:
                qqq_iwm_trend = "IWM_OUTPERFORM"

        market_regime = "NORMAL"
        regime_factor = Decimal("1.0")
        macro_conflict_score = Decimal("0.0")

        if vix >= self.vix_meltdown:
            market_regime = "MELTDOWN"
            regime_factor = Decimal("0.0")
            macro_conflict_score = Decimal("1.0")
        elif vix >= self.vix_risk_off:
            market_regime = "RISK_OFF"
            regime_factor = Decimal("0.5")
            macro_conflict_score = Decimal("0.8")
        elif spy_trend == "BEAR" and qqq_trend == "BEAR":
            market_regime = "BEAR"
            regime_factor = Decimal("0.8")
            macro_conflict_score = Decimal("0.5")
        elif spy_trend == "BULL" and qqq_trend == "BULL":
            market_regime = "BULL"
            regime_factor = Decimal("1.2")

        # Fear and Greed overrides
        if fear_greed is not None:
            if fear_greed < 25:
                # Extreme fear
                regime_factor *= Decimal("0.8")
                macro_conflict_score = max(macro_conflict_score, Decimal("0.6"))
            elif fear_greed > 75:
                # Extreme greed, overextended
                regime_factor *= Decimal("0.9")
                macro_conflict_score = max(macro_conflict_score, Decimal("0.4"))

        global_factor = regime_factor

        return GlobalContextSnapshot(
            vix_level=Decimal(str(vix)),
            spy_trend=spy_trend,
            qqq_trend=qqq_trend,
            spy_eem_trend=spy_eem_trend,
            qqq_iwm_trend=qqq_iwm_trend,
            fear_greed_index=fear_greed,
            market_regime=market_regime,
            macro_conflict_score=macro_conflict_score,
            regime_factor=regime_factor,
            global_factor=global_factor,
            is_valid=is_valid,
        )
