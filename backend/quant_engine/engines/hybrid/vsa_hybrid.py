"""
VSA Híbrido — Volume Spread Analysis + Vanna Flow de Opciones
═════════════════════════════════════════════════════════════
Combina el análisis clásico de VSA (Tom Williams / Wyckoff) con
el flujo de Vanna de opciones para distinguir si una barra de
alto volumen tiene respaldo institucional real o es un amago.

VSA CLÁSICO analiza 3 dimensiones de cada vela:
    Spread  = high - low  (magnitud del movimiento)
    Cierre  = posición del cierre relativo al spread
    Volumen = actividad transaccional de la vela

El patrón central del VSA:
    Esfuerzo = Volumen ALTO + Spread ALTO
    Resultado = movimiento sostenido en la dirección del esfuerzo
    Cuando Esfuerzo es alto pero Resultado es bajo → TRAMPA

VANNA FLOW añade la dimensión institucional:
    Vanna = dDelta/dIV = sensibilidad del delta a cambios en IV
    Vanna Flow = Σ [ Vanna(k) × OI(k) × 100 ] para toda la cadena

    Cuando la IV cambia (sube o baja), Vanna obliga a los dealers
    a rebalancear su delta hedge comprando o vendiendo acciones.

    Pico de Vanna positivo + barra alcista de alto volumen:
        → IV bajó → dealers reciben delta → compran acciones
        → el spread alcista tiene RESPALDO INSTITUCIONAL real
        → Esfuerzo con resultado → señal de continuación

    Pico de Vanna negativo + barra alcista de alto volumen:
        → IV subió → dealers pierden delta → venden acciones
        → el spread alcista es CONTRA el flujo institucional
        → Esfuerzo sin resultado → señal de trampa / reversión

Los 8 patrones VSA híbridos:
    1. BUYING_CLIMAX_VANNA:    volumen extremo alcista + Vanna neg
    2. SELLING_CLIMAX_VANNA:   volumen extremo bajista + Vanna pos
    3. EFFORT_RESULT_BULL:     alto vol + spread + Vanna pos confirmado
    4. EFFORT_RESULT_BEAR:     alto vol + spread + Vanna neg confirmado
    5. NO_SUPPLY_VANNA:        bajo vol + spread corto + Vanna pos (acumulación)
    6. NO_DEMAND_VANNA:        bajo vol + spread corto + Vanna neg (distribución)
    7. STOPPING_VOLUME_BULL:   volumen extremo + cierre bajo + Vanna pos (suelo)
    8. STOPPING_VOLUME_BEAR:   volumen extremo + cierre alto + Vanna neg (techo)

Score de calidad (0-100):
    F1. Magnitud relativa del volumen (z-score)     0-25 pts
    F2. Análisis del spread (vs ATR)                0-20 pts
    F3. Posición del cierre                         0-15 pts
    F4. Magnitud del Vanna flow                     0-25 pts
    F5. Alineación dirección vela vs Vanna          0-15 pts

Fuentes:
    BingX WebSocket  → velas 1m OHLCV
    Massive API      → Vanna por strike (dDelta/dIV × OI × 100)
"""

import warnings
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# 1. ESTRUCTURAS DE DATOS
# ─────────────────────────────────────────────


@dataclass
class VannaSnapshot:
    """
    Vanna flow de Massive API — 1 por minuto.

    Vanna = dDelta/dIV para cada strike.
    VannaFlow = Σ [ Vanna(k) × OI(k) × 100 ] para toda la cadena.

    Interpretación del dealer (dealers SHORT las opciones del cliente):
        VannaFlow > 0: si IV sube → dealers necesitan más delta → COMPRAN acciones
                       si IV baja → dealers tienen exceso delta → VENDEN acciones
        VannaFlow < 0: si IV sube → dealers pierden delta → VENDEN acciones
                       si IV baja → dealers ganan delta  → COMPRAN acciones

    Para detectar el efecto en TIEMPO REAL necesitamos saber
    si la IV acaba de subir o bajar en el minuto actual.
    """

    timestamp: pd.Timestamp
    ticker: str
    vanna_net: float  # Vanna flow neto total de la cadena
    vanna_calls: float  # Vanna solo de calls
    vanna_puts: float  # Vanna solo de puts
    vanna_atm: float  # Vanna de strikes ATM (±2%)
    iv_atm: float  # IV ATM actual
    iv_change_1m: float  # Cambio de IV en el último minuto (dIV)
    net_gex: float  # GEX neto para contexto
    spot: float  # Precio spot del subyacente real

    # Efecto neto calculado: Vanna × dIV = presión de cobertura en acciones
    vanna_pressure: float = field(init=False)

    def __post_init__(self):
        # Presión de cobertura = Vanna × cambio de IV
        # Positivo = dealers comprando acciones (alcista)
        # Negativo = dealers vendiendo acciones (bajista)
        self.vanna_pressure = self.vanna_net * self.iv_change_1m


@dataclass
class CandleBar:
    timestamp: pd.Timestamp
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float = field(init=False)
    close_pos: float = field(init=False)  # 0=cierre abajo, 1=cierre arriba

    def __post_init__(self):
        self.spread = self.high - self.low
        self.close_pos = (self.close - self.low) / self.spread if self.spread > 1e-9 else 0.5


