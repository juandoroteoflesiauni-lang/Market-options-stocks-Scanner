from __future__ import annotations
from typing import Any
"""Tests for backend/services/bingx_risk_desk.py."""


from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.layer_1_data.datos.bingx_client import _precision_to_step as client_precision_to_step
from backend.services.bingx_risk_desk import (
    REASON_BELOW_MIN_NOTIONAL,
    REASON_BELOW_MIN_QTY,
    REASON_COOLDOWN_ACTIVE,
    REASON_DAILY_LOSS_EXCEEDED,
    REASON_KILL_SWITCH_ACTIVE,
    REASON_L2_QUALITY_MISSING,
    REASON_L2_QUALITY_TOO_LOW,
    REASON_MAX_OPEN_POSITIONS,
    REASON_POSITION_CAP_EXCEEDED,
    REASON_PROVIDER_DEGRADED,
    REASON_SPREAD_TOO_WIDE,
    REASON_SYMBOL_EXPOSURE_EXCEEDED,
    BingXRiskDesk,
    BingXRiskDeskPolicy,
    BingXRiskDeskState,
    OrderIntent,
    _round_to_precision,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _policy(**overrides) -> BingXRiskDeskPolicy:
    defaults: dict[str, Any] = {
        "max_daily_loss_usdt": 5.0,
        "max_position_notional_usdt": 30.0,
        "max_open_positions": 3,
        "max_symbol_exposure_usdt": 15.0,
        "cooldown_after_loss_minutes": 10.0,
        "max_spread_pct": 0.005,
        "min_l2_quality_score": 0.30,
        "no_trade_when_provider_degraded": True,
    }
    defaults.update(overrides)
    return BingXRiskDeskPolicy(**defaults)


def _intent(**overrides) -> OrderIntent:
    defaults: dict[str, Any] = {
        "venue_symbol": "AAPL-USDT",
        "side": "BUY",
        "position_side": "LONG",
        "quantity": 1.0,
        "leverage": 2,
        "entry_type": "MARKET",
        "stop_loss": None,
        "take_profit": None,
        "client_order_id": None,
        "reduce_only": False,
        "cycle_id": "cycle-001",
        "notional_usdt": 10.0,
        "spread_pct": 0.001,
        "l2_quality_score": 0.70,
        "market_type": "stock_perp",
        "requires_l2": False,
        "provider_health": "ok",
    }
    defaults.update(overrides)
    return OrderIntent(**defaults)


def _meta(**kwargs) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "quantity_precision": 2,
        "price_precision": 2,
        "min_qty": 0.01,
        "min_notional": 1.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ─── Policy construction ───────────────────────────────────────────────────────


def test_policy_defaults():
    p = BingXRiskDeskPolicy()
    assert p.max_daily_loss_usdt == 3.0
    assert p.max_open_positions == 3
    assert p.no_trade_when_provider_degraded is True


def test_policy_rejects_negative_daily_loss():
    with pytest.raises(ValueError, match="max_daily_loss_usdt"):
        BingXRiskDeskPolicy(max_daily_loss_usdt=0.0)


def test_policy_rejects_zero_positions():
    with pytest.raises(ValueError, match="max_open_positions"):
        BingXRiskDeskPolicy(max_open_positions=0)


def test_policy_rejects_invalid_l2_score():
    with pytest.raises(ValueError, match="min_l2_quality_score"):
        BingXRiskDeskPolicy(min_l2_quality_score=1.5)


def test_policy_from_env(monkeypatch):
    monkeypatch.setenv("RISK_MAX_DAILY_LOSS_USDT", "7.5")
    monkeypatch.setenv("RISK_MAX_OPEN_POSITIONS", "5")
    monkeypatch.setenv("RISK_NO_TRADE_PROVIDER_DEGRADED", "false")
    p = BingXRiskDeskPolicy.from_env()
    assert p.max_daily_loss_usdt == 7.5
    assert p.max_open_positions == 5
    assert p.no_trade_when_provider_degraded is False


def test_policy_from_env_bad_values_use_defaults(monkeypatch):
    monkeypatch.setenv("RISK_MAX_DAILY_LOSS_USDT", "not-a-number")
    p = BingXRiskDeskPolicy.from_env()
    assert p.max_daily_loss_usdt == 3.0


# ─── Happy path: authorize ────────────────────────────────────────────────────


def test_authorize_clean_intent():
    desk = BingXRiskDesk(policy=_policy())
    decision = desk.authorize_intent(_intent())
    assert decision.authorized is True
    assert decision.reason_codes == []
    assert decision.adjusted_quantity == 1.0


def test_authorize_with_metadata_rounds_quantity():
    desk = BingXRiskDesk(policy=_policy())
    intent = _intent(quantity=1.23456789)
    meta = _meta(quantity_precision=3)
    decision = desk.authorize_intent(intent, contract_metadata=meta)
    assert decision.authorized is True
    assert decision.adjusted_quantity == pytest.approx(1.235)


def test_authorize_records_idempotency_key():
    desk = BingXRiskDesk(policy=_policy())
    intent = _intent()
    decision = desk.authorize_intent(intent)
    assert decision.idempotency_key in desk.state.seen_idempotency_keys


def test_authorize_audit_log_has_entry():
    desk = BingXRiskDesk(policy=_policy())
    desk.authorize_intent(_intent())
    assert len(desk.audit_log) == 1
    assert desk.audit_log[0].event_type == "authorize"


# ─── Idempotency ──────────────────────────────────────────────────────────────


def test_idempotency_same_intent_blocked_second_time():
    desk = BingXRiskDesk(policy=_policy())
    intent = _intent()
    first = desk.authorize_intent(intent)
    assert first.authorized is True
    second = desk.authorize_intent(intent)
    assert second.authorized is False
    assert second.already_seen is True


def test_idempotency_different_cycle_allowed():
    desk = BingXRiskDesk(policy=_policy())
    desk.authorize_intent(_intent(cycle_id="cycle-001"))
    second = desk.authorize_intent(_intent(cycle_id="cycle-002"))
    assert second.authorized is True


# ─── Gate 1: Kill switch ───────────────────────────────────────────────────────


def test_kill_switch_blocks_all_new_orders():
    desk = BingXRiskDesk(policy=_policy())
    desk.kill_switch(reason="test")
    decision = desk.authorize_intent(_intent(cycle_id="new-cycle"))
    assert decision.authorized is False
    assert REASON_KILL_SWITCH_ACTIVE in decision.reason_codes


def test_kill_switch_audit_event():
    desk = BingXRiskDesk(policy=_policy())
    desk.kill_switch(reason="test_reason")
    events = [e for e in desk.audit_log if e.event_type == "kill_switch"]
    assert len(events) == 1
    assert events[0].payload["reason"] == "test_reason"


def test_kill_switch_returns_dict():
    desk = BingXRiskDesk(policy=_policy())
    result = desk.kill_switch(reason="manual")
    assert result["kill_switch"] is True


# ─── Gate 2: Daily loss ───────────────────────────────────────────────────────


def test_daily_loss_cap_blocks_when_exceeded():
    desk = BingXRiskDesk(policy=_policy(max_daily_loss_usdt=5.0))
    desk.state.realized_pnl_today = -5.0
    decision = desk.authorize_intent(_intent())
    assert REASON_DAILY_LOSS_EXCEEDED in decision.reason_codes


def test_daily_loss_cap_allows_when_just_under():
    desk = BingXRiskDesk(policy=_policy(max_daily_loss_usdt=5.0))
    desk.state.realized_pnl_today = -4.99
    decision = desk.authorize_intent(_intent())
    assert REASON_DAILY_LOSS_EXCEEDED not in decision.reason_codes


# ─── Gate 3: Position cap ─────────────────────────────────────────────────────


def test_position_cap_blocks_when_projected_total_exceeds():
    desk = BingXRiskDesk(policy=_policy(max_position_notional_usdt=20.0))
    desk.state.open_positions["MSFT-USDT"] = 15.0
    decision = desk.authorize_intent(_intent(notional_usdt=10.0))
    assert REASON_POSITION_CAP_EXCEEDED in decision.reason_codes


def test_position_cap_allows_when_exactly_within():
    desk = BingXRiskDesk(policy=_policy(max_position_notional_usdt=20.0))
    desk.state.open_positions["MSFT-USDT"] = 10.0
    decision = desk.authorize_intent(_intent(notional_usdt=10.0))
    assert REASON_POSITION_CAP_EXCEEDED not in decision.reason_codes


# ─── Gate 4: Max open positions ───────────────────────────────────────────────


def test_max_open_positions_blocks_new_symbol():
    desk = BingXRiskDesk(policy=_policy(max_open_positions=2))
    desk.state.open_positions["X1-USDT"] = 5.0
    desk.state.open_positions["X2-USDT"] = 5.0
    decision = desk.authorize_intent(_intent(venue_symbol="AAPL-USDT", notional_usdt=5.0))
    assert REASON_MAX_OPEN_POSITIONS in decision.reason_codes


def test_max_open_positions_allows_adding_to_existing():
    desk = BingXRiskDesk(policy=_policy(max_open_positions=2))
    desk.state.open_positions["AAPL-USDT"] = 5.0
    desk.state.open_positions["X2-USDT"] = 5.0
    decision = desk.authorize_intent(_intent(venue_symbol="AAPL-USDT", notional_usdt=5.0))
    assert REASON_MAX_OPEN_POSITIONS not in decision.reason_codes


# ─── Gate 5: Symbol exposure ──────────────────────────────────────────────────


def test_symbol_exposure_blocks_when_exceeded():
    desk = BingXRiskDesk(policy=_policy(max_symbol_exposure_usdt=12.0))
    desk.state.open_positions["AAPL-USDT"] = 8.0
    decision = desk.authorize_intent(_intent(venue_symbol="AAPL-USDT", notional_usdt=5.0))
    assert REASON_SYMBOL_EXPOSURE_EXCEEDED in decision.reason_codes


# ─── Gate 6: Cooldown ─────────────────────────────────────────────────────────


def test_cooldown_blocks_during_window():
    desk = BingXRiskDesk(policy=_policy(cooldown_after_loss_minutes=10.0))
    desk.state.last_loss_at = datetime.now(UTC) - timedelta(minutes=5)
    decision = desk.authorize_intent(_intent())
    assert REASON_COOLDOWN_ACTIVE in decision.reason_codes


def test_cooldown_allows_after_window():
    desk = BingXRiskDesk(policy=_policy(cooldown_after_loss_minutes=10.0))
    desk.state.last_loss_at = datetime.now(UTC) - timedelta(minutes=11)
    decision = desk.authorize_intent(_intent())
    assert REASON_COOLDOWN_ACTIVE not in decision.reason_codes


def test_no_cooldown_when_last_loss_at_is_none():
    desk = BingXRiskDesk(policy=_policy())
    desk.state.last_loss_at = None
    decision = desk.authorize_intent(_intent())
    assert REASON_COOLDOWN_ACTIVE not in decision.reason_codes


# ─── Gate 7: Spread guard ─────────────────────────────────────────────────────


def test_spread_blocks_when_too_wide():
    desk = BingXRiskDesk(policy=_policy(max_spread_pct=0.003))
    decision = desk.authorize_intent(_intent(spread_pct=0.006))
    assert REASON_SPREAD_TOO_WIDE in decision.reason_codes


def test_spread_allows_when_none():
    desk = BingXRiskDesk(policy=_policy(max_spread_pct=0.003))
    decision = desk.authorize_intent(_intent(spread_pct=None))
    assert REASON_SPREAD_TOO_WIDE not in decision.reason_codes


# ─── Gate 8a: L2 quality ─────────────────────────────────────────────────────


def test_l2_quality_blocks_when_below_floor():
    desk = BingXRiskDesk(policy=_policy(min_l2_quality_score=0.5))
    decision = desk.authorize_intent(_intent(l2_quality_score=0.3))
    assert REASON_L2_QUALITY_TOO_LOW in decision.reason_codes


def test_l2_quality_allows_when_none():
    desk = BingXRiskDesk(policy=_policy(min_l2_quality_score=0.5))
    decision = desk.authorize_intent(_intent(l2_quality_score=None))
    assert REASON_L2_QUALITY_TOO_LOW not in decision.reason_codes


def test_l2_quality_blocks_when_required_but_missing():
    desk = BingXRiskDesk(policy=_policy(min_l2_quality_score=0.5))
    decision = desk.authorize_intent(_intent(l2_quality_score=None, requires_l2=True))
    assert decision.authorized is False
    assert REASON_L2_QUALITY_MISSING in decision.reason_codes


# ─── Gate 8b: Provider health ─────────────────────────────────────────────────


def test_provider_degraded_blocks_when_policy_on():
    desk = BingXRiskDesk(policy=_policy(no_trade_when_provider_degraded=True))
    decision = desk.authorize_intent(_intent(provider_health="degraded"))
    assert REASON_PROVIDER_DEGRADED in decision.reason_codes


def test_provider_unavailable_blocks():
    desk = BingXRiskDesk(policy=_policy(no_trade_when_provider_degraded=True))
    decision = desk.authorize_intent(_intent(provider_health="unavailable"))
    assert REASON_PROVIDER_DEGRADED in decision.reason_codes


def test_provider_degraded_allowed_when_policy_off():
    desk = BingXRiskDesk(policy=_policy(no_trade_when_provider_degraded=False))
    decision = desk.authorize_intent(_intent(provider_health="degraded"))
    assert REASON_PROVIDER_DEGRADED not in decision.reason_codes


def test_provider_ok_always_allowed():
    desk = BingXRiskDesk(policy=_policy(no_trade_when_provider_degraded=True))
    decision = desk.authorize_intent(_intent(provider_health="ok"))
    assert REASON_PROVIDER_DEGRADED not in decision.reason_codes


# ─── Precision gates ──────────────────────────────────────────────────────────


def test_below_min_qty_blocks():
    desk = BingXRiskDesk(policy=_policy())
    meta = _meta(min_qty=1.0, quantity_precision=2)
    decision = desk.authorize_intent(_intent(quantity=0.5), contract_metadata=meta)
    assert REASON_BELOW_MIN_QTY in decision.reason_codes


def test_below_min_notional_blocks():
    desk = BingXRiskDesk(policy=_policy())
    meta = _meta(min_notional=5.0, min_qty=0.01)
    decision = desk.authorize_intent(
        _intent(quantity=0.1, notional_usdt=2.0), contract_metadata=meta
    )
    assert REASON_BELOW_MIN_NOTIONAL in decision.reason_codes


def test_no_metadata_passes_through():
    desk = BingXRiskDesk(policy=_policy())
    decision = desk.authorize_intent(_intent(), contract_metadata=None)
    assert decision.authorized is True
    assert decision.adjusted_quantity == 1.0


# ─── record_fill / record_close ───────────────────────────────────────────────


def test_record_fill_updates_open_positions():
    desk = BingXRiskDesk(policy=_policy())
    intent = _intent(notional_usdt=10.0)
    decision = desk.authorize_intent(intent)
    desk.record_fill(decision, realized_pnl=0.0)
    assert desk.state.open_positions["AAPL-USDT"] == pytest.approx(10.0)


def test_record_fill_tracks_loss_timestamp():
    desk = BingXRiskDesk(policy=_policy())
    decision = desk.authorize_intent(_intent())
    desk.record_fill(decision, realized_pnl=-1.5)
    assert desk.state.last_loss_at is not None
    assert desk.state.realized_pnl_today == pytest.approx(-1.5)


def test_record_close_removes_position():
    desk = BingXRiskDesk(policy=_policy())
    desk.state.open_positions["AAPL-USDT"] = 10.0
    desk.record_close("AAPL-USDT", realized_pnl=0.5)
    assert "AAPL-USDT" not in desk.state.open_positions
    assert desk.state.realized_pnl_today == pytest.approx(0.5)


# ─── Multiple gates fire simultaneously ───────────────────────────────────────


def test_multiple_gates_all_reported():
    desk = BingXRiskDesk(
        policy=_policy(
            max_daily_loss_usdt=5.0,
            max_spread_pct=0.003,
            min_l2_quality_score=0.5,
        )
    )
    desk.state.realized_pnl_today = -5.0
    decision = desk.authorize_intent(_intent(spread_pct=0.010, l2_quality_score=0.2))
    assert REASON_DAILY_LOSS_EXCEEDED in decision.reason_codes
    assert REASON_SPREAD_TOO_WIDE in decision.reason_codes
    assert REASON_L2_QUALITY_TOO_LOW in decision.reason_codes
    assert decision.authorized is False


# ─── Precision helpers ────────────────────────────────────────────────────────


def test_round_to_precision():
    assert _round_to_precision(1.23456, 3) == pytest.approx(1.235)
    assert _round_to_precision(1.0, 0) == pytest.approx(1.0)


def test_client_precision_to_step():
    assert client_precision_to_step(0) == pytest.approx(1.0)
    assert client_precision_to_step(2) == pytest.approx(0.01)
    assert client_precision_to_step(4) == pytest.approx(0.0001)


# ─── State total helpers ──────────────────────────────────────────────────────


def test_state_total_open_notional():
    state = BingXRiskDeskState()
    state.open_positions["A"] = 5.0
    state.open_positions["B"] = 8.0
    assert state.total_open_notional == pytest.approx(13.0)


def test_state_open_position_count():
    state = BingXRiskDeskState()
    state.open_positions["A"] = 5.0
    assert state.open_position_count == 1


def test_state_symbol_exposure_missing_returns_zero():
    state = BingXRiskDeskState()
    assert state.symbol_exposure("AAPL-USDT") == pytest.approx(0.0)
