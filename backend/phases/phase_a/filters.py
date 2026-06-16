from __future__ import annotations
"""Phase A — Global Data Filter con indicadores clásicos.

Implementa 6 filtros técnicos ligeros sobre los MarketSnapshot para descartar
tickers de baja calidad antes de que ingresen a Phase B. Cada filtro produce un
score normalizado 0-100 que se agrega en un quality_score compuesto.

Arquitectura:
  1. EMA Cluster Alignment    20% — Valida alineación multitemporal
  2. ATR Volatility Gate      20% — Filtra tickers sin volatilidad o excesivos
  3. RSI Extreme Filter       15% — Descarta condiciones extremas sin confluence
  4. VWAP Distance Z-Score    15% — Descarta desviaciones sin catalizador
  5. Shannon Entropy          15% — Descarta mercados con exceso de ruido
  6. SuperTrend Regime        15% — Valida consistencia direccional

Early-Exit Pipeline: los filtros se ejecutan en orden de peso descendente por
costo ascendente. Si un filtro falla su umbral individual o si la suma de los
pesos de filtros fallidos hace imposible alcanzar validation_strictness, el
calculo se aborta inmediatamente sin evaluar los filtros restantes.

Los pesos son modulables desde StrategyWeights.PhaseAWeights.
"""


import logging
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from backend.config.phase_thresholds import get_active_weights
from backend.models.market_snapshot import MarketSnapshot, OHLCVBar
from backend.models.phase_a_filter import FilterScore, PhaseAFilterResult
from backend.models.strategy_weights import PhaseAWeights
from backend.quant_engine.math.technical.technical import TechnicalMath

logger = logging.getLogger(__name__)

EMPTY_OHLCV: tuple[OHLCVBar, ...] = ()

FloatArray = npt.NDArray[np.float64]

_MIN_BARS_FOR_FILTERS = 20

_ND = FloatArray  # re-export local para compatibilidad mypy


