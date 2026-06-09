"""Motor Cuantitativo SMC (Smart Money Concepts) — Sector Técnico.

Implementa la detección vectorizada de BOS (Break of Structure), CHoCH (Change of Character),
Order Blocks institucionales, Fair Value Gaps y modelos ICT.
"""

from __future__ import annotations

import logging
from typing import Final

import numpy as np
import pandas as pd

from ...domain.technical.smc_models import (
    DirectionalBias,
    FairValueGap,
    ICTModelName,
    ICTModelResult,
    LiquiditySweep,
    OrderBlock,
    SMCResult,
    StructureEvent,
    StructureEventType,
    SweepType,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# §0  CALIBRATED PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

_DISPLACEMENT_DELTA: Final[float] = 1.3
_OB_LOOKBACK:        Final[int]   = 10
_OB_INVALIDATION_PCT: Final[float] = 0.50
_OB_VOL_WINDOW:      Final[int]   = 20
_EPSILON:            Final[float] = 1e-8
_SWING_LOOKBACK:     Final[int]   = 5
_BOS_CONFIRM_MULT:   Final[float] = 1.001
_LIQUIDITY_WINDOW:   Final[int]   = 20
_LIQ_VOL_FACTOR:     Final[float] = 1.5
_ICT_WINDOW:         Final[int]   = 5
_RANGE_ATR_RATIO:    Final[float] = 0.30
_OTE_LOW:            Final[float] = 0.618
_OTE_HIGH:           Final[float] = 0.786
_OTE_PIVOT_BARS:     Final[int]   = 40
_OB_BIAS_RECENCY:    Final[int]   = 3

_ICT_CONFIDENCE: Final[dict[str, float]] = {
    "STOP_HUNT":  0.85,
    "TRAP":       0.72,
    "OTE":        0.90,
    "RANGE_TRAP": 0.78,
}

_MIN_ROWS: Final[int] = max(_OB_VOL_WINDOW, _LIQUIDITY_WINDOW, 30)


class SMCEngine:
    """Motor Cuantitativo de Smart Money Concepts."""

    def __init__(
        self,
        displacement_delta : float = _DISPLACEMENT_DELTA,
        ob_lookback        : int   = _OB_LOOKBACK,
        ob_invalidation_pct: float = _OB_INVALIDATION_PCT,
        ob_vol_window      : int   = _OB_VOL_WINDOW,
        swing_lookback     : int   = _SWING_LOOKBACK,
        bos_confirm_mult   : float = _BOS_CONFIRM_MULT,
        liquidity_window   : int   = _LIQUIDITY_WINDOW,
        liq_vol_factor     : float = _LIQ_VOL_FACTOR,
        ict_window         : int   = _ICT_WINDOW,
        range_atr_ratio    : float = _RANGE_ATR_RATIO,
        ote_pivot_bars     : int   = _OTE_PIVOT_BARS,
        ob_bias_recency    : int   = _OB_BIAS_RECENCY,
    ) -> None:
        self._delta           = max(float(displacement_delta), 1.3)
        self._ob_lookback     = int(ob_lookback)
        self._ob_inv_pct      = float(ob_invalidation_pct)
        self._ob_vol_window   = int(ob_vol_window)
        self._swing_lb        = int(swing_lookback)
        self._bos_mult        = float(bos_confirm_mult)
        self._liq_window      = int(liquidity_window)
        self._liq_vol_factor  = float(liq_vol_factor)
        self._ict_window      = int(ict_window)
        self._range_atr_ratio = float(range_atr_ratio)
        self._ote_pivot_bars  = int(ote_pivot_bars)
        self._ob_bias_recency = int(ob_bias_recency)

    def analyze(self, df: pd.DataFrame, ticker: str = "UNKNOWN", timeframe: str = "UNKNOWN") -> SMCResult:
        """Pipeline de análisis SMC completo."""
        try:
            df_work = self._validate_and_normalize(df)

            order_blocks = self._detect_order_blocks(df_work)
            fvg_zones    = self._detect_fvg(df_work)
            structure    = self._detect_bos_choch(df_work, fvg_zones)
            sweeps       = self._detect_liquidity_sweeps(df_work)
            sesgo        = self._compute_bias(df_work, order_blocks, fvg_zones, structure, sweeps)

            ict_models, dominant, agg_conf = self._detect_ict_models(df_work, order_blocks, fvg_zones, structure, sweeps)
            ote_top, ote_bot = self._compute_ote_zone(structure, df_work)

            score = self._smc_composite_score(sesgo, agg_conf, order_blocks, fvg_zones, structure)

            return SMCResult(
                ticker=ticker, timeframe=timeframe,
                order_blocks=order_blocks, fvg_zones=fvg_zones, structure_events=structure, liquidity_sweeps=sweeps,
                sesgo=sesgo, ict_models=ict_models, dominant_model=dominant, aggregate_confidence=round(agg_conf, 6),
                ote_top=ote_top, ote_bottom=ote_bot,
                key_levels=self._build_key_levels_approx(df_work, order_blocks, fvg_zones),
                composite_score=score
            )
        except Exception as exc:
            logger.exception("[SMCEngine] Error en %s/%s: %s", ticker, timeframe, exc)
            return SMCResult(ticker=ticker, timeframe=timeframe, sesgo=DirectionalBias.NEUTRAL, error=str(exc))

    def _build_key_levels_approx(self, df: pd.DataFrame, obs: list[OrderBlock], fvgs: list[FairValueGap]) -> dict[str, float]:
        """Versión aproximada de niveles clave ante ausencia de TechnicalMath."""
        levels: dict[str, float] = {}
        c, v = df["close"].to_numpy(), df["volume"].to_numpy()

        # VWAP aproximado
        levels["vwap_session"] = float(np.sum(c * v) / (np.sum(v) + _EPSILON))

        # OB Stop Loss (LONG: low del último OB BULLISH; SHORT: high del último OB BEARISH)
        bull_obs = [ob for ob in obs if ob.direction == "BULLISH"]
        if bull_obs: levels["ob_stop_loss"] = float(bull_obs[-1].low)
        bear_obs = [ob for ob in obs if ob.direction == "BEARISH"]
        if bear_obs: levels["ob_stop_loss_short"] = float(bear_obs[-1].high)

        # FVG 50%
        if fvgs:
            last = fvgs[-1]
            levels["fvg_gap_50"] = float(last.bottom + last.size * 0.5)

        return levels

    def _validate_and_normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        df_out = df.copy()
        df_out.columns = [c.lower().strip() for c in df_out.columns]
        alias = {"adj close": "close", "adj_close": "close", "vol": "volume", "v": "volume", "o": "open", "h": "high", "l": "low", "c": "close"}
        df_out.rename(columns={k: v for k, v in alias.items() if k in df_out.columns}, inplace=True)
        req = {"open", "high", "low", "close", "volume"}
        if not req.issubset(df_out.columns): raise ValueError(f"Missing columns: {req - set(df_out.columns)}")
        df_out.dropna(subset=list(req), inplace=True)
        if len(df_out) < _MIN_ROWS: raise ValueError(f"Insufficient data ({len(df_out)})")
        df_out.reset_index(drop=True, inplace=True)
        return df_out

    def _detect_order_blocks(self, df: pd.DataFrame) -> list[OrderBlock]:
        c, o, h, l, v = df["close"].to_numpy(), df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["volume"].to_numpy()
        n = len(df)
        ar = (df["high"] - df["low"]).rolling(self._ob_lookback).mean().to_numpy()
        vm = df["volume"].rolling(self._ob_vol_window, min_periods=1).mean().to_numpy()
        c_min_fwd = pd.Series(c).iloc[::-1].cummin().iloc[::-1].to_numpy()
        obs: list[OrderBlock] = []

        for t in range(n - 2):
            t1, t2 = t + 1, min(t + 2, n - 1)
            ar_t = ar[t]
            if np.isnan(ar_t) or ar_t < _EPSILON: continue

            h_t, l_t, o_t, c_t = h[t], l[t], o[t], c[t]
            ob_50 = l_t + (h_t - l_t) * self._ob_inv_pct
            delta_eff = abs(c[t1] - o[t1]) / ar_t
            wick_l = min(o_t, c_t) - l_t
            r_wb = wick_l / (abs(o_t - c_t) + _EPSILON)
            ezone = ezone = l_t + (wick_l * 0.5 if r_wb > 1.0 else (h_t - l_t) * 0.5)

            # BULLISH OB
            if (c_t < o_t and c[t1] > h_t and c[t2] > c[t1] and delta_eff >= self._delta):
                if c_min_fwd[t1] >= ob_50:
                    obs.append(OrderBlock(bar_index=t, direction="BULLISH", high=float(h_t), low=float(l_t),
                                          close=float(c_t), entry_zone=float(ezone), delta=float(delta_eff),
                                          r_wb=float(r_wb), sweep_candle=bool(r_wb > 1.0),
                                          fvg_present=bool(l[t2] > h_t), strength=float(v[t]/vm[t]), ob_50_level=float(ob_50)))
            # BEARISH OB (consumido por SHORT confluence + scoring bidireccional)
            elif (c_t > o_t and c[t1] < l_t and c[t2] < c[t1] and delta_eff >= self._delta):
                obs.append(OrderBlock(bar_index=t, direction="BEARISH", high=float(h_t), low=float(l_t),
                                      close=float(c_t), entry_zone=float(ezone), delta=float(delta_eff),
                                      r_wb=float(r_wb), sweep_candle=bool(r_wb > 1.0),
                                      fvg_present=bool(h[t2] < l_t), strength=float(v[t]/vm[t]), ob_50_level=float(ob_50)))
        return obs

    def _detect_fvg(self, df: pd.DataFrame) -> list[FairValueGap]:
        h, l, n = df["high"].to_numpy(), df["low"].to_numpy(), len(df)
        fvgs = []
        for i in range(1, n - 1):
            if l[i+1] > h[i-1]: fvgs.append(FairValueGap(bar_index=i, direction="BULLISH", top=float(l[i+1]), bottom=float(h[i-1]), size=float(l[i+1]-h[i-1])))
            elif h[i+1] < l[i-1]: fvgs.append(FairValueGap(bar_index=i, direction="BEARISH", top=float(l[i-1]), bottom=float(h[i+1]), size=float(l[i-1]-h[i+1])))
        return fvgs

    def _detect_bos_choch(self, df: pd.DataFrame, fvgs: list[FairValueGap]) -> list[StructureEvent]:
        lb, bm = self._swing_lb, self._bos_mult
        shs = df["high"].shift(1).rolling(lb, min_periods=2).max()
        sls = df["low"].shift(1).rolling(lb, min_periods=2).min()
        fvg_bars = {f.bar_index for f in fvgs}
        events = []

        for i in range(len(df)):
            if np.isnan(shs[i]) or np.isnan(sls[i]): continue
            c = df["close"].iloc[i]
            valid_range = range(int(i-1), int(i+2))
            has_fvg = any(idx in fvg_bars for idx in valid_range)
            if not has_fvg: continue

            if c > shs[i] * bm:
                etype = StructureEventType.CHOCH_BULL if sls[i] < sls.shift(lb)[i] else StructureEventType.BOS_BULL
                events.append(StructureEvent(bar_index=i, event_type=etype.value, level=float(shs[i])))
            elif c < sls[i] * (2.0 - bm):
                etype = StructureEventType.CHOCH_BEAR if shs[i] > shs.shift(lb)[i] else StructureEventType.BOS_BEAR
                events.append(StructureEvent(bar_index=i, event_type=etype.value, level=float(sls[i])))
        return events

    def _detect_liquidity_sweeps(self, df: pd.DataFrame) -> list[LiquiditySweep]:
        lw, n = self._liq_window, len(df)
        h, l, v = df["high"].to_numpy(), df["low"].to_numpy(), df["volume"].to_numpy()
        vm = df["volume"].rolling(lw, min_periods=1).mean().to_numpy()
        sweeps = []
        for i in range(lw, n):
            if v[i] > vm[i] * self._liq_vol_factor:
                rh, rl = float(h[i-lw:i].max()), float(l[i-lw:i].min())
                if h[i] > rh: sweeps.append(LiquiditySweep(bar_index=i, sweep_type=SweepType.BSL_SWEEP.value, level=rh, rvol=float(v[i]/vm[i])))
                elif l[i] < rl: sweeps.append(LiquiditySweep(bar_index=i, sweep_type=SweepType.SSL_SWEEP.value, level=rl, rvol=float(v[i]/vm[i])))
        return sweeps

    def _compute_bias(
        self,
        df: pd.DataFrame,
        order_blocks: list[OrderBlock],
        fvg_list: list[FairValueGap],
        bos_choch_events: list[StructureEvent],
        sweeps: list[LiquiditySweep],
    ) -> DirectionalBias:
        if df is None or len(df) < 5:
            return DirectionalBias.CASH

        close  = df["close"].iloc[-1]
        high20 = df["high"].rolling(20).max().iloc[-1]
        low20  = df["low"].rolling(20).min().iloc[-1]
        mid20  = (high20 + low20) / 2.0

        active_bull_obs = [ob for ob in order_blocks if ob.direction == "BULLISH" and not getattr(ob, "mitigated", False)]
        active_bear_obs = [ob for ob in order_blocks if ob.direction == "BEARISH" and not getattr(ob, "mitigated", False)]

        active_bull_fvg = [f for f in fvg_list if f.direction == "BULLISH" and not getattr(f, "mitigated", False)]
        active_bear_fvg = [f for f in fvg_list if f.direction == "BEARISH" and not getattr(f, "mitigated", False)]

        recent_events = bos_choch_events[-10:] if bos_choch_events else []
        bull_events = [e for e in recent_events if "BULL" in str(getattr(e, "event_type", "")).upper()]
        bear_events = [e for e in recent_events if "BEAR" in str(getattr(e, "event_type", "")).upper()]

        price_above_mid = close > mid20
        price_below_mid = close < mid20

        recent_sweeps = sweeps[-5:] if sweeps else []
        bull_sweeps = [s for s in recent_sweeps if getattr(s, "sweep_type", "").lower() in ("ssl_sweep", "equal_low_sweep")]
        bear_sweeps = [s for s in recent_sweeps if getattr(s, "sweep_type", "").lower() in ("bsh_sweep", "equal_high_sweep")]

        bull_evidence = (
            len(active_bull_obs) * 2
            + len(active_bull_fvg)
            + len(bull_events) * 1.5
            + len(bull_sweeps)
            + (1 if price_above_mid else 0)
        )
        bear_evidence = (
            len(active_bear_obs) * 2
            + len(active_bear_fvg)
            + len(bear_events) * 1.5
            + len(bear_sweeps)
            + (1 if price_below_mid else 0)
        )

        STRONG_THRESHOLD = 4.0
        WEAK_THRESHOLD   = 2.0

        if bull_evidence >= STRONG_THRESHOLD and bull_evidence > bear_evidence * 1.5:
            return DirectionalBias.BULLISH
        if bull_evidence >= WEAK_THRESHOLD and bull_evidence > bear_evidence:
            return DirectionalBias.BULLISH_WATCH
        if bear_evidence >= STRONG_THRESHOLD and bear_evidence > bull_evidence * 1.5:
            return DirectionalBias.BEARISH
        if bear_evidence >= WEAK_THRESHOLD and bear_evidence > bull_evidence:
            return DirectionalBias.BEARISH_WATCH
        if bull_evidence < 1 and bear_evidence < 1:
            return DirectionalBias.CASH

        return DirectionalBias.NEUTRAL

    def _detect_ict_models(self, df: pd.DataFrame, obs: list[OrderBlock], fvgs: list[FairValueGap],
                          structure: list[StructureEvent], sweeps: list[LiquiditySweep]) -> tuple[list[ICTModelResult], ICTModelResult | None, float]:
        # SSL sweep → setup LONG (stop hunt clásico bull). BSL sweep → setup SHORT (espejo).
        models = []
        if sweeps:
            last = sweeps[-1].sweep_type
            if last in (SweepType.SSL_SWEEP.value, SweepType.BSL_SWEEP.value):
                models.append(ICTModelResult(name=ICTModelName.STOP_HUNT, confidence=_ICT_CONFIDENCE["STOP_HUNT"]))

        if not models: return [], None, 0.0
        dom = max(models, key=lambda m: m.confidence)
        return models, dom, float(np.mean([m.confidence for m in models]))

    def _detect_ict_models_bearish_extension(self, df: pd.DataFrame, order_blocks: list[OrderBlock], sweeps: list[LiquiditySweep], fvg_list: list[FairValueGap]) -> list[dict[str, object]]:
        """
        Extensión bearish de _detect_ict_models.
        Llamar al final del método existente y concatenar resultados.
        """
        results: list[dict[str, object]] = []
        if df is None or len(df) < 10:
            return results

        close   = df["close"].values
        high    = df["high"].values
        low     = df["low"].values

        recent_bear_sweeps = [s for s in sweeps[-5:] if getattr(s, "sweep_type", "").lower() in ("bsh_sweep", "equal_high_sweep")]
        active_bear_obs    = [ob for ob in order_blocks if ob.direction == "bearish" and not getattr(ob, "mitigated", False)]

        if recent_bear_sweeps and close[-1] < high[-2]:
            results.append({
                "model": "BEARISH_STOP_HUNT",
                "confidence": 0.75,
                "bar_index": len(df) - 1,
                "direction": "bearish",
            })

        if len(df) >= 20:
            swing_high = max(high[-20:])
            swing_low  = min(low[-20:])
            ote_high   = swing_high - 0.618 * (swing_high - swing_low)
            ote_low    = swing_high - 0.786 * (swing_high - swing_low)
            if ote_low <= close[-1] <= ote_high and active_bear_obs:
                results.append({
                    "model": "BEARISH_OTE",
                    "confidence": 0.70,
                    "bar_index": len(df) - 1,
                    "ote_zone": {"high": ote_high, "low": ote_low},
                    "direction": "bearish",
                })

        return results

    def _compute_ote_zone(self, structure: list[StructureEvent], df: pd.DataFrame) -> tuple[float | None, float | None]:
        if not structure: return None, None
        p_idx = structure[-1].bar_index
        start = max(0, p_idx - self._ote_pivot_bars)
        h, l = df["high"].to_numpy()[start:p_idx], df["low"].to_numpy()[start:p_idx]
        if len(h) == 0: return None, None
        sh, sl = float(h.max()), float(l.min())
        imp = sh - sl
        return round(sh - _OTE_LOW * imp, 8), round(sh - _OTE_HIGH * imp, 8)

    def _smc_composite_score(
        self,
        bias: DirectionalBias,
        confidence: float,
        order_blocks: list[OrderBlock],
        fvg_list: list[FairValueGap],
        bos_choch_events: list[StructureEvent],
    ) -> float:
        """Score SMC simétrico [-100, +100].

        Positivo = LONG conviction, negativo = SHORT conviction. Cumple
        invariante 3 (DEBE retornar negativo para bias BEARISH).
        """
        if bias in (DirectionalBias.BULLISH, DirectionalBias.BULLISH_WATCH):
            bias_pts = 30.0 if bias == DirectionalBias.BULLISH else 15.0
            conf_pts = 30.0 * min(confidence, 1.0)

            active_bull_obs = [ob for ob in order_blocks if ob.direction == "BULLISH" and not getattr(ob, "mitigated", False)]
            ob_pts = 15.0 * min(len(active_bull_obs), 3) / 3.0

            active_bull_fvg = [f for f in fvg_list if f.direction == "BULLISH" and not getattr(f, "mitigated", False)]
            fvg_pts = 15.0 * min(len(active_bull_fvg), 2) / 2.0

            recent = bos_choch_events[-10:] if bos_choch_events else []
            bull_struct = [e for e in recent if "BULL" in str(getattr(e, "event_type", "")).upper()]
            struct_pts = 10.0 * min(len(bull_struct), 2) / 2.0

            raw = bias_pts + conf_pts + ob_pts + fvg_pts + struct_pts
            return float(min(raw, 100.0))

        if bias in (DirectionalBias.BEARISH, DirectionalBias.BEARISH_WATCH):
            bias_pts = 30.0 if bias == DirectionalBias.BEARISH else 15.0
            conf_pts = 30.0 * min(confidence, 1.0)

            active_bear_obs = [ob for ob in order_blocks if ob.direction == "BEARISH" and not getattr(ob, "mitigated", False)]
            ob_pts = 15.0 * min(len(active_bear_obs), 3) / 3.0

            active_bear_fvg = [f for f in fvg_list if f.direction == "BEARISH" and not getattr(f, "mitigated", False)]
            fvg_pts = 15.0 * min(len(active_bear_fvg), 2) / 2.0

            recent = bos_choch_events[-10:] if bos_choch_events else []
            bear_struct = [e for e in recent if "BEAR" in str(getattr(e, "event_type", "")).upper()]
            struct_pts = 10.0 * min(len(bear_struct), 2) / 2.0

            raw = bias_pts + conf_pts + ob_pts + fvg_pts + struct_pts
            return float(-min(raw, 100.0))

        return 0.0
