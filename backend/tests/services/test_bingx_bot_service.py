from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.domain.market_scanner_models import (
    MarketScannerResponse,
    MarketScannerRow,
    MarketScannerTimeframeSignal,
    ScannerCustomization,
    ScannerModuleSignal,
)
from backend.layer_1_data.datos.bingx_client import (
    BingXKline,
    BingXOrderResponse,
    BingXPerpOrderRequest,
)
from backend.layer_3_specialists.tecnico.lob_dynamics_engine import (
    LOBDynamicsAnalysis,
    LOBDynamicsResult,
    SpoofingState,
)
from backend.services.bingx_bot_service import (
    DEFAULT_UNIVERSE,
    EXECUTION_COOLDOWN_MINUTES,
    REASON_L2_DEPTH_TOO_THIN,
    REASON_L2_IMBALANCE_EXTREME,
    REASON_L2_SPREAD_TOO_WIDE,
    REASON_L2_UNAVAILABLE,
    BingXBotService,
    BingXMarketSnapshot,
    BingXRiskPolicy,
    BingXSignal,
    BingXTechnicalBlock,
    ExecutionQualityPolicy,
    FilterDecision,
    _evaluate_l2_execution_quality,
    _normalize_bingx_symbol_for_scanner,
    _synthetic_stock_symbols,
)
from backend.services.bingx_candidate_analysis import BingXCandidateAnalysis
from backend.services.scanner_funding_gate import (
    REASON_SCANNER_DAILY_OPPOSES,
    REASON_SCANNER_INTRADAY_NOT_ALIGNED,
    REASON_SCANNER_SCORE_TOO_LOW,
    REASON_SCANNER_UNAVAILABLE,
    REASON_SCANNER_VETO_PRESENT,
    REASON_WEAK_EDGE,
)


@pytest.fixture(autouse=True)
def clean_dry_run_env(monkeypatch) -> None:
    monkeypatch.setenv("BINGX_DRY_RUN", "true")
    monkeypatch.delenv("BINGX_BOT_TRADING_ENV", raising=False)


@dataclass
class FakeScanner:
    rows: list[MarketScannerRow]
    error: Exception | None = None
    requests: list[Any] | None = None

    async def scan(self, request: object) -> MarketScannerResponse:
        if self.requests is None:
            self.requests = []
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return MarketScannerResponse(universe="custom", rows=self.rows)


def _snapshot(symbol: str = "AAPL-USDT") -> BingXMarketSnapshot:
    return BingXMarketSnapshot(
        symbol=symbol,
        interval="5m",
        bars=80,
        latest_close=100.0,
        last_volume=500.0,
        volume_mean=200.0,
        volume_std=100.0,
        volume_z_score=3.0,
        close_position_in_range=0.75,
        range_pct=0.25,
        captured_at="2026-05-19T12:00:00Z",
        closes_recent=(98.0, 99.0, 100.0),
    )


def _signal(
    symbol: str = "AAPL-USDT",
    direction: str = "LONG",
    lob_analysis: LOBDynamicsAnalysis | None = None,
) -> BingXSignal:
    assert direction in {"LONG", "SHORT", "FLAT"}
    return BingXSignal(
        symbol=symbol,
        direction=direction,  # type: ignore[arg-type]
        score=1.0,
        horizon="1h",
        reason_codes=(),
        snapshot=_snapshot(symbol),
        timestamp="2026-05-19T12:00:00Z",
        lob_analysis=lob_analysis,
    )


class RouteRecordingClient:
    dry_run = True

    def __init__(self) -> None:
        self.perp_calls: list[str] = []
        self.perp_limits: list[int] = []
        self.spot_calls: list[str] = []

    async def fetch_klines_perp(
        self,
        symbol: str,
        interval: str = "5m",
        *,
        limit: int = 120,
    ) -> list[BingXKline]:
        self.perp_calls.append(symbol)
        self.perp_limits.append(limit)
        return [
            BingXKline(
                open_time_ms=i * 60_000,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000.0 + i,
                close_time_ms=i * 60_000 + 59_999,
            )
            for i in range(limit)
        ]

    async def fetch_klines(
        self,
        symbol: str,
        interval: str = "5m",
        *,
        limit: int = 120,
    ) -> list[BingXKline]:
        self.spot_calls.append(symbol)
        return []


class ExecutionRecordingClient:
    dry_run = True

    def __init__(self) -> None:
        self.perp_orders: list[BingXPerpOrderRequest] = []

    async def fetch_contract_metadata(self, display_name: str) -> Any:
        from backend.layer_1_data.datos.bingx_client import BingXContractMetadata

        return BingXContractMetadata(
            display_name=display_name,
            api_symbol=display_name,
            tick_size=0.01,
            step_size=0.01,
            min_qty=0.01,
            min_notional=1.0,
            max_leverage=20,
            quantity_precision=2,
            price_precision=2,
        )

    async def fetch_perp_balance(self) -> dict[str, Any]:
        return {
            "availableBalance": 1000.0,
            "equity": 1000.0,
        }

    async def set_leverage_perp(
        self, symbol: str, leverage: int, *, side: str = "BOTH"
    ) -> dict[str, Any]:
        return {"symbol": symbol, "leverage": leverage, "side": side}

    async def set_margin_type_perp(self, symbol: str, margin_type: str) -> dict[str, Any]:
        return {"symbol": symbol, "marginType": margin_type}

    async def place_order_perp(self, order: BingXPerpOrderRequest) -> BingXOrderResponse:
        self.perp_orders.append(order)
        return BingXOrderResponse(
            ok=True,
            dry_run=True,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            requested_qty=order.quantity,
            requested_quote_qty=None,
            price=order.price,
            venue_order_id=None,
            client_order_id=order.client_order_id,
            raw={"intercepted": True},
        )


class FakeAccountState:
    def to_dict(self) -> dict[str, Any]:
        return {
            "open_positions": [
                {"symbol": "AAPL-USDT", "size": 0.25, "mark_price": 200.0},
                {"symbol": "GOOGL-USDT", "notional_usdt": 15.0},
            ],
            "realized_pnl_today_usdt": -1.25,
        }


class FakeAccountService:
    async def get_account_state(self) -> FakeAccountState:
        return FakeAccountState()


@pytest.mark.asyncio
async def test_default_universe_contains_only_synthetic_stocks() -> None:
    assert DEFAULT_UNIVERSE
    assert all(
        not symbol.startswith(("BTC-", "ETH-", "SOL-", "LINK-")) for symbol in DEFAULT_UNIVERSE
    )


def test_synthetic_stock_symbols_accepts_ncsk_vst_api_tickers() -> None:
    symbols = _synthetic_stock_symbols(
        ("BTC-USDT", "NCSKPLTR2USD-USDT", "PLTR-USDT", "NCSKPLTR2USD-USDT")
    )
    assert symbols == ("NCSKPLTR2USD-USDT", "PLTR-USDT")


@pytest.mark.asyncio
async def test_scan_filters_explicit_crypto_symbols() -> None:
    client = RouteRecordingClient()
    service = BingXBotService(client=client, universe=("BTC-USDT", "AAPL-USDT"))  # type: ignore[arg-type]

    signals = await service.scan(("BTC-USDT", "AAPL-USDT"))

    assert client.perp_calls == ["AAPL-USDT"]
    assert client.perp_limits == [2000]
    assert client.spot_calls == []
    assert signals[0].snapshot.bars == 2000
    assert [signal.symbol for signal in signals] == ["AAPL-USDT"]


def _tf(direction: str, ok: bool = True) -> MarketScannerTimeframeSignal:
    return MarketScannerTimeframeSignal(
        timeframe="5m",
        ok=ok,
        direction=direction,  # type: ignore[arg-type]
        label="buy" if direction == "bullish" else "sell" if direction == "bearish" else "neutral",
        score=78.0 if direction == "bullish" else 22.0 if direction == "bearish" else 50.0,
        confidence=0.8,
    )


def _row(
    *,
    symbol: str = "AAPL",
    score: float = 86.0,
    signals: dict[str, MarketScannerTimeframeSignal] | None = None,
    vetoes: list[str] | None = None,
    funding_suitability: str = "allow",
    funding_reason_codes: list[str] | None = None,
    recommended_size_multiplier: float | None = 1.0,
) -> MarketScannerRow:
    row_signals = signals or {
        "5m": _tf("bullish"),
        "15m": _tf("bullish"),
        "1h": _tf("bullish"),
        "1D": _tf("bullish"),
    }
    return MarketScannerRow(
        symbol=symbol,
        price=100.0,
        signals=row_signals,
        scanner_score=score,
        setup_grade="A",
        direction="bullish",
        vetoes=vetoes or [],
        module_signals={
            "technical": ScannerModuleSignal(
                module="technical",
                label="buy",
                score=78.0,
                confidence=0.8,
                engine_count=1,
                available_count=1,
            )
        },
        score_ci_low=80.0,
        score_ci_high=90.0,
        funding_suitability=funding_suitability,
        funding_reason_codes=funding_reason_codes or [],
        recommended_size_multiplier=recommended_size_multiplier,
    )


@pytest.mark.parametrize(
    ("bingx_symbol", "scanner_symbol"),
    [
        ("PLTR-USDT", "PLTR"),
        ("MSFTON/USDT", "MSFT"),
    ],
)
def test_normalizes_bingx_symbols_for_scanner(bingx_symbol: str, scanner_symbol: str) -> None:
    assert _normalize_bingx_symbol_for_scanner(bingx_symbol) == scanner_symbol