# ─────────────────────────────────────────────
# 2. CLASIFICADOR VSA CLÁSICO
# ─────────────────────────────────────────────


class VSAClassifier:
    """
    Clasifica cada vela según VSA clásico usando percentiles
    de volumen y spread calculados sobre una ventana deslizante.

    Parámetros de clasificación:
        Volumen alto:    > percentil 70 de la ventana
        Volumen bajo:    < percentil 30 de la ventana
        Spread amplio:   > percentil 70 de spread de la ventana
        Spread estrecho: < percentil 30 de spread de la ventana
        Cierre alto:     close_pos > 0.67 (tercio superior)
        Cierre bajo:     close_pos < 0.33 (tercio inferior)
        Cierre medio:    entre 0.33 y 0.67

    Patrones VSA clásicos sin opciones:
        BUYING_CLIMAX:    vol alto + spread amplio + cierre alto
        SELLING_CLIMAX:   vol alto + spread amplio + cierre bajo
        EFFORT_UP:        vol alto + spread amplio + cierre alto (alcista)
        EFFORT_DOWN:      vol alto + spread amplio + cierre bajo (bajista)
        NO_SUPPLY:        vol bajo + spread estrecho + cierre alto
        NO_DEMAND:        vol bajo + spread estrecho + cierre bajo
        STOPPING_VOL_UP:  vol muy alto + spread amplio + cierre bajo (suelo potencial)
        STOPPING_VOL_DOWN:vol muy alto + spread amplio + cierre alto (techo potencial)
        WEAKNESS:         vol alto + spread estrecho (absorción)
        STRENGTH:         vol bajo + spread amplio (fuerza oculta)
        NEUTRAL:          ningún patrón claro
    """

    def __init__(self, window: int = 20):
        self.window = window
        self._vol_buf: deque = deque(maxlen=window)
        self._spr_buf: deque = deque(maxlen=window)
        self._atr_buf: deque = deque(maxlen=window)
        self._prev_close: float | None = None

    def _percentile_rank(self, buf: deque, value: float) -> float:
        """Rank del valor en el buffer (0-1)."""
        arr = np.array(buf)
        if len(arr) < 3:
            return 0.5
        return float(np.sum(arr <= value) / len(arr))

    def _atr_update(self, c: CandleBar) -> float:
        tr = c.spread
        if self._prev_close is not None:
            tr = max(tr, abs(c.high - self._prev_close), abs(c.low - self._prev_close))
        self._prev_close = c.close
        self._atr_buf.append(tr)
        return float(np.mean(self._atr_buf)) if self._atr_buf else tr

    def classify(self, candle: CandleBar) -> dict:
        """
        Clasifica la vela y retorna métricas VSA.
        """
        self._vol_buf.append(candle.volume)
        self._spr_buf.append(candle.spread)
        atr = self._atr_update(candle)

        vol_rank = self._percentile_rank(self._vol_buf, candle.volume)
        spr_rank = self._percentile_rank(self._spr_buf, candle.spread)

        # z-score del volumen (para detección de picos extremos)
        if len(self._vol_buf) >= 5:
            vol_arr = np.array(self._vol_buf)
            vol_mean = float(np.mean(vol_arr))
            vol_std = float(np.std(vol_arr)) + 1e-9
            vol_zscore = (candle.volume - vol_mean) / vol_std
        else:
            vol_zscore = 0.0

        # Clasificación de niveles
        vol_high = vol_rank >= 0.70
        vol_low = vol_rank <= 0.30
        vol_extreme = vol_rank >= 0.90 or vol_zscore >= 2.5

        spr_wide = spr_rank >= 0.70
        spr_narrow = spr_rank <= 0.30

        close_high = candle.close_pos >= 0.67
        close_low = candle.close_pos <= 0.33
        close_mid = not close_high and not close_low

        # Spread en unidades de ATR
        spread_atr = candle.spread / atr if atr > 1e-9 else 1.0

        # ── Clasificación de patrón ────────────────────────────
        pattern = "NEUTRAL"

        if vol_extreme and spr_wide and close_high:
            pattern = "BUYING_CLIMAX"
        elif vol_extreme and spr_wide and close_low:
            pattern = "SELLING_CLIMAX"
        elif vol_extreme and spr_wide and close_mid and close_low:
            pattern = "STOPPING_VOL_UP"  # potencial suelo
        elif vol_extreme and spr_wide and close_mid and close_high:
            pattern = "STOPPING_VOL_DOWN"  # potencial techo
        elif vol_high and spr_wide and close_high:
            pattern = "EFFORT_UP"
        elif vol_high and spr_wide and close_low:
            pattern = "EFFORT_DOWN"
        elif vol_high and spr_narrow:
            pattern = "WEAKNESS"  # absorción
        elif vol_low and spr_wide:
            pattern = "STRENGTH"  # fuerza oculta
        elif vol_low and spr_narrow and close_high:
            pattern = "NO_SUPPLY"
        elif vol_low and spr_narrow and close_low:
            pattern = "NO_DEMAND"

        return {
            "pattern": pattern,
            "vol_rank": round(vol_rank, 4),
            "vol_zscore": round(vol_zscore, 4),
            "spr_rank": round(spr_rank, 4),
            "spread_atr": round(spread_atr, 4),
            "close_pos": round(candle.close_pos, 4),
            "vol_high": vol_high,
            "vol_low": vol_low,
            "vol_extreme": vol_extreme,
            "spr_wide": spr_wide,
            "spr_narrow": spr_narrow,
            "close_high": close_high,
            "close_low": close_low,
            "atr": round(atr, 6),
        }


