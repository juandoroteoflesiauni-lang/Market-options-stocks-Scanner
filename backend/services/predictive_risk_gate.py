from typing import Any
from decimal import Decimal
from pydantic import BaseModel, ConfigDict

from backend.config.logger_setup import get_logger
from backend.domain.portfolio_risk_models import PositionSide

logger = get_logger(__name__)

class PredictiveRiskDecision(BaseModel):
    """Output from the advanced predictive risk gate."""
    model_config = ConfigDict(frozen=True)
    is_allowed: bool
    size_multiplier: Decimal
    reasons: list[str]
    warnings: list[str]

class PredictiveRiskGate:
    """
    Evaluates advanced quantitative signals extracted from predictive engines
    (NLP Catalyst, Skew Fat Tails, Zomma, Dealer Flow Dynamics, Gamma Exposure,
    Options Flow Toxicity, Markov Regime, VSA).
    Provides a real-time risk filter before the final position sizing in Phase D.
    """

    def evaluate(
        self, direction: PositionSide, symbol: str, context_data: dict[str, Any], entry: float | None = None
    ) -> PredictiveRiskDecision:
        reasons: list[str] = []
        warnings: list[str] = []
        is_allowed = True
        multiplier = Decimal("1.0")

        # Assume predictive signals are passed via context_data directly or inside 'predictive_signals' dict
        pred_signals = context_data.get("predictive_signals", {})
        if not pred_signals:
            # Fallback for flat dictionary injection
            pred_signals = context_data

        multiplier = self._evaluate_nlp_catalyst(pred_signals, direction, is_allowed, multiplier, reasons, warnings)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        multiplier = self._evaluate_skew_fattails(pred_signals, direction, is_allowed, multiplier, reasons, warnings)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        multiplier = self._evaluate_zomma(pred_signals, direction, is_allowed, multiplier, reasons, warnings)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        multiplier = self._evaluate_dealer_flow(pred_signals, direction, is_allowed, multiplier, reasons, warnings)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        multiplier = self._evaluate_gamma_exposure(pred_signals, direction, is_allowed, multiplier, reasons, warnings, entry)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        multiplier = self._evaluate_options_toxicity(pred_signals, direction, is_allowed, multiplier, reasons, warnings)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        multiplier = self._evaluate_markov_regime(pred_signals, direction, is_allowed, multiplier, reasons, warnings)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        multiplier = self._evaluate_vsa(pred_signals, direction, is_allowed, multiplier, reasons, warnings)
        if multiplier == Decimal("0.0"):
            is_allowed = False

        return PredictiveRiskDecision(
            is_allowed=is_allowed,
            size_multiplier=multiplier,
            reasons=reasons,
            warnings=warnings,
        )

    def _evaluate_nlp_catalyst(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str]
    ) -> Decimal:
        nlp = signals.get("nlp_catalyst")
        if not nlp:
            return multiplier
        
        # Structure expects something like EventRiskProfile
        tone = nlp.get("tone", "NEUTRAL")
        risk_score = float(nlp.get("event_risk_score", 0.0))
        
        if tone == "ALARMING":
            reasons.append(f"NLP Catalyst detected ALARMING tone with risk score {risk_score:.2f}.")
            return Decimal("0.0")
        
        # For a BEARISH tone ahead of long entry, or highly positive event risk, penalize
        if direction == "LONG" and tone == "BEARISH" and risk_score > 0.5:
            warnings.append(f"NLP Catalyst detected BEARISH tone. Risk score: {risk_score:.2f}. Sizing down.")
            return multiplier * Decimal("0.5")
            
        return multiplier

    def _evaluate_skew_fattails(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str]
    ) -> Decimal:
        skew = signals.get("skew_fat_tails")
        if not skew:
            return multiplier

        flag = skew.get("risk_flag", "RISK_CLEAR")
        
        if direction == "LONG":
            if flag == "RISK_AVOID":
                reasons.append("Skew/FatTails detected severe downside risk (RISK_AVOID).")
                return Decimal("0.0")
            elif flag == "RISK_CAUTION":
                warnings.append("Skew/FatTails detected moderate tail risk (RISK_CAUTION). Sizing down.")
                return multiplier * Decimal("0.5")
        else: # SHORT
            if flag == "RISK_SHORT_AVOID":
                reasons.append("Skew/FatTails detected severe upside melt-up risk (RISK_SHORT_AVOID).")
                return Decimal("0.0")
            elif flag == "RISK_SHORT_CAUTION":
                warnings.append("Skew/FatTails detected moderate upside risk (RISK_SHORT_CAUTION). Sizing down.")
                return multiplier * Decimal("0.5")
                
        return multiplier

    def _evaluate_zomma(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str]
    ) -> Decimal:
        zomma = signals.get("zomma")
        if not zomma:
            return multiplier
        
        # Expected from zomma engine dict
        vol_crush = zomma.get("vol_crush_pct", 0.0)
        # If severe vol crush is imminent (> 15% IV crush), penalize trades that rely on volatility or long gamma
        if vol_crush > 0.15:
            warnings.append(f"Zomma Engine detects imminent vol crush ({vol_crush*100:.1f}%). Adjusting risk.")
            return multiplier * Decimal("0.75")
            
        return multiplier

    def _evaluate_dealer_flow(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str]
    ) -> Decimal:
        flow = signals.get("dealer_flow")
        if not flow:
            return multiplier
            
        dealer_signal = float(flow.get("dealer_directional_signal", 0.0))
        
        if direction == "LONG" and dealer_signal < -0.7:
            warnings.append(f"Dealer flow strongly negative ({dealer_signal:.2f}). Conflicting with LONG bias.")
            return multiplier * Decimal("0.5")
        
        if direction == "SHORT" and dealer_signal > 0.7:
            warnings.append(f"Dealer flow strongly positive ({dealer_signal:.2f}). Conflicting with SHORT bias.")
            return multiplier * Decimal("0.5")

        return multiplier

    def _evaluate_gamma_exposure(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str], entry: float | None = None
    ) -> Decimal:
        # [PD-3][TH][IM]
        gex = signals.get("gamma_exposure")
        if not gex:
            return multiplier
        
        flip_signal = float(gex.get("flip_signal", 1.0))
        regime = gex.get("regime_context", "LONG_GAMMA")
        
        if direction == "LONG":
            if flip_signal < -0.7:
                reasons.append(f"Gamma Flip: spot deep in short gamma territory ({flip_signal:.2f}).")
                return Decimal("0.0")
            elif flip_signal < -0.3:
                warnings.append(f"Gamma Flip: spot below flip point ({flip_signal:.2f}). Sizing down.")
                multiplier *= Decimal("0.5")
            
            if regime == "SHORT_GAMMA":
                warnings.append("Gamma Flip: market is in SHORT_GAMMA regime. Sizing down.")
                multiplier *= Decimal("0.6")
        elif direction == "SHORT":
            if flip_signal > 0.7:
                warnings.append(f"Gamma Flip: spot deep in long gamma territory ({flip_signal:.2f}). Sizing down.")
                multiplier *= Decimal("0.75")
                
        multiplier = self._check_wall_proximity(
            direction, entry, gex.get("gamma_wall_up"), gex.get("gamma_wall_down"), multiplier, warnings
        )
        return multiplier

    def _check_wall_proximity(
        self, direction: PositionSide, entry: float | None, wall_up: Any, wall_down: Any,
        multiplier: Decimal, warnings: list[str]
    ) -> Decimal:
        # [PD-3][TH][IM]
        if entry is None or entry <= 0:
            return multiplier
        if direction == "LONG" and wall_up is not None:
            dist = (float(wall_up) - entry) / entry
            if 0.0 <= dist < 0.0075:
                warnings.append(f"Gamma Wall: entry {entry:.2f} is too close to Call Wall resistance {float(wall_up):.2f} (dist: {dist * 100:.2f}%). Sizing down.")
                return multiplier * Decimal("0.5")
        elif direction == "SHORT" and wall_down is not None:
            dist = (entry - float(wall_down)) / entry
            if 0.0 <= dist < 0.0075:
                warnings.append(f"Gamma Wall: entry {entry:.2f} is too close to Put Wall support {float(wall_down):.2f} (dist: {dist * 100:.2f}%). Sizing down.")
                return multiplier * Decimal("0.5")
        return multiplier

    def _evaluate_options_toxicity(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str]
    ) -> Decimal:
        # [PD-3][TH][IM]
        tox = signals.get("options_toxicity")
        if not tox:
            return multiplier
            
        vpin_percentile = float(tox.get("vpin_percentile", 0.0))
        flow_regime = tox.get("flow_regime", "NORMAL")
        net_flow = float(tox.get("net_options_flow", 0.0))
        
        if vpin_percentile > 0.95:
            reasons.append(f"Flow Toxicity: Extreme VPIN percentile {vpin_percentile:.2f} (EXTREME_TOXICITY_BLOCK).")
            return Decimal("0.0")
            
        if vpin_percentile > 0.70:
            if flow_regime == "STRESS":
                warnings.append(f"Flow Toxicity: High VPIN ({vpin_percentile:.2f}) and STRESS regime. Sizing down.")
                multiplier *= Decimal("0.6")
            else:
                warnings.append(f"Flow Toxicity: High VPIN ({vpin_percentile:.2f}). Sizing down.")
                multiplier *= Decimal("0.75")
                
        if direction == "LONG" and net_flow < -0.6:
            warnings.append(f"Flow Toxicity: Net options flow bearish ({net_flow:.2f}). Sizing down.")
            multiplier *= Decimal("0.7")
        elif direction == "SHORT" and net_flow > 0.6:
            warnings.append(f"Flow Toxicity: Net options flow bullish ({net_flow:.2f}). Sizing down.")
            multiplier *= Decimal("0.7")
            
        return multiplier

    def _evaluate_markov_regime(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str]
    ) -> Decimal:
        # [PD-3][TH][IM]
        markov = signals.get("markov_regime")
        if not markov:
            return multiplier
            
        state = markov.get("current_state", "UNKNOWN")
        risk = float(markov.get("transition_risk", 0.0))
        sig = markov.get("regime_signal", "STABLE")
        
        if state == "BEAR_VOLATILE" and direction == "LONG":
            if risk > 0.8:
                reasons.append(f"Markov Regime: BEAR_VOLATILE state with high transition risk ({risk:.2f}).")
                return Decimal("0.0")
            warnings.append("Markov Regime: BEAR_VOLATILE state detected. Sizing down.")
            multiplier *= Decimal("0.5")
            
        if state == "CHAOTIC":
            if sig == "CRITICAL":
                reasons.append("Markov Regime: CHAOTIC state with CRITICAL transition signal.")
                return Decimal("0.0")
            warnings.append("Markov Regime: CHAOTIC state detected. Sizing down.")
            multiplier *= Decimal("0.5")
            
        if sig == "CRITICAL":
            warnings.append("Markov Regime: CRITICAL transition signal detected. Sizing down.")
            multiplier *= Decimal("0.75")
            
        return multiplier

    def _evaluate_vsa(
        self, signals: dict[str, Any], direction: PositionSide, is_allowed: bool, multiplier: Decimal,
        reasons: list[str], warnings: list[str]
    ) -> Decimal:
        # [PD-3][TH][IM]
        vsa = signals.get("vsa")
        if not vsa:
            return multiplier
            
        score = float(vsa.get("composite_score", 0.0))
        buy_abs = bool(vsa.get("buy_absorption", False))
        sell_abs = bool(vsa.get("sell_absorption", False))
        climax = bool(vsa.get("is_buying_climax_active", False))
        
        if direction == "LONG":
            if climax:
                reasons.append("VSA: Buying Climax active (exhaustion risk).")
                return Decimal("0.0")
            if score < -35.0:
                warnings.append(f"VSA: Bearish composite score ({score:.1f}). Sizing down.")
                multiplier *= Decimal("0.6")
            if sell_abs:
                warnings.append("VSA: Sell absorption active (institutional distribution). Sizing down.")
                multiplier *= Decimal("0.5")
        elif direction == "SHORT":
            if score > 35.0:
                warnings.append(f"VSA: Bullish composite score ({score:.1f}). Sizing down.")
                multiplier *= Decimal("0.6")
            if buy_abs:
                warnings.append("VSA: Buy absorption active (institutional accumulation). Sizing down.")
                multiplier *= Decimal("0.5")
                
        return multiplier