@pytest.mark.asyncio
async def test_filter_signals_builds_scanner_request_and_allows_aligned_signal() -> None:
    scanner = FakeScanner([_row()])
    service = BingXBotService(scanner_service=scanner)

    decisions = await service.filter_signals([_signal()])

    assert decisions[0].suitability == "ALLOW"
    assert decisions[0].reason_codes == ()
    assert scanner.requests is not None
    request = scanner.requests[0]
    assert request.universe == "custom"
    assert request.symbols == ["AAPL"]
    assert request.timeframes == ["5m", "15m", "1h", "1D"]
    assert request.direction == "both"
    assert request.include_deep_metrics is True
    assert request.include_funding_gate is True
    assert request.filters.include_vetoed is True
    assert request.customization.enabled_modules == ["technical", "probabilistic", "options_gex"]
    assert request.customization.primary_timeframe == "15m"


@pytest.mark.asyncio
async def test_filter_signals_can_skip_scanner_confirmation_for_lightweight_ui_scan() -> None:
    scanner = FakeScanner([_row()])
    service = BingXBotService(scanner_service=scanner)

    decisions = await service.filter_signals(
        [_signal()],
        use_scanner_confirmation=False,
    )

    assert decisions[0].suitability == "ALLOW"
    assert decisions[0].provider == "heuristic_vsa"
    assert scanner.requests is None


@pytest.mark.asyncio
async def test_filter_signals_blocks_when_daily_trend_opposes_direction() -> None:
    scanner = FakeScanner(
        [
            _row(
                signals={
                    "5m": _tf("bullish"),
                    "15m": _tf("bullish"),
                    "1h": _tf("bullish"),
                    "1D": _tf("bearish"),
                }
            )
        ]
    )
    service = BingXBotService(scanner_service=scanner)

    decisions = await service.filter_signals([_signal()])

    assert decisions[0].suitability == "BLOCK"
    assert REASON_SCANNER_DAILY_OPPOSES in decisions[0].reason_codes


@pytest.mark.asyncio
async def test_filter_signals_blocks_when_intraday_alignment_is_too_low() -> None:
    scanner = FakeScanner(
        [
            _row(
                signals={
                    "5m": _tf("bullish"),
                    "15m": _tf("bearish"),
                    "1h": _tf("bearish"),
                    "1D": _tf("bullish"),
                }
            )
        ]
    )
    service = BingXBotService(scanner_service=scanner)

    decisions = await service.filter_signals([_signal()])

    assert decisions[0].suitability == "BLOCK"
    assert REASON_SCANNER_INTRADAY_NOT_ALIGNED in decisions[0].reason_codes


@pytest.mark.asyncio
async def test_filter_signals_blocks_when_scanner_veto_is_present() -> None:
    scanner = FakeScanner([_row(vetoes=["VETO_ILLIQUID"])])
    service = BingXBotService(scanner_service=scanner)

    decisions = await service.filter_signals([_signal()])

    assert decisions[0].suitability == "BLOCK"
    assert REASON_SCANNER_VETO_PRESENT in decisions[0].reason_codes


@pytest.mark.asyncio
async def test_filter_signals_blocks_when_scanner_score_is_too_low() -> None:
    scanner = FakeScanner([_row(score=30.0)])
    service = BingXBotService(scanner_service=scanner)

    decisions = await service.filter_signals([_signal()])

    assert decisions[0].suitability == "BLOCK"
    assert REASON_SCANNER_SCORE_TOO_LOW in decisions[0].reason_codes


@pytest.mark.asyncio
async def test_filter_signals_blocks_when_scanner_unavailable_or_missing_row() -> None:
    failing = BingXBotService(scanner_service=FakeScanner([], error=RuntimeError("boom")))
    missing = BingXBotService(scanner_service=FakeScanner([]))

    failing_decisions = await failing.filter_signals([_signal()])
    missing_decisions = await missing.filter_signals([_signal()])

    assert failing_decisions[0].suitability == "BLOCK"
    assert failing_decisions[0].reason_codes == (REASON_SCANNER_UNAVAILABLE,)
    assert missing_decisions[0].suitability == "BLOCK"
    assert missing_decisions[0].reason_codes == (REASON_SCANNER_UNAVAILABLE,)


@pytest.mark.asyncio
async def test_filter_signals_sizes_down_when_funding_gate_says_size_down() -> None:
    scanner = FakeScanner(
        [
            _row(
                funding_suitability="size_down",
                funding_reason_codes=[REASON_WEAK_EDGE],
                recommended_size_multiplier=0.5,
            )
        ]
    )
    service = BingXBotService(
        scanner_service=scanner,
        risk_policy=BingXRiskPolicy(equity_usdt=10.0, notional_per_trade_usdt=10.0),
    )
    signal = _signal(lob_analysis=_lob_analysis())

    decisions = await service.filter_signals([signal])
    plans = service.build_order_plans([signal], decisions)

    assert decisions[0].suitability == "SIZE_DOWN"
    assert REASON_WEAK_EDGE in decisions[0].reason_codes
    assert plans[0].authorized is True
    assert plans[0].notional_usdt == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_filter_signals_forwards_customization_to_scanner_request() -> None:
    """Custom weight matrix and modules are forwarded verbatim to MarketScannerRequest."""
    scanner = FakeScanner([_row()])
    service = BingXBotService(scanner_service=scanner)

    custom = ScannerCustomization(
        enabled_modules=["technical", "options_gex"],
        weight_matrix={"vsa": {"5m": 2.0, "15m": 1.5}},
        primary_timeframe="5m",
    )
    await service.filter_signals([_signal()], customization=custom)

    assert scanner.requests is not None
    req = scanner.requests[0]
    assert req.customization is custom
    assert req.customization.enabled_modules == ["technical", "options_gex"]
    assert req.customization.primary_timeframe == "5m"


@pytest.mark.asyncio
async def test_filter_signals_uses_default_customization_when_none_provided() -> None:
    """When no customization is given the default institutional preset is applied."""
    scanner = FakeScanner([_row()])
    service = BingXBotService(scanner_service=scanner)

    await service.filter_signals([_signal()])

    assert scanner.requests is not None
    req = scanner.requests[0]
    assert req.customization.enabled_modules == ["technical", "probabilistic", "options_gex"]
    assert req.customization.primary_timeframe == "15m"


# ── L2 (lob_analysis) wiring ─────────────────────────────────────────────────