# ─────────────────────────────────────────────
# 3. ANALIZADOR VANNA
# ─────────────────────────────────────────────


class VannaAnalyzer:
    """
    Analiza el flujo de Vanna y su efecto sobre la cobertura de dealers.

    El mecanismo clave en scalping 1m:

    1. Una vela de alto volumen aparece
    2. Simultáneamente la IV cambia (sube o baja)
    3. El cambio de IV activa el rebalanceo de Vanna
    4. Si Vanna × dIV > 0 → dealers COMPRANDO (refuerzan el movimiento alcista)
       Si Vanna × dIV < 0 → dealers VENDIENDO (contrarrestan el movimiento)

    La pregunta central del VSA híbrido:
        ¿El volumen de la vela incluye el flujo de cobertura de dealers por Vanna?
        SI  → el movimiento es "real" (esfuerzo CON resultado esperado)
        NO  → el movimiento puede ser un amago (esfuerzo SIN resultado)

    Clasificación del Vanna pressure:
        STRONG_SUPPORT:   pressure > +ref      (compra institucional fuerte)
        MODERATE_SUPPORT: pressure > +ref*0.3  (compra institucional moderada)
        NEUTRAL:          |pressure| < ref*0.3  (sin efecto Vanna)
        MODERATE_OPPOSE:  pressure < -ref*0.3  (venta institucional moderada)
        STRONG_OPPOSE:    pressure < -ref       (venta institucional fuerte)
    """

    def __init__(
        self,
        vanna_ref: float = 30_000,  # Referencia para normalización
        window: int = 10,  # Ventana de percentil Vanna
    ):
        self.vanna_ref = vanna_ref
        self._pressure_buf: deque = deque(maxlen=window)
        self._iv_buf: deque = deque(maxlen=5)

    def analyze(self, snap: VannaSnapshot | None) -> dict:
        if snap is None:
            return self._empty()

        pressure = snap.vanna_pressure
        self._pressure_buf.append(pressure)
        self._iv_buf.append(snap.iv_atm)

        # z-score del Vanna pressure
        if len(self._pressure_buf) >= 3:
            arr = np.array(self._pressure_buf)
            mean = float(np.mean(arr))
            std = float(np.std(arr)) + 1e-9
            p_zscore = (pressure - mean) / std
        else:
            p_zscore = 0.0

        # Clasificación de presión
        ref = self.vanna_ref
        if pressure > ref:
            pressure_class = "STRONG_SUPPORT"
        elif pressure > ref * 0.3:
            pressure_class = "MODERATE_SUPPORT"
        elif pressure < -ref:
            pressure_class = "STRONG_OPPOSE"
        elif pressure < -ref * 0.3:
            pressure_class = "MODERATE_OPPOSE"
        else:
            pressure_class = "NEUTRAL"

        # Dirección del efecto Vanna
        vanna_bullish = pressure > 0
        vanna_bearish = pressure < 0

        # IV acelerando (magnifica el efecto Vanna)
        iv_accel = 0.0
        if len(self._iv_buf) >= 2:
            iv_vals = list(self._iv_buf)
            iv_accel = iv_vals[-1] - iv_vals[-2]

        # Score del Vanna (0-100) basado en magnitud y z-score
        vanna_score = min(
            100.0,
            (abs(pressure) / ref) * 50.0
            + min(25.0, abs(p_zscore) * 10.0)
            + min(25.0, abs(snap.vanna_atm) / (ref * 0.5) * 25.0),
        )

        return {
            "vanna_net": round(snap.vanna_net, 0),
            "vanna_calls": round(snap.vanna_calls, 0),
            "vanna_puts": round(snap.vanna_puts, 0),
            "vanna_atm": round(snap.vanna_atm, 0),
            "iv_atm": round(snap.iv_atm, 4),
            "iv_change_1m": round(snap.iv_change_1m, 6),
            "iv_accel": round(iv_accel, 6),
            "vanna_pressure": round(pressure, 0),
            "pressure_zscore": round(p_zscore, 4),
            "pressure_class": pressure_class,
            "vanna_bullish": vanna_bullish,
            "vanna_bearish": vanna_bearish,
            "vanna_score": round(vanna_score, 2),
            "net_gex": round(snap.net_gex, 0),
        }

    def _empty(self) -> dict:
        return {
            "vanna_net": 0,
            "vanna_calls": 0,
            "vanna_puts": 0,
            "vanna_atm": 0,
            "iv_atm": 0.0,
            "iv_change_1m": 0.0,
            "iv_accel": 0.0,
            "vanna_pressure": 0,
            "pressure_zscore": 0.0,
            "pressure_class": "NEUTRAL",
            "vanna_bullish": False,
            "vanna_bearish": False,
            "vanna_score": 0.0,
            "net_gex": 0,
        }


# ─────────────────────────────────────────────
# 4. SCORER HÍBRIDO
# ─────────────────────────────────────────────


