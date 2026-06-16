from __future__ import annotations

import logging
from collections.abc import Iterable

# MIGRATION: Dependencias de dominio interno
from ..domain.mic_models import (
    AggregatedSignals,
    AlligatorSignal,
    ComponentScore,
    ForensicSignal,
    FractalSignal,
    GEXSignal,
    MacroSignal,
    MICDecision,
    OptionsSignal,
    SentimentSignal,
    SignalDirection,
    VetoCode,
    VSALabel,
    VSASignal,
    WyckoffFase,
)

log = logging.getLogger("quantum_analyzer.specialists.portfolio.mic")

# ─────────────────────────────────────────────────────────────────────────────
# CANONICAL CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────


class MICConstants:
    """
    All MIC numeric constants in one namespace.
    Weights sum to 10.0 (MAX_SCORE).
    """

    WEIGHTS: dict[str, float] = {
        "fractal_score": 2.0,
        "gex_score": 2.0,
        "macro_score": 1.5,
        "vsa_score": 1.5,
        "options_score": 1.0,
        "sentiment_score": 0.5,
        "alligator_score": 1.0,
        "forensic_score": 0.5,
    }

    MAX_SCORE: float = 10.0

    THRESHOLD_SNIPER: float = 8.2
    THRESHOLD_REDUCED: float = 6.0
    THRESHOLD_CASH: float = 4.5

    BONUS_WYCKOFF_OB: float = 0.5
    BONUS_VANNA_SWEEP: float = 1.5

    V_ALLIGATOR_SPREAD_MIN: float = 0.005
    V_CALLWALL_DISTANCE_MAX: float = 0.005
    V_VIX_MAX: float = 30.0
    V_EMBI_ARG_MAX: float = 1200.0
    V_KELLY_MIN: float = 0.0
    V_BENEISH_THRESHOLD: float = -2.22
    V_ALTMAN_DISTRESS_ZONE: str = "DISTRESS"
    V_PIOTROSKI_MIN: int = 2
    V_FEAR_GREED_MAX: float = 75.0

    HMM_LOW_CONFIDENCE_THRESHOLD: float = 40.0
    HMM_LOW_CONFIDENCE_PENALTY: float = 0.75

    MACRO_PLACEHOLDER_RATIO: float = 0.5
    FORENSIC_PLACEHOLDER_SCORE: float = 0.5

    ARG_FINANCIAL_TICKERS: frozenset[str] = frozenset({"GGAL", "BMA", "SUPV", "BBAR"})


# ─────────────────────────────────────────────────────────────────────────────
# MICArbitrator
# ─────────────────────────────────────────────────────────────────────────────