class L2RecordingClient(RouteRecordingClient):
    """Extends the route-recording client with an L2 order book stub."""

    def __init__(
        self,
        *,
        l2_payload: dict[str, Any] | None = None,
        raise_l2: Exception | None = None,
    ) -> None:
        super().__init__()
        self.l2_calls: list[tuple[str, dict[str, Any]]] = []
        self._l2_payload = l2_payload
        self._raise_l2 = raise_l2

    async def fetch_order_book_perp(
        self,
        symbol: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        self.l2_calls.append((symbol, {"limit": limit}))
        if self._raise_l2 is not None:
            raise self._raise_l2
        return self._l2_payload or {"bids": [["100", "5"]], "asks": [["101", "5"]]}


@pytest.mark.asyncio
async def test_scan_attaches_lob_analysis_for_stock_perp_symbols() -> None:
    """Synthetic stock perps (AAPL-USDT) must carry a populated lob_analysis."""
    client = L2RecordingClient(
        l2_payload={
            "bids": [["100", "600"], ["99.5", "500"]],
            "asks": [["100.1", "600"], ["100.5", "500"]],
        }
    )
    service = BingXBotService(client=client, universe=("AAPL-USDT",))  # type: ignore[arg-type]

    signals = await service.scan()

    assert client.l2_calls == [("AAPL-USDT", {"limit": 20})]
    assert len(signals) == 1
    sig = signals[0]
    assert sig.lob_analysis is not None
    assert sig.lob_analysis.ok is True
    assert sig.lob_analysis.data_quality_score is not None
    assert 0.0 <= sig.lob_analysis.data_quality_score <= 1.0


@pytest.mark.asyncio
async def test_scan_excludes_crypto_symbols_from_lob_analysis() -> None:
    """Crypto symbols are not analyzed by the synthetic-stock-only bot."""
    client = L2RecordingClient()
    service = BingXBotService(client=client, universe=("BTC-USDT",))  # type: ignore[arg-type]

    signals = await service.scan()

    assert client.l2_calls == []
    assert signals == []


@pytest.mark.asyncio
async def test_scan_lob_analysis_unavailable_does_not_break_signal() -> None:
    """When the L2 endpoint raises, the signal still flows with a degraded analysis."""
    client = L2RecordingClient(raise_l2=RuntimeError("upstream timeout"))
    service = BingXBotService(client=client, universe=("AAPL-USDT",))  # type: ignore[arg-type]

    signals = await service.scan()

    assert len(signals) == 1
    sig = signals[0]
    # Bridge surfaces the error explicitly — never silently mocked.
    assert sig.lob_analysis is not None
    assert sig.lob_analysis.ok is False
    assert sig.lob_analysis.error is not None
    assert sig.lob_analysis.data_quality_score is None


@pytest.mark.asyncio
async def test_scan_lob_analysis_is_jsonable_via_to_dict() -> None:
    """``BingXSignal.to_dict`` must serialize ``lob_analysis`` (pydantic model)."""
    client = L2RecordingClient(
        l2_payload={
            "bids": [["100", "600"]],
            "asks": [["100.1", "600"]],
        }
    )
    service = BingXBotService(client=client, universe=("AAPL-USDT",))  # type: ignore[arg-type]

    signals = await service.scan()
    payload = signals[0].to_dict()

    assert payload["lob_analysis"] is not None
    assert payload["lob_analysis"]["ok"] is True
    assert "data_quality_score" in payload["lob_analysis"]
    # ``to_dict`` for a crypto signal would put None here — assert structural key.
    assert "source" in payload["lob_analysis"]


# ── L2 execution-quality gate (ExecutionQualityPolicy) ───────────────────────


def _lob_result(imbalance: float = 0.0) -> LOBDynamicsResult:
    return LOBDynamicsResult(
        timestamp=1_700_000_000_000,
        imbalance_rho=imbalance,
        ctr_bid=0.0,
        ctr_ask=0.0,
        spoofing_state=SpoofingState.NORMAL,
    )


def _lob_analysis(
    *,
    ok: bool = True,
    spread: float | None = 0.1,
    bid_depth: float | None = 1_000.0,
    ask_depth: float | None = 1_000.0,
    mid_price: float | None = 100.0,
    imbalance: float = 0.0,
    error: str | None = None,
) -> LOBDynamicsAnalysis:
    return LOBDynamicsAnalysis(
        ok=ok,
        error=error,
        source="bingx_l2_snapshot_rest" if ok else "bingx_l2_unavailable",
        result=_lob_result(imbalance) if ok else None,
        data_quality_score=0.9 if ok else None,
        spread=spread,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        mid_price=mid_price,
    )


def _allow_decision(symbol: str) -> FilterDecision:
    return FilterDecision(
        symbol=symbol,
        suitability="ALLOW",
        probability=0.9,
        threshold=0.55,
        provider="test",
        reason_codes=(),
    )


def test_evaluate_l2_execution_quality_passes_for_crypto() -> None:
    """Non-stock-perp symbols bypass the L2 gate entirely (crypto exec quality
    is enforced by the venue, not by this gate)."""
    allowed, reasons = _evaluate_l2_execution_quality(
        None, ExecutionQualityPolicy(), is_stock_perp=False
    )
    assert allowed is True
    assert reasons == ()


def test_evaluate_l2_execution_quality_blocks_when_l2_unavailable_for_stock_perp() -> None:
    """Stock perps with no/failed L2 analysis must fail closed."""
    none_allowed, none_reasons = _evaluate_l2_execution_quality(
        None, ExecutionQualityPolicy(), is_stock_perp=True
    )
    failed_allowed, failed_reasons = _evaluate_l2_execution_quality(
        _lob_analysis(ok=False, spread=None, bid_depth=None, ask_depth=None, mid_price=None),
        ExecutionQualityPolicy(),
        is_stock_perp=True,
    )

    assert none_allowed is False
    assert none_reasons == (REASON_L2_UNAVAILABLE,)
    assert failed_allowed is False
    assert failed_reasons == (REASON_L2_UNAVAILABLE,)


def test_evaluate_l2_execution_quality_blocks_when_spread_exceeds_threshold() -> None:
    """A spread above ``max_spread_pct`` of mid blocks execution."""
    # mid=100, spread=1.0 → 1.0% spread, policy max=0.5% → blocked.
    analysis = _lob_analysis(spread=1.0, mid_price=100.0)
    allowed, reasons = _evaluate_l2_execution_quality(
        analysis, ExecutionQualityPolicy(max_spread_pct=0.5), is_stock_perp=True
    )

    assert allowed is False
    assert REASON_L2_SPREAD_TOO_WIDE in reasons


def test_evaluate_l2_execution_quality_blocks_when_depth_too_thin() -> None:
    """Either side below its depth floor blocks execution."""
    thin_bids = _lob_analysis(bid_depth=10.0, ask_depth=1_000.0)
    thin_asks = _lob_analysis(bid_depth=1_000.0, ask_depth=10.0)
    policy = ExecutionQualityPolicy(min_bid_depth_usdt=500.0, min_ask_depth_usdt=500.0)

    bid_blocked, bid_reasons = _evaluate_l2_execution_quality(thin_bids, policy, is_stock_perp=True)
    ask_blocked, ask_reasons = _evaluate_l2_execution_quality(thin_asks, policy, is_stock_perp=True)

    assert bid_blocked is False
    assert REASON_L2_DEPTH_TOO_THIN in bid_reasons
    assert ask_blocked is False
    assert REASON_L2_DEPTH_TOO_THIN in ask_reasons


def test_evaluate_l2_execution_quality_blocks_when_imbalance_extreme() -> None:
    """An |imbalance_rho| above the ceiling blocks execution (when policy sets it)."""
    skewed = _lob_analysis(imbalance=0.9)  # |rho| = 0.9 > 0.8
    policy = ExecutionQualityPolicy(max_imbalance_abs=0.8)
    allowed, reasons = _evaluate_l2_execution_quality(skewed, policy, is_stock_perp=True)

    assert allowed is False
    assert REASON_L2_IMBALANCE_EXTREME in reasons


def test_evaluate_l2_execution_quality_imbalance_disabled_by_default() -> None:
    """``max_imbalance_abs=None`` (the default) never fires the imbalance code."""
    skewed = _lob_analysis(imbalance=0.95)
    allowed, reasons = _evaluate_l2_execution_quality(
        skewed, ExecutionQualityPolicy(), is_stock_perp=True
    )

    assert allowed is True
    assert REASON_L2_IMBALANCE_EXTREME not in reasons


def test_evaluate_l2_execution_quality_passes_when_book_is_healthy() -> None:
    """A tight, deep, balanced book passes the gate."""
    healthy = _lob_analysis(
        spread=0.05, bid_depth=2_000.0, ask_depth=2_000.0, mid_price=100.0, imbalance=0.1
    )
    allowed, reasons = _evaluate_l2_execution_quality(
        healthy,
        ExecutionQualityPolicy(max_imbalance_abs=0.8),
        is_stock_perp=True,
    )

    assert allowed is True
    assert reasons == ()


def test_build_order_plans_blocks_stock_perp_when_spread_too_wide() -> None:
    """Wide-spread L2 vetoes the order even when the filter decision is ALLOW."""
    service = BingXBotService(
        risk_policy=BingXRiskPolicy(equity_usdt=10.0, notional_per_trade_usdt=10.0),
        execution_quality_policy=ExecutionQualityPolicy(max_spread_pct=0.5),
    )
    # mid=100, spread=1.0 → 1.0% > 0.5%
    bad_l2 = _lob_analysis(spread=1.0, mid_price=100.0)
    signal = BingXSignal(
        symbol="AAPL-USDT",
        direction="LONG",
        score=1.0,
        horizon="1h",
        reason_codes=(),
        snapshot=_snapshot("AAPL-USDT"),
        timestamp="2026-05-19T12:00:00Z",
        lob_analysis=bad_l2,
    )

    plans = service.build_order_plans([signal], [_allow_decision("AAPL-USDT")])

    assert plans[0].authorized is False
    assert REASON_L2_SPREAD_TOO_WIDE in plans[0].reason_codes
    assert plans[0].notional_usdt == 0.0


def test_build_order_plans_blocks_stock_perp_when_l2_unavailable() -> None:
    """A stock-perp ALLOW with no L2 evidence must fail closed."""
    service = BingXBotService(
        risk_policy=BingXRiskPolicy(equity_usdt=10.0, notional_per_trade_usdt=10.0),
    )
    signal = BingXSignal(
        symbol="AAPL-USDT",
        direction="LONG",
        score=1.0,
        horizon="1h",
        reason_codes=(),
        snapshot=_snapshot("AAPL-USDT"),
        timestamp="2026-05-19T12:00:00Z",
        lob_analysis=None,
    )

    plans = service.build_order_plans([signal], [_allow_decision("AAPL-USDT")])

    assert plans[0].authorized is False
    assert REASON_L2_UNAVAILABLE in plans[0].reason_codes


def test_build_order_plans_authorizes_stock_perp_when_l2_healthy() -> None:
    """ALLOW + healthy L2 produces an authorized plan."""
    service = BingXBotService(
        risk_policy=BingXRiskPolicy(equity_usdt=10.0, notional_per_trade_usdt=10.0),
        execution_quality_policy=ExecutionQualityPolicy(max_spread_pct=1.0),
    )
    good_l2 = _lob_analysis(spread=0.05, bid_depth=2_000.0, ask_depth=2_000.0, mid_price=100.0)
    signal = BingXSignal(
        symbol="AAPL-USDT",
        direction="LONG",
        score=1.0,
        horizon="1h",
        reason_codes=(),
        snapshot=_snapshot("AAPL-USDT"),
        timestamp="2026-05-19T12:00:00Z",
        lob_analysis=good_l2,
    )

    plans = service.build_order_plans([signal], [_allow_decision("AAPL-USDT")])

    assert plans[0].authorized is True
    assert REASON_L2_UNAVAILABLE not in plans[0].reason_codes
    assert REASON_L2_SPREAD_TOO_WIDE not in plans[0].reason_codes
    assert REASON_L2_DEPTH_TOO_THIN not in plans[0].reason_codes


def test_build_order_plans_ignores_l2_gate_for_crypto_even_when_l2_missing() -> None:
    """Crypto ALLOW must not depend on L2 — the gate is stock-perp only."""
    service = BingXBotService(
        risk_policy=BingXRiskPolicy(equity_usdt=10.0, notional_per_trade_usdt=10.0),
        execution_quality_policy=ExecutionQualityPolicy(max_spread_pct=0.01),  # absurdly strict
    )
    signal = BingXSignal(
        symbol="BTC-USDT",
        direction="LONG",
        score=1.0,
        horizon="1h",
        reason_codes=(),
        snapshot=_snapshot("BTC-USDT"),
        timestamp="2026-05-19T12:00:00Z",
        lob_analysis=None,  # crypto never carries L2 → must not be blocked
    )

    plans = service.build_order_plans([signal], [_allow_decision("BTC-USDT")])

    assert plans[0].authorized is True
    assert REASON_L2_UNAVAILABLE not in plans[0].reason_codes


def test_status_exposes_l2_execution_quality_reason_codes_and_policy() -> None:
    """The UI must be able to discover the L2 reason codes + the active policy."""
    service = BingXBotService(
        execution_quality_policy=ExecutionQualityPolicy(
            max_spread_pct=0.3,
            min_bid_depth_usdt=750.0,
            min_ask_depth_usdt=750.0,
            max_imbalance_abs=0.7,
        )
    )

    status = service.status()
    reasons = set(status["reason_codes"])
    policy = status["execution_quality_policy"]

    assert REASON_L2_UNAVAILABLE in reasons
    assert REASON_L2_SPREAD_TOO_WIDE in reasons
    assert REASON_L2_DEPTH_TOO_THIN in reasons
    assert REASON_L2_IMBALANCE_EXTREME in reasons
    assert policy["max_spread_pct"] == pytest.approx(0.3)
    assert policy["min_bid_depth_usdt"] == pytest.approx(750.0)
    assert policy["min_ask_depth_usdt"] == pytest.approx(750.0)
    assert policy["max_imbalance_abs"] == pytest.approx(0.7)


# ─── decide_candidates — multi-module decision engine integration ─────────────


def _decision_engine_analysis(
    *,
    market_type: str = "stock_perp",
    l2_available: bool = True,
    predictive_bias: str = "LONG",
    predictive_confidence: float = 0.75,
) -> Any:
    """Build a ``BingXCandidateAnalysis`` covering all engines as available.

    Local helper — full fidelity isn't required here because the bot-service
    test only verifies that the engine is invoked correctly and that
    ``mode`` follows ``self.dry_run``. The decision engine itself is
    exhaustively tested in ``test_bingx_decision_engine.py``.
    """
    from backend.services.bingx_candidate_analysis import (
        BingXCandidateAnalysis,
        BingXL2Block,
        BingXOptionsBlock,
        BingXPredictiveBlock,
        BingXTechnicalBlock,
        BingXUnderlyingBlock,
        BingXVenueBlock,
    )

    venue = BingXVenueBlock(
        venue_symbol="GOOGL-USDT",
        status="available",
        source="bingx_perp_klines",
        venue_ta={
            "bars_count": 60,
            "last_price": 180.0,
            "trend": "bullish",
            "rsi_14": 58.0,
            "ema_9": 180.5,
            "ema_21": 179.0,
        },
    )
    underlying = BingXUnderlyingBlock(
        underlying_symbol="GOOGL",
        market_type=market_type,
        ohlcv_status="available",
        source="fmp",
    )
    options = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.85,
        metrics={
            "status": "available",
            "metrics": {
                "dealer_bias": "BULLISH",
                "call_wall": 185.0,
                "value_area": {
                    "val": 190.0 if predictive_bias == "LONG" else 160.0,
                    "vah": 200.0 if predictive_bias == "LONG" else 170.0,
                },
            },
        },
    )
    technical = BingXTechnicalBlock(
        status="available",
        source="fmp",
        quality_score=0.85,
        metrics={"ok": True, "bars_used": 200},
        venue_technical={
            "status": "available",
            "technical_quality_score": 0.85,
            "summary": {
                "trend_direction": "bullish" if predictive_bias == "LONG" else "bearish",
                "smc_bias": "BULLISH" if predictive_bias == "LONG" else "BEARISH",
                "vsa_signal": "STRONG_BUY" if predictive_bias == "LONG" else "STRONG_SELL",
                "fvg_state": "bullish_dominant",
                "volume_profile_bias": "bullish",
                "composite_score": 0.7,
                "bars_used": 40,
            },
            "payload": {
                "volume_profile": {
                    "val": 190.0 if predictive_bias == "LONG" else 160.0,
                    "vah": 200.0 if predictive_bias == "LONG" else 170.0,
                }
            },
        },
    )
    predictive = BingXPredictiveBlock(
        status="available",
        source="meta_signal",
        quality_score=0.7,
        signal={
            "directional_bias": predictive_bias,
            "probability_long": 0.7 if predictive_bias == "LONG" else 0.2,
            "probability_short": 0.2 if predictive_bias == "LONG" else 0.7,
            "confidence": predictive_confidence,
            "horizon": "intraday",
            "source": "meta_signal",
            "quality_score": 0.7,
            "reason_codes": [],
        },
    )
    l2 = (
        BingXL2Block(
            status="available",
            source="bingx_l2_snapshot_rest",
            quality_score=0.78,
            lob_analysis={
                "ok": True,
                "source": "bingx_l2_snapshot_rest",
                "spread": 0.01,
                "mid_price": 100.0,
                "bid_depth": 20000.0,
                "ask_depth": 20000.0,
                "hhi_concentration": 0.05,
            },
        )
        if l2_available
        else BingXL2Block(
            status="unavailable",
            source="bingx_l2_unavailable",
            reason="snapshot_empty",
        )
    )
    return BingXCandidateAnalysis(
        venue_symbol="GOOGL-USDT",
        underlying_symbol="GOOGL",
        market_type=market_type,
        venue=venue,
        underlying=underlying,
        options=options,
        technical=technical,
        predictive=predictive,
        l2=l2,
    )