class VSAVannaScorer:
    """
    Combina VSA clásico + Vanna en un score de calidad (0-100)
    y determina si la barra es Esfuerzo-con-Resultado o Amago.

    Los 5 factores:
        F1. Vol z-score (25 pts max): cuánto destaca el volumen
        F2. Spread vs ATR (20 pts max): amplitud de la vela
        F3. Posición del cierre (15 pts max): confirmación direccional
        F4. Vanna flow magnitude (25 pts max): respaldo institucional
        F5. Alineación (15 pts max): Vanna y vela van en la misma dirección

    Clasificación del bar:
        score >= 70 + alineación = CONFIRMED_EFFORT (entrada de máxima calidad)
        score 50-70 + alineación = PROBABLE_EFFORT  (buena señal)
        score 50-70 + desalineado= SUSPECTED_TRAP   (precaución)
        score < 50               = WEAK_SIGNAL      (ignorar en scalping)
    """

    def score(
        self,
        vsa: dict,
        vanna: dict,
        candle: CandleBar,
        direction: str,  # "BULL" o "BEAR" del patrón VSA
    ) -> tuple[float, dict, str]:
        """
        Retorna (score_total, breakdown, bar_quality).
        """
        # F1: Magnitud del volumen
        vol_z = abs(vsa["vol_zscore"])
        f1 = min(25.0, vol_z * 8.0)

        # F2: Spread vs ATR
        spr_atr = vsa["spread_atr"]
        f2 = min(20.0, (spr_atr / 2.0) * 20.0)

        # F3: Posición del cierre (confirmación direccional)
        cp = candle.close_pos
        if direction == "BULL":
            f3 = min(15.0, cp * 15.0)
        else:
            f3 = min(15.0, (1 - cp) * 15.0)

        # F4: Vanna flow magnitude
        f4 = min(25.0, (vanna["vanna_score"] / 100.0) * 25.0)

        # F5: Alineación dirección vela ↔ Vanna
        vanna_aligned = (direction == "BULL" and vanna["vanna_bullish"]) or (
            direction == "BEAR" and vanna["vanna_bearish"]
        )
        if vanna_aligned:
            f5 = min(15.0, (vanna["vanna_score"] / 100.0) * 15.0)
        else:
            f5 = 0.0  # Desalineado = sin bonus

        total = f1 + f2 + f3 + f4 + f5

        # Penalización si Vanna OPONE al movimiento (trampa potencial)
        vanna_opposing = (
            direction == "BULL"
            and vanna["vanna_bearish"]
            and vanna["pressure_class"] in ("STRONG_OPPOSE", "MODERATE_OPPOSE")
        ) or (
            direction == "BEAR"
            and vanna["vanna_bullish"]
            and vanna["pressure_class"] in ("STRONG_SUPPORT", "MODERATE_SUPPORT")
        )
        if vanna_opposing:
            total *= 0.70  # penalización del 30%

        total = min(100.0, total)

        # Clasificación de calidad
        if total >= 70 and vanna_aligned:
            quality = "CONFIRMED_EFFORT"
        elif total >= 50 and vanna_aligned:
            quality = "PROBABLE_EFFORT"
        elif total >= 50 and vanna_opposing:
            quality = "SUSPECTED_TRAP"
        elif total >= 70 and not vanna_aligned:
            quality = "UNCONFIRMED_EFFORT"
        else:
            quality = "WEAK_SIGNAL"

        breakdown = {
            "f1_vol_magnitude": round(f1, 2),
            "f2_spread_atr": round(f2, 2),
            "f3_close_pos": round(f3, 2),
            "f4_vanna_flow": round(f4, 2),
            "f5_alignment": round(f5, 2),
            "vanna_opposing": vanna_opposing,
            "vanna_aligned": vanna_aligned,
        }

        return round(total, 2), breakdown, quality


# ─────────────────────────────────────────────
# 5. MOTOR PRINCIPAL VSA HÍBRIDO
# ─────────────────────────────────────────────


