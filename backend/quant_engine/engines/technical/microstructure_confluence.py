from __future__ import annotations
from typing import Any
"""Motor de Confluencia de Microestructura — Sector Técnico.

Orquestador de estado sin estado (stateless) que integra los cinco especialistas técnicos:
1. SMC (Estructura)
2. VSA (Volumen/Absorción)
3. GEX (Gamma Exposure)
4. Vanna/Charm (Flujo de Opciones)
5. Wyckoff (Fases de Mercado)
"""


import logging
from datetime import UTC, datetime

import numpy as np

# Importamos modelos de dominio locales
from .confluence_models import (
    CONFLUENCE_BUY_THRESHOLD,
    CONFLUENCE_CONVICTION_HIGH,
    CONFLUENCE_CONVICTION_MEDIUM,
    CONFLUENCE_SELL_THRESHOLD,
    CONFLUENCE_SQUEEZE_OVERRIDE,
    CONFLUENCE_WEIGHT_GEX,
    CONFLUENCE_WEIGHT_IV,
    CONFLUENCE_WEIGHT_SMC,
    CONFLUENCE_WEIGHT_STRAT,
    CONFLUENCE_WEIGHT_WY_VSA,
    GEX_STOP_ZGL_TOLERANCE,
    ConfluenceAction,
    ConfluenceConviction,
    MicrostructureConfluenceResult,
    SpotVsZGL,
    VSAVannaGEXResult,
    VSAVannaSignal,
    WyckoffFase,
    WyckoffGEXDecision,
)
from .smc import SMCResult
from .vsa import VSAResult

logger = logging.getLogger("quantum_analyzer.microstructure_confluence")


# ══════════════════════════════════════════════════════════════════════════════
# §1  SMC SUB-SCORE  [-1, +1]
# ══════════════════════════════════════════════════════════════════════════════


class SMCScore:
    """Normaliza un SMCResult a un sub-score en escala [-1, +1]."""

    @staticmethod
    def compute(smc: SMCResult | None) -> float:
        """Calcula sub-score basado en sesgo, Order Blocks y FVGs activos."""
        if smc is None:
            return 0.0
        try:
            bias = getattr(smc, "bias", None)
            if bias not in ("LONG", "SHORT"):
                return 0.0

            score = 0.0
            # Mandato Long-Only: solo sumamos si el bias es alcista
            direction = 1.0 if bias == "LONG" else -1.0
            if bias == "LONG":
                score += 0.40
                ob_count = getattr(smc, "ob_count_active", 0)
                fvg_count = getattr(smc, "fvg_count_active", 0)
                choch_count = getattr(smc, "choch_count", 0)
            else:
                score -= 0.40
                ob_count = getattr(smc, "ob_count_active_bearish", 0)
                fvg_count = getattr(smc, "fvg_count_active_bearish", 0)
                choch_count = getattr(smc, "choch_count_bearish", 0)

            # OBs y FVGs (limitados a 2 para evitar sobre-ponderación)
            score += direction * min(ob_count, 2) * 0.15
            score += direction * min(fvg_count, 2) * 0.10

            # Penalización por falta de estructura
            if choch_count == 0 and ob_count == 0:
                score -= direction * 0.20

            return float(np.clip(score, -1.0, 1.0))
        except Exception as exc:
            logger.debug("SMCScore.compute fallback: %s", exc)
            return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# §2  VSA × VANNA × GEX CONFLUENCE MATRIX
# ──────────────────────────────────────────────────────────────────────────────
# Basada en Tabla 20 (Super Expediente N°3)
# ══════════════════════════════════════════════════════════════════════════════

