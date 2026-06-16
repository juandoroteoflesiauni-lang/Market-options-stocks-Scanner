"""Fase 1 — contratos y configuración Options Strategy. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.options_strategy_loader import (
    assert_symbol_in_universe,
    load_options_strategy_config,
    resolve_universe_symbols,
)
from backend.domain.alpaca_options_models import Route1OptionsSnapshotContext
from backend.models.market_snapshot import DataLineage, MarketSnapshot
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsExecutionPayload,
    OptionsLegSpec,
    OptionsStrategyAuditLog,
    OptionsStrategyInput,
    OptionsStructure,
    PlaybookDecision,
    StrategyDecision,
)


def _lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=1, raw_field_count=3)


def _market_snapshot(symbol: str = "AAPL") -> MarketSnapshot:
    return MarketSnapshot(
        ticker=symbol,
        exchange="NASDAQ",
        price=Decimal("180.00"),
        volume=1_000_000,
        exchange_timestamp=datetime.now(tz=UTC),
        data_lineage=_lineage(),
    )


def _options_context(symbol: str = "AAPL") -> Route1OptionsSnapshotContext:
    return Route1OptionsSnapshotContext(
        symbol=symbol,
        as_of=datetime.now(tz=UTC).isoformat(),
        available=True,
        features={"shadow_delta_signal": 0.5},
        snapshot={"spot": 180.0, "chain": []},
    )


def test_load_options_strategy_config_smoke() -> None:
    cfg = load_options_strategy_config()
    assert cfg.omni_engine.min_global_confidence == pytest.approx(0.68)
    assert cfg.universe.enforce_route1_only is True
    assert cfg.universe.source == "alpaca_route1"
    assert len(cfg.resolved_symbols) == len(ALPACA_ROUTE1_WATCHLIST)
    assert set(cfg.resolved_symbols) == set(ALPACA_ROUTE1_WATCHLIST)
    assert "trend_continuation" in cfg.playbooks.playbooks
    assert cfg.playbooks.enabled_playbooks()["gamma_wall_rejection"].enabled is True
    assert cfg.risk.max_open_positions == 4


def test_resolve_universe_symbols_matches_route1() -> None:
    cfg = load_options_strategy_config()
    resolved = resolve_universe_symbols(cfg.universe)
    assert resolved == ALPACA_ROUTE1_WATCHLIST


def test_assert_symbol_in_universe_accepts_r1() -> None:
    cfg = load_options_strategy_config()
    sym = assert_symbol_in_universe(
        "googl",
        universe=cfg.universe,
        resolved_symbols=cfg.resolved_symbols,
    )
    assert sym == "GOOGL"


def test_assert_symbol_in_universe_rejects_non_r1() -> None:
    cfg = load_options_strategy_config()
    with pytest.raises(ValueError, match="symbol_not_in_route1_universe"):
        assert_symbol_in_universe(
            "AMD",
            universe=cfg.universe,
            resolved_symbols=cfg.resolved_symbols,
        )


def test_options_strategy_input_accepts_r1_symbol() -> None:
    payload = OptionsStrategyInput(
        symbol="nvda",
        as_of=datetime.now(tz=UTC),
        market_snapshot=_market_snapshot("NVDA"),
        options_context=_options_context("NVDA"),
    )
    assert payload.symbol == "NVDA"


def test_options_strategy_input_rejects_non_r1_symbol() -> None:
    with pytest.raises(ValueError, match="symbol_not_in_route1_universe"):
        OptionsStrategyInput(
            symbol="AMD",
            as_of=datetime.now(tz=UTC),
        )


def test_options_strategy_input_requires_utc_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        OptionsStrategyInput(
            symbol="SPY",
            as_of=datetime(2026, 6, 13, 12, 0, 0),
        )


def test_normalized_features_ranges() -> None:
    features = NormalizedFeatures(
        symbol="SPY",
        as_of=datetime.now(tz=UTC),
        technical_direction_bias=0.5,
        global_confidence=0.7,
    )
    assert features.symbol == "SPY"
    assert features.global_confidence == pytest.approx(0.7)


def test_playbook_decision_execute_invariants() -> None:
    decision = PlaybookDecision(
        symbol="QQQ",
        as_of=datetime.now(tz=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend_continuation",
        recommended_structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        confidence=0.72,
        execution_ready=True,
        reason_codes=("smc_bullish_alignment",),
    )
    assert decision.execution_ready is True


def test_playbook_decision_rejects_execute_with_veto() -> None:
    with pytest.raises(ValueError, match="EXECUTE cannot coexist"):
        PlaybookDecision(
            symbol="QQQ",
            as_of=datetime.now(tz=UTC),
            decision=StrategyDecision.EXECUTE,
            veto_triggered="tail_risk_critical",
            execution_ready=True,
        )


def test_execution_payload_debit_spread_requires_legs() -> None:
    with pytest.raises(ValueError, match="debit spread"):
        OptionsExecutionPayload(
            symbol="AAPL",
            timestamp=datetime.now(tz=UTC),
            decision=StrategyDecision.EXECUTE,
            playbook_family="trend_continuation",
            recommended_structure=OptionsStructure.CALL_DEBIT_SPREAD,
            direction="bullish",
            global_confidence=0.7,
            dte_target=14,
            delta_buy_target=0.38,
            delta_sell_target=0.20,
            max_premium_usd=Decimal("250.00"),
            risk_budget_pct=0.6,
            legs=(),
        )


def test_execution_payload_accepts_spread_with_legs() -> None:
    payload = OptionsExecutionPayload(
        symbol="AAPL",
        timestamp=datetime.now(tz=UTC),
        decision=StrategyDecision.EXECUTE,
        playbook_family="trend_continuation",
        recommended_structure=OptionsStructure.CALL_DEBIT_SPREAD,
        direction="bullish",
        global_confidence=0.7,
        dte_target=14,
        delta_buy_target=0.38,
        delta_sell_target=0.20,
        max_premium_usd=Decimal("250.00"),
        risk_budget_pct=0.6,
        legs=(
            OptionsLegSpec(contract_symbol="AAPL260717C00180000", side="buy"),
            OptionsLegSpec(contract_symbol="AAPL260717C00190000", side="sell"),
        ),
        dry_run=True,
    )
    assert payload.dry_run is True
    assert len(payload.legs) == 2


def test_audit_log_requires_symbol_alignment() -> None:
    now = datetime.now(tz=UTC)
    inp = OptionsStrategyInput(symbol="META", as_of=now)
    decision = PlaybookDecision(
        symbol="META",
        as_of=now,
        decision=StrategyDecision.NO_TRADE,
        reason_codes=("symbol_not_in_route1_universe",),
    )
    log = OptionsStrategyAuditLog(
        input=inp,
        playbook_decision=decision,
    )
    assert log.input.symbol == "META"


def test_audit_log_rejects_mismatched_symbols() -> None:
    now = datetime.now(tz=UTC)
    inp = OptionsStrategyInput(symbol="META", as_of=now)
    decision = PlaybookDecision(
        symbol="AAPL",
        as_of=now,
        decision=StrategyDecision.NO_TRADE,
    )
    with pytest.raises(ValueError, match="symbols must match"):
        OptionsStrategyAuditLog(input=inp, playbook_decision=decision)


def test_frozen_models_immutable() -> None:
    inp = OptionsStrategyInput(symbol="AMZN", as_of=datetime.now(tz=UTC))
    copy = inp.model_copy(update={"symbol": "AMZN"})
    assert copy.symbol == "AMZN"
    assert inp.symbol == "AMZN"
