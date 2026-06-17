"""Risk desk nativo para acciones (Alpaca). # [PD-3][IM][TH]

LONG-only, contado 1x (sin apalancamiento). Sizing por capital disponible,
cantidad entera, stops/targets dinámicos por ATR. Mantiene idempotencia y
guardas de posición abierta / máximo de posiciones. No reutiliza
``bingx_risk_desk`` ni semántica de perpetuos.
"""

from __future__ import annotations

import math
import os

from pydantic import BaseModel, ConfigDict

from backend.config.alpaca_priority_route import (
    ROUTE1_NOTIONAL_MULTIPLIER,
    ROUTE2_NOTIONAL_MULTIPLIER,
)
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_models import (
    AlpacaCandidateAnalysis,
    AlpacaDecision,
    AlpacaRoute,
    EquityOrderIntent,
    EquityRiskDecision,
)
from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate
from backend.services.options_strategy.sizing_engine import (
    atr_pct_to_vix_proxy,
    equity_confidence_multiplier,
    volatility_regime_scalar,
)

logger = get_logger(__name__)

# ─── Reason codes estables ────────────────────────────────────────────────────
REASON_NO_REFERENCE_PRICE = "no_reference_price"
REASON_ZERO_QUANTITY = "zero_quantity"
REASON_POSITION_ALREADY_OPEN = "position_already_open"
REASON_MAX_POSITIONS_REACHED = "max_positions_reached"
REASON_ALREADY_SEEN = "already_seen"

_SIZE_DOWN_MULTIPLIER = 0.5


class AlpacaRiskPolicy(BaseModel):
    """Política de riesgo de acciones (sin apalancamiento)."""

    model_config = ConfigDict(frozen=True)

    notional_per_trade_usd: float = 1000.0
    buying_power_pct: float = 0.05
    max_open_positions: int = 5
    atr_stop_mult: float = 1.5
    atr_take_mult: float = 2.5

    @classmethod
    def from_env(cls) -> AlpacaRiskPolicy:
        return cls(
            notional_per_trade_usd=float(os.getenv("ALPACA_NOTIONAL_PER_TRADE_USD", "1000.0")),
            buying_power_pct=float(os.getenv("ALPACA_BUYING_POWER_PCT", "0.05")),
            max_open_positions=int(os.getenv("ALPACA_MAX_OPEN_POSITIONS", "5")),
            atr_stop_mult=float(os.getenv("ALPACA_ATR_STOP_MULT", "1.5")),
            atr_take_mult=float(os.getenv("ALPACA_ATR_TAKE_MULT", "2.5")),
        )


def compute_bracket_levels(
    price: float, atr: float | None, stop_mult: float, take_mult: float
) -> tuple[float | None, float | None]:
    """Niveles SL/TP por ATR para una posición LONG. ``None`` sin ATR válido."""
    if atr is None or atr <= 0 or price <= 0:
        return None, None
    stop_loss = round(price - stop_mult * atr, 2)
    take_profit = round(price + take_mult * atr, 2)
    return (stop_loss if stop_loss > 0 else None), take_profit


def _budget(policy: AlpacaRiskPolicy, buying_power: float | None) -> float:
    """Notional objetivo por trade: el menor entre fijo y % del poder de compra."""
    if buying_power is not None and buying_power > 0:
        return min(policy.notional_per_trade_usd, buying_power * policy.buying_power_pct)
    return policy.notional_per_trade_usd