_VSA_VANNA_GEX_TABLE: dict[
    tuple[str, str, str], tuple[ConfluenceAction, ConfluenceConviction, str]
] = {
    ("STOPPING_VOLUME", "BUY_PRESSURE", "POSITIVE"): (
        ConfluenceAction.BUY,
        ConfluenceConviction.HIGH,
        "Triple compra: absorción + Vanna buy + régimen GEX positivo",
    ),
    ("STOPPING_VOLUME", "BUY_PRESSURE", "TRANSITIONAL"): (
        ConfluenceAction.BUY,
        ConfluenceConviction.MEDIUM,
        "Absorción + Vanna buy (régimen en transición)",
    ),
    ("STOPPING_VOLUME", "NEUTRAL", "POSITIVE"): (
        ConfluenceAction.BUY,
        ConfluenceConviction.MEDIUM,
        "Absorción en régimen positivo sin soporte Vanna",
    ),
    ("NO_SUPPLY", "BUY_PRESSURE", "POSITIVE"): (
        ConfluenceAction.BUY,
        ConfluenceConviction.MEDIUM,
        "Sin oferta + Vanna buy + régimen positivo",
    ),
    ("UPTHRUST", "SELL_PRESSURE", "NEGATIVE"): (
        ConfluenceAction.SELL_BLOCKED,
        ConfluenceConviction.HIGH,
        "Triple venta: distribución + Vanna sell + régimen desestabilizador",
    ),
    ("UPTHRUST", "SELL_PRESSURE", "POSITIVE"): (
        ConfluenceAction.SELL_BLOCKED,
        ConfluenceConviction.MEDIUM,
        "Distribución + Vanna sell; GEX positivo actúa como freno",
    ),
    ("NO_DEMAND", "SELL_PRESSURE", "NEGATIVE"): (
        ConfluenceAction.SELL_BLOCKED,
        ConfluenceConviction.MEDIUM,
        "Sin demanda + Vanna sell (régimen negativo)",
    ),
    ("EFFORT_VS_RESULT", "NEUTRAL", "POSITIVE"): (
        ConfluenceAction.WAIT,
        ConfluenceConviction.LOW,
        "Equilibrio Effort/Result — esperar resolución",
    ),
    ("NORMAL", "NEUTRAL", "POSITIVE"): (
        ConfluenceAction.WAIT,
        ConfluenceConviction.LOW,
        "Sin señales específicas — WAIT",
    ),
}

_GEX_REGIME_MAP: dict[str, str] = {
    "POSITIVE": "POSITIVE",
    "POSITIVE_GEX": "POSITIVE",
    "LONG_GAMMA": "POSITIVE",
    "SHORT_GAMMA": "POSITIVE",
    "NEGATIVE": "NEGATIVE",
    "NEGATIVE_GEX": "NEGATIVE",
    "TRANSITIONAL": "TRANSITIONAL",
    "NEUTRAL": "TRANSITIONAL",
}


class VSAVannaGEXConfluence:
    """Orquestador de confluencia triple: VSA x Vanna x GEX."""

    @staticmethod
    def compute(
        vsa_result: VSAResult | None,
        gex_result: Any | None,  # Tipo dinámico (Microestructura Specialist)
    ) -> VSAVannaGEXResult:
        try:
            vsa_label = "NORMAL"
            vanna_signal = VSAVannaSignal.NEUTRAL
            gex_regime = "TRANSITIONAL"

            if vsa_result is not None:
                if getattr(vsa_result, "stopping_volume", False):
                    vsa_label = "STOPPING_VOLUME"
                elif getattr(vsa_result, "no_supply", False):
                    vsa_label = "NO_SUPPLY"
                elif getattr(vsa_result, "no_demand", False):
                    vsa_label = "NO_DEMAND"
                elif getattr(vsa_result, "sell_absorption", False):
                    vsa_label = "UPTHRUST"
                elif getattr(vsa_result, "effort_result_ratio", 1.0) < 0.20:
                    vsa_label = "EFFORT_VS_RESULT"

            if gex_result is not None:
                flow = getattr(gex_result, "net_vanna_flow", 0.0)
                if flow > 1000.0:
                    vanna_signal = VSAVannaSignal.BUY_PRESSURE
                elif flow < -1000.0:
                    vanna_signal = VSAVannaSignal.SELL_PRESSURE
                gex_regime = _GEX_REGIME_MAP.get(
                    str(getattr(gex_result, "dealer_bias", "TRANSITIONAL")).upper(), "TRANSITIONAL"
                )

            lookup_key = (vsa_label, vanna_signal.value, gex_regime)
            action, conviction, explanation = _VSA_VANNA_GEX_TABLE.get(
                lookup_key,
                (
                    ConfluenceAction.WAIT,
                    ConfluenceConviction.LOW,
                    "Sin coincidencia en matriz de confluencia",
                ),
            )

            return VSAVannaGEXResult(
                action=action,
                conviction=conviction,
                vsa_label=vsa_label,
                vanna_pressure=vanna_signal,
                gex_regime=gex_regime,
                explanation=explanation,
            )
        except Exception as exc:
            logger.warning("VSAVannaGEXConfluence error: %s", exc)
            return VSAVannaGEXResult(
                action=ConfluenceAction.WAIT,
                conviction=ConfluenceConviction.LOW,
                vsa_label="UNKNOWN",
                vanna_pressure=VSAVannaSignal.NEUTRAL,
                gex_regime="UNKNOWN",
                explanation=f"Error: {exc}",
            )