def test_decide_candidates_dry_run_default_allows_without_l2_block() -> None:
    """When ``dry_run=True`` is forwarded as the mode, the L2 live gate is OFF
    so an equity perp without L2 still produces a non-BLOCK decision."""
    service = BingXBotService()  # default dry_run=True
    assert service.dry_run is True

    analysis = _decision_engine_analysis(l2_available=False)
    decisions = service.decide_candidates([analysis])

    assert len(decisions) == 1
    assert decisions[0].decision != "BLOCK"
    # The reason for skipping the live L2 gate is implicit (not surfaced) —
    # callers see the dry_run mode via the service ``dry_run`` flag.


def test_decide_candidates_allows_when_all_modules_align() -> None:
    """Full confluence + L2 available + live mode → ALLOW LONG."""
    service = BingXBotService()  # dry_run=True keeps the L2 gate off so we
    # also exercise the ALLOW path without needing a live client.
    analysis = _decision_engine_analysis(
        l2_available=True, predictive_bias="LONG", predictive_confidence=0.80
    )
    decisions = service.decide_candidates([analysis])

    assert len(decisions) == 1
    assert decisions[0].decision == "ALLOW"
    assert decisions[0].direction == "LONG"


def test_decide_candidates_respects_explicit_mode_override() -> None:
    """Caller passing ``mode="live"`` overrides the auto-derivation from dry_run."""
    from backend.services.bingx_decision_engine import REASON_L2_REQUIRED_FOR_EQUITY_LIVE

    service = BingXBotService()  # dry_run=True
    analysis = _decision_engine_analysis(l2_available=False)

    decisions = service.decide_candidates([analysis], mode="live")
    assert decisions[0].decision == "BLOCK"
    assert REASON_L2_REQUIRED_FOR_EQUITY_LIVE in decisions[0].reason_codes


def test_decide_candidates_batch_preserves_order() -> None:
    service = BingXBotService()
    a1 = _decision_engine_analysis(predictive_bias="LONG")
    a2 = _decision_engine_analysis(predictive_bias="SHORT")
    decisions = service.decide_candidates([a1, a2])
    assert [d.direction for d in decisions] == ["LONG", "SHORT"]


@pytest.mark.asyncio
async def test_run_cycle_uses_candidate_decision_risk_path(monkeypatch) -> None:
    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("GOOGL-USDT",))  # type: ignore[arg-type]
    calls: list[str] = []

    async def _fake_build_candidate_analysis(symbol: str, **_: Any) -> Any:
        calls.append(symbol)
        return _decision_engine_analysis(
            market_type="stock_perp",
            l2_available=True,
            predictive_bias="LONG",
            predictive_confidence=0.80,
        )

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis",
        _fake_build_candidate_analysis,
        raising=False,
    )

    result = await service.run_cycle()

    assert calls == ["GOOGL-USDT"]
    assert len(result.analyses) == 1
    assert result.engine_decisions[0].decision == "ALLOW"
    assert result.risk_decisions[0].authorized is True
    assert len(client.perp_orders) == 3  # entry + safety SL + safety TP
    entry_order = client.perp_orders[0]
    assert entry_order.symbol == "GOOGL-USDT"
    assert entry_order.side == "BUY"
    assert entry_order.position_side == "LONG"
    assert entry_order.client_order_id is not None
    assert entry_order.client_order_id.startswith("bingxbot_")
    # SL/TP are placed as separate STOP_MARKET / TAKE_PROFIT_MARKET orders
    assert entry_order.stop_loss_price is None
    assert entry_order.take_profit_price is None

    scale_out_orders = client.perp_orders[1:]
    assert all(o.reduce_only for o in scale_out_orders)
    assert all(o.symbol == "GOOGL-USDT" for o in scale_out_orders)
    assert [o.order_type for o in scale_out_orders] == [
        "STOP_MARKET",
        "TAKE_PROFIT_MARKET",
    ]
    assert service.risk_desk.state.open_positions["GOOGL-USDT"] == pytest.approx(10.0)
    assert result.to_dict()["candidate_analyses"][0]["venue_symbol"] == "GOOGL-USDT"


