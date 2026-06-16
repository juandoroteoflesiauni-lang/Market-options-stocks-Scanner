from __future__ import annotations
from typing import Any
"""BingX Risk Desk — institutional order controls for the BingX Bot.

Applies 8 independent guardrails before any order reaches the venue:

1. Daily-loss cap (``max_daily_loss_usdt``)
2. Total open-position notional cap (``max_position_notional_usdt``)
3. Maximum concurrent open positions (``max_open_positions``)
4. Per-symbol exposure cap (``max_symbol_exposure_usdt``)
5. Post-loss cooldown (``cooldown_after_loss_minutes``)
6. Spread guard (``max_spread_pct``)
7. L2 quality floor (``min_l2_quality_score``)
8. Provider-degraded block (``no_trade_when_provider_degraded``)

All decisions are immutable dataclasses; mutable runtime state is isolated in
``BingXRiskDeskState``.  ``BingXRiskDesk`` is thin — it holds policy + state
and delegates logic to pure functions.  The kill-switch permanently blocks new
orders and emits an audit event.

This module is intentionally free of HTTP I/O.  Callers inject account state
(realized PnL, open positions) so the desk can be tested without a live client.
"""


import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

# ─── Stable reason codes (dashboards match on these strings) ──────────────────
REASON_KILL_SWITCH_ACTIVE = "risk_kill_switch_active"
REASON_DAILY_LOSS_EXCEEDED = "risk_daily_loss_exceeded"
REASON_POSITION_CAP_EXCEEDED = "risk_position_cap_exceeded"
REASON_MAX_OPEN_POSITIONS = "risk_max_open_positions"
REASON_SYMBOL_EXPOSURE_EXCEEDED = "risk_symbol_exposure_exceeded"
REASON_COOLDOWN_ACTIVE = "risk_cooldown_active"
REASON_SPREAD_TOO_WIDE = "risk_spread_too_wide"
REASON_L2_QUALITY_MISSING = "risk_l2_quality_missing"
REASON_L2_QUALITY_TOO_LOW = "risk_l2_quality_too_low"
REASON_PROVIDER_DEGRADED = "risk_provider_degraded"
REASON_PRECISION_INVALID = "risk_precision_invalid"
REASON_BELOW_MIN_NOTIONAL = "risk_below_min_notional"
REASON_BELOW_MIN_QTY = "risk_below_min_qty"
REASON_ZONE_VETO_SHORT = "risk_zone_veto_short"
REASON_ZONE_VETO_LONG = "risk_zone_veto_long"
REASON_ZONE_LONG_FULL = "risk_zone_long_full"
REASON_ZONE_SHORT_FULL = "risk_zone_short_full"


# ─── OrderIntent ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OrderIntent:
    """Proposed order before Risk Desk review.

    ``notional_usdt`` is the caller's desired position size in USDT.
    ``spread_pct`` and ``l2_quality_score`` are pre-computed by the caller from
    live market data — Risk Desk does not re-fetch them.
    ``provider_health`` is ``"ok"`` when all required data providers responded
    within SLA; any other value triggers the provider-degraded block.
    """

    venue_symbol: str
    side: str  # "BUY" | "SELL"
    position_side: str  # "LONG" | "SHORT" | "BOTH"
    quantity: float
    leverage: int
    entry_type: str  # "MARKET" | "LIMIT"
    stop_loss: float | None  # absolute price
    take_profit: float | None  # absolute price
    client_order_id: str | None
    reduce_only: bool
    cycle_id: str  # deterministic per scan cycle
    notional_usdt: float
    spread_pct: float | None  # ask/bid spread as fraction (0.003 = 0.3%)
    l2_quality_score: float | None  # 0–1 from LOB engine
    provider_health: str  # "ok" | "degraded" | "unavailable"
    market_type: str = ""
    requires_l2: bool = False
    price_zone: str = "NEUTRAL"