# ══════════════════════════════════════════════════════════════════════════════
# §3  WYCKOFF × GEX REGIME TIMING
# ──────────────────────────────────────────────────────────────────────────────
# Basada en Tabla 23 (Super Expediente N°3)
# ══════════════════════════════════════════════════════════════════════════════

_WYCKOFF_GEX_TABLE: dict[tuple[str, str, str], tuple[ConfluenceAction, str, bool]] = {
    ("ACUMULACION", "NEGATIVE", "BELOW"): (
        ConfluenceAction.MONITOR_BUY,
        "Fase de acumulación; monitorear Spring o ZGL",
        False,
    ),
    ("ACUMULACION", "TRANSITIONAL", "AT"): (
        ConfluenceAction.BUY,
        "Golden Setup: Acumulación en Breakout de ZGL",
        True,
    ),
    ("MARKUP", "POSITIVE", "ABOVE"): (
        ConfluenceAction.BUY,
        "Tendencia fuerte en régimen positivo",
        False,
    ),
    ("DISTRIBUCION", "POSITIVE", "ABOVE"): (
        ConfluenceAction.WAIT,
        "Distribución activa; esperar SOW",
        False,
    ),
    ("MARKDOWN", "NEGATIVE", "BELOW"): (
        ConfluenceAction.SELL_BLOCKED,
        "Confirmación bajista; compra bloqueada",
        False,
    ),
    ("RANGO", "TRANSITIONAL", "AT"): (
        ConfluenceAction.WAIT,
        "Sin dirección clara en zona de equilibrio",
        False,
    ),
}


class WyckoffGEXTiming:
    """Orquestador de timing institucional: Wyckoff x Régimen GEX."""

    @staticmethod
    def compute(
        wyckoff_fase: str,
        gex_result: Any | None,
        spot: float,
        squeeze_risk: float = 0.0,
    ) -> WyckoffGEXDecision:
        try:
            gex_regime_raw = "TRANSITIONAL"
            zgl = None
            if gex_result:
                gex_regime_raw = str(getattr(gex_result, "dealer_bias", "TRANSITIONAL")).upper()
                zgl = getattr(gex_result, "zero_gamma_level", None)

            gex_regime = _GEX_REGIME_MAP.get(gex_regime_raw, "TRANSITIONAL")

            # Ubicación relativa respecto al ZGL
            spot_vs_zgl = SpotVsZGL.ABOVE
            if zgl is not None and zgl > 0.0:
                dist = (spot - zgl) / zgl
                if dist > GEX_STOP_ZGL_TOLERANCE:
                    spot_vs_zgl = SpotVsZGL.ABOVE
                elif dist < -GEX_STOP_ZGL_TOLERANCE:
                    spot_vs_zgl = SpotVsZGL.BELOW
                else:
                    spot_vs_zgl = SpotVsZGL.AT

            # Mapeo de fase Wyckoff
            f_up = wyckoff_fase.upper()
            f_key = (
                "ACUMULACION"
                if "ACUM" in f_up
                else (
                    "MARKUP"
                    if "MARKUP" in f_up
                    else (
                        "DISTRIBUCION"
                        if "DIST" in f_up
                        else "MARKDOWN" if "DOWN" in f_up else "RANGO"
                    )
                )
            )

            # Squeeze Override
            if f_key == "MARKDOWN" and gex_regime == "NEGATIVE" and squeeze_risk > 0.80:
                return WyckoffGEXDecision(
                    action=ConfluenceAction.CASH,
                    wyckoff_fase=WyckoffFase(f_key),
                    gex_regime=gex_regime,
                    spot_vs_zgl=spot_vs_zgl,
                    squeeze_risk=squeeze_risk,
                    stop_logic="CASH compulsivo por riesgo de squeeze extremo",
                    is_golden_setup=False,
                )

            lookup_key = (f_key, gex_regime, spot_vs_zgl.value)
            action, stop_logic, is_golden = _WYCKOFF_GEX_TABLE.get(
                lookup_key, (ConfluenceAction.WAIT, "Sin regla de timing específica", False)
            )

            return WyckoffGEXDecision(
                action=action,
                wyckoff_fase=WyckoffFase(f_key),
                gex_regime=gex_regime,
                spot_vs_zgl=spot_vs_zgl,
                squeeze_risk=squeeze_risk,
                stop_anchor=zgl,
                stop_logic=stop_logic,
                is_golden_setup=is_golden,
            )
        except Exception as exc:
            logger.debug("WyckoffGEXTiming error: %s", exc)
            return WyckoffGEXDecision(
                action=ConfluenceAction.WAIT,
                wyckoff_fase=WyckoffFase.UNKNOWN,
                gex_regime="UNKNOWN",
                spot_vs_zgl=SpotVsZGL.ABOVE,
                squeeze_risk=squeeze_risk,
            )