@pytest.mark.asyncio
async def test_run_cycle_size_down_halves_notional(monkeypatch) -> None:
    from backend.services.bingx_decision_engine import BingXDecision, BingXModuleScores

    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("GOOGL-USDT",))  # type: ignore[arg-type]
    analysis = _decision_engine_analysis(
        market_type="stock_perp",
        l2_available=True,
        predictive_bias="LONG",
        predictive_confidence=0.80,
    )

    async def _fake_build_candidate_analysis(_symbol: str, **_: Any) -> Any:
        return analysis

    def _fake_decide_candidates(*_: Any, **__: Any) -> list[BingXDecision]:
        return [
            BingXDecision(
                symbol="GOOGL-USDT",
                decision="SIZE_DOWN",
                direction="LONG",
                confidence=0.7,
                score_total=0.6,
                module_scores=BingXModuleScores(
                    venue=1.0,
                    technical=1.0,
                    options=1.0,
                    predictive=1.0,
                    l2=1.0,
                    risk=0.6,
                ),
                reason_codes=["weak_edge"],
                market_type="stock_perp",
            )
        ]

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis",
        _fake_build_candidate_analysis,
        raising=False,
    )
    monkeypatch.setattr(service, "decide_candidates", _fake_decide_candidates)

    result = await service.run_cycle()

    assert result.order_intents[0].notional_usdt == pytest.approx(5.0)
    assert client.perp_orders[0].quantity == pytest.approx(round(5.0 / 180.0, 2))


@pytest.mark.asyncio
async def test_risk_state_hydrates_from_account_positions() -> None:
    service = BingXBotService(
        client=ExecutionRecordingClient(),  # type: ignore[arg-type]
        account_service=FakeAccountService(),  # type: ignore[arg-type]
    )

    await service._hydrate_risk_state_from_account()

    assert service.risk_desk.state.open_positions == {
        "AAPL-USDT": pytest.approx(50.0),
        "GOOGL-USDT": pytest.approx(15.0),
    }
    assert service.risk_desk.state.realized_pnl_today == pytest.approx(-1.25)


@pytest.mark.asyncio
async def test_place_scale_out_orders_with_tp4() -> None:
    client = ExecutionRecordingClient()
    service = BingXBotService(client=client)  # type: ignore[arg-type]

    from backend.services.bingx_risk_desk import OrderIntent

    intent = OrderIntent(
        venue_symbol="AAPL-USDT",
        side="BUY",
        position_side="LONG",
        quantity=10.0,
        leverage=1,
        entry_type="MARKET",
        stop_loss=None,
        take_profit=None,
        client_order_id="test1",
        reduce_only=False,
        cycle_id="test_cycle_1",
        notional_usdt=10.0,
        spread_pct=0.001,
        l2_quality_score=0.9,
        provider_health="ok",
    )

    from backend.services.bingx_candidate_analysis import (
        BingXCandidateAnalysis,
        BingXOptionsBlock,
        BingXTechnicalBlock,
    )
    from backend.tests.services.test_bingx_decision_engine import (
        _l2,
        _predictive,
        _underlying,
        _venue,
    )

    opts = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "put_wall": 170.0,
                "gamma_flip": 180.0,
                "call_wall": 190.0,
                "implied_percentile_99": 200.0,
            },
        },
    )
    tech = BingXTechnicalBlock(
        status="available",
        source="fmp",
        quality_score=0.8,
        metrics={"ok": True, "rsi_14": 55.0},
        venue_technical={
            "status": "available",
            "technical_quality_score": 0.8,
            "summary": {
                "trend_direction": "bullish",
                "smc_bias": "BULLISH",
            },
        },
    )

    analysis = BingXCandidateAnalysis(
        venue_symbol="AAPL-USDT",
        underlying_symbol="AAPL",
        market_type="stock_perp",
        venue=_venue(),
        underlying=_underlying(),
        options=opts,
        technical=tech,
        predictive=_predictive(),
        l2=_l2(),
    )

    await service._place_scale_out_orders(
        intent=intent,
        quantity=10.0,
        entry_price=175.0,
        analysis=analysis,
        contract_metadata=None,
    )

    assert len(client.perp_orders) == 2
    order_types = [o.order_type for o in client.perp_orders]
    assert "STOP_MARKET" in order_types
    assert "TAKE_PROFIT_MARKET" in order_types

    sl_order = [o for o in client.perp_orders if o.order_type == "STOP_MARKET"][0]
    tp_order = [o for o in client.perp_orders if o.order_type == "TAKE_PROFIT_MARKET"][0]

    assert sl_order.stop_price == pytest.approx(87.5)
    assert sl_order.quantity == 10.0
    assert tp_order.stop_price == pytest.approx(1050.0)
    assert tp_order.quantity == 10.0


# ── Execution-spam protection tests ─────────────────────────────────────────


def _authorized_decision(
    symbol: str = "TEST-USDT",
    side: str = "BUY",
    position_side: str = "LONG",
) -> Any:
    """Build a minimal authorized RiskDeskDecision for testing spam filters."""
    from backend.services.bingx_risk_desk import OrderIntent, RiskDeskDecision

    intent = OrderIntent(
        venue_symbol=symbol,
        side=side,
        position_side=position_side,
        quantity=0.1,
        leverage=1,
        entry_type="MARKET",
        stop_loss=None,
        take_profit=None,
        client_order_id=None,
        reduce_only=False,
        cycle_id="test_spam_cycle",
        notional_usdt=10.0,
        spread_pct=None,
        l2_quality_score=None,
        provider_health="ok",
    )
    return RiskDeskDecision(
        authorized=True,
        intent=intent,
        idempotency_key="test_spam_key",
        reason_codes=[],
        adjusted_quantity=0.1,
        adjusted_entry_price=100.0,
    )


@pytest.mark.asyncio
async def test_exec_spam_position_already_open_blocks() -> None:
    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("TEST-USDT",))  # type: ignore[arg-type]
    # Simulate an existing open position
    service._risk_desk.state.open_positions["TEST-USDT"] = 10.0

    decision = _authorized_decision()
    results = await service.execute_risk_decisions([decision])

    assert results == []
    assert len(client.perp_orders) == 0  # no real order placed


@pytest.mark.asyncio
async def test_exec_spam_cooldown_blocks() -> None:
    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("TEST-USDT",))  # type: ignore[arg-type]
    # Simulate a recent execution
    service._last_execution["TEST-USDT"] = datetime.now(UTC)

    decision = _authorized_decision()
    results = await service.execute_risk_decisions([decision])

    assert results == []
    assert len(client.perp_orders) == 0


@pytest.mark.asyncio
async def test_exec_spam_allows_fresh_execution() -> None:
    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("TEST-USDT",))  # type: ignore[arg-type]
    # No open position, no cooldown — should pass
    decision = _authorized_decision()
    results = await service.execute_risk_decisions([decision])

    assert len(results) == 1
    assert results[0].ok is True
    assert len(client.perp_orders) == 1


@pytest.mark.asyncio
async def test_exec_spam_cooldown_expired_allows_execution() -> None:
    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("TEST-USDT",))  # type: ignore[arg-type]
    # Simulate an old execution outside the cooldown window
    service._last_execution["TEST-USDT"] = datetime.now(UTC) - timedelta(
        minutes=EXECUTION_COOLDOWN_MINUTES + 1
    )

    decision = _authorized_decision()
    results = await service.execute_risk_decisions([decision])

    assert len(results) == 1
    assert results[0].ok is True
    assert len(client.perp_orders) == 1


@pytest.mark.asyncio
async def test_exec_spam_updates_cooldown_on_fill() -> None:
    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("TEST-USDT",))  # type: ignore[arg-type]
    assert "TEST-USDT" not in service._last_execution

    decision = _authorized_decision()
    await service.execute_risk_decisions([decision])

    assert "TEST-USDT" in service._last_execution


def _fake_analysis_for_exit(
    symbol: str,
    spot_price: float,
    gamma_flip: float | None = None,
    confluence_score: float | None = None,
    confluence_signal: str | None = None,
    shadow_delta: float | None = None,
) -> BingXCandidateAnalysis:
    from backend.services.bingx_candidate_analysis import (
        BingXCandidateAnalysis,
        BingXOptionsBlock,
        BingXTechnicalBlock,
    )
    from backend.tests.services.test_bingx_decision_engine import (
        _l2,
        _predictive,
        _underlying,
        _venue,
    )

    opts = BingXOptionsBlock(
        status="available",
        source="underlying_options",
        quality_score=0.8,
        metrics={
            "status": "available",
            "metrics": {
                "gamma_flip": gamma_flip,
                "confluence_score": confluence_score,
                "confluence_signal": confluence_signal,
                "shadow_delta_imbalance": shadow_delta,
            },
        },
    )

    from dataclasses import replace

    venue_block = replace(_venue(), klines=({"close": spot_price},))

    return BingXCandidateAnalysis(
        venue_symbol=symbol,
        underlying_symbol=symbol.split("-")[0],
        market_type="stock_perp",
        venue=venue_block,
        underlying=_underlying(),
        options=opts,
        technical=BingXTechnicalBlock(
            status="available", source="fmp", quality_score=0.8, metrics={}
        ),
        predictive=_predictive(),
        l2=_l2(),
    )


class FakeAccountStateForExit:
    def __init__(self, open_positions: list[Any]) -> None:
        self.open_positions = open_positions

    def to_dict(self) -> dict[str, Any]:
        return {"open_positions": [p.to_dict() for p in self.open_positions]}


class FakeAccountServiceForExit:
    def __init__(self, open_positions: list[Any]) -> None:
        self.open_positions = open_positions

    async def get_account_state(self) -> FakeAccountStateForExit:
        return FakeAccountStateForExit(self.open_positions)


@pytest.mark.asyncio
async def test_monitor_exits_no_positions() -> None:
    service = BingXBotService(
        client=ExecutionRecordingClient(),  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([]),  # type: ignore[arg-type]
    )
    executions = await service.monitor_exits()
    assert len(executions) == 0


