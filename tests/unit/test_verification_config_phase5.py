"""Tests Fase 5 paso 1 — cambios [config] P0/P1 del perfil verification. # [PD-6]

Valida (AAA) que:
- las constantes relajadas Alpaca/BingX endurecidas tienen el valor aprobado;
- ``apply_verification_session_env`` inyecta el env esperado (incl. bullish=false
  y caps risk desk recalibrados), SIN tocar EOD flatten ni meta-learner sintético;
- los defaults institucionales de ``PhaseAWeights`` (RSI/ATR/VWAP) son los aprobados;
- ``AlpacaDecisionConfig.from_env`` lee el nuevo prob_floor.
"""

from __future__ import annotations

import os

import pytest

from backend.config import bot_relaxed_thresholds as brt
from backend.models.strategy_weights import StrategyWeights
from backend.services.alpaca_decision_engine import AlpacaDecisionConfig


@pytest.fixture
def restore_environ():
    """Snapshot/restore os.environ around env-mutating calls."""
    snapshot = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def test_relaxed_constants_hardened_to_approved_values():
    # ASSERT — Alpaca
    assert brt.ALPACA_RELAXED_PROB_FLOOR == 0.45
    assert brt.ALPACA_RELAXED_MIN_VOLUME_Z == 0.50
    assert brt.ALPACA_RELAXED_MIN_CLOSE_POSITION == 0.45
    assert brt.ALPACA_RELAXED_R2_MIN_SCORE == 40.0
    assert brt.ALPACA_RELAXED_R2_GATE_VETO == 0.10
    assert brt.ALPACA_RELAXED_R2_CONFLUENCE_MIN == 2
    # ASSERT — BingX
    assert brt.BINGX_RELAXED_MIN_DECISION_SCORE == 0.40
    assert brt.BINGX_RELAXED_MIN_PREDICTIVE_CONF == 0.40
    # ASSERT — caps risk desk verification (H5)
    assert brt.VERIFICATION_RISK_MAX_DAILY_LOSS_USDT == 2_000.0
    assert brt.VERIFICATION_RISK_MAX_POSITION_NOTIONAL_USDT == 8_000.0
    assert brt.VERIFICATION_RISK_MAX_SYMBOL_EXPOSURE_USDT == 1_500.0
    assert brt.VERIFICATION_RISK_COOLDOWN_AFTER_LOSS_MINUTES == 5.0


def test_verification_env_injects_hardened_gates(restore_environ):
    # ACT
    brt.apply_verification_session_env(execute_orders=False)

    # ASSERT — gates de entrada Alpaca
    assert os.environ["ALPACA_PROB_FLOOR"] == "0.45"
    assert os.environ["ALPACA_MIN_VOLUME_Z"] == "0.5"
    assert os.environ["ALPACA_MIN_CLOSE_POSITION"] == "0.45"
    assert os.environ["ALPACA_R2_MIN_SCORE"] == "40.0"
    assert os.environ["ALPACA_R2_GATE_VETO_THRESHOLD"] == "0.1"
    assert os.environ["ALPACA_R2_CONFLUENCE_MIN_ENGINES"] == "2"
    # CRÍTICO (H4): bullish bypass desactivado
    assert os.environ["ALPACA_VERIFICATION_RELAXED_BULLISH"] == "false"
    # ASSERT — BingX
    assert os.environ["BINGX_MIN_DECISION_SCORE"] == "0.4"
    assert os.environ["BINGX_MIN_PREDICTIVE_CONFIDENCE"] == "0.4"
    # ASSERT — caps risk desk recalibrados (override verification)
    assert os.environ["RISK_MAX_DAILY_LOSS_USDT"] == "2000.0"
    assert os.environ["RISK_MAX_POSITION_NOTIONAL_USDT"] == "8000.0"
    assert os.environ["RISK_MAX_SYMBOL_EXPOSURE_USDT"] == "1500.0"
    assert os.environ["RISK_COOLDOWN_AFTER_LOSS_MINUTES"] == "5.0"


def test_verification_env_preserves_inviolable_constraints(restore_environ):
    # ACT
    brt.apply_verification_session_env(execute_orders=False)
    # ASSERT — restricciones que NO se deben tocar
    assert os.environ["ALPACA_EOD_FLATTEN_ENABLED"] == "true"
    assert os.environ["META_LEARNER_PROMOTE_SYNTHETIC"] == "false"
    assert os.environ["BOT_SESSION_MODE"] == "verification"


def test_phase_a_classic_thresholds_are_institutional():
    # ARRANGE / ACT
    pa = StrategyWeights.DEFAULT.phase_a
    # ASSERT
    assert pa.rsi_oversold_threshold == 25.0
    assert pa.rsi_overbought_threshold == 75.0
    assert pa.min_atr_pct == 0.005
    assert pa.vwap_max_zscore == 2.5


def test_alpaca_decision_config_reads_new_prob_floor(restore_environ):
    # ARRANGE
    os.environ["ALPACA_PROB_FLOOR"] = str(brt.ALPACA_RELAXED_PROB_FLOOR)
    # ACT
    cfg = AlpacaDecisionConfig.from_env()
    # ASSERT
    assert cfg.prob_floor == 0.45