# ══════════════════════════════════════════════════════════════════════════════
# §4  TOTAL CONFLUENCE SCORER
# ══════════════════════════════════════════════════════════════════════════════


class TotalConfluenceScorer:
    """Orquestador final de confluencia técnica y microestructura."""

    @staticmethod
    def compute(
        smc_result: SMCResult | None,
        gex_result: Any | None,
        vsa_result: VSAResult | None,
        options_result: Any | None,
        spot: float,
        squeeze_risk: float = 0.0,
        wyckoff_fase: str = "UNKNOWN",
        strat_score: float = 0.0,
        ticker: str = "UNKNOWN",
    ) -> MicrostructureConfluenceResult:
        ts = datetime.now(UTC).isoformat()
        try:
            # 1. Squeeze Override (Freno de emergencia)
            if squeeze_risk > CONFLUENCE_SQUEEZE_OVERRIDE:
                return MicrostructureConfluenceResult(
                    ticker=ticker, timestamp=ts, signal=ConfluenceAction.CASH, squeeze_override=True
                )

            # 2. Sub-Scoring Independiente
            # GEX (35% peso)
            gex_sub = 0.0
            if gex_result:
                score_raw = getattr(gex_result, "gex_score", 1.0)
                gex_sub = float(np.clip((score_raw / 2.0) * 2.0 - 1.0, -1.0, 1.0))

            # SMC (25% peso)
            smc_sub = SMCScore.compute(smc_result)

            # IV Surface (20% peso)
            iv_sub = 0.0
            iv_surface = getattr(options_result, "iv_surface", None)
            if iv_surface:
                iv_sub = float(getattr(iv_surface, "composite_score", 0.0))

            # Strategy / VSA-Vanna (Extra 20%)
            v_v_gex = VSAVannaGEXConfluence.compute(vsa_result, gex_result)
            wy_vsa_sub = 0.0
            if v_v_gex.action == ConfluenceAction.BUY:
                wy_vsa_sub = 0.80 if v_v_gex.conviction == ConfluenceConviction.HIGH else 0.40
            elif v_v_gex.action == ConfluenceAction.SELL_BLOCKED:
                wy_vsa_sub = -0.60

            # 3. Agregación Ponderada
            total = (
                gex_sub * CONFLUENCE_WEIGHT_GEX
                + smc_sub * CONFLUENCE_WEIGHT_SMC
                + iv_sub * CONFLUENCE_WEIGHT_IV
                + strat_score * CONFLUENCE_WEIGHT_STRAT
                + wy_vsa_sub * CONFLUENCE_WEIGHT_WY_VSA
            )
            total = float(np.clip(total, -1.0, 1.0))

            # 4. Determinación de Acción Final
            if total > CONFLUENCE_BUY_THRESHOLD:
                signal = ConfluenceAction.BUY
            elif total < CONFLUENCE_SELL_THRESHOLD:
                signal = ConfluenceAction.SELL
            else:
                signal = ConfluenceAction.WAIT

            # 5. Confianza y Convicción
            confidence = float(np.clip(abs(total) * 0.5 + 0.3, 0.0, 1.0))
            conv = (
                ConfluenceConviction.HIGH
                if confidence > CONFLUENCE_CONVICTION_HIGH
                else (
                    ConfluenceConviction.MEDIUM
                    if confidence > CONFLUENCE_CONVICTION_MEDIUM
                    else ConfluenceConviction.LOW
                )
            )

            return MicrostructureConfluenceResult(
                ticker=ticker,
                timestamp=ts,
                score=round(total, 4),
                signal=signal,
                confidence=round(confidence, 4),
                conviction=conv,
                gex_sub_score=round(gex_sub, 4),
                smc_sub_score=round(smc_sub, 4),
                iv_sub_score=round(iv_sub, 4),
                strat_sub_score=round(strat_score, 4),
                wyckoff_vsa_sub=round(wy_vsa_sub, 4),
                squeeze_override=False,
                vsa_vanna_gex=v_v_gex,
                wyckoff_gex=WyckoffGEXTiming.compute(wyckoff_fase, gex_result, spot, squeeze_risk),
                ok=True,
            )

        except Exception as exc:
            logger.error("TotalConfluenceScorer crash [%s]: %s", ticker, exc)
            return MicrostructureConfluenceResult(
                ticker=ticker, timestamp=ts, ok=False, error=str(exc)
            )


# ══════════════════════════════════════════════════════════════════════════════
# §5  SYMMETRIC SCORER (additive, bidirectional)
# ══════════════════════════════════════════════════════════════════════════════