@pytest.mark.asyncio
async def test_monitor_exits_long_gamma_flip_breached(monkeypatch) -> None:
    client = ExecutionRecordingClient()
    from backend.services.bingx_account_service import BingXPositionSnapshot

    pos = BingXPositionSnapshot(
        symbol="AAPL-USDT",
        side="LONG",
        size=10.0,
        entry_price=150.0,
        mark_price=140.0,
        unrealized_pnl=-10.0,
        leverage=1,
        liquidation_price=None,
        margin_type="isolated",
        fmp_quote=None,
        funding_rate=None,
        current_price=140.0,
    )
    service = BingXBotService(
        client=client,  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    async def _fake_build(symbol: str, **_: Any) -> BingXCandidateAnalysis:
        return _fake_analysis_for_exit(
            symbol=symbol,
            spot_price=140.0,
            gamma_flip=145.0,  # spot 140 < gamma_flip 145 -> breach for LONG!
            confluence_score=0.8,
            confluence_signal="LONG",
        )

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis", _fake_build, raising=False
    )

    executions = await service.monitor_exits()
    assert len(executions) == 1
    assert executions[0].ok is True
    assert len(client.perp_orders) == 1
    order = client.perp_orders[0]
    assert order.symbol == "AAPL-USDT"
    assert order.side == "SELL"
    assert order.position_side == "LONG"
    assert order.reduce_only is True


@pytest.mark.asyncio
async def test_monitor_exits_confluence_score_too_low(monkeypatch) -> None:
    client = ExecutionRecordingClient()
    from backend.services.bingx_account_service import BingXPositionSnapshot

    pos = BingXPositionSnapshot(
        symbol="AAPL-USDT",
        side="LONG",
        size=10.0,
        entry_price=150.0,
        mark_price=155.0,
        unrealized_pnl=5.0,
        leverage=1,
        liquidation_price=None,
        margin_type="isolated",
        fmp_quote=None,
        funding_rate=None,
    )
    service = BingXBotService(
        client=client,  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    async def _fake_build(symbol: str, **_: Any) -> BingXCandidateAnalysis:
        return _fake_analysis_for_exit(
            symbol=symbol,
            spot_price=155.0,
            gamma_flip=150.0,
            confluence_score=0.2,  # too low! (< 0.3)
            confluence_signal="LONG",
        )

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis", _fake_build, raising=False
    )

    executions = await service.monitor_exits()
    assert len(executions) == 1
    assert executions[0].ok is True
    assert len(client.perp_orders) == 1
    assert client.perp_orders[0].side == "SELL"


@pytest.mark.asyncio
async def test_monitor_exits_confluence_signal_contradicts(monkeypatch) -> None:
    client = ExecutionRecordingClient()
    from backend.services.bingx_account_service import BingXPositionSnapshot

    pos = BingXPositionSnapshot(
        symbol="AAPL-USDT",
        side="LONG",
        size=10.0,
        entry_price=150.0,
        mark_price=155.0,
        unrealized_pnl=5.0,
        leverage=1,
        liquidation_price=None,
        margin_type="isolated",
        fmp_quote=None,
        funding_rate=None,
    )
    service = BingXBotService(
        client=client,  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    async def _fake_build(symbol: str, **_: Any) -> BingXCandidateAnalysis:
        return _fake_analysis_for_exit(
            symbol=symbol,
            spot_price=155.0,
            gamma_flip=150.0,
            confluence_score=0.8,
            confluence_signal="BEARISH",  # opposes LONG!
        )

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis", _fake_build, raising=False
    )

    executions = await service.monitor_exits()
    assert len(executions) == 1
    assert executions[0].ok is True
    assert len(client.perp_orders) == 1
    assert client.perp_orders[0].side == "SELL"


@pytest.mark.asyncio
async def test_monitor_exits_hold_position(monkeypatch) -> None:
    client = ExecutionRecordingClient()
    from backend.services.bingx_account_service import BingXPositionSnapshot

    pos = BingXPositionSnapshot(
        symbol="AAPL-USDT",
        side="LONG",
        size=10.0,
        entry_price=150.0,
        mark_price=152.0,
        unrealized_pnl=2.0,
        leverage=1,
        liquidation_price=None,
        margin_type="isolated",
        fmp_quote=None,
        funding_rate=None,
        current_price=152.0,
    )
    service = BingXBotService(
        client=client,  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    async def _fake_build(symbol: str, **_: Any) -> BingXCandidateAnalysis:
        return _fake_analysis_for_exit(
            symbol=symbol,
            spot_price=154.0,
            gamma_flip=150.0,
            confluence_score=0.8,
            confluence_signal="BULLISH",
        )

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis", _fake_build, raising=False
    )

    executions = await service.monitor_exits()
    assert len(executions) == 0
    assert len(client.perp_orders) == 0
    assert service._conviction_scores["AAPL-USDT"] == 0.8
    assert service._exit_reasons["AAPL-USDT"] == []


@pytest.mark.asyncio
async def test_get_account_state_includes_conviction_metrics(monkeypatch) -> None:
    from backend.services.bingx_account_service import BingXPositionSnapshot

    pos = BingXPositionSnapshot(
        symbol="AAPL-USDT",
        side="LONG",
        size=10.0,
        entry_price=150.0,
        mark_price=155.0,
        unrealized_pnl=5.0,
        leverage=1,
        liquidation_price=None,
        margin_type="isolated",
        fmp_quote=None,
        funding_rate=None,
    )
    service = BingXBotService(
        client=ExecutionRecordingClient(),  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    service._conviction_scores["AAPL-USDT"] = 0.75
    service._exit_reasons["AAPL-USDT"] = ["test_reason"]

    state = await service.get_account_state()
    pos_data = state["open_positions"][0]
    assert pos_data["conviction_score"] == 0.75
    assert pos_data["exit_reasons"] == ["test_reason"]


@pytest.mark.asyncio
async def test_evaluate_dynamic_exits_half_tp_at_three_percent(monkeypatch) -> None:
    client = ExecutionRecordingClient()
    from backend.services.bingx_account_service import BingXPositionSnapshot

    pos = BingXPositionSnapshot(
        symbol="PLTR-USDT",
        side="LONG",
        size=10.0,
        entry_price=100.0,
        mark_price=103.1,
        unrealized_pnl=3.1,
        leverage=5,
        liquidation_price=None,
        margin_type="CROSSED",
        fmp_quote=None,
        funding_rate=None,
        current_price=103.1,
    )
    service = BingXBotService(
        client=client,  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    async def _fake_build(symbol: str, **_: Any) -> BingXCandidateAnalysis:
        return _fake_analysis_for_exit(
            symbol=symbol,
            spot_price=103.1,
            gamma_flip=90.0,
            confluence_score=1.0,
            confluence_signal="BULLISH",
        )

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis",
        _fake_build,
        raising=False,
    )

    executions = await service.evaluate_dynamic_exits()
    assert len(executions) == 1
    assert client.perp_orders[0].reduce_only is True
    assert client.perp_orders[0].quantity == pytest.approx(5.0)
    assert service._parametric_exit_state["PLTR-USDT"].half_tp_done is True


@pytest.mark.asyncio
async def test_evaluate_dynamic_exits_fade_and_flip(monkeypatch) -> None:
    client = ExecutionRecordingClient()
    from backend.services.bingx_account_service import BingXPositionSnapshot

    pos = BingXPositionSnapshot(
        symbol="AAPL-USDT",
        side="LONG",
        size=8.0,
        entry_price=100.0,
        mark_price=104.0,
        unrealized_pnl=4.0,
        leverage=5,
        liquidation_price=None,
        margin_type="CROSSED",
        fmp_quote=None,
        funding_rate=None,
    )
    service = BingXBotService(
        client=client,  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    async def _fake_build(symbol: str, **_: Any) -> BingXCandidateAnalysis:
        return _fake_analysis_for_exit(
            symbol=symbol,
            spot_price=104.0,
            gamma_flip=105.0,
            confluence_score=0.2,
            confluence_signal="BEARISH",
            shadow_delta=-0.20,
        )

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis",
        _fake_build,
        raising=False,
    )

    async def _fake_notional() -> float:
        return 10.0

    monkeypatch.setattr(service, "_get_dynamic_notional", _fake_notional)

    executions = await service.evaluate_dynamic_exits()
    assert len(executions) == 2
    assert client.perp_orders[0].reduce_only is True
    assert client.perp_orders[0].quantity == pytest.approx(8.0)
    assert client.perp_orders[1].position_side == "SHORT"
    assert client.perp_orders[1].side == "SELL"


@pytest.mark.asyncio
async def test_run_cycle_exits_after_decide_reuses_cycle_analyses(monkeypatch) -> None:
    from dataclasses import replace

    from backend.services.bingx_decision_engine import BingXDecision, BingXModuleScores

    client = ExecutionRecordingClient()
    service = BingXBotService(client=client, universe=("GOOGL-USDT",))  # type: ignore[arg-type]
    pipeline_order: list[str] = []
    analysis = _decision_engine_analysis(
        market_type="stock_perp",
        l2_available=True,
        predictive_bias="LONG",
        predictive_confidence=0.80,
    )

    async def _fake_build_candidate_analysis(symbol: str, **_: Any) -> Any:
        return replace(analysis, venue_symbol=symbol)

    def _fake_decide_candidates(analyses: list[Any], **__: Any) -> list[BingXDecision]:
        pipeline_order.append("decide")
        return [
            BingXDecision(
                symbol=a.venue_symbol,
                decision="BLOCK",
                direction="FLAT",
                confidence=0.0,
                score_total=0.0,
                module_scores=BingXModuleScores(0, 0, 0, 0, 0, 0),
                reason_codes=[],
            )
            for a in analyses
        ]

    async def _fake_evaluate_dynamic_exits(
        self: BingXBotService, *, cycle_analyses: dict[str, Any] | None = None
    ) -> list[Any]:
        assert pipeline_order == ["decide"]
        pipeline_order.append("exits")
        assert cycle_analyses is not None
        assert "GOOGL-USDT" in cycle_analyses
        return []

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.build_candidate_analysis",
        _fake_build_candidate_analysis,
        raising=False,
    )
    monkeypatch.setattr(service, "decide_candidates", _fake_decide_candidates)
    monkeypatch.setattr(
        BingXBotService,
        "evaluate_dynamic_exits",
        _fake_evaluate_dynamic_exits,
    )

    await service.run_cycle()

    assert pipeline_order == ["decide", "exits"]


