from __future__ import annotations
"""Hard Vetoes — cortocircuitos de cómputo para Phase A.

Los hard vetoes se evalúan ANTES del PhaseAGlobalFilter para evitar
gastar CPU en los 6 filtros técnicos (EMA, RSI, Entropy, etc.) cuando
un ticker ya es descartable por condiciones absolutas.

Cada veto retorna HardVetoResult.vetoed=True + razón — el ticker
nunca entra al filtro global, liberando al worker pool en milisegundos.
"""


from backend.models.hard_veto import HardVetoResult, VetoType
from backend.models.market_snapshot import MarketSnapshot
from backend.models.strategy_weights import PhaseAWeights

_MIN_BARS_FOR_VETO = 5


class HardVetoChecker:
    """Evaluación secuencial de vetos absolutos sobre un MarketSnapshot.

    Orden de evaluación (barato → caro):
      1. VETO_NO_DATA            — Sin precio o volumen
      2. VETO_ILLIQUID           — Volumen bajo el umbral configurado
      3. VETO_EXTREME_EXHAUSTION — Movimiento extremo en una vela
    """

    @staticmethod
    def check(
        snapshot: MarketSnapshot,
        cfg: PhaseAWeights,
    ) -> HardVetoResult:
        ticker = snapshot.ticker

        # ── 1. VETO_NO_DATA ──────────────────────────────────────────
        if snapshot.price <= 0:
            return HardVetoResult.veto(
                VetoType.VETO_NO_DATA,
                f"{ticker}: price={snapshot.price} is zero or negative",
            )
        if snapshot.volume <= 0:
            return HardVetoResult.veto(
                VetoType.VETO_NO_DATA,
                f"{ticker}: volume={snapshot.volume} is zero or negative",
            )

        # ── 2. VETO_ILLIQUID ─────────────────────────────────────────
        if snapshot.volume < cfg.min_volume:
            return HardVetoResult.veto(
                VetoType.VETO_ILLIQUID,
                f"{ticker}: volume={snapshot.volume} < min_volume={cfg.min_volume}",
            )

        # ── 3. VETO_EXTREME_EXHAUSTION ───────────────────────────────
        if len(snapshot.ohlcv) >= _MIN_BARS_FOR_VETO:
            closes = [float(b.close) for b in snapshot.ohlcv]
            if len(closes) >= 2:
                last_close = closes[-1]
                prev_close = closes[-2]
                if prev_close > 0:
                    pct_change = abs(last_close - prev_close) / prev_close
                    if pct_change > cfg.max_spread_pct:
                        reason = (
                            f"{ticker}: bar change={pct_change:.4f} "
                            f"> max_spread={cfg.max_spread_pct:.4f}"
                        )
                        return HardVetoResult.veto(
                            VetoType.VETO_EXTREME_EXHAUSTION,
                            reason,
                        )

        return HardVetoResult.passed()