def compute_symmetric(
    self,
    smc_score: float,
    gex_score: float,
    iv_score: float,
    strategy_score: float,
    wyckoff_vsa_score: float,
    dispersion: float | None = None,
) -> MicrostructureConfluenceResult:
    """
    Score de confluencia simétrico [-1, +1].
    Positivo → señal LONG, negativo → señal SHORT.

    Pesos (inmutables hasta walk-forward param opt — ver B4):
        GEX        35%
        SMC        25%
        IV surface 20%
        Estrategia 10%
        Wyckoff/VSA 10%
    """
    smc_normalized = max(-1.0, min(1.0, smc_score / 100.0))
    wy_normalized = max(-1.0, min(1.0, wyckoff_vsa_score / 100.0))

    total = (
        gex_score * 0.35
        + smc_normalized * 0.25
        + iv_score * 0.20
        + strategy_score * 0.10
        + wy_normalized * 0.10
    )
    total = max(-1.0, min(1.0, total))

    if dispersion is not None and dispersion > 0.7 and abs(total) < 0.5:
        action = ConfluenceAction.CONFLICT
        conviction = ConfluenceConviction.NEUTRAL
    elif total >= 0.50:
        action = ConfluenceAction.BUY
        conviction = (
            ConfluenceConviction.HIGH_BULL if total >= 0.75 else ConfluenceConviction.MEDIUM_BULL
        )
    elif total >= 0.25:
        action = ConfluenceAction.BUY_WATCH
        conviction = ConfluenceConviction.LOW_BULL
    elif total <= -0.50:
        action = ConfluenceAction.SELL
        conviction = (
            ConfluenceConviction.HIGH_BEAR if total <= -0.75 else ConfluenceConviction.MEDIUM_BEAR
        )
    elif total <= -0.25:
        action = ConfluenceAction.SELL_WATCH
        conviction = ConfluenceConviction.LOW_BEAR
    else:
        action = ConfluenceAction.WAIT
        conviction = ConfluenceConviction.NEUTRAL

    direction = "LONG" if total > 0.05 else "SHORT" if total < -0.05 else "NEUTRAL"

    return {
        "action": action,
        "conviction": conviction,
        "score": round(total, 4),
        "direction": direction,
        "smc_component": round(smc_normalized, 4),
        "gex_component": round(gex_score, 4),
        "iv_component": round(iv_score, 4),
        "wy_component": round(wy_normalized, 4),
    }


# Inyectar como método estático en TotalConfluenceScorer
TotalConfluenceScorer.compute_symmetric = staticmethod(compute_symmetric)  # type: ignore[attr-defined]


def compute_engine_dispersion(
    smc_score: float,
    vsa_score: float,
    ofd_score: float,
    structure_score: float,
    hmm_score: float,
) -> tuple[float, list[tuple[str, str]]]:
    """
    Calcula dispersión entre engines para detectar contradicciones.

    Returns:
        (dispersion, conflicting_pairs)
        dispersion: stdev de polaridades ∈ [0, 1]
        conflicting_pairs: pares en contradicción
    """
    import statistics

    def polarity(score: float, threshold: float = 20.0) -> int:
        if score > threshold:
            return 1
        if score < -threshold:
            return -1
        return 0

    engines = {
        "smc": polarity(smc_score),
        "vsa": polarity(vsa_score),
        "ofd": polarity(ofd_score),
        "structure": polarity(structure_score),
        "hmm": polarity(hmm_score),
    }

    values = list(engines.values())
    dispersion = statistics.stdev(values) if len(values) > 1 else 0.0

    names = list(engines.keys())
    pol_vals = list(engines.values())
    conflicting_pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if pol_vals[i] != 0 and pol_vals[j] != 0 and pol_vals[i] != pol_vals[j]:
                a_dir = "+" if pol_vals[i] > 0 else "-"
                b_dir = "+" if pol_vals[j] > 0 else "-"
                conflicting_pairs.append((f"{names[i]}:{a_dir}", f"{names[j]}:{b_dir}"))

    return round(dispersion, 4), conflicting_pairs


# ─────────────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: TÉCNICO
# Archivo      : microstructure_confluence.py
# Sub-capa     : Confluence Orchestrator
# Eliminado    : Referencias QuantumBeta / Expediente N°3 / TradingView direct.
# Reconstruido : Reemplazo de confluence_models.py local.
# Preservado   : Matriz Triple (VSAxVannaxGEX), Pesos del Score, Squeeze Override.
# Mandato      : Long-Only (SELL_BLOCKED).
# ─────────────────────────────────────────────────────────