def test_extract_options_exit_signals_prefers_predictive_report() -> None:
    from dataclasses import replace

    from backend.layer_3_specialists.ia_probabilistico.domain.probabilistic_models import (
        PredictiveOptionsBundleReport,
    )
    from backend.services.bingx_candidate_analysis import BingXOptionsBlock

    bundle = PredictiveOptionsBundleReport(
        gamma_flip_level=142.5,
        is_gamma_negative_regime=True,
        shadow_delta_imbalance=-0.42,
        zero_day_pinning_strike=0.0,
        speed_instability_warning=False,
        tail_risk_severity="LOW",
        zomma_risk_score=0.1,
    )
    analysis = replace(
        _fake_analysis_for_exit(
            symbol="AAPL-USDT",
            spot_price=140.0,
            gamma_flip=None,
            confluence_score=0.25,
            confluence_signal="BEARISH",
            shadow_delta=None,
        ),
        options=BingXOptionsBlock(
            status="available",
            source="institutional",
            quality_score=0.9,
            metrics={
                "metrics": {
                    "confluence_score": 0.25,
                    "confluence_signal": "BEARISH",
                }
            },
            predictive_report=bundle,
        ),
    )

    confluence_score, gamma_flip, signal, shadow, *_ = (
        BingXBotService._extract_options_exit_signals(analysis)
    )
    assert gamma_flip == pytest.approx(142.5)
    assert confluence_score == pytest.approx(0.25)
    assert signal == "BEARISH"
    assert shadow == pytest.approx(-0.42)


# ─── Tests for Hybrid structural Price Action & Scalping Strategy ────────────


def _fake_analysis_with_technical(
    symbol: str,
    spot_price: float,
    val: float,
    vah: float,
    active_pools: list[dict[str, Any]] | None = None,
    delta_bias: str = "BULLISH",
    imbalance_rho: float = 0.0,
) -> BingXCandidateAnalysis:
    from dataclasses import replace

    from backend.services.bingx_candidate_analysis import BingXTechnicalBlock

    analysis = _fake_analysis_for_exit(symbol, spot_price)

    venue_block = replace(
        analysis.venue,
        klines=({"close": spot_price},),
        venue_ta={
            "bars_count": 60,
            "last_price": spot_price,
            "close": spot_price,
            "trend": "bullish",
            "rsi_14": 58.0,
            "ema_9": spot_price,
            "ema_21": spot_price,
        },
    )

    venue_tech = {
        "status": "available",
        "source": "technical_terminal_venue",
        "payload": {
            "ok": True,
            "volume_profile": {
                "ok": True,
                "val": val,
                "vah": vah,
                "poc": (val + vah) / 2.0,
            },
            "market_structure": {
                "ok": True,
                "active_pools": active_pools or [],
            },
            "order_flow_delta": {
                "ok": True,
                "delta_bias": delta_bias,
            },
            "lob_dynamics": {
                "ok": True,
                "result": {
                    "imbalance_rho": imbalance_rho,
                },
            },
        },
    }
    return replace(
        analysis,
        venue=venue_block,
        technical=BingXTechnicalBlock(
            status="available",
            source="technical_terminal_venue",
            quality_score=0.8,
            venue_technical=venue_tech,
        ),
    )


def test_hydrate_value_area_from_gex_chain_quality() -> None:
    from backend.services.bingx_candidate_analysis import BingXOptionsBlock

    service = BingXBotService()
    analysis = _decision_engine_analysis(market_type="stock_perp", l2_available=True)
    analysis = replace(
        analysis,
        options=BingXOptionsBlock(
            status="available",
            source="underlying_options",
            quality_score=0.9,
            metrics={
                "chain_quality": {
                    "value_area": {"val": 98.0, "vah": 108.0, "source": "massive_equity_bars"},
                }
            },
        ),
    )
    hydrated = service._hydrate_analysis_value_area(analysis)
    assert service.resolve_price_zone(97.0, hydrated) == "ACUMULACION"
    assert service.resolve_price_zone(109.0, hydrated) == "DISTRIBUCION"


def test_resolve_price_zone_massive_fallback(monkeypatch) -> None:
    import pandas as pd

    service = BingXBotService()
    analysis = _decision_engine_analysis(market_type="stock_perp", l2_available=True)
    analysis = replace(
        analysis,
        technical=BingXTechnicalBlock(
            status="available",
            source="technical_terminal_venue",
            quality_score=0.5,
            venue_technical={"status": "available", "payload": {}},
        ),
    )

    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0}
        for _ in range(120)
    ]
    rows[-1] = {"open": 90.0, "high": 91.0, "low": 89.0, "close": 90.0, "volume": 5000.0}
    fallback_df = pd.DataFrame(rows)

    def _fake_fetch(_symbol: str, **_kw: Any) -> tuple[Any, Any, dict[str, Any]]:
        return None, fallback_df, {"bars": len(fallback_df)}

    monkeypatch.setattr(
        "backend.services.bingx_bot_service.fetch_equity_daily_bars",
        _fake_fetch,
        raising=False,
    )

    zone = service.resolve_price_zone(85.0, analysis)
    assert zone in {"ACUMULACION", "NEUTRAL", "DISTRIBUCION"}
    assert zone != "UNKNOWN"


def test_resolve_price_zone() -> None:
    service = BingXBotService()

    # 1. Neutral (no pools, spot in between val/vah)
    analysis = _fake_analysis_with_technical("AAPL-USDT", 105.0, 100.0, 110.0)
    assert service.resolve_price_zone(105.0, analysis) == "NEUTRAL"

    # 2. Accumulation (spot <= val)
    analysis = _fake_analysis_with_technical("AAPL-USDT", 99.0, 100.0, 110.0)
    assert service.resolve_price_zone(99.0, analysis) == "ACUMULACION"

    # 3. Distribution (spot >= vah)
    analysis = _fake_analysis_with_technical("AAPL-USDT", 111.0, 100.0, 110.0)
    assert service.resolve_price_zone(111.0, analysis) == "DISTRIBUCION"

    # 4. Accumulation via structural swing low (SwingLow at 102, spot <= SwingLow)
    pools = [{"type": "SwingLow", "price_level": 102.0, "is_swept": False}]
    analysis = _fake_analysis_with_technical("AAPL-USDT", 101.5, 100.0, 110.0, active_pools=pools)
    assert service.resolve_price_zone(101.5, analysis) == "ACUMULACION"

    # 5. Distribution via structural swing high (SwingHigh at 108, spot >= SwingHigh)
    pools = [{"type": "SwingHigh", "price_level": 108.0, "is_swept": False}]
    analysis = _fake_analysis_with_technical("AAPL-USDT", 108.5, 100.0, 110.0, active_pools=pools)
    assert service.resolve_price_zone(108.5, analysis) == "DISTRIBUCION"


def test_order_intent_mutual_exclusion_and_neutral_zone() -> None:
    service = BingXBotService()
    from backend.services.bingx_decision_engine import BingXDecision, BingXModuleScores

    # Setup base decision
    decision = BingXDecision(
        symbol="AAPL-USDT",
        decision="ALLOW",
        direction="LONG",
        confidence=0.8,
        score_total=0.7,
        module_scores=BingXModuleScores(
            venue=0.8, technical=0.8, options=0.8, predictive=0.8, l2=0.8, risk=1.0
        ),
        reason_codes=[],
        market_type="stock_perp",
    )

    # Case A: New position, price in NEUTRAL -> Vetoed/returns None
    analysis = _fake_analysis_with_technical("AAPL-USDT", 105.0, 100.0, 110.0)
    service._risk_desk.state.open_positions = {}
    intent = service._order_intent_from_decision(analysis, decision, cycle_id="test_cycle")
    assert intent is None

    # Case B: New position, price in ACUMULACION, direction LONG -> Allowed!
    analysis = _fake_analysis_with_technical("AAPL-USDT", 98.0, 100.0, 110.0)
    intent = service._order_intent_from_decision(analysis, decision, cycle_id="test_cycle")
    assert intent is not None
    assert intent.price_zone == "ACUMULACION"
    assert intent.stop_loss is None  # strict rule: no fixed SL
    assert intent.take_profit is None

    # Case C: Accumulation zone, direction SHORT -> Vetoed/returns None
    short_decision = BingXDecision(
        symbol="AAPL-USDT",
        decision="ALLOW",
        direction="SHORT",
        confidence=0.8,
        score_total=0.7,
        module_scores=BingXModuleScores(
            venue=0.8, technical=0.8, options=0.8, predictive=0.8, l2=0.8, risk=1.0
        ),
        reason_codes=[],
        market_type="stock_perp",
    )
    analysis = _fake_analysis_with_technical("AAPL-USDT", 98.0, 100.0, 110.0)
    intent = service._order_intent_from_decision(analysis, short_decision, cycle_id="test_cycle")
    assert intent is None

    # Case D: Distribution zone, direction LONG -> Vetoed/returns None
    analysis = _fake_analysis_with_technical("AAPL-USDT", 112.0, 100.0, 110.0)
    intent = service._order_intent_from_decision(analysis, decision, cycle_id="test_cycle")
    assert intent is None


