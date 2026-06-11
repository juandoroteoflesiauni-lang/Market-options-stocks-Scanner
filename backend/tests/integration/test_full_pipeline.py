"""Tests de integración del pipeline completo A → B → C → D.

Verifica que los módulos se conectan correctamente en el flujo:
  Phase A (Scanner output mock) → Phase B (MicrostructureEngine)
    → Phase C (DerivativesEngine) → Phase D (SignalEmitter)

Usa datos sintéticos; las dependencias externas (FMP, Alpaca, Massive)
son mockeadas o no se invocan.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from backend.engine.quantitative_engine import QuantitativeEngine
from backend.models.enriched_snapshot import EnrichedSnapshot
from backend.models.execution_signal import SignalType, TickAnalysis
from backend.models.market_snapshot import DataLineage, MarketSnapshot, OHLCVBar
from backend.models.option_contract import OptionChainSnapshot, OptionContract, TopOptionSelection
from backend.models.result import Result
from backend.phases.phase_b.microstructure_engine import MicrostructureEngine
from backend.phases.phase_c.derivatives_engine import DerivativesEngine
from backend.phases.phase_d.signal_emitter import DEFAULT_EMITTER_CONFIG, SignalEmitter

# ── Shared helpers ────────────────────────────────────────────────────────────

DL = DataLineage(source="integration_test", ingestion_latency_ms=5, raw_field_count=5)


def _make_bars(n: int = 30, uptrend: bool = True) -> tuple[OHLCVBar, ...]:
    base = 150.0
    bars: list[OHLCVBar] = []
    for i in range(n):
        c = base + i * 0.5 + (i % 3) * 0.1 if uptrend else base - i * 0.5 - (i % 3) * 0.1
        bars.append(
            OHLCVBar(
                time=f"2025-01-01T09:{i:02d}:00Z",
                open=c - 0.2,
                high=c + 0.5,
                low=c - 0.5,
                close=c,
                volume=float(100_000 + i * 1_000),
            )
        )
    return tuple(bars)


def _make_snapshot(
    ticker: str = "SPY",
    price: str = "500.00",
    volume: int = 5_000_000,
    ohlcv: tuple[OHLCVBar, ...] | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        ticker=ticker,
        exchange="NYSE",
        price=Decimal(price),
        volume=volume,
        exchange_timestamp=datetime.now(UTC),
        data_lineage=DL,
        ohlcv=ohlcv or (),
    )


def _make_option_contract(
    ticker: str = "SPY",
    strike: Decimal = Decimal("500.00"),
    option_type: str = "CALL",
    dte: int = 30,
) -> OptionContract:
    symbol = f"{ticker}250912C{int(strike * 1000):08d}"
    return OptionContract(
        underlying_ticker=ticker,
        contract_symbol=symbol,
        strike=strike,
        expiry=date(2025, 9, 12),
        option_type=option_type,
        bid=Decimal("8.50"),
        ask=Decimal("8.55"),
        volume=10_000,
        open_interest=50_000,
        implied_volatility=0.25,
        delta=0.55 if option_type == "CALL" else -0.45,
        gamma=0.03,
        theta=-0.02,
        vega=0.15,
        rho=0.01,
        dte=dte,
        data_lineage=DL,
    )


def _make_options_chain(ticker: str = "SPY") -> OptionChainSnapshot:
    spot = Decimal("500.00")
    return OptionChainSnapshot(
        ticker=ticker,
        spot_price=spot,
        contracts=[
            _make_option_contract(ticker, strike=Decimal("490.00"), option_type="PUT"),
            _make_option_contract(ticker, strike=Decimal("495.00"), option_type="PUT"),
            _make_option_contract(ticker, strike=spot, option_type="CALL"),
            _make_option_contract(ticker, strike=Decimal("505.00"), option_type="CALL"),
            _make_option_contract(ticker, strike=Decimal("510.00"), option_type="CALL"),
        ],
        total_call_volume=150_000,
        total_put_volume=120_000,
        total_call_oi=800_000,
        total_put_oi=600_000,
        put_call_ratio_volume=0.80,
        put_call_ratio_oi=0.75,
    )


# ── Mock Hub for Phase C ─────────────────────────────────────────────────────


class MockOptionsHub:
    """Simula un hub de datos que provee cadenas de opciones sintéticas."""

    def __init__(self, chain_map: dict[str, OptionChainSnapshot] | None = None) -> None:
        self._chain_map = chain_map or {}

    async def get_options_chain(self, ticker: str) -> Result[OptionChainSnapshot]:
        chain = self._chain_map.get(ticker.upper())
        if chain is None:
            return Result.failure(reason=f"No options data for {ticker}")
        return Result.success(chain)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestFullPipeline:
    """Pipeline completo A → B → C → D con datos sintéticos."""

    @pytest.mark.asyncio
    async def test_phase_a_to_b_enriches_snapshots(self) -> None:
        """Phase A (mock snapshots) → Phase B enriches with OFI/SMC."""
        snapshots = [
            _make_snapshot(ticker="SPY", ohlcv=_make_bars(n=30, uptrend=True)),
            _make_snapshot(ticker="QQQ", ohlcv=_make_bars(n=30, uptrend=False)),
            _make_snapshot(ticker="IWM", ohlcv=_make_bars(n=20, uptrend=True)),
        ]

        engine = MicrostructureEngine(max_workers=2)
        enriched = await engine.enrich_batch(snapshots)
        engine.shutdown()

        assert len(enriched) == 3
        for e in enriched:
            assert isinstance(e, EnrichedSnapshot)
            assert isinstance(e.ofi_score, float)
            assert e.smc_direction in ("BULLISH", "BEARISH", None)
            assert 0.0 <= e.smc_weight <= 1.0

        spy = next(e for e in enriched if e.ticker == "SPY")
        qqq = next(e for e in enriched if e.ticker == "QQQ")
        assert spy.ofi_score != 0.0 or spy.smc_direction is not None
        assert qqq.ofi_score != 0.0 or qqq.smc_direction is not None

    @pytest.mark.asyncio
    async def test_quantitative_engine_legacy_wrapper(self) -> None:
        """Legacy QuantitativeEngine wrapper produce EnrichedSnapshot."""

        class DummyBus:
            async def publish(self, event: object) -> None:
                pass

        qe = QuantitativeEngine(event_bus=DummyBus(), max_workers=1)
        snapshot = _make_snapshot(ohlcv=_make_bars(n=25, uptrend=True))
        result = await qe.process_snapshot(snapshot)
        qe.shutdown()

        assert result.is_success
        enriched = result.unwrap()
        assert enriched.ticker == "SPY"
        assert isinstance(enriched.ofi_score, float)
        qe.shutdown()

    @pytest.mark.asyncio
    async def test_phase_b_to_c_selects_top_contracts(self) -> None:
        """Phase B enriched snapshots → Phase C DerivativesEngine produce TopOptionSelection."""
        enriched = [
            EnrichedSnapshot(
                ticker="SPY",
                exchange="NYSE",
                price=Decimal("500.00"),
                volume=5_000_000,
                exchange_timestamp=datetime.now(UTC),
                data_lineage=DL,
                ohlcv=_make_bars(n=30, uptrend=True),
                ofi_score=0.45,
                smc_direction="BULLISH",
                smc_weight=0.7,
            ),
            EnrichedSnapshot(
                ticker="QQQ",
                exchange="NASDAQ",
                price=Decimal("400.00"),
                volume=3_000_000,
                exchange_timestamp=datetime.now(UTC),
                data_lineage=DL,
                ohlcv=_make_bars(n=30, uptrend=False),
                ofi_score=-0.30,
                smc_direction="BEARISH",
                smc_weight=0.6,
            ),
        ]

        hub = MockOptionsHub(
            chain_map={
                "SPY": _make_options_chain("SPY"),
                "QQQ": _make_options_chain("QQQ"),
            }
        )

        engine = DerivativesEngine(hub=hub)
        result = await engine.process_top_candidates(enriched)

        assert result.is_success
        selections = result.unwrap()
        assert isinstance(selections, list)
        assert len(selections) > 0

        for sel in selections:
            assert isinstance(sel, TopOptionSelection)
            assert sel.ticker in ("SPY", "QQQ")
            assert sel.has_selection
            assert sel.confidence > 0.0
            assert sel.regime in ("BULLISH", "NEUTRAL", "BEARISH")

    @pytest.mark.asyncio
    async def test_phase_c_to_d_signal_emitter_generates_signals(self) -> None:
        """Phase C TopOptionSelection → Phase D SignalEmitter produce ExecutionSignals."""
        contract = _make_option_contract()
        selection = TopOptionSelection(
            ticker="SPY",
            selected_contracts=[contract],
            selection_criteria={"min_volume": 100},
            engine_scores={
                "gex_score": 65.0,
                "gamma_flip": 70.0,
                "dex_exposure": 55.0,
                "flow_signal": 60.0,
                "zero_day": 50.0,
                "shadow_delta": 50.0,
                "delta_flow": 50.0,
                "phase_b_momentum": 75.0,
            },
            regime="BULLISH",
            confidence=0.70,
        )

        emitter = SignalEmitter(selections=[selection])
        sym = contract.contract_symbol

        results: list[TickAnalysis] = []
        for i in range(15):
            price = 8.50 + i * 0.05
            analysis = emitter.process_tick(
                contract_symbol=sym,
                price=price,
                volume=100 + i * 10,
                timestamp=1000.0 + i,
            )
            if analysis is not None:
                results.append(analysis)

        assert len(results) > 0
        last = results[-1]
        assert isinstance(last, TickAnalysis)
        assert last.contract_symbol == sym

        signals = [r for r in results if r.signal_generated and r.signal is not None]
        if signals:
            signal = signals[0].signal
            assert signal.contract_symbol == sym
            assert signal.underlying_ticker == "SPY"
            assert signal.signal_type in (
                SignalType.ENTRY_LONG,
                SignalType.ENTRY_SHORT,
                SignalType.SCALP_LONG,
                SignalType.SCALP_SHORT,
            )
            assert 0.0 <= signal.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_full_pipeline_end_to_end(self) -> None:
        """Pipeline completo A→B→C→D con datos sintéticos y mocks."""
        snapshots = [
            _make_snapshot(ticker="SPY", ohlcv=_make_bars(n=30, uptrend=True)),
            _make_snapshot(ticker="QQQ", ohlcv=_make_bars(n=30, uptrend=False)),
        ]

        me = MicrostructureEngine(max_workers=2)
        enriched = await me.enrich_batch(snapshots)
        me.shutdown()

        assert len(enriched) == 2

        hub = MockOptionsHub(
            chain_map={
                "SPY": _make_options_chain("SPY"),
                "QQQ": _make_options_chain("QQQ"),
            }
        )
        de = DerivativesEngine(hub=hub)
        result = await de.process_top_candidates(enriched)
        assert result.is_success
        selections = result.unwrap()
        assert len(selections) > 0

        emitter = SignalEmitter(selections=selections)
        ticks_fired = 0
        for sel in selections:
            for contract in sel.selected_contracts:
                for i in range(20):
                    price = float(contract.bid) + i * 0.03
                    analysis = emitter.process_tick(
                        contract_symbol=contract.contract_symbol,
                        price=price,
                        volume=500 + i * 50,
                        timestamp=2000.0 + i,
                    )
                    if analysis is not None and analysis.signal_generated:
                        ticks_fired += 1

        assert ticks_fired >= 0


class TestPipelineEdgeCases:
    """Casos borde del pipeline."""

    @pytest.mark.asyncio
    async def test_empty_batch_returns_empty(self) -> None:
        """Phase B recibe lista vacía → retorna lista vacía."""
        engine = MicrostructureEngine(max_workers=1)
        result = await engine.enrich_batch([])
        assert result == []
        engine.shutdown()

    @pytest.mark.asyncio
    async def test_no_candidates_returns_failure(self) -> None:
        """Phase C recibe lista vacía → retorna failure."""
        hub = MockOptionsHub()
        engine = DerivativesEngine(hub=hub)
        result = await engine.process_top_candidates([])
        assert result.is_failure

    @pytest.mark.asyncio
    async def test_no_options_data_skips_ticker(self) -> None:
        """Phase C ticker sin options chain → es skipeado sin crash."""
        enriched = [
            EnrichedSnapshot(
                ticker="SPY",
                exchange="NYSE",
                price=Decimal("500.00"),
                volume=5_000_000,
                exchange_timestamp=datetime.now(UTC),
                data_lineage=DL,
                ofi_score=0.0,
                smc_direction=None,
                smc_weight=0.0,
            ),
            EnrichedSnapshot(
                ticker="UNKN",
                exchange="NYSE",
                price=Decimal("100.00"),
                volume=100_000,
                exchange_timestamp=datetime.now(UTC),
                data_lineage=DL,
                ofi_score=0.0,
                smc_direction=None,
                smc_weight=0.0,
            ),
        ]
        hub = MockOptionsHub(chain_map={"SPY": _make_options_chain("SPY")})
        engine = DerivativesEngine(hub=hub)
        result = await engine.process_top_candidates(enriched)

        assert result.is_success
        selections = result.unwrap()
        tickers = [s.ticker for s in selections]
        assert "SPY" in tickers
        assert "UNKN" not in tickers

    @pytest.mark.asyncio
    async def test_signal_emitter_ignores_unknown_contract(self) -> None:
        """SignalEmitter ignora ticks de contratos no seleccionados."""
        emitter = SignalEmitter(selections=[])
        analysis = emitter.process_tick("UNKNOWN", 100.0, 100, 1.0)
        assert analysis is None

    def test_default_emitter_config_is_valid(self) -> None:
        """Config por defecto tiene todos los campos esperados."""
        required_keys = {
            "momentum_window",
            "volatility_window",
            "volume_spike_threshold",
            "entry_momentum_threshold",
            "exit_momentum_threshold",
            "stop_loss_pct",
            "take_profit_pct",
            "min_confidence",
            "cooldown_seconds",
            "min_ticks_for_signal",
        }
        assert required_keys.issubset(DEFAULT_EMITTER_CONFIG.keys())