# ─── Risk Desk Policy ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BingXRiskDeskPolicy:
    """Immutable policy thresholds.  Validated at construction; env-overridable."""

    max_daily_loss_usdt: float = 3.0
    max_position_notional_usdt: float = 25.0
    max_open_positions: int = 3
    max_symbol_exposure_usdt: float = 12.0
    cooldown_after_loss_minutes: float = 15.0
    max_spread_pct: float = 0.005  # 0.5 % — tight for micro-account
    min_l2_quality_score: float = 0.30
    no_trade_when_provider_degraded: bool = True

    def __post_init__(self) -> None:
        if self.max_daily_loss_usdt <= 0:
            raise ValueError("max_daily_loss_usdt must be positive")
        if self.max_position_notional_usdt <= 0:
            raise ValueError("max_position_notional_usdt must be positive")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions must be >= 1")
        if not (0.0 <= self.min_l2_quality_score <= 1.0):
            raise ValueError("min_l2_quality_score must be in [0, 1]")

    @classmethod
    def from_env(cls: type[BingXRiskDeskPolicy]) -> BingXRiskDeskPolicy:
        def _float(name: str, default: float) -> float:
            raw = os.getenv(name, "").strip()
            try:
                v = float(raw)
            except (ValueError, TypeError):
                return default
            return max(0.0, v)

        def _int(name: str, default: int) -> int:
            raw = os.getenv(name, "").strip()
            try:
                v = int(raw)
            except (ValueError, TypeError):
                return default
            return max(0, v)

        def _bool(name: str, default: bool) -> bool:
            raw = os.getenv(name, "").strip().lower()
            if raw in {"1", "true", "yes"}:
                return True
            if raw in {"0", "false", "no"}:
                return False
            return default

        return cls(
            max_daily_loss_usdt=_float("RISK_MAX_DAILY_LOSS_USDT", 3.0),
            max_position_notional_usdt=_float("RISK_MAX_POSITION_NOTIONAL_USDT", 25.0),
            max_open_positions=_int("RISK_MAX_OPEN_POSITIONS", 3),
            max_symbol_exposure_usdt=_float("RISK_MAX_SYMBOL_EXPOSURE_USDT", 12.0),
            cooldown_after_loss_minutes=_float("RISK_COOLDOWN_AFTER_LOSS_MINUTES", 15.0),
            max_spread_pct=_float("RISK_MAX_SPREAD_PCT", 0.005),
            min_l2_quality_score=_float("RISK_MIN_L2_QUALITY_SCORE", 0.30),
            no_trade_when_provider_degraded=_bool("RISK_NO_TRADE_PROVIDER_DEGRADED", True),
        )


# ─── Mutable Runtime State ────────────────────────────────────────────────────
@dataclass
class BingXRiskDeskState:
    """Mutable per-session state updated by ``record_fill`` and ``kill_switch``."""

    realized_pnl_today: float = 0.0
    last_loss_at: datetime | None = None
    # symbol → notional exposure in USDT
    open_positions: dict[str, float] = field(default_factory=dict)
    # stable idempotency keys already processed this session
    seen_idempotency_keys: set[str] = field(default_factory=set)
    kill_switch_engaged: bool = False
    kill_switch_reason: str | None = None
    available_margin_usdt: float = 0.0

    @property
    def total_open_notional(self) -> float:
        return sum(self.open_positions.values())

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)

    def symbol_exposure(self, symbol: str) -> float:
        return self.open_positions.get(symbol, 0.0)


# ─── Decision & Audit ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RiskDeskDecision:
    """Result of ``BingXRiskDesk.authorize_intent()``."""

    authorized: bool
    intent: OrderIntent
    idempotency_key: str
    reason_codes: list[str]
    adjusted_quantity: float | None  # rounded to venue precision
    adjusted_entry_price: float | None  # rounded to venue tick size
    already_seen: bool = False  # True if idempotency key was already processed


@dataclass(frozen=True)
class RiskDeskAuditEvent:
    """Append-only audit record."""

    timestamp: str  # ISO-8601 UTC
    event_type: str  # "authorize" | "fill" | "kill_switch" | "reject"
    symbol: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "symbol": self.symbol,
            "payload": self.payload,
        }


# ─── Precision helpers ────────────────────────────────────────────────────────
def _round_to_precision(value: float, precision: int) -> float:
    return round(value, max(0, precision))


def _is_valid_precision(value: float, precision: int) -> bool:
    rounded = _round_to_precision(value, precision)
    return abs(value - rounded) < 1e-10


