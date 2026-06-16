"""Fase 4 — fusión, vetos, playbooks y pipeline dry-run. # [TH][IM]"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.config.options_strategy_loader import load_options_strategy_config
from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsStrategyCandidate,
    OptionsStrategyInput,
    OptionsStructure,
    PlaybookDecision,
    StrategyDecision,
    StructureSelection,
)
from backend.services.options_strategy.fusion_router import FusionRouter, fuse_features
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline
from backend.services.options_strategy.playbook_matcher import PlaybookMatcher
from backend.services.options_strategy.veto_engine import VetoEngine
from tests.unit.test_options_strategy_phase3 import _make_googl_input


def _relaxed_config():
    cfg = load_options_strategy_config()
    trend = cfg.playbooks.playbooks["trend_continuation"].model_copy(
        update={
            "min_trend_quality": 0.05,
            "min_predictive_bias": 0.05,
            "min_options_bias": 0.05,
        }
    )
    playbooks = cfg.playbooks.model_copy(
        update={"playbooks": {**cfg.playbooks.playbooks, "trend_continuation": trend}}
    )
    return cfg.model_copy(
        update={
            "omni_engine": cfg.omni_engine.model_copy(
                update={"min_global_confidence": 0.05}
            ),
            "playbooks": playbooks,
            "risk": cfg.risk.model_copy(update={"min_chain_liquidity_score": 0.05}),
        }
    )


def _strong_features(symbol: str = "GOOGL") -> NormalizedFeatures:
    return NormalizedFeatures(
        symbol=symbol,
        as_of=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
        technical_direction_bias=0.72,
        predictive_direction_bias=0.68,
        options_direction_bias=0.65,
        trend_quality_score=0.80,
        structure_alignment_score=0.75,
        expected_move_confidence=0.70,
        forecast_dispersion_score=0.10,
        flow_conviction_score=0.65,
        chain_liquidity_score=0.85,
        iv_state="fair",
        left_tail_risk_score=0.20,
        right_tail_risk_score=0.25,
    )


def _bullish_candidate(symbol: str = "GOOGL") -> OptionsStrategyCandidate:
    from backend.models.options_strategy import SelectedOptionContract
    from datetime import date

    expiry = date(2026, 6, 27)
    leg = SelectedOptionContract(
        underlying=symbol,
        expiry=expiry,
        strike=180.0,
        right="call",
        side="long",
        delta=0.38,
        open_interest=1200,
        mark=3.50,
        iv=0.28,
        dte=14,
        contract_symbol="GOOGL20260627C00180000",
    )
    selection = StructureSelection(
        symbol=symbol,
        as_of=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
        structure=OptionsStructure.LONG_CALL,
        direction="bullish",
        reason_codes=("test_setup",),
        confidence=0.75,
    )
    return OptionsStrategyCandidate(
        symbol=symbol,
        as_of=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
        selection=selection,
        legs=(leg,),
        max_profit=None,
        max_loss=350.0,
    )


def test_fuse_features_sets_global_bias_and_confidence() -> None:
    raw = _strong_features()
    fused = fuse_features(raw)
    assert fused.global_bias > 0.5
    assert 0.0 < fused.global_confidence <= 1.0


def test_veto_chain_liquidity_poor() -> None:
    cfg = load_options_strategy_config()
    features = _strong_features().model_copy(update={"chain_liquidity_score": 0.10})
    fused = fuse_features(features, config=cfg)
    inp = _make_googl_input()
    veto = VetoEngine.evaluate(fused, inp, config=cfg)
    assert veto.triggered is True
    assert veto.veto_code == "chain_liquidity_poor"


def test_veto_tail_risk_critical_blocks_bullish() -> None:
    cfg = load_options_strategy_config()
    features = _strong_features().model_copy(
        update={"left_tail_risk_score": 0.90, "global_bias": 0.6}
    )
    fused = fuse_features(features, config=cfg)
    inp = _make_googl_input()
    veto = VetoEngine.evaluate(fused, inp, config=cfg)
    assert veto.veto_code == "tail_risk_critical"


def test_playbook_matcher_selects_trend_continuation() -> None:
    cfg = _relaxed_config()
    inp = _make_googl_input()
    features = fuse_features(_strong_features(), config=cfg)
    candidate = _bullish_candidate()
    match = PlaybookMatcher.match(inp, features, candidate, config=cfg)
    assert match.playbook_family == "trend_continuation"
    assert match.score > 0


def test_fusion_router_execute_on_strong_signal() -> None:
    cfg = _relaxed_config()
    inp = _make_googl_input()
    features = fuse_features(_strong_features(), config=cfg)
    candidate = _bullish_candidate()
    decision, payload = FusionRouter.decide(inp, features, candidate, config=cfg)
    assert decision.decision == StrategyDecision.EXECUTE
    assert decision.execution_ready is True
    assert decision.playbook_family == "trend_continuation"
    assert payload is not None
    assert payload.dry_run is True
    assert len(payload.legs) == 1


def test_fusion_router_no_trade_on_veto() -> None:
    cfg = load_options_strategy_config()
    inp = _make_googl_input()
    features = fuse_features(
        _strong_features().model_copy(update={"chain_liquidity_score": 0.05}),
        config=cfg,
    )
    candidate = _bullish_candidate()
    decision, payload = FusionRouter.decide(inp, features, candidate, config=cfg)
    assert decision.decision == StrategyDecision.NO_TRADE
    assert decision.veto_triggered == "chain_liquidity_poor"
    assert payload is None


def test_pipeline_dry_run_produces_audit_log() -> None:
    cfg = _relaxed_config()
    inp = _make_googl_input(iv_atm=0.28)
    log = OptionsStrategyPipeline.run_dry(inp, config=cfg)
    assert log.pipeline_phase == "phase5-risk-audit"
    assert log.features is not None
    assert log.playbook_decision.symbol == "GOOGL"
    assert isinstance(log.playbook_decision, PlaybookDecision)


def test_gamma_wall_playbook_requires_wall_levels() -> None:
    cfg = load_options_strategy_config()
    inp = _make_googl_input()
    features = fuse_features(_strong_features(), config=cfg)
    candidate = _bullish_candidate()
    match = PlaybookMatcher.match(inp, features, candidate, config=cfg)
    assert match.playbook_family != "gamma_wall_rejection"

    ctx = inp.options_context
    assert ctx is not None
    inp_with_wall = inp.model_copy(
        update={
            "options_context": ctx.model_copy(update={"call_wall": 185.0}),
        }
    )
    match_wall = PlaybookMatcher.match(inp_with_wall, features, candidate, config=cfg)
    assert match_wall.playbook_family in {"gamma_wall_rejection", "trend_continuation"}
