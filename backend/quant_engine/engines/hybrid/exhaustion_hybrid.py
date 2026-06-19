"""
Trend Exhaustion Híbrido — TD Sequential + ATR Stretch + Charm Flow
═════════════════════════════════════════════════════════════════════
Combina tres mecanismos de detección de agotamiento de tendencia:

  CAPA 1 — TD Sequential (DeMark)
    Cuenta velas consecutivas donde el cierre supera (o es menor que)
    el cierre de 4 velas atrás. Al llegar a 9 activa una "setup" y
    al llegar a 13 activa un "countdown" de agotamiento.
    Señal de reversión cuando Setup = 9 ó Countdown = 13.

  CAPA 2 — ATR Stretch
    Mide si el precio se ha alejado demasiado de su media en términos
    de ATR. Una extensión > 2.5× ATR desde la EMA se considera
    "sobreextendido" → el precio necesita comprimir o revertir.
    Rationale: en scalping 1m el mercado raramente sostiene extensiones
    de >2.5 ATR más de 3-5 velas.

  CAPA 3 — Charm Flow de Opciones (la dimensión institucional)
    Charm = dDelta/dt = la tasa de cambio del delta con el tiempo.
    Un call OTM pierde delta con el paso del tiempo (Charm negativo).
    Cuando una tendencia alcista envejece, los calls que los dealers
    vendieron se acercan al vencimiento y pierden delta rápidamente.
    Esto obliga a los dealers a VENDER acciones que compraron como
    hedge → presión bajista adicional que acelera el agotamiento.

    Charm Flow alcista  → dealers COMPRANDO (onda alcista vigente)
    Charm Flow negativo → dealers VENDIENDO (tendencia perdiendo soporte)

La CONFLUENCIA de los tres agotamientos produce la señal más limpia:
    TD Setup 9 + ATR sobreextendido + Charm negativo = agotamiento
    confirmado institucionalmente → señal de máxima convicción.

Score de agotamiento (0-100):
    TD Sequential weight:  35%
    ATR Stretch weight:    30%
    Charm Flow weight:     35%

Fuentes:
    BingX WebSocket  → velas 1m OHLCV
    Massive API      → Charm por strike (dDelta/dT × OI × 100)
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
class CharmSnapshot:
    """
    Charm flow de Massive API — 1 por minuto.

    Charm = dDelta/dT por strike. Suma sobre toda la cadena:
        CharmFlow = Σ [ Charm(k) × OI(k) × 100 ] para todos los strikes.

    Interpretación del dealer (dealer está SHORT las opciones):
        Calls OTM con Charm positivo → delta del call sube con tiempo
          → dealer long delta en estas → debe VENDER acciones (bajista para precio)
        Calls ATM/ITM con Charm negativo → delta del call baja
          → dealer pierde delta → debe COMPRAR más acciones (sostenedor de precio)

    CharmFlow neto < 0 (calls perdiendo delta + puts ganando delta):
        Dealers reciben "devolución" de delta → VENDEN acciones hedgeadas.
        En tendencia alcista → saca el soporte institucional debajo del precio.

    CharmFlow neto > 0:
        Dealers necesitan más delta → COMPRAN acciones.
        Soporte institucional activo.
    """

    timestamp: pd.Timestamp
    ticker: str
    charm_net: float  # Charm flow neto total (negativo = dealers venden)
    charm_calls: float  # Charm de calls (dominante en tendencia alcista)
    charm_puts: float  # Charm de puts
    charm_atm: float  # Charm solo de strikes ATM (±2%)
    theta_net: float  # Theta neto (pérdida de valor por tiempo)
    iv_atm: float  # IV ATM para contexto
    net_gex: float  # GEX neto para régimen
    days_to_expiry: float  # DTE promedio ponderado de la cadena


@dataclass
class CandleBar:
    timestamp: pd.Timestamp
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    hlc3: float = field(init=False)

    def __post_init__(self):
        self.hlc3 = (self.high + self.low + self.close) / 3.0


# ─────────────────────────────────────────────
# 2. MOTOR TD SEQUENTIAL
# ─────────────────────────────────────────────


class TDSequentialEngine:
    """
    TD Sequential de Tom DeMark — implementación completa.

    Setup Phase (1-9):
        Alcista: Cada vela cierra MENOR que el cierre de 4 velas atrás.
                 Al llegar a 9 → "Buy Setup Perfecto" si velas 8 y 9
                 tienen low menor que lows de velas 6 y 7.
        Bajista: Cada vela cierra MAYOR que el cierre de 4 velas atrás.
                 Al llegar a 9 → "Sell Setup Perfecto".

    Countdown Phase (1-13):
        Se inicia después de un Setup completado.
        Alcista: Cada vela donde cierre ≤ low de 2 velas atrás.
        Bajista: Cada vela donde cierre ≥ high de 2 velas atrás.
        Countdown 13 = Señal de reversión de alta probabilidad.

    En scalping 1m el TD Setup 9 es la señal más confiable.
    El Countdown 13 es más raro pero de mayor calidad.
    """

    def __init__(self):
        self._buf: deque = deque(maxlen=20)  # últimas 20 velas

        # Setup
        self._setup_count: int = 0  # 1-9 (positivo = alcista, negativo = bajista)
        self._setup_dir: int = 0  # +1 bull, -1 bear, 0 sin setup

        # Countdown
        self._cd_count: int = 0
        self._cd_dir: int = 0
        self._cd_active: bool = False
        self._setup_high: float = 0.0  # high del setup completado
        self._setup_low: float = float("inf")

        self._history: list[dict] = []

    def update(self, candle: CandleBar) -> dict:
        self._buf.append(candle)

        if len(self._buf) < 5:
            return self._empty()

        close = candle.close
        buf = list(self._buf)
        close_4_back = buf[-5].close if len(buf) >= 5 else close

        # ── SETUP PHASE ──────────────────────────────────────
        bull_cond = close < close_4_back
        bear_cond = close > close_4_back

        if bull_cond:
            if self._setup_dir == +1:
                self._setup_count += 1
            else:
                self._setup_count = 1
                self._setup_dir = +1
                self._cd_active = False
        elif bear_cond:
            if self._setup_dir == -1:
                self._setup_count += 1
            else:
                self._setup_count = 1
                self._setup_dir = -1
                self._cd_active = False
        else:
            self._setup_count = 0
            self._setup_dir = 0

        setup_complete = False
        setup_perfect = False
        setup_signal = "NEUTRAL"

        if abs(self._setup_count) == 9:
            setup_complete = True
            # Comprobar perfección (velas 8 y 9 vs 6 y 7)
            if len(buf) >= 9:
                if self._setup_dir == +1:
                    # Bull Setup: lows de velas 8-9 < lows de velas 6-7
                    low_8 = buf[-2].low
                    low_9 = candle.low
                    low_6 = buf[-4].low
                    low_7 = buf[-3].low
                    setup_perfect = low_8 < low_6 and low_9 < low_7
                    setup_signal = "BUY_SETUP_9" if not setup_perfect else "BUY_SETUP_PERFECT"
                    self._setup_high = max(c.high for c in list(buf)[-9:])
                    self._setup_low = min(c.low for c in list(buf)[-9:])
                else:
                    high_8 = buf[-2].high
                    high_9 = candle.high
                    high_6 = buf[-4].high
                    high_7 = buf[-3].high
                    setup_perfect = high_8 > high_6 and high_9 > high_7
                    setup_signal = "SELL_SETUP_9" if not setup_perfect else "SELL_SETUP_PERFECT"
                    self._setup_high = max(c.high for c in list(buf)[-9:])
                    self._setup_low = min(c.low for c in list(buf)[-9:])

            # Iniciar Countdown
            self._cd_active = True
            self._cd_count = 0
            self._cd_dir = self._setup_dir

        # ── COUNTDOWN PHASE ───────────────────────────────────
        cd_signal = "NEUTRAL"
        cd_complete = False

        if self._cd_active and len(buf) >= 3:
            close_2_back = buf[-3].close

            if self._cd_dir == +1:
                cd_cond = close <= buf[-3].low
            else:
                cd_cond = close >= buf[-3].high

            if cd_cond:
                self._cd_count += 1
                if self._cd_count == 13:
                    cd_complete = True
                    cd_signal = "BUY_COUNTDOWN_13" if self._cd_dir == +1 else "SELL_COUNTDOWN_13"
                    self._cd_active = False

        result = {
            "setup_count": self._setup_count,
            "setup_dir": self._setup_dir,
            "setup_complete": setup_complete,
            "setup_perfect": setup_perfect,
            "setup_signal": setup_signal,
            "cd_count": self._cd_count,
            "cd_dir": self._cd_dir,
            "cd_active": self._cd_active,
            "cd_complete": cd_complete,
            "cd_signal": cd_signal,
            "setup_high": self._setup_high,
            "setup_low": self._setup_low,
        }

        self._history.append(result)
        return result

    def _empty(self) -> dict:
        return {
            "setup_count": 0,
            "setup_dir": 0,
            "setup_complete": False,
            "setup_perfect": False,
            "setup_signal": "NEUTRAL",
            "cd_count": 0,
            "cd_dir": 0,
            "cd_active": False,
            "cd_complete": False,
            "cd_signal": "NEUTRAL",
            "setup_high": 0.0,
            "setup_low": 0.0,
        }


# ─────────────────────────────────────────────
# 3. MOTOR ATR STRETCH
# ─────────────────────────────────────────────


class ATRStretchEngine:
    """
    Detecta sobreextensión de precio respecto a su media móvil
    medida en unidades de ATR.

    stretch = (precio - EMA) / ATR

    Niveles de alerta para scalping 1m:
        |stretch| > 1.5 × ATR  → zona de alerta (tendencia extendida)
        |stretch| > 2.0 × ATR  → sobreextendido (posible agotamiento)
        |stretch| > 2.5 × ATR  → extremo (alta probabilidad de reversión)
        |stretch| > 3.0 × ATR  → muy extremo (casi siempre revierte)
    """

    ALERT_LEVELS = {1.5: "ALERT", 2.0: "STRETCHED", 2.5: "EXTREME", 3.0: "CRITICAL"}

    def __init__(
        self,
        ema_period: int = 20,
        atr_period: int = 14,
        stretch_alert: float = 1.5,
        stretch_exhaust: float = 2.5,
    ):
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.stretch_alert = stretch_alert
        self.stretch_exhaust = stretch_exhaust

        # EMAs
        self._ema_alpha = 2.0 / (ema_period + 1)
        self._ema_val: float | None = None
        self._ema_buf: list[float] = []

        # ATR (Wilder)
        self._atr_val: float | None = None
        self._atr_buf: list[float] = []
        self._prev_close: float | None = None

        # Duración de la sobreextensión
        self._stretch_duration: int = 0
        self._last_stretch_dir: int = 0  # +1 up, -1 down

    def _update_ema(self, price: float) -> float | None:
        if self._ema_val is None:
            self._ema_buf.append(price)
            if len(self._ema_buf) >= self.ema_period:
                self._ema_val = float(np.mean(self._ema_buf))
            return self._ema_val
        self._ema_val = self._ema_alpha * price + (1 - self._ema_alpha) * self._ema_val
        return self._ema_val

    def _update_atr(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            self._prev_close = close
            return None
        tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close

        if self._atr_val is None:
            self._atr_buf.append(tr)
            if len(self._atr_buf) >= self.atr_period:
                self._atr_val = float(np.mean(self._atr_buf))
            return self._atr_val
        # Wilder smoothing
        self._atr_val = (self._atr_val * (self.atr_period - 1) + tr) / self.atr_period
        return self._atr_val

    def update(self, candle: CandleBar) -> dict:
        ema = self._update_ema(candle.close)
        atr = self._update_atr(candle.high, candle.low, candle.close)

        if ema is None or atr is None or atr < 1e-9:
            return {
                "stretch": 0.0,
                "stretch_level": "NORMAL",
                "stretch_dir": 0,
                "stretch_duration": 0,
                "atr": 0.0,
                "ema": candle.close,
                "signal": "NEUTRAL",
                "atr_score": 0.0,
            }

        stretch = (candle.close - ema) / atr
        stretch_abs = abs(stretch)
        stretch_dir = int(np.sign(stretch))

        # Nivel de alerta
        level = "NORMAL"
        for threshold, name in sorted(self.ALERT_LEVELS.items()):
            if stretch_abs >= threshold:
                level = name

        # Duración de la sobreextensión (velas consecutivas en la misma dirección)
        if stretch_dir == self._last_stretch_dir and stretch_abs >= self.stretch_alert:
            self._stretch_duration += 1
        else:
            self._stretch_duration = 1
            self._last_stretch_dir = stretch_dir

        # Score de agotamiento por ATR (0-100)
        atr_score = min(100.0, (stretch_abs / 3.0) * 100.0)

        # Bonus por duración (señal más fuerte cuanto más persiste)
        duration_bonus = min(20.0, self._stretch_duration * 2.0)
        atr_score = min(100.0, atr_score + duration_bonus)

        # Señal
        signal = "NEUTRAL"
        if stretch_abs >= self.stretch_exhaust:
            signal = "BULL_EXHAUSTION_ATR" if stretch > 0 else "BEAR_EXHAUSTION_ATR"
        elif stretch_abs >= self.stretch_alert:
            signal = "BULL_EXTENDED_ATR" if stretch > 0 else "BEAR_EXTENDED_ATR"

        return {
            "stretch": round(stretch, 4),
            "stretch_abs": round(stretch_abs, 4),
            "stretch_level": level,
            "stretch_dir": stretch_dir,
            "stretch_duration": self._stretch_duration,
            "atr": round(atr, 6),
            "ema": round(ema, 4),
            "signal": signal,
            "atr_score": round(atr_score, 2),
        }


# ─────────────────────────────────────────────
# 4. MOTOR CHARM FLOW
# ─────────────────────────────────────────────


class CharmFlowEngine:
    """
    Analiza el Charm flow para detectar cuando una tendencia pierde
    su soporte institucional por decaimiento del delta.

    El mecanismo de agotamiento por Charm:

    Durante una tendencia alcista:
    1. Los dealers vendieron calls en el rally → están LONG delta
    2. Compraron acciones como hedge → soporte de precio
    3. Con el tiempo (Charm), los calls pierden delta
    4. Los dealers necesitan MENOS acciones como hedge
    5. Venden las acciones de hedge → presión bajista
    6. El precio ya no tiene el soporte institucional

    CharmFlow_neto < 0 → dealers devolviendo delta → señal de agotamiento

    Amplificadores del Charm negativo:
    - DTE bajo (< 7 días): el decay acelera → Charm más negativo
    - IV cayendo: reduce delta de calls OTM → más Charm negativo
    - Precio cerca del strike de máximo OI: Charm ATM es el más grande

    La velocidad del Charm (aceleración):
        dCharm/dt > 0 → el flujo de devolución de delta se acelera
                      → agotamiento inminente

    Score de agotamiento por Charm (0-100):
        Base:    abs(charm_net) normalizado
        Bonus1:  +20 si charm_atm muy negativo
        Bonus2:  +15 si DTE < 7 días (decay acelerado)
        Bonus3:  +10 si charm acelera (derivada positiva)
        Bonus4:  +15 si IV cayendo (reduce delta OTM)
    """

    def __init__(
        self,
        charm_ref: float = 50_000,  # Charm de referencia para normalización
        ema_period: int = 5,  # Suavizado del Charm
        accel_window: int = 3,  # Ventana de aceleración
    ):
        self.charm_ref = charm_ref
        self._charm_buf: deque = deque(maxlen=ema_period)
        self._charm_hist: deque = deque(maxlen=accel_window + 1)
        self._iv_buf: deque = deque(maxlen=5)

        # EMA del Charm para suavizado
        self._alpha = 2.0 / (ema_period + 1)
        self._ema_charm: float | None = None

    def _ema(self, val: float) -> float:
        if self._ema_charm is None:
            self._ema_charm = val
        else:
            self._ema_charm = self._alpha * val + (1 - self._alpha) * self._ema_charm
        return self._ema_charm

    def update(self, snap: CharmSnapshot | None) -> dict:
        if snap is None:
            return self._empty()

        charm_smooth = self._ema(snap.charm_net)
        self._charm_hist.append(charm_smooth)
        self._iv_buf.append(snap.iv_atm)

        # ── Aceleración del Charm ─────────────────────────────
        charm_accel = 0.0
        if len(self._charm_hist) >= 2:
            charm_accel = float(list(self._charm_hist)[-1] - list(self._charm_hist)[-2])

        # ── IV en declive ─────────────────────────────────────
        iv_declining = False
        if len(self._iv_buf) >= 3:
            iv_vals = list(self._iv_buf)
            iv_declining = iv_vals[-1] < iv_vals[0] * 0.95  # IV bajó >5%

        # ── Score de agotamiento ──────────────────────────────
        # Base: magnitud del Charm negativo
        charm_norm = abs(charm_smooth) / max(self.charm_ref, 1e-9)
        score_base = min(50.0, charm_norm * 50.0)

        # Dirección: solo score alto si el Charm indica devolución de delta
        # (negativo en tendencia alcista, positivo en tendencia bajista)
        is_bull_exhaustion = charm_smooth < 0  # dealers vendiendo hedge alcista
        is_bear_exhaustion = charm_smooth > 0  # dealers vendiendo hedge bajista

        bonus_atm = 0.0
        if abs(snap.charm_atm) > self.charm_ref * 0.5:
            bonus_atm = min(20.0, abs(snap.charm_atm) / self.charm_ref * 20.0)

        bonus_dte = 0.0
        if snap.days_to_expiry < 7:
            # DTE bajo → Charm acelera exponencialmente
            bonus_dte = 15.0 * (1 - snap.days_to_expiry / 7.0)
        elif snap.days_to_expiry < 14:
            bonus_dte = 7.0

        bonus_accel = 0.0
        if charm_accel > 0 and charm_smooth < 0:  # acelerando la devolución
            bonus_accel = min(10.0, abs(charm_accel) / self.charm_ref * 20.0)

        bonus_iv = 10.0 if iv_declining else 0.0

        charm_score = min(100.0, score_base + bonus_atm + bonus_dte + bonus_accel + bonus_iv)

        # ── Señal ─────────────────────────────────────────────
        signal = "NEUTRAL"
        if charm_score >= 50 and is_bull_exhaustion:
            signal = "BULL_EXHAUSTION_CHARM"
        elif charm_score >= 50 and is_bear_exhaustion:
            signal = "BEAR_EXHAUSTION_CHARM"
        elif charm_score >= 30 and is_bull_exhaustion:
            signal = "BULL_CHARM_WARNING"
        elif charm_score >= 30 and is_bear_exhaustion:
            signal = "BEAR_CHARM_WARNING"

        # ── Clasificación del Charm ───────────────────────────
        if charm_smooth < -self.charm_ref * 1.5:
            charm_label = "STRONG_NEGATIVE"
        elif charm_smooth < -self.charm_ref * 0.5:
            charm_label = "MODERATE_NEGATIVE"
        elif charm_smooth < 0:
            charm_label = "WEAK_NEGATIVE"
        elif charm_smooth > self.charm_ref * 1.5:
            charm_label = "STRONG_POSITIVE"
        elif charm_smooth > self.charm_ref * 0.5:
            charm_label = "MODERATE_POSITIVE"
        elif charm_smooth > 0:
            charm_label = "WEAK_POSITIVE"
        else:
            charm_label = "NEUTRAL"

        return {
            "charm_net": round(snap.charm_net, 0),
            "charm_smooth": round(charm_smooth, 0),
            "charm_atm": round(snap.charm_atm, 0),
            "charm_calls": round(snap.charm_calls, 0),
            "charm_puts": round(snap.charm_puts, 0),
            "charm_accel": round(charm_accel, 0),
            "theta_net": round(snap.theta_net, 0),
            "charm_label": charm_label,
            "iv_atm": round(snap.iv_atm, 4),
            "iv_declining": iv_declining,
            "days_to_expiry": round(snap.days_to_expiry, 2),
            "charm_score": round(charm_score, 2),
            "score_breakdown": {
                "base": round(score_base, 2),
                "atm_bonus": round(bonus_atm, 2),
                "dte_bonus": round(bonus_dte, 2),
                "accel_bonus": round(bonus_accel, 2),
                "iv_bonus": round(bonus_iv, 2),
            },
            "is_bull_exhaustion": is_bull_exhaustion,
            "is_bear_exhaustion": is_bear_exhaustion,
            "signal": signal,
            "net_gex": round(snap.net_gex, 0),
        }

    def _empty(self) -> dict:
        return {
            "charm_net": 0,
            "charm_smooth": 0,
            "charm_atm": 0,
            "charm_calls": 0,
            "charm_puts": 0,
            "charm_accel": 0,
            "theta_net": 0,
            "charm_label": "NEUTRAL",
            "iv_atm": 0.0,
            "iv_declining": False,
            "days_to_expiry": 30.0,
            "charm_score": 0.0,
            "score_breakdown": {
                "base": 0,
                "atm_bonus": 0,
                "dte_bonus": 0,
                "accel_bonus": 0,
                "iv_bonus": 0,
            },
            "is_bull_exhaustion": False,
            "is_bear_exhaustion": False,
            "signal": "NEUTRAL",
            "net_gex": 0,
        }


# ─────────────────────────────────────────────
# 5. MOTOR PRINCIPAL — TREND EXHAUSTION HÍBRIDO
# ─────────────────────────────────────────────


class HybridTrendExhaustionEngine:
    """
    Motor principal que combina TD Sequential, ATR Stretch y Charm Flow
    en un score unificado de agotamiento de tendencia.

    Pesos:
        TD Sequential: 35% del score
        ATR Stretch:   30% del score
        Charm Flow:    35% del score

    Score ≥ 60 + confluencia de al menos 2 de 3 fuentes = señal accionable

    Señales especiales:
        TRIPLE_EXHAUSTION:   Los 3 motores confirman agotamiento
        TD_CHARM_COMBO:      TD Setup 9 + Charm negativo (sin ATR) = válido
        ATR_CHARM_COMBO:     ATR extremo + Charm negativo = válido
        TD_PERFECT_COMBO:    TD Setup Perfecto + cualquier otro = alta prioridad

    Args:
        td_weight:     Peso del TD Sequential en el score. Default 0.35.
        atr_weight:    Peso del ATR Stretch. Default 0.30.
        charm_weight:  Peso del Charm Flow. Default 0.35.
        min_score:     Score mínimo para emitir señal. Default 40.
    """

    def __init__(
        self,
        ticker: str,
        td_weight: float = 0.35,
        atr_weight: float = 0.30,
        charm_weight: float = 0.35,
        min_score: float = 40.0,
        ema_period: int = 20,
        atr_period: int = 14,
    ):
        self.ticker = ticker
        self.td_weight = td_weight
        self.atr_weight = atr_weight
        self.charm_weight = charm_weight
        self.min_score = min_score

        self._td = TDSequentialEngine()
        self._atr = ATRStretchEngine(ema_period, atr_period)
        self._charm = CharmFlowEngine()

        self._history: list[dict] = []

    # ── Score combinado ────────────────────────────────────────
    def _td_score(self, td: dict) -> float:
        """Convierte el estado del TD Sequential a score 0-100."""
        count = abs(td["setup_count"])
        if td["cd_complete"]:
            return 100.0
        if td["setup_perfect"]:
            return 90.0
        if td["setup_complete"]:
            return 80.0
        if count >= 7:
            return 40.0 + (count - 7) * 13.0
        if count >= 4:
            return 10.0 + (count - 4) * 10.0
        return count * 2.5

    def _confluence_signal(
        self,
        td: dict,
        atr: dict,
        charm: dict,
        score: float,
        direction: str,
    ) -> tuple[str, int]:
        """
        Determina la señal final y su prioridad (1-5).
        """
        td_exhaust = td["setup_complete"] or td["cd_complete"]
        atr_exhaust = atr["signal"] not in ("NEUTRAL", "BULL_EXTENDED_ATR", "BEAR_EXTENDED_ATR")
        atr_extreme = atr.get("stretch_level") in ("EXTREME", "CRITICAL")
        charm_exhaust = charm["signal"] not in ("NEUTRAL",)

        all_three = td_exhaust and atr_exhaust and charm_exhaust
        two_of_three = sum([td_exhaust, atr_exhaust, charm_exhaust]) >= 2

        # Señales especiales
        if all_three:
            sig = f"TRIPLE_EXHAUSTION_{direction}"
            return sig, 5

        if td["setup_perfect"] and charm_exhaust:
            sig = f"TD_PERFECT_CHARM_{direction}"
            return sig, 4

        if td["cd_complete"]:
            sig = f"TD_COUNTDOWN13_{direction}"
            return sig, 4

        if td_exhaust and charm_exhaust:
            sig = f"TD_CHARM_COMBO_{direction}"
            return sig, 3

        if atr_extreme and charm_exhaust:
            sig = f"ATR_CHARM_COMBO_{direction}"
            return sig, 3

        if two_of_three and score >= 50:
            sig = f"DUAL_EXHAUSTION_{direction}"
            return sig, 3

        if td_exhaust and score >= 40:
            sig = f"TD_SETUP9_{direction}"
            return sig, 2

        if atr_exhaust and score >= 40:
            sig = f"ATR_EXHAUSTION_{direction}"
            return sig, 2

        if charm_exhaust and score >= 40:
            sig = f"CHARM_EXHAUSTION_{direction}"
            return sig, 2

        if score >= self.min_score:
            sig = f"EXHAUSTION_WARNING_{direction}"
            return sig, 1

        return "NEUTRAL", 0

    # ── Tick principal ──────────────────────────────────────────
    def update(
        self,
        candle: CandleBar,
        charm_snap: CharmSnapshot | None = None,
    ) -> dict:

        # ── 1. Motores individuales ────────────────────────────
        td = self._td.update(candle)
        atr = self._atr.update(candle)
        charm = self._charm.update(charm_snap)

        # ── 2. Dirección dominante ────────────────────────────
        atr_dir = atr["stretch_dir"]
        charm_bull = charm["is_bull_exhaustion"]
        charm_bear = charm["is_bear_exhaustion"]
        td_dir = td["setup_dir"]

        # Mayoría de votos para la dirección del agotamiento
        bull_votes = sum(
            [
                atr_dir > 0,
                charm_bull,
                td_dir < 0,  # TD bullish setup: price < close[4] = en caída, agotamiento bajista
            ]
        )
        bear_votes = sum(
            [
                atr_dir < 0,
                charm_bear,
                td_dir > 0,  # TD bearish setup: price > close[4] = en subida, agotamiento alcista
            ]
        )

        if bear_votes > bull_votes:
            direction = "BULL"  # agotamiento de tendencia alcista → probable reversión
        elif bull_votes > bear_votes:
            direction = "BEAR"
        else:
            direction = "NEUTRAL"

        # ── 3. Score TD ────────────────────────────────────────
        td_score = self._td_score(td)

        # ── 4. Score combinado ────────────────────────────────
        score_raw = (
            self.td_weight * td_score
            + self.atr_weight * atr["atr_score"]
            + self.charm_weight * charm["charm_score"]
        )
        score = min(100.0, score_raw)

        # Bonus de confluencia (cuando los 3 coinciden)
        n_sources = sum(
            [
                td_score >= 30,
                atr["atr_score"] >= 30,
                charm["charm_score"] >= 30,
            ]
        )
        if n_sources == 3:
            score = min(100.0, score * 1.25)
        elif n_sources == 2:
            score = min(100.0, score * 1.10)

        # ── 5. Señal ──────────────────────────────────────────
        signal, priority = self._confluence_signal(td, atr, charm, score, direction)

        result = {
            # Identificación
            "timestamp": candle.timestamp,
            "ticker": self.ticker,
            "close": candle.close,
            # TD Sequential
            "td_count": td["setup_count"],
            "td_dir": td["setup_dir"],
            "td_complete": td["setup_complete"],
            "td_perfect": td["setup_perfect"],
            "td_signal": td["setup_signal"],
            "cd_count": td["cd_count"],
            "cd_complete": td["cd_complete"],
            "cd_signal": td["cd_signal"],
            "td_score": round(td_score, 2),
            # ATR Stretch
            "stretch": atr["stretch"],
            "stretch_abs": atr.get("stretch_abs", 0),
            "stretch_level": atr["stretch_level"],
            "stretch_dur": atr["stretch_duration"],
            "atr": atr["atr"],
            "ema": atr["ema"],
            "atr_signal": atr["signal"],
            "atr_score": atr["atr_score"],
            # Charm Flow
            "charm_net": charm["charm_net"],
            "charm_smooth": charm["charm_smooth"],
            "charm_atm": charm["charm_atm"],
            "charm_accel": charm["charm_accel"],
            "charm_label": charm["charm_label"],
            "charm_signal": charm["signal"],
            "charm_score": charm["charm_score"],
            "iv_atm": charm["iv_atm"],
            "iv_declining": charm["iv_declining"],
            "dte": charm["days_to_expiry"],
            "net_gex": charm["net_gex"],
            # Score combinado y señal
            "score": round(score, 2),
            "n_sources": n_sources,
            "direction": direction,
            "signal": signal,
            "priority": priority,
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
) -> tuple[list[CandleBar], list[CharmSnapshot]]:
    """
    4 fases que producen los 3 tipos de agotamiento:
        Fase 1: tendencia alcista limpia (sin agotamiento)
        Fase 2: TD cuenta hacia 9 + ATR se extiende (agotamiento alcista)
        Fase 3: Charm negativo confirma el agotamiento + reversión
        Fase 4: tendencia bajista con agotamiento por los 3 motores
    """
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-15 09:30:00", tz="America/New_York")
    tss = pd.date_range(start, periods=n, freq="1min")

    # (bars, p_trend, p_noise, charm_base, charm_trend, dte, iv_base, iv_trend, gex)
    phases = [
        # Fase 1: alcista limpio, Charm positivo (dealers comprando)
        (90, +0.0007, 0.0005, 40_000, +200, 21, 0.18, 0.000, 1.5e6),
        # Fase 2: tendencia alcista madura, TD se acerca a 9, ATR estira
        (100, +0.0005, 0.0006, 10_000, -500, 10, 0.20, -0.001, 0.8e6),
        # Fase 3: agotamiento pleno, Charm muy negativo, reversión
        (100, -0.0003, 0.0009, -60_000, -1_000, 5, 0.22, -0.002, -0.5e6),
        # Fase 4: bajista con triple agotamiento
        (100, -0.0007, 0.0008, -80_000, -500, 7, 0.28, -0.001, -1.2e6),
    ]

    candles, snaps = [], []
    price = base
    charm = 40_000.0
    idx = 0

    for n_b, p_tr, p_n, ch_base, ch_tr, dte_base, iv_base, iv_tr, gex in phases:
        charm = ch_base
        iv = iv_base
        dte = float(dte_base)
        for _ in range(n_b):
            if idx >= n:
                break
            ts = tss[idx]

            price *= 1 + p_tr + rng.normal(0, p_n)
            sp = price * rng.uniform(0.0006, 0.0022)
            candles.append(
                CandleBar(
                    timestamp=ts,
                    ticker=ticker,
                    open=price * (1 - rng.uniform(0, 0.0003)),
                    high=price + sp * rng.uniform(0.2, 1.0),
                    low=price - sp * rng.uniform(0.2, 1.0),
                    close=price,
                    volume=float(rng.integers(70_000, 480_000)),
                )
            )

            charm += ch_tr + rng.normal(0, abs(ch_tr) * 2)
            charm = float(np.clip(charm, -200_000, 200_000))
            iv += iv_tr + rng.normal(0, 0.005)
            iv = float(np.clip(iv, 0.08, 0.60))
            dte = max(0.5, dte - 1 / 390)

            snaps.append(
                CharmSnapshot(
                    timestamp=ts,
                    ticker=ticker,
                    charm_net=charm,
                    charm_calls=charm * rng.uniform(0.55, 0.75),
                    charm_puts=charm * rng.uniform(0.25, 0.45),
                    charm_atm=charm * rng.uniform(0.40, 0.60),
                    theta_net=float(rng.normal(-5000, 1000)),
                    iv_atm=iv,
                    net_gex=float(gex + rng.normal(0, abs(gex) * 0.1)),
                    days_to_expiry=dte,
                )
            )
            idx += 1

    return candles, snaps


# ─────────────────────────────────────────────
# 7. PIPELINE + REPORTE
# ─────────────────────────────────────────────


def run_hybrid_exhaustion(
    ticker: str = "AAPL",
    n: int = 390,
    verbose: bool = True,
) -> pd.DataFrame:

    print(f"\n{'═'*70}")
    print(f"  TREND EXHAUSTION HÍBRIDO  |  {ticker}  |  {n} velas 1m")
    print(f"{'═'*70}")

    candles, snaps = generate_demo(ticker, n)
    snap_map = {s.timestamp: s for s in snaps}
    engine = HybridTrendExhaustionEngine(ticker=ticker)

    for c in candles:
        snap = snap_map.get(c.timestamp)
        engine.update(c, snap)

    df = engine.to_dataframe()

    if verbose:
        _print_report(df, ticker)

    return df


def _print_report(df: pd.DataFrame, ticker: str):
    last = df.iloc[-1]
    print(f"\n── Estado actual {ticker} ──────────────────────────────")
    print(f"  Precio             : ${last['close']:.2f}")
    print(f"  Score combinado    : {last['score']:.2f}/100")
    print(f"  Fuentes activas    : {last['n_sources']}/3")
    print(f"  Dirección agot.    : {last['direction']}")
    print(f"  Señal              : {last['signal']}  (P{last['priority']})")

    print("\n── TD Sequential ──")
    print(f"  Conteo Setup       : {last['td_count']:+d}  (dir: {last['td_dir']})")
    print(f"  Setup completo     : {last['td_complete']}")
    print(f"  Setup perfecto     : {last['td_perfect']}")
    print(f"  Countdown          : {last['cd_count']}/13  (completo: {last['cd_complete']})")
    print(f"  TD Score           : {last['td_score']:.2f}")
    print(f"  Señal TD           : {last['td_signal']}")

    print("\n── ATR Stretch ──")
    print(f"  Stretch            : {last['stretch']:+.4f}× ATR")
    print(f"  Nivel              : {last['stretch_level']}")
    print(f"  Duración           : {last['stretch_dur']} velas")
    print(f"  ATR actual         : {last['atr']:.6f}")
    print(f"  EMA                : ${last['ema']:.4f}")
    print(f"  ATR Score          : {last['atr_score']:.2f}")
    print(f"  Señal ATR          : {last['atr_signal']}")

    print("\n── Charm Flow ──")
    print(f"  Charm neto         : {last['charm_net']:+,.0f}")
    print(f"  Charm suavizado    : {last['charm_smooth']:+,.0f}")
    print(f"  Charm ATM          : {last['charm_atm']:+,.0f}")
    print(f"  Aceleración        : {last['charm_accel']:+,.0f}")
    print(f"  Clasificación      : {last['charm_label']}")
    print(f"  DTE                : {last['dte']:.2f} días")
    print(f"  IV ATM             : {last['iv_atm']:.2%}  (declinando: {last['iv_declining']})")
    print(f"  Charm Score        : {last['charm_score']:.2f}")
    print(f"  Señal Charm        : {last['charm_signal']}")

    # Señales de alta prioridad
    high = df[df["priority"] >= 3]
    print(f"\n── Señales prioridad ≥ 3 : {len(high)} ──")
    if not high.empty:
        cols = [
            "close",
            "td_count",
            "stretch",
            "charm_smooth",
            "charm_label",
            "score",
            "n_sources",
            "direction",
            "signal",
            "priority",
        ]
        print(high[cols].to_string())

    # TD Setup 9 detectados
    setups = df[df["td_complete"] == True]
    print(f"\n── TD Setup 9 completados: {len(setups)} ──")
    if not setups.empty:
        print(
            setups[
                [
                    "close",
                    "td_dir",
                    "td_perfect",
                    "td_score",
                    "charm_smooth",
                    "charm_label",
                    "score",
                    "signal",
                ]
            ].to_string()
        )

    # Distribución de señales
    sigs = df[df["priority"] > 0]["signal"].value_counts()
    print("\n── Distribución de señales ──")
    print(sigs.to_string())

    # Estadísticas del Charm
    print("\n── Estadísticas Charm Flow ──")
    print(f"  Charm promedio     : {df['charm_smooth'].mean():+,.0f}")
    print(f"  Charm mínimo       : {df['charm_smooth'].min():+,.0f}")
    print(f"  Charm máximo       : {df['charm_smooth'].max():+,.0f}")
    neg_charm = df[df["charm_smooth"] < -20_000]
    print(f"  Velas Charm neg    : {len(neg_charm)} ({len(neg_charm)/len(df)*100:.1f}%)")

    print(f"\n{'═'*70}")


# ─────────────────────────────────────────────
# 8. INTEGRACIÓN PRODUCCIÓN
# ─────────────────────────────────────────────


class HybridExhaustionLive:
    """
    Wrapper para BingX WebSocket + Massive API en producción.

    Uso:
        engine = HybridExhaustionLive("AAPL")

        def on_1m_candle(raw_candle, raw_charm):
            candle = HybridExhaustionLive.parse_bingx(raw_candle)
            charm  = HybridExhaustionLive.parse_massive("AAPL", raw_charm)
            result = engine.core.update(candle, charm)
            engine.on_signal(result)
    """

    PRIORITY = {
        "TRIPLE_EXHAUSTION_BULL": 5,
        "TRIPLE_EXHAUSTION_BEAR": 5,
        "TD_PERFECT_CHARM_BULL": 4,
        "TD_PERFECT_CHARM_BEAR": 4,
        "TD_COUNTDOWN13_BULL": 4,
        "TD_COUNTDOWN13_BEAR": 4,
        "TD_CHARM_COMBO_BULL": 3,
        "TD_CHARM_COMBO_BEAR": 3,
        "ATR_CHARM_COMBO_BULL": 3,
        "ATR_CHARM_COMBO_BEAR": 3,
        "DUAL_EXHAUSTION_BULL": 3,
        "DUAL_EXHAUSTION_BEAR": 3,
        "TD_SETUP9_BULL": 2,
        "TD_SETUP9_BEAR": 2,
        "ATR_EXHAUSTION_BULL": 2,
        "ATR_EXHAUSTION_BEAR": 2,
        "CHARM_EXHAUSTION_BULL": 2,
        "CHARM_EXHAUSTION_BEAR": 2,
        "EXHAUSTION_WARNING_BULL": 1,
        "EXHAUSTION_WARNING_BEAR": 1,
        "NEUTRAL": 0,
    }

    def __init__(self, ticker: str, **kwargs):
        self.ticker = ticker
        self.core = HybridTrendExhaustionEngine(ticker=ticker, **kwargs)

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
    def parse_massive(ticker: str, raw: dict) -> CharmSnapshot:
        """
        Formato esperado Massive API para Charm:
        {
          "charmNet":      -35000,
          "charmCalls":   -25000,
          "charmPuts":    -10000,
          "charmAtm":     -18000,
          "thetaNet":      -4500,
          "ivAtm":          0.22,
          "netGex":       800000,
          "daysToExpiry":    5.2
        }
        """
        return CharmSnapshot(
            timestamp=pd.Timestamp.now(tz="UTC"),
            ticker=ticker,
            charm_net=float(raw.get("charmNet", 0)),
            charm_calls=float(raw.get("charmCalls", 0)),
            charm_puts=float(raw.get("charmPuts", 0)),
            charm_atm=float(raw.get("charmAtm", 0)),
            theta_net=float(raw.get("thetaNet", 0)),
            iv_atm=float(raw.get("ivAtm", 0.20)),
            net_gex=float(raw.get("netGex", 0)),
            days_to_expiry=float(raw.get("daysToExpiry", 21.0)),
        )

    def on_signal(self, result: dict):
        p = self.PRIORITY.get(result["signal"], 0)
        if p >= 2:
            print(
                f"[{result['timestamp']}] {self.ticker:5s} | "
                f"P{p} {result['signal']:32s} | "
                f"${result['close']:.2f} | "
                f"TD={result['td_count']:+2d} "
                f"Str={result['stretch']:+.2f}σ "
                f"Charm={result['charm_smooth']:+,.0f} | "
                f"Score={result['score']:.0f} | "
                f"Src={result['n_sources']}/3"
            )


# ─────────────────────────────────────────────
# 9. EJECUCIÓN DEMO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    TICKERS = ["AAPL", "MSFT", "PLTR", "TSLA", "GOOGL"]

    for ticker in TICKERS:
        df = run_hybrid_exhaustion(ticker=ticker, n=390, verbose=True)
        df.to_csv(f"/tmp/exhaustion_{ticker.lower()}.csv")

    print("\n✓ Trend Exhaustion Híbrido completado para los 5 proxies BingX.")