# ─── BingXRiskDesk ────────────────────────────────────────────────────────────
class BingXRiskDesk:
    """Stateful risk desk that authorizes or blocks order intents.

    Usage::

        desk = BingXRiskDesk(policy=BingXRiskDeskPolicy.from_env())
        decision = desk.authorize_intent(intent, contract_metadata=metadata)
        if decision.authorized:
            response = await client.place_order_perp(...)
            desk.record_fill(decision, realized_pnl=0.0)
    """

    def __init__(
        self,
        policy: BingXRiskDeskPolicy | None = None,
        state: BingXRiskDeskState | None = None,
    ) -> None:
        self._policy = policy or BingXRiskDeskPolicy()
        self._state = state or BingXRiskDeskState()
        self._audit: list[RiskDeskAuditEvent] = []

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def policy(self) -> BingXRiskDeskPolicy:
        return self._policy

    @property
    def state(self) -> BingXRiskDeskState:
        return self._state

    @property
    def audit_log(self) -> list[RiskDeskAuditEvent]:
        return list(self._audit)

    def make_idempotency_key(self, intent: OrderIntent) -> str:
        """Deterministic key from cycle_id + symbol + side — deduplicates retries."""
        raw = f"{intent.cycle_id}:{intent.venue_symbol}:{intent.side}:{intent.position_side}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def authorize_intent(
        self,
        intent: OrderIntent,
        *,
        contract_metadata: Any | None = None,
    ) -> RiskDeskDecision:
        """Check intent against all 8 guardrails and return authorization decision."""
        idem_key = self.make_idempotency_key(intent)
        reason_codes: list[str] = []

        # Idempotency: already processed → return prior block
        if idem_key in self._state.seen_idempotency_keys:
            decision = RiskDeskDecision(
                authorized=False,
                intent=intent,
                idempotency_key=idem_key,
                reason_codes=[],
                adjusted_quantity=None,
                adjusted_entry_price=None,
                already_seen=True,
            )
            self._append_audit(
                "authorize", intent.venue_symbol, {"already_seen": True, "key": idem_key}
            )
            return decision

        # ── Gate 1: Kill switch ───────────────────────────────────────────────
        if self._state.kill_switch_engaged:
            reason_codes.append(REASON_KILL_SWITCH_ACTIVE)
            return self._reject(intent, idem_key, reason_codes)

        # ── Gate 2: Daily loss cap ────────────────────────────────────────────
        if self._state.realized_pnl_today <= -abs(self._policy.max_daily_loss_usdt):
            reason_codes.append(REASON_DAILY_LOSS_EXCEEDED)

        # ── Gate 3: Total notional cap ────────────────────────────────────────
        projected_total = self._state.total_open_notional + intent.notional_usdt
        if projected_total > self._policy.max_position_notional_usdt:
            reason_codes.append(REASON_POSITION_CAP_EXCEEDED)

        # ── Gate 4: Max open positions ────────────────────────────────────────
        existing_exposure = self._state.symbol_exposure(intent.venue_symbol)
        is_new_position = existing_exposure == 0.0
        if is_new_position and self._state.open_position_count >= self._policy.max_open_positions:
            reason_codes.append(REASON_MAX_OPEN_POSITIONS)

        # ── Gate 5: Per-symbol exposure cap ───────────────────────────────────
        projected_symbol = existing_exposure + intent.notional_usdt
        if projected_symbol > self._policy.max_symbol_exposure_usdt:
            reason_codes.append(REASON_SYMBOL_EXPOSURE_EXCEEDED)

        # ── Gate 6: Cooldown after loss ───────────────────────────────────────
        if self._state.last_loss_at is not None:
            elapsed = (datetime.now(UTC) - self._state.last_loss_at).total_seconds() / 60.0
            if elapsed < self._policy.cooldown_after_loss_minutes:
                reason_codes.append(REASON_COOLDOWN_ACTIVE)

        # ── Gate 7: Spread guard ──────────────────────────────────────────────
        if intent.spread_pct is not None and intent.spread_pct > self._policy.max_spread_pct:
            reason_codes.append(REASON_SPREAD_TOO_WIDE)

        # ── Gate 8a: L2 quality floor ─────────────────────────────────────────
        requires_l2 = intent.requires_l2 and os.getenv(
            "BINGX_RISK_REQUIRES_L2", "true"
        ).lower() not in {"0", "false", "no", "off"}
        if requires_l2 and intent.l2_quality_score is None:
            reason_codes.append(REASON_L2_QUALITY_MISSING)
        if (
            intent.l2_quality_score is not None
            and intent.l2_quality_score < self._policy.min_l2_quality_score
        ):
            reason_codes.append(REASON_L2_QUALITY_TOO_LOW)

        # ── Gate 8b: Provider health ──────────────────────────────────────────
        if self._policy.no_trade_when_provider_degraded and intent.provider_health not in {
            "ok",
            "",
        }:
            reason_codes.append(REASON_PROVIDER_DEGRADED)

        # ── Gate 9: Zone Validation (Mutual Exclusion) ───────────────────────
        zone_veto_on = os.getenv("BINGX_ZONE_VETO_ENABLED", "true").lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        if zone_veto_on and not intent.reduce_only:
            allow_long = intent.price_zone != "DISTRIBUCION"
            veto_short = intent.price_zone == "ACUMULACION"
            logger.info(
                "risk_desk.zone_validation symbol=%s zone=%s | ALLOW_LONG=%s | VETO_SHORT=%s",
                intent.venue_symbol,
                intent.price_zone,
                allow_long,
                veto_short,
            )
            if intent.price_zone == "ACUMULACION" and intent.position_side == "SHORT":
                reason_codes.append(REASON_ZONE_VETO_SHORT)
            elif intent.price_zone == "DISTRIBUCION" and intent.position_side == "LONG":
                reason_codes.append(REASON_ZONE_VETO_LONG)

        # ── Gate 10: Margin Firewall (15% limit of available_margin) ─────────
        if not intent.reduce_only and existing_exposure > 0.0:
            avail_margin = self._state.available_margin_usdt
            if avail_margin > 0.0:
                limit_margin = 0.15 * avail_margin
                if projected_symbol >= limit_margin:
                    if intent.position_side == "LONG":
                        reason_codes.append(REASON_ZONE_LONG_FULL)
                    elif intent.position_side == "SHORT":
                        reason_codes.append(REASON_ZONE_SHORT_FULL)

        if reason_codes:
            return self._reject(intent, idem_key, reason_codes)

        # ── Precision validation & rounding ───────────────────────────────────
        adjusted_quantity, adjusted_price, prec_codes = self._apply_precision(
            intent, contract_metadata
        )
        if prec_codes:
            return self._reject(intent, idem_key, prec_codes)

        # ── Authorized ────────────────────────────────────────────────────────
        self._state.seen_idempotency_keys.add(idem_key)
        decision = RiskDeskDecision(
            authorized=True,
            intent=intent,
            idempotency_key=idem_key,
            reason_codes=[],
            adjusted_quantity=adjusted_quantity,
            adjusted_entry_price=adjusted_price,
        )
        self._append_audit(
            "authorize",
            intent.venue_symbol,
            {
                "authorized": True,
                "key": idem_key,
                "notional": intent.notional_usdt,
                "adjusted_qty": adjusted_quantity,
            },
        )
        logger.info(
            "risk_desk.authorized symbol=%s side=%s qty=%.6f notional=%.2f key=%s",
            intent.venue_symbol,
            intent.side,
            adjusted_quantity or intent.quantity,
            intent.notional_usdt,
            idem_key,
        )
        # Audit: capture risk desk decision (fire-and-forget)
        try:
            import asyncio as _aio

            from backend.audit.hooks import audit_decision_snapshot

            _aio.get_event_loop().create_task(
                audit_decision_snapshot(
                    module="bingx_risk_desk",
                    symbol=intent.venue_symbol,
                    indicators={},
                    decisions={
                        "authorized": decision.authorized,
                        "intent_side": intent.side,
                        "notional_usdt": intent.notional_usdt,
                        "adjusted_quantity": adjusted_quantity,
                        "reason_codes": [],
                    },
                )
            )
        except Exception:
            pass
        return decision

    def record_fill(self, decision: RiskDeskDecision, *, realized_pnl: float) -> None:
        """Update state after an order has been confirmed filled by the venue."""
        symbol = decision.intent.venue_symbol
        notional = decision.intent.notional_usdt
        self._state.open_positions[symbol] = self._state.open_positions.get(symbol, 0.0) + notional
        if realized_pnl < 0:
            self._state.last_loss_at = datetime.now(UTC)
        self._state.realized_pnl_today += realized_pnl
        self._append_audit(
            "fill",
            symbol,
            {
                "realized_pnl": realized_pnl,
                "pnl_today": self._state.realized_pnl_today,
                "notional": notional,
            },
        )
        logger.info(
            "risk_desk.fill symbol=%s pnl=%.4f pnl_today=%.4f",
            symbol,
            realized_pnl,
            self._state.realized_pnl_today,
        )

    def sync_open_positions_from_venue(self, rows: list[dict[str, Any]]) -> None:
        """Reconcilia posiciones abiertas con el exchange (evita estado fantasma)."""
        synced: dict[str, float] = {}
        for row in rows:
            symbol = str(row.get("symbol") or row.get("symbolName") or "").strip()
            if not symbol:
                continue
            qty = float(row.get("positionAmt") or row.get("quantity") or 0.0)
            if abs(qty) < 1e-12:
                continue
            mark = float(row.get("markPrice") or row.get("avgPrice") or row.get("entryPrice") or 0.0)
            notional = abs(qty) * mark if mark > 0 else abs(float(row.get("positionValue") or 0.0))
            if notional > 0:
                synced[symbol] = synced.get(symbol, 0.0) + notional
        self._state.open_positions = synced
        logger.info("risk_desk.positions_synced count=%d symbols=%s", len(synced), list(synced))

    def record_close(self, symbol: str, *, realized_pnl: float) -> None:
        """Remove a symbol from open positions after it has been closed."""
        removed = self._state.open_positions.pop(symbol, 0.0)
        if realized_pnl < 0:
            self._state.last_loss_at = datetime.now(UTC)
        self._state.realized_pnl_today += realized_pnl
        self._append_audit(
            "fill",
            symbol,
            {
                "type": "close",
                "removed_notional": removed,
                "realized_pnl": realized_pnl,
                "pnl_today": self._state.realized_pnl_today,
            },
        )

    def kill_switch(self, *, reason: str = "operator") -> dict[str, Any]:
        """Permanently block new orders and emit an audit event.

        Does NOT close existing positions — caller is responsible for calling
        ``BingXClient.close_all_positions`` and ``cancel_all_orders_perp`` if
        required.
        """
        self._state.kill_switch_engaged = True
        self._state.kill_switch_reason = reason
        event_payload = {
            "reason": reason,
            "pnl_today": self._state.realized_pnl_today,
            "open_positions": dict(self._state.open_positions),
        }
        self._append_audit("kill_switch", "*", event_payload)
        logger.warning("risk_desk.kill_switch reason=%s", reason)
        return {"kill_switch": True, "reason": reason}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _reject(
        self,
        intent: OrderIntent,
        idem_key: str,
        reason_codes: list[str],
    ) -> RiskDeskDecision:
        self._append_audit(
            "reject",
            intent.venue_symbol,
            {
                "reason_codes": reason_codes,
                "key": idem_key,
            },
        )
        logger.info(
            "risk_desk.rejected symbol=%s reasons=%s",
            intent.venue_symbol,
            ",".join(reason_codes),
        )
        return RiskDeskDecision(
            authorized=False,
            intent=intent,
            idempotency_key=idem_key,
            reason_codes=reason_codes,
            adjusted_quantity=None,
            adjusted_entry_price=None,
        )

    def _apply_precision(
        self,
        intent: OrderIntent,
        contract_metadata: Any | None,
    ) -> tuple[float, float | None, list[str]]:
        """Round quantity/price to venue grid; validate min_qty and min_notional."""
        reason_codes: list[str] = []

        if contract_metadata is None:
            # No metadata: pass through unmodified
            return intent.quantity, None, reason_codes

        qty_prec: int = getattr(contract_metadata, "quantity_precision", 6)
        price_prec: int = getattr(contract_metadata, "price_precision", 2)
        min_qty: float = getattr(contract_metadata, "min_qty", 0.0)
        min_notional: float = getattr(contract_metadata, "min_notional", 0.0)

        adj_qty = _round_to_precision(intent.quantity, qty_prec)

        if adj_qty <= 0 or adj_qty < min_qty:
            reason_codes.append(REASON_BELOW_MIN_QTY)

        if min_notional > 0 and intent.notional_usdt < min_notional:
            reason_codes.append(REASON_BELOW_MIN_NOTIONAL)

        adj_price: float | None = None
        if intent.entry_type == "LIMIT" and intent.stop_loss is not None:
            # Round the entry price hint (stop_loss used as proxy if no entry price)
            adj_price = _round_to_precision(intent.stop_loss, price_prec)

        return adj_qty, adj_price, reason_codes

    def _append_audit(self, event_type: str, symbol: str, payload: dict[str, Any]) -> None:
        self._audit.append(
            RiskDeskAuditEvent(
                timestamp=datetime.now(UTC).isoformat(),
                event_type=event_type,
                symbol=symbol,
                payload=payload,
            )
        )
