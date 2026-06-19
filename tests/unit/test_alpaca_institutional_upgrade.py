"""Unit tests for institutional Alpaca upgrade components. # [TH]"""

from __future__ import annotations

from backend.backtesting.slippage_models import bur_zone_from_slippage_pct
from backend.domain.alpaca_models import EquityOrderIntent, EquityRiskDecision
from backend.quant_engine.math.technical.ivpin import compute_ivpin
from backend.services.alpaca_dynamic_sizer import DynamicSizer
from backend.services.alpaca_event_journal import AlpacaEventJournal
from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate
from backend.services.alpaca_r1_options_confluence import compute_options_stock_imbalance
from backend.services.alpaca_r1_options_replay import AlpacaR1ReplayVerifier
from backend.services.alpaca_r2_technical_scoring import score_route2_technical
from backend.services.bot.alpaca_bot_execution_mixin import AlpacaBotExecutionMixin


def _intent(qty: int = 10, price: float = 100.0) -> EquityOrderIntent:
    return EquityOrderIntent(
        symbol="AAPL",
        quantity=qty,
        reference_price=price,
        notional_usd=qty * price,
        client_order_id="qa-aapl-test",
        cycle_id="c1",
    )


def _authorized_decision(qty: int = 10) -> EquityRiskDecision:
    intent = _intent(qty=qty)
    return EquityRiskDecision(
        authorized=True,
        intent=intent,
        idempotency_key=intent.client_order_id,
        adjusted_quantity=qty,
    )


def test_ivpin_stable_on_small_buckets() -> None:
    buy = [100.0, 50.0, 30.0]
    sell = [80.0, 60.0, 40.0]
    result = compute_ivpin(buy, sell)
    assert result["ivpin"] is not None
    assert 0.0 <= result["ivpin"] <= 1.0
    assert result["method"] == "ivpin_mle_v1"


def test_pre_trade_gate_blocks_kill_switch() -> None:
    PreTradeRiskGate.reset_instance()
    from backend.config.alpaca_institutional_config import AlpacaPreTradeLimits

    gate = PreTradeRiskGate(AlpacaPreTradeLimits(kill_switch=True))
    verdict = gate.evaluate(_authorized_decision())
    assert verdict.allowed is False
    assert "kill_switch_active" in verdict.reason_codes


def test_pre_trade_gate_red_bur_blocks() -> None:
    PreTradeRiskGate.reset_instance()
    gate = PreTradeRiskGate.instance()
    gate.update_bur(0.9)
    verdict = gate.evaluate(_authorized_decision())
    assert verdict.allowed is False
    assert "bur_red_zone_block" in verdict.reason_codes
    PreTradeRiskGate.reset_instance()


def test_dynamic_sizer_convergence_halves_on_low_rr() -> None:
    result = DynamicSizer.size(
        base_quantity=10,
        reference_price=100.0,
        signal_score=0.8,
        stop_loss=95.0,
        take_profit=102.0,
    )
    assert result.convergence_ok is False
    assert result.quantity < 10


def test_event_journal_deterministic_hash() -> None:
    AlpacaEventJournal.reset_instance()
    journal = AlpacaEventJournal.instance()
    journal.append("cycle_start", cycle_id="c1", payload={"n": 1})
    journal.append("risk_decision", cycle_id="c1", symbol="AAPL", payload={"ok": True})
    hash_a = journal.state_hash
    events = journal.export_glass_box("c1")
    assert len(events) == 2
    assert journal.replay_verify(hash_a)


def test_os_imbalance_bullish_tilt() -> None:
    imb = compute_options_stock_imbalance(1000.0, 200.0, 5000.0)
    assert imb is not None
    assert imb > 0


def test_r2_ivpin_gate_reduces_score() -> None:
    payload = {
        "ok": True,
        "hmm_regime": {"ok": True, "regime_signal": "BULLISH"},
        "microstructure": {
            "buy_volumes": [1000, 900, 800],
            "sell_volumes": [200, 150, 100],
        },
    }
    for engine in (
        "market_structure",
        "volume_profile",
        "vsa_footprint",
        "single_prints",
        "volume_nodes",
    ):
        payload[engine] = {"ok": True, "bias": "BULLISH", "regime": "BULLISH"}
    result = score_route2_technical(payload)
    assert result.ivpin is not None
    assert result.ivpin_gate <= 1.0


def test_bur_zone_from_slippage() -> None:
    bur, zone = bur_zone_from_slippage_pct(0.02)
    assert zone == "GREEN"
    assert bur < 0.5


def test_elite_advanced_instructions_dma() -> None:
    mixin = AlpacaBotExecutionMixin()
    decision = _authorized_decision(qty=30)
    decision = decision.model_copy(
        update={
            "intent": decision.intent.model_copy(update={"notional_usd": 3000.0}),
        }
    )
    from backend.config.alpaca_institutional_config import AlpacaEliteOrderConfig

    mixin._elite_config = lambda: AlpacaEliteOrderConfig(  # type: ignore[method-assign]
        enabled=True,
        algorithm="DMA",
        destination="NASDAQ",
        min_notional_for_elite_usd=1000.0,
    )
    adv = mixin._build_advanced_instructions(decision)
    assert adv is not None
    assert adv["algorithm"] == "DMA"
    assert adv["destination"] == "NASDAQ"


def test_replay_verifier_hash_stable() -> None:
    match, hash_a, hash_b = AlpacaR1ReplayVerifier.replay_twice_verify([], None)
    assert match is True
    assert hash_a == hash_b
