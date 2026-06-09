"""Tests unitarios para Phase D — Real-Time Monitor.

Cubre:
- ExecutionSignal model validation
- TickBuffer
- SignalEmitter (tick processing, signal generation)
- Signal classification and confidence
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.models.execution_signal import (
    ExecutionSignal,
    SignalStrength,
    SignalType,
    TickAnalysis,
)
from backend.models.market_snapshot import DataLineage
from backend.models.option_contract import OptionContract, TopOptionSelection

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_lineage() -> DataLineage:
    return DataLineage(source="test", ingestion_latency_ms=5, raw_field_count=10)


def _make_contract(
    ticker: str = "AAPL",
    strike: float = 150.0,
    option_type: str = "CALL",
) -> OptionContract:
    return OptionContract(
        underlying_ticker=ticker,
        contract_symbol=f"{ticker}240119{option_type[0]}{int(strike):08d}",
        strike=Decimal(str(strike)),
        expiry=date(2026, 7, 19),
        option_type=option_type,
        bid=Decimal("2.50"),
        ask=Decimal("2.60"),
        last_price=Decimal("2.55"),
        volume=500,
        open_interest=2000,
        implied_volatility=0.25,
        delta=0.50,
        gamma=0.02,
        theta=-0.05,
        vega=0.15,
        rho=0.03,
        dte=30,
        data_lineage=_make_lineage(),
    )


def _make_selection() -> TopOptionSelection:
    return TopOptionSelection(
        ticker="AAPL",
        selected_contracts=[_make_contract()],
        engine_scores={
            "gex_score": 65.0,
            "gamma_flip": 55.0,
            "dex_exposure": 60.0,
            "flow_signal": 70.0,
        },
        confidence=0.75,
    )


def _make_signal() -> ExecutionSignal:
    return ExecutionSignal(
        signal_id="test-001",
        contract_symbol="AAPL240119C00000150",
        underlying_ticker="AAPL",
        signal_type=SignalType.ENTRY_LONG,
        strength=SignalStrength.STRONG,
        direction="LONG",
        entry_price=Decimal("150.00"),
        current_price=Decimal("150.00"),
        stop_loss_price=Decimal("147.00"),
        take_profit_price=Decimal("156.00"),
        confidence=0.75,
        expected_move_pct=0.5,
        risk_reward_ratio=2.0,
        trigger_reason="Momentum +0.35% | Volume spike",
        engine_scores={"gex_score": 65.0},
        data_lineage=_make_lineage(),
    )


# ── ExecutionSignal Tests ────────────────────────────────────────────────────


def test_execution_signal_frozen():
    signal = _make_signal()
    with pytest.raises(ValidationError):
        signal.signal_id = "changed"


def test_execution_signal_is_entry():
    signal = _make_signal()
    assert signal.is_entry is True
    assert signal.is_exit is False


def test_execution_signal_is_exit():
    signal = ExecutionSignal(
        signal_id="test-002",
        contract_symbol="AAPL240119C00000150",
        underlying_ticker="AAPL",
        signal_type=SignalType.EXIT_LONG,
        strength=SignalStrength.MODERATE,
        direction="LONG",
        entry_price=Decimal("150.00"),
        current_price=Decimal("152.00"),
        confidence=0.70,
        trigger_reason="Take profit",
        data_lineage=_make_lineage(),
    )
    assert signal.is_exit is True
    assert signal.is_entry is False


def test_execution_signal_ticker_uppercase():
    signal = ExecutionSignal(
        signal_id="test-003",
        contract_symbol="aapl240119c00000150",
        underlying_ticker="aapl",
        signal_type=SignalType.ENTRY_LONG,
        strength=SignalStrength.WEAK,
        direction="LONG",
        entry_price=Decimal("150.00"),
        current_price=Decimal("150.00"),
        confidence=0.50,
        trigger_reason="Test",
        data_lineage=_make_lineage(),
    )
    assert signal.underlying_ticker == "AAPL"
    assert signal.contract_symbol == "AAPL240119C00000150"


def test_execution_signal_websocket_payload():
    signal = _make_signal()
    payload = signal.to_websocket_payload()

    assert payload["signal_id"] == "test-001"
    assert payload["signal_type"] == "ENTRY_LONG"
    assert payload["direction"] == "LONG"
    assert payload["underlying"] == "AAPL"
    assert "entry_price" in payload
    assert "confidence" in payload


def test_execution_signal_confidence_bounds():
    with pytest.raises(ValidationError):
        ExecutionSignal(
            signal_id="test",
            contract_symbol="AAPL240119C00000150",
            underlying_ticker="AAPL",
            signal_type=SignalType.ENTRY_LONG,
            strength=SignalStrength.WEAK,
            direction="LONG",
            entry_price=Decimal("150.00"),
            current_price=Decimal("150.00"),
            confidence=1.5,
            trigger_reason="Test",
            data_lineage=_make_lineage(),
        )


def test_signal_type_enum():
    assert SignalType.ENTRY_LONG.value == "ENTRY_LONG"
    assert SignalType.STOP_LOSS.value == "STOP_LOSS"
    assert SignalType.SCALP_SHORT.value == "SCALP_SHORT"


def test_signal_strength_enum():
    assert SignalStrength.WEAK.value == "WEAK"
    assert SignalStrength.CRITICAL.value == "CRITICAL"


# ── TickAnalysis Tests ───────────────────────────────────────────────────────


def test_tick_analysis_frozen():
    analysis = TickAnalysis(
        contract_symbol="AAPL240119C00000150",
        price=Decimal("150.00"),
        volume=100,
        vwap=150.0,
        price_change_pct=0.01,
        momentum_score=50.0,
        volatility_score=30.0,
    )
    assert analysis.signal_generated is False
    assert analysis.signal is None


# ── TickBuffer Tests ─────────────────────────────────────────────────────────


def test_tick_buffer_add():
    from backend.phases.phase_d.signal_emitter import TickBuffer

    buffer = TickBuffer(max_size=10)
    buffer.add(150.0, 100, 1.0)
    buffer.add(151.0, 200, 2.0)

    assert buffer.count == 2
    assert buffer.last_price == 151.0


def test_tick_buffer_max_size():
    from backend.phases.phase_d.signal_emitter import TickBuffer

    buffer = TickBuffer(max_size=3)
    buffer.add(150.0, 100, 1.0)
    buffer.add(151.0, 200, 2.0)
    buffer.add(152.0, 300, 3.0)
    buffer.add(153.0, 400, 4.0)

    assert buffer.count == 3
    assert buffer.last_price == 153.0
    assert buffer.prices[0] == 151.0


def test_tick_buffer_vwap():
    from backend.phases.phase_d.signal_emitter import TickBuffer

    buffer = TickBuffer()
    buffer.add(100.0, 10, 1.0)
    buffer.add(200.0, 20, 2.0)

    # VWAP = (100*10 + 200*20) / (10+20) = 5000/30 = 166.67
    expected_vwap = (100 * 10 + 200 * 20) / 30
    assert abs(buffer.vwap() - expected_vwap) < 0.01


def test_tick_buffer_price_change():
    from backend.phases.phase_d.signal_emitter import TickBuffer

    buffer = TickBuffer()
    buffer.add(100.0, 10, 1.0)
    buffer.add(105.0, 10, 2.0)

    change = buffer.price_change_pct(window=2)
    assert abs(change - 0.05) < 0.001


def test_tick_buffer_volatility():
    from backend.phases.phase_d.signal_emitter import TickBuffer

    buffer = TickBuffer()
    for i in range(25):
        buffer.add(100.0 + i * 0.5, 100, float(i))

    vol = buffer.volatility(window=20)
    assert vol > 0


def test_tick_buffer_volume_spike():
    from backend.phases.phase_d.signal_emitter import TickBuffer

    buffer = TickBuffer()
    for i in range(10):
        buffer.add(100.0, 100, float(i))
    buffer.add(100.0, 500, 10.0)

    assert buffer.volume_spike(threshold=2.5) is True


def test_tick_buffer_no_volume_spike():
    from backend.phases.phase_d.signal_emitter import TickBuffer

    buffer = TickBuffer()
    for i in range(10):
        buffer.add(100.0, 100, float(i))

    assert buffer.volume_spike(threshold=2.5) is False


# ── SignalEmitter Tests ──────────────────────────────────────────────────────


def test_signal_emitter_initialization():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    assert "AAPL240119C00000150" in emitter._buffers


def test_signal_emitter_ignores_unknown_contract():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    result = emitter.process_tick("UNKNOWN", 100.0, 100)
    assert result is None


def test_signal_emitter_needs_min_ticks():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection], config={"min_ticks_for_signal": 5})

    for i in range(4):
        result = emitter.process_tick("AAPL240119C00000150", 150.0 + i * 0.1, 100)
        assert result is None


def test_signal_emitter_generates_analysis():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection], config={"min_ticks_for_signal": 3})

    emitter.process_tick("AAPL240119C00000150", 150.0, 100)
    emitter.process_tick("AAPL240119C00000150", 150.5, 100)
    result = emitter.process_tick("AAPL240119C00000150", 151.0, 100)

    assert result is not None
    assert result.contract_symbol == "AAPL240119C00000150"
    assert result.momentum_score > 0


def test_signal_emitter_detects_momentum():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(
        selections=[selection],
        config={
            "min_ticks_for_signal": 5,
            "momentum_window": 5,
            "entry_momentum_threshold": 0.001,
            "cooldown_seconds": 0,
        },
    )

    prices = [150.0, 150.1, 150.3, 150.6, 151.0, 151.5]
    for price in prices:
        result = emitter.process_tick("AAPL240119C00000150", price, 100)

    assert result is not None
    assert result.price_change_pct > 0


def test_signal_emitter_volume_spike():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(
        selections=[selection],
        config={
            "min_ticks_for_signal": 3,
            "volume_spike_threshold": 2.0,
            "cooldown_seconds": 0,
        },
    )

    for _i in range(5):
        emitter.process_tick("AAPL240119C00000150", 150.0, 100)

    result = emitter.process_tick("AAPL240119C00000150", 151.0, 500)
    assert result is not None


def test_signal_emitter_cooldown():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(
        selections=[selection],
        config={
            "min_ticks_for_signal": 3,
            "momentum_window": 3,
            "entry_momentum_threshold": 0.001,
            "cooldown_seconds": 60,
        },
    )

    for i in range(10):
        emitter.process_tick("AAPL240119C00000150", 150.0 + i * 2.0, 100)

    stats = emitter.get_buffer_stats("AAPL240119C00000150")
    assert stats is not None
    assert stats["count"] == 10


def test_signal_emitter_buffer_stats():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    for i in range(5):
        emitter.process_tick("AAPL240119C00000150", 150.0 + i, 100)

    stats = emitter.get_buffer_stats("AAPL240119C00000150")
    assert stats is not None
    assert stats["count"] == 5
    assert stats["last_price"] == 154.0


def test_signal_emitter_empty_buffer_stats():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    stats = emitter.get_buffer_stats("AAPL240119C00000150")
    assert stats is None


# ── Signal Classification Tests ──────────────────────────────────────────────


def test_signal_emitter_classify_strength_critical():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    strength = emitter._classify_strength(momentum=0.01, volatility=0.01, vol_spike=True)
    assert strength in (SignalStrength.CRITICAL, SignalStrength.STRONG)


def test_signal_emitter_classify_strength_weak():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    strength = emitter._classify_strength(momentum=0.001, volatility=0.001, vol_spike=False)
    assert strength in (SignalStrength.WEAK, SignalStrength.MODERATE)


def test_signal_emitter_confidence_with_selection():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    conf = emitter._compute_confidence(
        momentum=0.005,
        volatility=0.003,
        vol_spike=True,
        selection=selection,
    )
    assert 0.5 < conf <= 1.0


def test_signal_emitter_confidence_without_selection():
    from backend.phases.phase_d.signal_emitter import SignalEmitter

    selection = _make_selection()
    emitter = SignalEmitter(selections=[selection])

    conf = emitter._compute_confidence(
        momentum=0.001,
        volatility=0.001,
        vol_spike=False,
        selection=None,
    )
    assert 0.0 < conf < 0.7