class HybridVSAEngine:
    """
    Motor VSA híbrido: VSA clásico + Vanna flow.

    Por cada vela produce:
        pattern_vsa:    Patrón VSA clásico
        pattern_hybrid: Patrón VSA enriquecido con Vanna
        score:          Score de calidad 0-100
        bar_quality:    CONFIRMED_EFFORT / SUSPECTED_TRAP / etc.
        signal:         Señal de trading
        priority:       1-5

    Los 8 patrones híbridos (patrón VSA + contexto Vanna):
        1. BUYING_CLIMAX_VANNA_CONFIRMED  → clímax + Vanna soporta = trampa alcista fuerte
        2. BUYING_CLIMAX_VANNA_OPPOSED    → clímax + Vanna opone = señal de reversión bajista
        3. SELLING_CLIMAX_VANNA_CONFIRMED → clímax + Vanna opone = trampa bajista fuerte
        4. SELLING_CLIMAX_VANNA_OPPOSED   → clímax + Vanna soporta = señal de reversión alcista
        5. EFFORT_RESULT_BULL             → esfuerzo + Vanna alcista = momentum real
        6. EFFORT_RESULT_BEAR             → esfuerzo + Vanna bajista = momentum real
        7. NO_SUPPLY_VANNA                → bajo vol + Vanna alcista = acumulación silenciosa
        8. NO_DEMAND_VANNA                → bajo vol + Vanna bajista = distribución silenciosa

    Args:
        ticker:      Símbolo del proxy
        vsa_window:  Ventana de percentiles VSA. Default 20.
        vanna_ref:   Referencia Vanna para normalización. Default 30_000.
        min_score:   Score mínimo para emitir señal. Default 40.
    """

    def __init__(
        self,
        ticker: str,
        vsa_window: int = 20,
        vanna_ref: float = 30_000,
        min_score: float = 40.0,
    ):
        self.ticker = ticker
        self.min_score = min_score

        self._vsa = VSAClassifier(vsa_window)
        self._vanna = VannaAnalyzer(vanna_ref)
        self._scorer = VSAVannaScorer()

        self._history: list[dict] = []
        self._prev_patterns: deque = deque(maxlen=5)  # contexto reciente

    # ── Clasificación híbrida ──────────────────────────────────
    def _hybrid_pattern(
        self,
        vsa_pattern: str,
        vanna: dict,
        candle: CandleBar,
    ) -> tuple[str, str, int]:
        """
        Combina el patrón VSA con el contexto Vanna.
        Retorna (pattern_hybrid, signal, priority).
        """
        vb = vanna["vanna_bullish"]
        vr = vanna["vanna_bearish"]
        pc = vanna["pressure_class"]
        strong = pc in ("STRONG_SUPPORT", "STRONG_OPPOSE")
        moderate = pc in ("MODERATE_SUPPORT", "MODERATE_OPPOSE")

        # ── Clímax alcista ─────────────────────────────────────
        if vsa_pattern == "BUYING_CLIMAX":
            if vr and strong:
                # Vanna opone fuertemente → reversión bajista inminente
                return ("BUYING_CLIMAX_VANNA_BEAR", "SHORT_HIGH_CONVICTION", 5)
            elif vr:
                return ("BUYING_CLIMAX_VANNA_OPPOSE", "SHORT_MEDIUM", 4)
            elif vb:
                # Vanna soporta → podría continuar pero el clímax es un techo
                return ("BUYING_CLIMAX_VANNA_SUPPORT", "WATCH_SHORT", 3)
            return ("BUYING_CLIMAX", "WATCH_SHORT", 2)

        # ── Clímax bajista ─────────────────────────────────────
        if vsa_pattern == "SELLING_CLIMAX":
            if vb and strong:
                return ("SELLING_CLIMAX_VANNA_BULL", "LONG_HIGH_CONVICTION", 5)
            elif vb:
                return ("SELLING_CLIMAX_VANNA_SUPPORT", "LONG_MEDIUM", 4)
            elif vr:
                return ("SELLING_CLIMAX_VANNA_OPPOSE", "WATCH_LONG", 3)
            return ("SELLING_CLIMAX", "WATCH_LONG", 2)

        # ── Esfuerzo alcista con resultado ─────────────────────
        if vsa_pattern == "EFFORT_UP":
            if vb and strong:
                return ("EFFORT_RESULT_BULL_CONFIRMED", "LONG_HIGH_CONVICTION", 4)
            elif vb:
                return ("EFFORT_RESULT_BULL", "LONG_MEDIUM", 3)
            elif vr:
                return ("EFFORT_NO_RESULT_BULL", "SUSPECTED_TRAP_SHORT", 4)
            return ("EFFORT_UP_NEUTRAL", "WATCH_LONG", 2)

        # ── Esfuerzo bajista con resultado ─────────────────────
        if vsa_pattern == "EFFORT_DOWN":
            if vr and strong:
                return ("EFFORT_RESULT_BEAR_CONFIRMED", "SHORT_HIGH_CONVICTION", 4)
            elif vr:
                return ("EFFORT_RESULT_BEAR", "SHORT_MEDIUM", 3)
            elif vb:
                return ("EFFORT_NO_RESULT_BEAR", "SUSPECTED_TRAP_LONG", 4)
            return ("EFFORT_DOWN_NEUTRAL", "WATCH_SHORT", 2)

        # ── Stopping volume (suelo potencial) ─────────────────
        if vsa_pattern == "STOPPING_VOL_UP":
            if vb:
                return ("STOPPING_VOLUME_BULL_CONFIRMED", "LONG_HIGH_CONVICTION", 4)
            return ("STOPPING_VOLUME_BULL", "WATCH_LONG", 3)

        if vsa_pattern == "STOPPING_VOL_DOWN":
            if vr:
                return ("STOPPING_VOLUME_BEAR_CONFIRMED", "SHORT_HIGH_CONVICTION", 4)
            return ("STOPPING_VOLUME_BEAR", "WATCH_SHORT", 3)

        # ── Sin oferta (acumulación silenciosa) ───────────────
        if vsa_pattern == "NO_SUPPLY":
            if vb and (strong or moderate):
                return ("NO_SUPPLY_VANNA_CONFIRMED", "LONG_MEDIUM", 3)
            elif vb:
                return ("NO_SUPPLY_VANNA", "LONG_LOW", 2)
            return ("NO_SUPPLY", "WATCH_LONG", 1)

        # ── Sin demanda (distribución silenciosa) ─────────────
        if vsa_pattern == "NO_DEMAND":
            if vr and (strong or moderate):
                return ("NO_DEMAND_VANNA_CONFIRMED", "SHORT_MEDIUM", 3)
            elif vr:
                return ("NO_DEMAND_VANNA", "SHORT_LOW", 2)
            return ("NO_DEMAND", "WATCH_SHORT", 1)

        # ── Debilidad / Fuerza ────────────────────────────────
        if vsa_pattern == "WEAKNESS" and vr:
            return ("WEAKNESS_VANNA_CONFIRMED", "SHORT_MEDIUM", 3)
        if vsa_pattern == "STRENGTH" and vb:
            return ("STRENGTH_VANNA_CONFIRMED", "LONG_MEDIUM", 3)

        return (vsa_pattern or "NEUTRAL", "NEUTRAL", 0)

    # ── Dirección del patrón ───────────────────────────────────
    def _pattern_direction(self, pattern: str) -> str:
        bull_patterns = {
            "EFFORT_UP",
            "NO_SUPPLY",
            "STOPPING_VOL_UP",
            "STRENGTH",
            "BUYING_CLIMAX",
        }
        bear_patterns = {
            "EFFORT_DOWN",
            "NO_DEMAND",
            "STOPPING_VOL_DOWN",
            "WEAKNESS",
            "SELLING_CLIMAX",
        }
        if pattern in bull_patterns:
            return "BULL"
        if pattern in bear_patterns:
            return "BEAR"
        return "NEUTRAL"

    # ── Tick principal ──────────────────────────────────────────
    def update(
        self,
        candle: CandleBar,
        vanna_snap: VannaSnapshot | None = None,
    ) -> dict:

        # ── 1. VSA clásico ─────────────────────────────────────
        vsa = self._vsa.classify(candle)

        # ── 2. Vanna ──────────────────────────────────────────
        vanna = self._vanna.analyze(vanna_snap)

        # ── 3. Patrón híbrido ─────────────────────────────────
        direction = self._pattern_direction(vsa["pattern"])
        hybrid_pat, signal, priority = self._hybrid_pattern(vsa["pattern"], vanna, candle)

        # ── 4. Score ──────────────────────────────────────────
        score, breakdown, bar_quality = self._scorer.score(
            vsa, vanna, candle, direction=direction if direction != "NEUTRAL" else "BULL"
        )

        if score < self.min_score:
            signal = "NEUTRAL"
            priority = 0

        # ── 5. Contexto de barras previas ─────────────────────
        # Un patrón cobra más fuerza si confirma un patrón reciente
        self._prev_patterns.append(hybrid_pat)

        result = {
            # Identificación
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            "volume": candle.volume,
            "spread": round(candle.spread, 6),
            "close_pos": round(candle.close_pos, 4),
            # VSA clásico
            "pattern_vsa": vsa["pattern"],
            "vol_rank": vsa["vol_rank"],
            "vol_zscore": vsa["vol_zscore"],
            "spr_rank": vsa["spr_rank"],
            "spread_atr": vsa["spread_atr"],
            "vol_extreme": vsa["vol_extreme"],
            "spr_wide": vsa["spr_wide"],
            "atr": vsa["atr"],
            # Vanna
            "vanna_net": vanna["vanna_net"],
            "vanna_atm": vanna["vanna_atm"],
            "vanna_pressure": vanna["vanna_pressure"],
            "pressure_class": vanna["pressure_class"],
            "iv_atm": vanna["iv_atm"],
            "iv_change_1m": vanna["iv_change_1m"],
            "vanna_score": vanna["vanna_score"],
            "vanna_bullish": vanna["vanna_bullish"],
            "vanna_bearish": vanna["vanna_bearish"],
            # Patrón híbrido y score
            "pattern_hybrid": hybrid_pat,
            "direction": direction,
            "score": score,
            "bar_quality": bar_quality,
            "f1_vol": breakdown["f1_vol_magnitude"],
            "f2_spread": breakdown["f2_spread_atr"],
            "f3_close": breakdown["f3_close_pos"],
            "f4_vanna": breakdown["f4_vanna_flow"],
            "f5_align": breakdown["f5_alignment"],
            "vanna_aligned": breakdown["vanna_aligned"],
            "vanna_opposing": breakdown["vanna_opposing"],
            # Señal
            "signal": signal,
            "priority": priority,
            "net_gex": vanna["net_gex"],
        }

        self._history.append(result)
        return result

    def to_dataframe(self) -> pd.DataFrame:
        if not self._history:
            return pd.DataFrame()
        df = pd.DataFrame(self._history)
        df.set_index("timestamp", inplace=True)
        return df