class AlpacaRiskDesk:
    """Autoriza y dimensiona intenciones de orden de acciones."""

    def __init__(self, policy: AlpacaRiskPolicy | None = None) -> None:
        self._policy = policy or AlpacaRiskPolicy()
        self.open_positions: dict[str, float] = {}
        self._seen_keys: set[str] = set()

    @property
    def policy(self) -> AlpacaRiskPolicy:
        return self._policy

    def build_intent(
        self,
        decision: AlpacaDecision,
        analysis: AlpacaCandidateAnalysis,
        *,
        cycle_id: str,
        buying_power: float | None = None,
        route: AlpacaRoute | None = None,
    ) -> EquityOrderIntent | None:
        """Construye una intención LONG dimensionada por capital + ATR."""
        if decision.decision not in {"ALLOW", "SIZE_DOWN"} or decision.direction != "LONG":
            return None
        price = analysis.latest_close
        if price is None or price <= 0:
            return None
        resolved_route = route or decision.route or analysis.route
        route_mult = (
            ROUTE1_NOTIONAL_MULTIPLIER
            if resolved_route == "priority"
            else ROUTE2_NOTIONAL_MULTIPLIER
        )
        multiplier = _SIZE_DOWN_MULTIPLIER if decision.decision == "SIZE_DOWN" else 1.0
        conf_mult = equity_confidence_multiplier(
            score=decision.score,
            probability=decision.probability,
        )
        atr = analysis.atr
        price = analysis.latest_close or 0.0
        regime_mult = volatility_regime_scalar(atr_pct_to_vix_proxy(atr or 0.0, price))
        notional = (
            _budget(self._policy, buying_power) * multiplier * route_mult * conf_mult * regime_mult
        )
        quantity = math.floor(notional / price)
        if quantity <= 0:
            return None
        stop_mult = self._effective_stop_mult(self._policy)
        stop_loss, take_profit = compute_bracket_levels(
            price, analysis.atr, stop_mult, self._policy.atr_take_mult
        )
        return EquityOrderIntent(
            symbol=analysis.symbol,
            quantity=int(quantity),
            reference_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            notional_usd=round(quantity * price, 2),
            client_order_id=f"qa-{analysis.symbol}-{cycle_id}"[:48],
            cycle_id=cycle_id,
            route=resolved_route,
        )

    def authorize_intent(self, intent: EquityOrderIntent) -> EquityRiskDecision:
        """Aplica idempotencia, guardas de posición/cupo y pre-trade gate."""
        key = intent.client_order_id
        if key in self._seen_keys:
            return self._reject(intent, key, REASON_ALREADY_SEEN, already_seen=True)
        if self.open_positions.get(intent.symbol, 0.0) > 0.0:
            return self._reject(intent, key, REASON_POSITION_ALREADY_OPEN)
        if len(self.open_positions) >= self._policy.max_open_positions:
            return self._reject(intent, key, REASON_MAX_POSITIONS_REACHED)
        self._seen_keys.add(key)
        base = EquityRiskDecision(
            authorized=True,
            intent=intent,
            idempotency_key=key,
            adjusted_quantity=intent.quantity,
        )
        gate = PreTradeRiskGate.instance()
        verdict = gate.evaluate(base, open_position_count=len(self.open_positions))
        return gate.apply_to_decision(base, verdict)

    @staticmethod
    def _reject(
        intent: EquityOrderIntent, key: str, reason: str, *, already_seen: bool = False
    ) -> EquityRiskDecision:
        return EquityRiskDecision(
            authorized=False,
            intent=intent,
            idempotency_key=key,
            reason_codes=(reason,),
            already_seen=already_seen,
        )

    def record_fill(self, decision: EquityRiskDecision) -> None:
        """Registra la posición abierta tras una ejecución confirmada."""
        qty = decision.adjusted_quantity or decision.intent.quantity
        self.open_positions[decision.intent.symbol] = float(qty)

    def clear_position(self, symbol: str) -> None:
        self.open_positions.pop(symbol, None)

    @staticmethod
    def _effective_stop_mult(policy: AlpacaRiskPolicy) -> float:
        """Apply macro agent stop-loss multiplier when agentic layer is active."""
        try:
            from backend.services.agentic_execution_bridge import macro_stop_loss_multiplier

            return policy.atr_stop_mult * macro_stop_loss_multiplier()
        except Exception:
            return policy.atr_stop_mult