class PhaseAGlobalFilter:
    """Filtro global de Phase A — 6 indicadores clásicos con pesos dinámicos.

    Stateless: toda la configuración de pesos y umbrales se obtiene en
    tiempo real desde StrategyWeights vía get_active_weights(), o se
    inyecta vía el parámetro opcional ``cfg`` para regímenes macro.
    """

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        cfg: PhaseAWeights | None = None,
    ) -> PhaseAFilterResult:
        """Evalúa un MarketSnapshot contra los 6 filtros con early-exit.

        Los filtros se ordenan por peso descendente, costo computacional
        ascendente para maximizar los early-exits:

          1. EMA      (20%, barato)
          2. ATR      (20%, barato)
          3. RSI      (15%, barato)
          4. VWAP     (15%, medio)
          5. Entropía (15%, medio)
          6. SuperT.  (15%, caro)

        Si un filtro falla su umbral individual o la suma ponderada hace
        imposible alcanzar validation_strictness, se aborta y los filtros
        restantes se marcan como SKIPPED.

        Args:
            snapshot: MarketSnapshot con datos de precio/volumen + OHLCV opcional.
            cfg: Config opcional PhaseAWeights. Si no se provee, se obtiene
                 del singleton activo vía get_active_weights().

        Returns:
            PhaseAFilterResult con scores, breakdown y decisión de aceptación.
        """
        cfg = cfg or get_active_weights().phase_a
        ticker = snapshot.ticker
        has_ohlcv = len(snapshot.ohlcv) >= _MIN_BARS_FOR_FILTERS
        strictness_threshold = cfg.validation_strictness * 100.0

        scores: list[FilterScore] = []
        running_weighted = 0.0
        remaining_weight = 1.0

        # Pipeline ordenado: peso descendente, costo ascendente
        pipeline: list[tuple[str, float, float, Callable[[], tuple[float, str]]]] = [
            (
                "EMA_CLUSTER",
                0.20,
                cfg.ema_cluster_min_score,
                lambda: self._ema_cluster_filter(snapshot, has_ohlcv),
            ),
            (
                "ATR_GATE",
                0.20,
                cfg.atr_gate_min_score,
                lambda: self._atr_volatility_gate(snapshot, cfg, has_ohlcv),
            ),
            (
                "RSI_EXTREME",
                0.15,
                cfg.rsi_extreme_min_score,
                lambda: self._rsi_extreme_filter(snapshot, cfg, has_ohlcv),
            ),
            (
                "VWAP_ZSCORE",
                0.15,
                cfg.vwap_zscore_min_score,
                lambda: self._vwap_zscore_filter(snapshot, cfg, has_ohlcv),
            ),
            (
                "ENTROPY",
                0.15,
                cfg.entropy_min_score,
                lambda: self._entropy_filter(snapshot, cfg, has_ohlcv),
            ),
            (
                "SUPERTREND",
                0.15,
                cfg.supertrend_min_score,
                lambda: self._supertrend_filter(snapshot, cfg, has_ohlcv),
            ),
        ]

        early_exit_reason = ""

        for name, weight, min_score, filter_fn in pipeline:
            score_val, reason = filter_fn()
            passed = score_val >= min_score
            scores.append(
                FilterScore(
                    name=name,
                    score=score_val,
                    weight=weight,
                    passed=passed,
                    reason=reason,
                )
            )

            running_weighted += score_val * weight
            remaining_weight -= weight

            # Early exit 1: filtro falló su umbral individual
            if not passed:
                early_exit_reason = f"FAILED:{name} score={score_val:.1f} ({reason})"
                break

            # Early exit 2: matemáticamente imposible alcanzar el umbral
            max_possible = running_weighted + remaining_weight * 100.0
            if max_possible < strictness_threshold:
                early_exit_reason = (
                    f"EARLY_EXIT: max_possible={max_possible:.1f} < "
                    f"threshold={strictness_threshold:.1f} after {name}"
                )
                break

        # Si hubo early exit, rellenar filtros restantes como SKIPPED
        if early_exit_reason:
            executed_names = {s.name for s in scores}
            for name, weight, _, _ in pipeline:
                if name not in executed_names:
                    scores.append(
                        FilterScore(
                            name=name,
                            score=0.0,
                            weight=weight,
                            passed=False,
                            reason="SKIPPED",
                        )
                    )

        quality = sum(s.score * s.weight for s in scores)
        all_passed = all(s.passed for s in scores)
        rejection = (
            early_exit_reason
            if early_exit_reason
            else ("" if all_passed else next(s for s in scores if not s.passed).name)
        )

        return PhaseAFilterResult(
            ticker=ticker,
            accepted=all_passed,
            quality_score=round(quality, 2),
            breakdown=tuple(scores),
            rejection_reason=rejection,
        )

    # ── Filtro 1: EMA Cluster ───────────────────────────────────────────────

    @staticmethod
    def _ema_cluster_filter(
        snapshot: MarketSnapshot,
        has_ohlcv: bool,
    ) -> tuple[float, str]:
        """Valida que al menos 3 de 4 EMAs (9,21,50,200) estén alineadas."""
        if not has_ohlcv:
            return 50.0, "INSUFFICIENT_DATA"

        close = _extract_close(snapshot.ohlcv)
        clusters = TechnicalMath.ema_clusters(close, [9, 21, 50, 200])
        latest = {
            p: float(arr[-1])
            for p, arr in clusters.items()
            if len(arr) > 0 and not np.isnan(arr[-1])
        }

        if len(latest) < 3:
            return 30.0, f"Only {len(latest)} EMAs computed"

        last_close = float(close[-1])
        sorted_emas = sorted(latest.items(), key=lambda x: x[1])
        aligned_bull = all(ema < last_close for _, ema in sorted_emas)
        aligned_bear = all(ema > last_close for _, ema in sorted_emas)

        if aligned_bull or aligned_bear:
            return 90.0, "ALIGNED"

        # Contar cuántas EMAs están del mismo lado que el precio
        bull_count = sum(1 for _, ema in sorted_emas if ema < last_close)
        if bull_count >= 3:
            return 70.0, f"{bull_count}/4 EMAs aligned"

        return 30.0, f"NO_ALIGNMENT bull={bull_count}/4"

    # ── Filtro 2: ATR Volatility Gate ──────────────────────────────────────

    @staticmethod
    def _atr_volatility_gate(
        snapshot: MarketSnapshot,
        cfg: PhaseAWeights,
        has_ohlcv: bool,
    ) -> tuple[float, str]:
        """Filtra tickers con volatilidad fuera de rango [min_atr_pct, max_atr_pct]."""
        if not has_ohlcv:
            return 50.0, "INSUFFICIENT_DATA"

        high = _extract_high(snapshot.ohlcv)
        low = _extract_low(snapshot.ohlcv)
        close = _extract_close(snapshot.ohlcv)

        atr_values = TechnicalMath.atr(close, high, low, 14)
        last_atr = (
            float(atr_values[-1]) if len(atr_values) > 0 and not np.isnan(atr_values[-1]) else 0.0
        )
        last_price = float(close[-1])
        atr_pct = last_atr / last_price if last_price > 0 else 0.0

        if atr_pct < cfg.min_atr_pct:
            return 20.0, f"ATR%={atr_pct:.4f} < min={cfg.min_atr_pct:.4f}"
        if atr_pct > cfg.max_atr_pct:
            return 20.0, f"ATR%={atr_pct:.4f} > max={cfg.max_atr_pct:.4f}"

        midpoint = (cfg.min_atr_pct + cfg.max_atr_pct) / 2.0
        half_range = (cfg.max_atr_pct - cfg.min_atr_pct) / 2.0
        if half_range > 0:
            distance = abs(atr_pct - midpoint) / half_range
            score = max(0.0, 100.0 * (1.0 - distance))
            return round(score, 2), f"ATR%={atr_pct:.4f} mid={midpoint:.4f}"
        return 50.0, f"ATR%={atr_pct:.4f}"

    # ── Filtro 3: RSI Extreme Filter ───────────────────────────────────────

    @staticmethod
    def _rsi_extreme_filter(
        snapshot: MarketSnapshot,
        cfg: PhaseAWeights,
        has_ohlcv: bool,
    ) -> tuple[float, str]:
        """Descarta RSI extremo sin señales de confluence (squeeze/volumen)."""
        if not has_ohlcv:
            return 50.0, "INSUFFICIENT_DATA"

        close = _extract_close(snapshot.ohlcv)
        rsi_values = TechnicalMath.rsi(close, 14)
        last_rsi = (
            float(rsi_values[-1]) if len(rsi_values) > 0 and not np.isnan(rsi_values[-1]) else 50.0
        )

        if last_rsi < cfg.rsi_oversold_threshold:
            return 20.0, f"RSI={last_rsi:.1f} < oversold={cfg.rsi_oversold_threshold}"
        if last_rsi > cfg.rsi_overbought_threshold:
            return 20.0, f"RSI={last_rsi:.1f} > overbought={cfg.rsi_overbought_threshold}"

        # Puntuación: óptimo en zona neutral 40-60
        if 40.0 <= last_rsi <= 60.0:
            return 90.0, f"RSI={last_rsi:.1f} neutral"
        return 60.0, f"RSI={last_rsi:.1f} moderate"

    # ── Filtro 4: VWAP Distance Z-Score ────────────────────────────────────

    @staticmethod
    def _vwap_zscore_filter(
        snapshot: MarketSnapshot,
        cfg: PhaseAWeights,
        has_ohlcv: bool,
    ) -> tuple[float, str]:
        """Descarta tickers con precio > N desviaciones del VWAP sin catalizador."""
        if not has_ohlcv:
            return 50.0, "INSUFFICIENT_DATA"

        high = _extract_high(snapshot.ohlcv)
        low = _extract_low(snapshot.ohlcv)
        close = _extract_close(snapshot.ohlcv)
        volume = _extract_volume(snapshot.ohlcv)

        vwap_arr = TechnicalMath.vwap(high, low, close, volume)
        last_vwap = float(vwap_arr[-1]) if len(vwap_arr) > 0 and not np.isnan(vwap_arr[-1]) else 0.0
        last_close = float(close[-1])

        if last_vwap <= 0:
            return 50.0, "VWAP_ZERO"

        # Desviación simple sin std dev disponible
        spread_pct = abs(last_close - last_vwap) / last_vwap
        z_estimate = spread_pct * 100  # Aproximación: 1% ≈ 1 z-score

        if z_estimate > cfg.vwap_max_zscore:
            return 20.0, f"Z~{z_estimate:.2f} > max={cfg.vwap_max_zscore:.2f}"

        score = max(0.0, 100.0 * (1.0 - z_estimate / cfg.vwap_max_zscore))
        return round(score, 2), f"Z~{z_estimate:.2f} in range"

    # ── Filtro 5: Shannon Entropy ──────────────────────────────────────────

    @staticmethod
    def _entropy_filter(
        snapshot: MarketSnapshot,
        cfg: PhaseAWeights,
        has_ohlcv: bool,
    ) -> tuple[float, str]:
        """Descarta tickers con entropía > threshold (mercado ruidoso/aleatorio)."""
        if not has_ohlcv:
            return 50.0, "INSUFFICIENT_DATA"

        close = _extract_close(snapshot.ohlcv)
        entropy_arr = TechnicalMath.shannon_entropy(close, n=20, bins=10)
        last_entropy = (
            float(entropy_arr[-1])
            if len(entropy_arr) > 0 and not np.isnan(entropy_arr[-1])
            else 0.0
        )

        if last_entropy > cfg.max_entropy:
            return 20.0, f"Entropy={last_entropy:.2f} > max={cfg.max_entropy:.2f}"

        score = max(0.0, 100.0 * (1.0 - last_entropy / cfg.max_entropy))
        return round(score, 2), f"Entropy={last_entropy:.2f} ok"

    # ── Filtro 6: SuperTrend Regime ────────────────────────────────────────

    @staticmethod
    def _supertrend_filter(
        snapshot: MarketSnapshot,
        cfg: PhaseAWeights,
        has_ohlcv: bool,
    ) -> tuple[float, str]:
        """Valida consistencia direccional del SuperTrend (sin cambios en < N velas)."""
        if not has_ohlcv:
            return 50.0, "INSUFFICIENT_DATA"

        high = _extract_high(snapshot.ohlcv)
        low = _extract_low(snapshot.ohlcv)
        close = _extract_close(snapshot.ohlcv)

        _, direction = TechnicalMath.supertrend(
            close,
            high,
            low,
            n=cfg.supertrend_period,
            multiplier=cfg.supertrend_multiplier,
        )

        if len(direction) < 5:
            return 50.0, f"INSUFFICIENT direction data ({len(direction)})"

        last_5 = direction[-5:]
        changes = sum(1 for i in range(1, len(last_5)) if last_5[i] != last_5[i - 1])

        if changes >= cfg.supertrend_max_changes:
            return 20.0, f"{changes} direction changes in 5 bars"

        return 80.0, f"Direction stable ({changes} changes)"


# ── Helpers de extracción OHLCV ─────────────────────────────────────────────


def _extract_close(bars: tuple[OHLCVBar, ...]) -> npt.NDArray[np.float64]:
    return np.array([float(b.close) for b in bars], dtype=np.float64)


def _extract_high(bars: tuple[OHLCVBar, ...]) -> npt.NDArray[np.float64]:
    return np.array([float(b.high) for b in bars], dtype=np.float64)


def _extract_low(bars: tuple[OHLCVBar, ...]) -> npt.NDArray[np.float64]:
    return np.array([float(b.low) for b in bars], dtype=np.float64)


def _extract_volume(bars: tuple[OHLCVBar, ...]) -> npt.NDArray[np.float64]:
    return np.array([float(b.volume) for b in bars], dtype=np.float64)