def test_pyramiding_sizing_and_rules() -> None:
    service = BingXBotService()
    from backend.services.bingx_decision_engine import BingXDecision, BingXModuleScores

    # Set equity/capital to 100 USDT for easy pct math
    service._risk_policy = BingXRiskPolicy(equity_usdt=100.0)

    # Establish an existing position
    service._risk_desk.state.open_positions = {"AAPL-USDT": 10.0}

    # Case A: Confluence score total < 0.65 -> blocked (returns None)
    low_score_decision = BingXDecision(
        symbol="AAPL-USDT",
        decision="ALLOW",
        direction="LONG",
        confidence=0.8,
        score_total=0.60,
        module_scores=BingXModuleScores(
            venue=0.8, technical=0.8, options=0.8, predictive=0.8, l2=0.8, risk=1.0
        ),
        reason_codes=[],
        market_type="stock_perp",
    )
    analysis = _fake_analysis_with_technical("AAPL-USDT", 98.0, 100.0, 110.0)
    intent = service._order_intent_from_decision(
        analysis, low_score_decision, cycle_id="test_cycle"
    )
    assert intent is None

    # Case B: Confluence score >= 0.65, LONG, in ACUMULACION.
    high_score_decision = BingXDecision(
        symbol="AAPL-USDT",
        decision="ALLOW",
        direction="LONG",
        confidence=0.8,
        score_total=0.75,
        module_scores=BingXModuleScores(
            venue=0.8, technical=0.8, options=0.8, predictive=0.8, l2=0.8, risk=1.0
        ),
        reason_codes=[],
        market_type="stock_perp",
    )

    # Sub-case B1: Ratio >= 0.5 (closer to support base) AND absorption confirmed -> 5% size (5.0 USDT)
    # support_base = min(100.0, 95.0) = 95.0. support_top = 100.0. spot = 96.0.
    # ratio = (100 - 96) / 5 = 4/5 = 0.8 >= 0.5.
    pools = [{"type": "SwingLow", "price_level": 95.0, "is_swept": False}]
    analysis = _fake_analysis_with_technical(
        "AAPL-USDT", 96.0, 100.0, 110.0, active_pools=pools, delta_bias="BULLISH", imbalance_rho=0.1
    )
    intent = service._order_intent_from_decision(
        analysis, high_score_decision, cycle_id="test_cycle"
    )
    assert intent is not None
    assert intent.notional_usdt == pytest.approx(5.0)

    # Sub-case B2: Ratio >= 0.5 (closer to support base) but no absorption -> 4% size (4.0 USDT)
    analysis = _fake_analysis_with_technical(
        "AAPL-USDT", 96.0, 100.0, 110.0, active_pools=pools, delta_bias="BEARISH", imbalance_rho=0.0
    )
    intent = service._order_intent_from_decision(
        analysis, high_score_decision, cycle_id="test_cycle"
    )
    assert intent is not None
    assert intent.notional_usdt == pytest.approx(4.0)

    # Sub-case B3: Ratio < 0.5 (closer to support top) but absorption confirmed -> 4% size (4.0 USDT)
    # spot = 98.5. ratio = (100 - 98.5) / 5 = 1.5/5 = 0.3 < 0.5.
    analysis = _fake_analysis_with_technical(
        "AAPL-USDT", 98.5, 100.0, 110.0, active_pools=pools, delta_bias="BULLISH", imbalance_rho=0.0
    )
    intent = service._order_intent_from_decision(
        analysis, high_score_decision, cycle_id="test_cycle"
    )
    assert intent is not None
    assert intent.notional_usdt == pytest.approx(4.0)

    # Sub-case B4: Ratio < 0.5 and no absorption -> 2% size (2.0 USDT)
    analysis = _fake_analysis_with_technical(
        "AAPL-USDT", 98.5, 100.0, 110.0, active_pools=pools, delta_bias="BEARISH", imbalance_rho=0.0
    )
    intent = service._order_intent_from_decision(
        analysis, high_score_decision, cycle_id="test_cycle"
    )
    assert intent is not None
    assert intent.notional_usdt == pytest.approx(2.0)


def test_risk_desk_mutual_exclusion_and_firewall() -> None:
    from backend.services.bingx_risk_desk import BingXRiskDesk, OrderIntent

    desk = BingXRiskDesk()

    # 1. Zone Veto (Mutual Exclusion)
    # LONG intent in DISTRIBUCION -> Vetoed
    long_intent = OrderIntent(
        venue_symbol="AAPL-USDT",
        side="BUY",
        position_side="LONG",
        quantity=1.0,
        leverage=2,
        entry_type="MARKET",
        stop_loss=None,
        take_profit=None,
        client_order_id="test",
        reduce_only=False,
        cycle_id="test_cycle",
        notional_usdt=10.0,
        spread_pct=0.001,
        l2_quality_score=0.8,
        provider_health="ok",
        price_zone="DISTRIBUCION",
    )
    decision = desk.authorize_intent(long_intent)
    assert not decision.authorized
    assert "risk_zone_veto_long" in decision.reason_codes

    # SHORT intent in ACUMULACION -> Vetoed
    short_intent = OrderIntent(
        venue_symbol="AAPL-USDT",
        side="SELL",
        position_side="SHORT",
        quantity=1.0,
        leverage=2,
        entry_type="MARKET",
        stop_loss=None,
        take_profit=None,
        client_order_id="test",
        reduce_only=False,
        cycle_id="test_cycle",
        notional_usdt=10.0,
        spread_pct=0.001,
        l2_quality_score=0.8,
        provider_health="ok",
        price_zone="ACUMULACION",
    )
    decision = desk.authorize_intent(short_intent)
    assert not decision.authorized
    assert "risk_zone_veto_short" in decision.reason_codes

    # 2. Firewall 15% of available_margin_usdt
    # available_margin_usdt = 100 USDT. 15% limit = 15 USDT.
    # Existing exposure is 10 USDT. Proposed intent is 6 USDT (Total projected = 16 USDT >= 15 USDT limit).
    desk.state.available_margin_usdt = 100.0
    desk.state.open_positions = {"AAPL-USDT": 10.0}

    intent_over_limit = OrderIntent(
        venue_symbol="AAPL-USDT",
        side="BUY",
        position_side="LONG",
        quantity=1.0,
        leverage=2,
        entry_type="MARKET",
        stop_loss=None,
        take_profit=None,
        client_order_id="test",
        reduce_only=False,
        cycle_id="test_cycle",
        notional_usdt=6.0,
        spread_pct=0.001,
        l2_quality_score=0.8,
        provider_health="ok",
        price_zone="ACUMULACION",
    )
    decision = desk.authorize_intent(intent_over_limit)
    assert not decision.authorized
    assert "risk_zone_long_full" in decision.reason_codes


def test_risk_desk_zone_validation_accumulation() -> None:
    from backend.services.bingx_risk_desk import (
        REASON_ZONE_VETO_SHORT,
        BingXRiskDesk,
        BingXRiskDeskPolicy,
        OrderIntent,
    )

    desk = BingXRiskDesk(BingXRiskDeskPolicy())
    long_intent = OrderIntent(
        venue_symbol="AAPL-USDT",
        side="BUY",
        position_side="LONG",
        quantity=1.0,
        leverage=2,
        entry_type="MARKET",
        stop_loss=None,
        take_profit=None,
        client_order_id="test",
        reduce_only=False,
        cycle_id="test_cycle",
        notional_usdt=5.0,
        spread_pct=0.001,
        l2_quality_score=0.8,
        provider_health="ok",
        price_zone="ACUMULACION",
    )
    assert desk.authorize_intent(long_intent).authorized

    short_intent = replace(
        long_intent,
        side="SELL",
        position_side="SHORT",
        client_order_id="test_short",
    )
    short_decision = desk.authorize_intent(short_intent)
    assert not short_decision.authorized
    assert REASON_ZONE_VETO_SHORT in short_decision.reason_codes


@pytest.mark.asyncio
async def test_structural_stop_loss() -> None:
    from backend.services.bingx_account_service import BingXPositionSnapshot
    from backend.services.bingx_bot_service import _ParametricExitState

    # 1. Long position in support breaks support limit with sell pressure (delta_bias = BEARISH)
    # support_limit = min(val=100.0, SwingLow=98.0) = 98.0. spot = 97.0.
    pos = BingXPositionSnapshot(
        symbol="AAPL-USDT",
        side="LONG",
        size=10.0,
        entry_price=100.0,
        mark_price=97.0,
        unrealized_pnl=-3.0,
        leverage=5,
        liquidation_price=None,
        margin_type="isolated",
        fmp_quote=None,
        funding_rate=None,
        current_price=97.0,
    )

    pools = [{"type": "SwingLow", "price_level": 98.0, "is_swept": False}]
    analysis = _fake_analysis_with_technical(
        "AAPL-USDT",
        97.0,
        100.0,
        110.0,
        active_pools=pools,
        delta_bias="BEARISH",
        imbalance_rho=-0.1,
    )

    service = BingXBotService(
        client=ExecutionRecordingClient(),  # type: ignore[arg-type]
        account_service=FakeAccountServiceForExit([pos]),  # type: ignore[arg-type]
    )

    # Pre-populate exit state tracker
    service._parametric_exit_state["AAPL-USDT"] = _ParametricExitState(initial_size=10.0)

    executions = await service.evaluate_dynamic_exits(cycle_analyses={"AAPL-USDT": analysis})
    assert len(executions) == 1
    assert executions[0].ok is True
    assert len(service._client.perp_orders) == 1
    order = service._client.perp_orders[0]
    assert order.symbol == "AAPL-USDT"
    assert order.side == "SELL"
    assert order.position_side == "LONG"
    assert order.reduce_only is True