# ─────────────────────────────────────────────
# 6. GENERADOR DE DATOS DEMO
# ─────────────────────────────────────────────


def generate_demo(
    ticker: str = "AAPL",
    n: int = 390,
    base: float = 192.50,
    seed: int = 42,
) -> tuple[list[CandleBar], list[VannaSnapshot]]:
    """
    5 fases con los 5 patrones VSA híbridos más importantes:
        Fase 1: Acumulación → NO_SUPPLY_VANNA (vol bajo + Vanna alcista)
        Fase 2: Markup → EFFORT_RESULT_BULL (vol alto + Vanna alcista)
        Fase 3: Distribución → BUYING_CLIMAX_VANNA_BEAR (climax + Vanna negativa)
        Fase 4: Markdown → EFFORT_RESULT_BEAR (vol alto + Vanna bajista)
        Fase 5: Reacumulación → SELLING_CLIMAX_VANNA_BULL (suelo + Vanna positiva)
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    tss = pd.date_range(start, periods=n, freq="1min")

    # (bars, p_trend, vol_base, vol_noise, vanna_base, dIV_base, gex)
    phases = [
        (75, +0.00020, 150_000, 0.30, +25_000, -0.0005, 1.2e6),  # Acumulación
        (80, +0.00070, 350_000, 0.50, +50_000, -0.0010, 0.8e6),  # Markup
        (55, +0.00030, 480_000, 0.60, -60_000, +0.0015, -0.3e6),  # Distribución
        (80, -0.00060, 380_000, 0.55, -45_000, +0.0012, -1.1e6),  # Markdown
        (100, -0.00020, 400_000, 0.70, +55_000, -0.0008, -0.6e6),  # Reacumulación
    ]

    candles, snaps = [], []
    price = base
    iv = 0.20
    idx = 0

    for n_b, p_tr, v_base, v_n, vanna_base, div_base, gex in phases:
        for _ in range(n_b):
            if idx >= n:
                break
            ts = tss[idx]

            price *= 1 + p_tr + rng.normal(0, 0.0006)
            sp = price * rng.uniform(0.0005, 0.0022)
            vol = max(10_000, v_base * (1 + rng.normal(0, v_n)))

            candles.append(
                CandleBar(
                    timestamp=ts,
                    ticker=ticker,
                    open=price * (1 - rng.uniform(0, 0.0003)),
                    high=price + sp * rng.uniform(0.2, 1.0),
                    low=price - sp * rng.uniform(0.2, 1.0),
                    close=price,
                    volume=float(vol),
                )
            )

            dIV = div_base + rng.normal(0, abs(div_base) * 1.5)
            iv = float(np.clip(iv + dIV, 0.08, 0.60))
            vanna_net = vanna_base + rng.normal(0, abs(vanna_base) * 0.4)
            vanna_calls = vanna_net * rng.uniform(0.55, 0.75)
            vanna_puts = vanna_net - vanna_calls

            snaps.append(
                VannaSnapshot(
                    timestamp=ts,
                    ticker=ticker,
                    vanna_net=float(vanna_net),
                    vanna_calls=float(vanna_calls),
                    vanna_puts=float(vanna_puts),
                    vanna_atm=float(vanna_net * rng.uniform(0.40, 0.65)),
                    iv_atm=iv,
                    iv_change_1m=float(dIV),
                    net_gex=float(gex + rng.normal(0, abs(gex) * 0.1)),
                    spot=price,
                )
            )
            idx += 1

    return candles, snaps


# ─────────────────────────────────────────────
# 7. PIPELINE + REPORTE
# ─────────────────────────────────────────────


def run_hybrid_vsa(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> pd.DataFrame:

    print(f"\n{'═'*68}")
    print(f"  VSA HÍBRIDO  |  {ticker}  |  {n} velas 1m")
    print(f"{'═'*68}")

    candles, snaps = generate_demo(ticker, n)
    snap_map = {s.timestamp: s for s in snaps}
    engine = HybridVSAEngine(ticker=ticker)

    for c in candles:
        engine.update(c, snap_map.get(c.timestamp))

    df = engine.to_dataframe()

    if verbose:
        _print_report(df, ticker)

    return df


def _print_report(df: pd.DataFrame, ticker: str):
    last = df.iloc[-1]
    print(f"\n── Estado actual {ticker} ──────────────────────────────")
    print(f"  Precio             : ${last['close']:.2f}")
    print(f"  Patrón VSA clásico : {last['pattern_vsa']}")
    print(f"  Patrón híbrido     : {last['pattern_hybrid']}")
    print(
        f"  Volumen            : {last['volume']:,.0f} (rank={last['vol_rank']:.2f} z={last['vol_zscore']:+.2f})"
    )
    print(f"  Spread ATR         : {last['spread_atr']:.3f}×")
    print(f"  Cierre posición    : {last['close_pos']:.3f}")
    print("  ── Vanna ──────────────────────────────────────────")
    print(f"  Vanna neto         : {last['vanna_net']:+,.0f}")
    print(f"  Vanna ATM          : {last['vanna_atm']:+,.0f}")
    print(f"  Vanna pressure     : {last['vanna_pressure']:+,.0f}")
    print(f"  Clasificación      : {last['pressure_class']}")
    print(f"  IV ATM             : {last['iv_atm']:.2%}")
    print(f"  dIV último 1m      : {last['iv_change_1m']:+.5f}")
    print(f"  Vanna score        : {last['vanna_score']:.2f}")
    print("  ── Score y señal ──────────────────────────────────")
    print(f"  Score total        : {last['score']:.2f}")
    print(f"  Calidad de barra   : {last['bar_quality']}")
    print(f"  Vanna alineada     : {last['vanna_aligned']}")
    print(f"  Vanna opone        : {last['vanna_opposing']}")
    print(f"  Señal              : {last['signal']}  (P{last['priority']})")

    print("\n── Desglose de score (último tick) ──")
    print(f"  F1 vol magnitude   : {last['f1_vol']:.2f} pts")
    print(f"  F2 spread ATR      : {last['f2_spread']:.2f} pts")
    print(f"  F3 close position  : {last['f3_close']:.2f} pts")
    print(f"  F4 vanna flow      : {last['f4_vanna']:.2f} pts")
    print(f"  F5 alignment       : {last['f5_align']:.2f} pts")

    # Patrones VSA clásicos detectados
    print("\n── Patrones VSA clásicos ──")
    print(df["pattern_vsa"].value_counts().to_string())

    # Patrones híbridos más relevantes
    print("\n── Patrones híbridos (top 10) ──")
    top_hyb = df[df["pattern_hybrid"] != "NEUTRAL"]["pattern_hybrid"].value_counts().head(10)
    print(top_hyb.to_string())

    # Señales de alta prioridad
    high = df[df["priority"] >= 3]
    print(f"\n── Señales prioridad ≥ 3 : {len(high)} ──")
    if not high.empty:
        cols = [
            "close",
            "volume",
            "pattern_vsa",
            "pattern_hybrid",
            "vanna_pressure",
            "pressure_class",
            "score",
            "bar_quality",
            "signal",
            "priority",
        ]
        print(high[cols].tail(12).to_string())

    # Barras CONFIRMED_EFFORT vs SUSPECTED_TRAP
    confirmed = df[df["bar_quality"] == "CONFIRMED_EFFORT"]
    traps = df[df["bar_quality"] == "SUSPECTED_TRAP"]
    print("\n── Calidad de barras ──")
    print(df["bar_quality"].value_counts().to_string())
    print(f"\n  CONFIRMED_EFFORT  : {len(confirmed)}")
    print(f"  SUSPECTED_TRAP    : {len(traps)}")
    if not traps.empty:
        print("\n── TRAMPAS DETECTADAS ──")
        print(
            traps[
                ["close", "pattern_vsa", "vanna_pressure", "pressure_class", "score", "signal"]
            ].to_string()
        )

    # Estadísticas del Vanna
    print("\n── Estadísticas Vanna Pressure ──")
    print(f"  Promedio           : {df['vanna_pressure'].mean():+,.0f}")
    print(f"  Máximo             : {df['vanna_pressure'].max():+,.0f}")
    print(f"  Mínimo             : {df['vanna_pressure'].min():+,.0f}")
    print(f"  Strong support     : {(df['pressure_class']=='STRONG_SUPPORT').sum()}")
    print(f"  Strong oppose      : {(df['pressure_class']=='STRONG_OPPOSE').sum()}")

    print(f"\n{'═'*68}")


# ─────────────────────────────────────────────
# 8. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class HybridVSALive:
    """Wrapper para BingX WebSocket + Massive API en producción."""

    PRIORITY = {
        "LONG_HIGH_CONVICTION": 5,
        "SHORT_HIGH_CONVICTION": 5,
        "LONG_MEDIUM": 3,
        "SHORT_MEDIUM": 3,
        "SUSPECTED_TRAP_SHORT": 4,
        "SUSPECTED_TRAP_LONG": 4,
        "WATCH_LONG": 2,
        "WATCH_SHORT": 2,
        "LONG_LOW": 1,
        "SHORT_LOW": 1,
        "NEUTRAL": 0,
    }

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = HybridVSAEngine(ticker=ticker, **kwargs)

    @staticmethod
    def parse_bingx(raw: dict, ticker: str) -> CandleBar:
        return CandleBar(
            timestamp=pd.Timestamp(int(raw["T"]), unit="ms", tz="UTC"),
            ticker=ticker,
            open=float(raw["o"]),
            high=float(raw["h"]),
            low=float(raw["l"]),
            close=float(raw["c"]),
            volume=float(raw["v"]),
        )

    @staticmethod
    def parse_massive(ticker: str, raw: dict, spot: float) -> VannaSnapshot:
        """
        Formato esperado Massive API para Vanna:
        {
          "vannaNet":    25000,
          "vannaCalls":  18000,
          "vannaPuts":    7000,
          "vannaAtm":    12000,
          "ivAtm":        0.22,
          "ivChange1m": -0.001,
          "netGex":    800000
        }
        """
        return VannaSnapshot(
            timestamp=pd.Timestamp.now(tz="UTC"),
            ticker=ticker,
            vanna_net=float(raw.get("vannaNet", 0)),
            vanna_calls=float(raw.get("vannaCalls", 0)),
            vanna_puts=float(raw.get("vannaPuts", 0)),
            vanna_atm=float(raw.get("vannaAtm", 0)),
            iv_atm=float(raw.get("ivAtm", 0.20)),
            iv_change_1m=float(raw.get("ivChange1m", 0.0)),
            net_gex=float(raw.get("netGex", 0)),
            spot=spot,
        )

    def on_signal(self, result: dict):
        p = self.PRIORITY.get(result["signal"], 0)
        if p >= 3:
            print(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{p} {result['signal']:26s} | "
                f"${result['close']:.2f} | "
                f"VSA={result['pattern_vsa']:16s} | "
                f"Hyb={result['pattern_hybrid'][:22]:22s} | "
                f"Vanna={result['vanna_pressure']:+,.0f} "
                f"[{result['pressure_class']}] | "
                f"Score={result['score']:.0f} "
                f"Q={result['bar_quality']}"
            )


# ─────────────────────────────────────────────
# 9. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df = run_hybrid_vsa(ticker=ticker, n=390, verbose=True)
        df.to_csv(f"/tmp/vsa_hybrid_{ticker.lower()}.csv")

    print("\n✓ VSA Híbrido completado para los 5 proxies BingX.")