class MICArbitrator:
    """
    The quantitative arbitrator of QuantumAnalyzer.
    Deterministic, mathematical scoring engine for LONG-ONLY management.
    """

    C = MICConstants

    def __init__(
        self,
        threshold_sniper: float | None = None,
        threshold_reduced: float | None = None,
        threshold_cash: float | None = None,
        enabled_engines: Iterable[str] | None = None,
    ) -> None:
        self._threshold_sniper = (
            float(threshold_sniper) if threshold_sniper is not None else self.C.THRESHOLD_SNIPER
        )
        self._threshold_reduced = (
            float(threshold_reduced) if threshold_reduced is not None else self.C.THRESHOLD_REDUCED
        )
        self._threshold_cash = (
            float(threshold_cash) if threshold_cash is not None else self.C.THRESHOLD_CASH
        )
        self._enabled_engines = (
            set(enabled_engines) if enabled_engines is not None else set(self.C.WEIGHTS.keys())
        )

    def decide(self, signals: AggregatedSignals) -> MICDecision:
        """Entry point. Runs the full pipeline and returns the verdict."""
        try:
            return self._pipeline(signals)
        except Exception as exc:
            log.error("[MIC Arbitrator][%s] Internal error: %s", signals.ticker, exc, exc_info=True)
            return MICDecision(
                ticker=signals.ticker,
                signal=SignalDirection.CASH,
                size_multiplier=0.0,
                score_bruto=0.0,
                score_final=0.0,
                kelly_macro=1.0,
                vetos_activos=[f"INTERNAL_ERROR [{type(exc).__name__}]"],
                size_label="CASH (error)",
            )

    def _pipeline(self, s: AggregatedSignals) -> MICDecision:
        vetos: list[str] = []

        # ── PHASE 1: PRE-FILTERS ──────────────────────────────────────────────
        if s.news and s.news.binary_event_warning:
            vetos.append(f"{VetoCode.PRE_FILTRO_BINARY_EVENT.value} [{s.news.max_categoria}]")
            return self._cash(s, vetos)

        if s.macro and s.macro.liquidity_drain:
            vetos.append(
                f"{VetoCode.PRE_FILTRO_LIQUIDITY_DRAIN.value} [sev={s.macro.drain_severity:.2f}]"
            )
            return self._cash(s, vetos)

        # ── PHASE 2: ABSOLUTE VETOS ───────────────────────────────────────────
        if s.alligator and s.alligator.jaw_lips_spread_pct < self.C.V_ALLIGATOR_SPREAD_MIN:
            vetos.append(VetoCode.VETO_1_ALLIGATOR_DORMIDO.value)
            return self._cash(s, vetos)

        if s.gex and s.gex.call_wall and s.gex.spot_price:
            dist = abs(s.gex.spot_price - s.gex.call_wall) / s.gex.spot_price
            if dist <= self.C.V_CALLWALL_DISTANCE_MAX:
                vetos.append(VetoCode.VETO_2_CALLWALL.value)
                return self._cash(s, vetos)

        if s.macro and s.macro.vix_actual > self.C.V_VIX_MAX:
            vetos.append(VetoCode.VETO_3_MARKOV_SHOCK.value)
            return self._cash(s, vetos)

        # EMBI Arg
        is_arg_fin = s.ticker.endswith(".BA") and any(
            fin in s.ticker for fin in self.C.ARG_FINANCIAL_TICKERS
        )
        if is_arg_fin and s.macro and s.macro.embi_arg_pb > self.C.V_EMBI_ARG_MAX:
            vetos.append(VetoCode.VETO_4_EMBI_ARG.value)
            return self._cash(s, vetos)

        if s.risk and s.risk.kelly_fraction <= self.C.V_KELLY_MIN:
            vetos.append(VetoCode.VETO_5_KELLY_NEGATIVO.value)
            return self._cash(s, vetos)

        if s.forensic and self._is_forensic_veto_active(s.forensic):
            vetos.append(VetoCode.VETO_6_FORENSIC.value)
            return self._cash(s, vetos)

        if s.macro and s.macro.fear_greed_index > self.C.V_FEAR_GREED_MAX:
            vetos.append(VetoCode.VETO_BLANDO_FEAR_GREED.value)
            return self._cash(s, vetos)

        # ── PHASE 3: WEIGHTED SCORE ───────────────────────────────────────────
        component_scores = self._compute_all_scores(s)
        score_bruto = self._normalize_weighted_score(component_scores)

        # ── PHASE 4: BONUSES ──────────────────────────────────────────────────
        score_post, bonificaciones = self._aplicar_bonificaciones(score_bruto, s)
        score_post = min(score_post, self.C.MAX_SCORE)

        # ── PHASE 5: MARKOV MULTIPLIER ────────────────────────────────────────
        kelly_macro = s.markov.kelly_macro_multiplier if s.markov else 1.0
        if s.markov and s.markov.hmm_confidence_pct < self.C.HMM_LOW_CONFIDENCE_THRESHOLD:
            kelly_macro *= self.C.HMM_LOW_CONFIDENCE_PENALTY
        score_final = score_post * kelly_macro

        # ── PHASE 6: SIGNAL + SIZING ──────────────────────────────────────────
        risk_factor = s.modo_conservador_factor if s.modo_conservador else 1.0
        confluencia = False

        if score_final >= self._threshold_sniper:
            confluencia = self._check_triple_confluencia(s)
            signal, size_mult, size_label = (
                SignalDirection.LONG,
                1.0 if confluencia else 0.5,
                "SNIPER" if confluencia else "LONG (HALF)",
            )
        elif score_final >= self._threshold_reduced:
            signal, size_mult, size_label = (SignalDirection.LONG, 0.5, "LONG HALF SIZE")
        else:
            signal, size_mult, size_label = (SignalDirection.CASH, 0.0, "CASH")

        return MICDecision(
            ticker=s.ticker,
            signal=signal,
            size_multiplier=round(size_mult * risk_factor, 4),
            score_bruto=round(score_bruto, 4),
            score_final=round(score_final, 4),
            kelly_macro=round(kelly_macro, 4),
            vetos_activos=vetos,
            component_scores=component_scores,
            bonificaciones_aplicadas=bonificaciones,
            triple_confluencia_activa=confluencia,
            modo_conservador=s.modo_conservador,
            size_label=size_label,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # SCORERS & HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_all_scores(self, s: AggregatedSignals) -> dict[str, ComponentScore]:
        return {
            "fractal_score": self._score_fractal(s.fractal),
            "gex_score": self._score_gex(s.gex),
            "macro_score": self._score_macro(s.macro),
            "vsa_score": self._score_vsa(s.vsa),
            "alligator_score": self._score_alligator(s.alligator),
            "forensic_score": self._score_forensic(s.forensic),
            "sentiment_score": self._score_sentiment(s.sentiment),
            "options_score": self._score_options(s.options),
        }

    def _score_fractal(self, fractal: FractalSignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["fractal_score"]
        if not fractal:
            return ComponentScore(raw_sub_score=0.0, weight=w, contribution=0.0, detail="N/A")
        sub = 1.0 if fractal.bias == "LONG" else 0.0
        return ComponentScore(
            raw_sub_score=sub, weight=w, contribution=sub * w, detail=f"bias={fractal.bias}"
        )

    def _score_gex(self, gex: GEXSignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["gex_score"]
        if not gex:
            return ComponentScore(raw_sub_score=0.0, weight=w, contribution=0.0, detail="N/A")
        pkr = gex.pcr_oi
        sub = 1.0 if pkr >= 1.5 else (0.75 if pkr >= 1.0 else (0.4 if pkr >= 0.7 else 0.1))
        if gex.gex_regime == "SHORT_GAMMA":
            sub = min(sub * 1.2, 1.0)
        return ComponentScore(
            raw_sub_score=sub, weight=w, contribution=sub * w, detail=f"pcr={pkr:.2f}"
        )

    def _score_macro(self, macro: MacroSignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["macro_score"]
        if not macro:
            return ComponentScore(
                raw_sub_score=0.5, weight=w, contribution=0.5 * w, detail="Default"
            )
        sub = max(0.0, (macro.regime_score + 1.0) / 2.0 - macro.drain_severity * 0.5)
        return ComponentScore(
            raw_sub_score=sub,
            weight=w,
            contribution=sub * w,
            detail=f"reg={macro.regime_score:.2f}",
        )

    def _score_vsa(self, vsa: VSASignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["vsa_score"]
        if not vsa:
            return ComponentScore(raw_sub_score=0.0, weight=w, contribution=0.0, detail="N/A")
        m = {
            VSALabel.STOPPING_VOLUME: 1.0,
            VSALabel.NO_SUPPLY: 0.85,
            VSALabel.CLIMAX_SELL: 0.7,
            VSALabel.NEUTRAL: 0.3,
        }
        sub = m.get(vsa.señal_dominante, 0.3)
        if vsa.a_index_zscore > 2.0:
            sub = min(sub * 1.15, 1.0)
        return ComponentScore(
            raw_sub_score=sub, weight=w, contribution=sub * w, detail=vsa.señal_dominante.value
        )

    def _score_alligator(self, alligator: AlligatorSignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["alligator_score"]
        if not alligator:
            return ComponentScore(raw_sub_score=0.0, weight=w, contribution=0.0, detail="N/A")
        m = {WyckoffFase.MARKUP: 1.0, WyckoffFase.ACUMULACION: 0.7, WyckoffFase.RANGO: 0.3}
        sub = m.get(alligator.wyckoff_fase, 0.3)
        return ComponentScore(
            raw_sub_score=sub, weight=w, contribution=sub * w, detail=alligator.wyckoff_fase.value
        )

    def _score_forensic(self, forensic: ForensicSignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["forensic_score"]
        if not forensic:
            return ComponentScore(
                raw_sub_score=0.5, weight=w, contribution=0.5 * w, detail="Default"
            )
        score = 0.0
        if forensic.piotroski_score and forensic.piotroski_score >= 7:
            score += 0.5
        if forensic.altman_zone == "SAFE":
            score += 0.3
        if forensic.beneish_m and forensic.beneish_m < -2.99:
            score += 0.2
        sub = min(score, 1.0)
        return ComponentScore(
            raw_sub_score=sub,
            weight=w,
            contribution=sub * w,
            detail=f"Pio={forensic.piotroski_score}",
        )

    def _score_sentiment(self, sentiment: SentimentSignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["sentiment_score"]
        sub = sentiment.score if sentiment else 0.5
        return ComponentScore(raw_sub_score=sub, weight=w, contribution=sub * w, detail="Sentiment")

    def _score_options(self, options: OptionsSignal | None) -> ComponentScore:
        w = self.C.WEIGHTS["options_score"]
        return OptionsScorer.compute_contribution(options, w)

    def _aplicar_bonificaciones(self, base: float, s: AggregatedSignals) -> tuple[float, list[str]]:
        score, bonus = base, []
        if (
            s.alligator
            and s.alligator.wyckoff_fase == WyckoffFase.ACUMULACION
            and s.alligator.ob_bullish_activo
        ):
            score += self.C.BONUS_WYCKOFF_OB
            bonus.append("Wyckoff_Bonus")
        if (s.options and s.options.vanna_sweep_probability > 0.8) or (
            s.forensic and s.forensic.bonificacion_paso_b
        ):
            score *= self.C.BONUS_VANNA_SWEEP
            bonus.append("Vanna_Sweep_Bonus")
        return score, bonus

    def _check_triple_confluencia(self, s: AggregatedSignals) -> bool:
        gex_ok = s.gex and (s.gex.gex_regime == "SHORT_GAMMA" or s.gex.pcr_oi >= 1.0)
        vsa_ok = s.vsa and (
            s.vsa.a_index_zscore > 2.0 or s.vsa.señal_dominante == VSALabel.CLIMAX_SELL
        )
        smc_ok = s.smc and s.smc.has_active_ob_bullish and s.smc.has_unmitigated_fvg
        return bool(gex_ok and vsa_ok and smc_ok)

    def _normalize_weighted_score(self, scores: dict[str, ComponentScore]) -> float:
        active = [k for k in scores if k in self._enabled_engines]
        if not active:
            return 0.0
        w_sum = sum(scores[k].contribution for k in active)
        d_max = sum(self.C.WEIGHTS[k] for k in active)
        return (w_sum / d_max) * self.C.MAX_SCORE if d_max > 0 else 0.0

    def _is_forensic_veto_active(self, f: ForensicSignal) -> bool:
        if f.beneish_m and f.beneish_m > self.C.V_BENEISH_THRESHOLD:
            return True
        if f.altman_zone == self.C.V_ALTMAN_DISTRESS_ZONE:
            return True
        if (
            f.piotroski_score
            and f.piotroski_reliable
            and f.piotroski_score <= self.C.V_PIOTROSKI_MIN
        ):
            return True
        return f.is_distressed

    def _cash(self, s: AggregatedSignals, vetos: list[str]) -> MICDecision:
        return MICDecision(
            ticker=s.ticker,
            signal=SignalDirection.CASH,
            size_multiplier=0.0,
            score_bruto=0.0,
            score_final=0.0,
            kelly_macro=1.0,
            vetos_activos=vetos,
            size_label=f"CASH ({vetos[0] if vetos else 'Veto'})",
        )


class OptionsScorer:
    @staticmethod
    def compute_contribution(signal: OptionsSignal | None, weight: float) -> ComponentScore:
        if not signal:
            return ComponentScore(raw_sub_score=0.0, weight=weight, contribution=0.0, detail="N/A")
        sub = (signal.vex_score * 0.65 + signal.cex_score * 0.35) / 10.0
        if signal.vanna_sweep_probability > 0.8:
            sub = min(sub * 1.25, 1.0)
        return ComponentScore(
            raw_sub_score=sub, weight=weight, contribution=sub * weight, detail="Options"
        )


# ────────────────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: PORTAFOLIO
# Archivo         : mic.py
# Sub-capa        : Arbitrator
# Solver/Optimizer: N/A (Scoring based)
# Eliminado       : Definición de modelos (ahora en mic_models.py).
# Preservado      : Lógica de Vetos, Scoring Pesado y Triple Confluencia.
# Pendientes      : Integración con Orchestrator (Phase 7).
# ────────────────────────────────────────────────────────────────────
