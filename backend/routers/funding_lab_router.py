"""Funding Lab API: FTMO-specific strict signal gate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import MarketScannerRequest
from backend.services.ftmo_playbook_service import FTMOPlaybookService
from backend.services.ftmo_simulation_service import FTMOSimulationService
from backend.services.funding_lab_scanner_confirmation import get_scanner_confirmation
from backend.services.funding_lab_service import FundingLabService
from backend.services.funding_lab_side_meta_learner import get_side_meta_confirmation
from backend.services.market_scanner_service import MarketScannerService

router = APIRouter(prefix="/api/v1/funding-lab", tags=["funding-lab"])
logger = get_logger(__name__)


async def _scanner_confirmation_with_side_meta(**kwargs: Any) -> dict[str, Any]:
    return await get_scanner_confirmation(
        **kwargs,
        side_meta_provider=get_side_meta_confirmation,
    )


async def _command_deck_scanner_snapshot(symbols: list[str]) -> list[dict[str, Any]]:
    request = MarketScannerRequest(
        universe="funding_lab",
        symbols=symbols,
        timeframes=["15m"],
        filters={
            "min_score": 0,
            "min_price": 0,
            "min_volume": 0,
            "min_relative_volume": 0,
            "allow_reversal": True,
            "include_vetoed": True,
        },
        sort="scanner_score",
        direction="both",
        max_rows=max(1, len(symbols)),
        include_deep_metrics=True,
        include_funding_gate=True,
        customization={
            "enabled_modules": ["technical", "probabilistic", "options_gex"],
            "weight_matrix": {},
            "module_synthesis_limit": 0,
            "primary_timeframe": "15m",
        },
    )
    response = await MarketScannerService().scan(request)
    return [row.model_dump(mode="json") for row in response.rows]


service: Any | None = None
playbook_service: Any | None = None
simulation_service: Any | None = None


def _get_funding_lab_service() -> Any:
    global service
    if service is None:
        service = FundingLabService(
            scanner_confirmation_provider=_scanner_confirmation_with_side_meta,
            scanner_snapshot_provider=_command_deck_scanner_snapshot,
        )
    return service


def _get_playbook_service() -> Any:
    global playbook_service
    if playbook_service is None:
        playbook_service = FTMOPlaybookService(funding_service=_get_funding_lab_service())
    return playbook_service


def _get_simulation_service() -> Any:
    global simulation_service
    if simulation_service is None:
        simulation_service = FTMOSimulationService(playbook_service=_get_playbook_service())
    return simulation_service


class FundingLabBacktestRequest(BaseModel):
    symbols: list[str] | None = None
    modules: list[str] | None = None
    n_days: int | str = Field(default="eod")
    limit_per_symbol: int = Field(default=5_000, ge=1, le=100_000)


class FundingLabTradeHistoryItem(BaseModel):
    date: str = Field(min_length=1)
    pnl: float


class FundingLabAccountState(BaseModel):
    initial_capital: float = Field(gt=0)
    current_equity: float = Field(gt=0)
    start_of_day_balance: float = Field(gt=0)
    realized_daily_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trade_history: list[FundingLabTradeHistoryItem] = Field(default_factory=list)


class FundingLabSignalCheckRequest(BaseModel):
    symbol: str = Field(min_length=1)
    entry_direction: str | None = None
    account_state: FundingLabAccountState | None = None


class FundingLabPlaybookStateRequest(BaseModel):
    phase: str = "challenge"
    initial_capital: float = Field(gt=0)
    current_equity: float = Field(gt=0)
    start_of_day_balance: float = Field(gt=0)
    realized_daily_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    commissions: float = 0.0
    swaps: float = 0.0
    risk_budget_per_trade_pct: float = Field(default=0.5, ge=0, le=5)
    trade_history: list[FundingLabTradeHistoryItem] = Field(default_factory=list)


class FundingLabTradeIntentRequest(BaseModel):
    symbol: str = Field(min_length=1)
    side: str = Field(min_length=1)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target: float | None = Field(default=None, gt=0)
    requested_risk_amount: float | None = Field(default=None, ge=0)


class FundingLabJournalRequest(BaseModel):
    symbol: str = Field(min_length=1)
    intent_id: str | None = None
    side: str | None = None
    status: str = Field(min_length=1)
    pnl: float = 0.0
    actual_entry: float | None = None
    actual_exit: float | None = None
    actual_stop: float | None = None
    actual_size_units: float | None = None
    fees: float = 0.0
    swap: float = 0.0
    gross_pnl: float | None = None
    net_pnl: float | None = None
    closed_at: str | None = None
    reason: str | None = None
    notes: str | None = None


class FundingLabSimulationSessionRequest(BaseModel):
    initial_capital: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] | None = None


class FundingLabSimulationOrderRequest(BaseModel):
    intent_id: str = Field(min_length=1)
    session_id: str | None = None
    size_units: float | None = Field(default=None, gt=0)


class FundingLabSimulationMarkRequest(BaseModel):
    session_id: str | None = None


@router.get("/universe")
async def get_funding_lab_universe() -> dict[str, Any]:
    return _get_funding_lab_service().universe()


@router.get("/status")
async def get_funding_lab_status() -> dict[str, Any]:
    return _get_funding_lab_service().status()


@router.get("/command-deck")
async def get_funding_lab_command_deck(
    symbols: str | None = Query(default=None),
    modules: str | None = Query(default=None),
    limit_per_symbol: int = Query(default=5_000, ge=1, le=100_000),
) -> dict[str, Any]:
    selected_symbols = _csv_query(symbols)
    selected_modules = _csv_query(modules)
    return await _get_funding_lab_service().command_deck(
        symbols=selected_symbols or None,
        modules=selected_modules or None,
        limit_per_symbol=limit_per_symbol,
    )


@router.post("/backtest")
async def post_funding_lab_backtest(request: FundingLabBacktestRequest) -> dict[str, Any]:
    try:
        return _get_funding_lab_service().backtest(
            symbols=request.symbols,
            modules=request.modules,
            n_days=request.n_days,
            limit_per_symbol=request.limit_per_symbol,
        )
    except ValueError as exc:
        logger.info("funding_lab.backtest_rejected error=%s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/signal-check")
async def post_funding_lab_signal_check(request: FundingLabSignalCheckRequest) -> dict[str, Any]:
    try:
        return await _get_funding_lab_service().signal_check(
            symbol=request.symbol,
            entry_direction=request.entry_direction,
            account_state=(
                request.account_state.model_dump(mode="json")
                if request.account_state is not None
                else None
            ),
        )
    except ValueError as exc:
        logger.info("funding_lab.signal_check_rejected symbol=%s error=%s", request.symbol, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/playbook/state")
async def get_funding_lab_playbook_state() -> dict[str, Any]:
    return _get_playbook_service().get_state()


@router.put("/playbook/state")
async def put_funding_lab_playbook_state(
    request: FundingLabPlaybookStateRequest,
) -> dict[str, Any]:
    return _get_playbook_service().update_state(request.model_dump(mode="json"))


@router.post("/playbook/trade-intent")
async def post_funding_lab_playbook_trade_intent(
    request: FundingLabTradeIntentRequest,
) -> dict[str, Any]:
    return await _get_playbook_service().evaluate_trade_intent(request.model_dump(mode="json"))


@router.post("/playbook/journal")
async def post_funding_lab_playbook_journal(request: FundingLabJournalRequest) -> dict[str, Any]:
    return _get_playbook_service().record_journal(request.model_dump(mode="json"))


@router.get("/playbook/report")
async def get_funding_lab_playbook_report(date: str | None = Query(default=None)) -> dict[str, Any]:
    return _get_playbook_service().report(date=date)


@router.get("/playbook/audit")
async def get_funding_lab_playbook_audit(
    date: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
) -> dict[str, Any]:
    return _get_playbook_service().audit_report(date=date, symbol=symbol, event_type=event_type)


@router.get("/playbook/audit/export")
async def get_funding_lab_playbook_audit_export(
    date: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    format: str = Query(default="json"),
) -> Any:
    output_format = format.lower()
    if output_format == "markdown":
        markdown = _get_playbook_service().audit_export(
            date=date,
            symbol=symbol,
            event_type=event_type,
            output_format="markdown",
        )
        return Response(content=markdown, media_type="text/markdown")
    if output_format != "json":
        raise HTTPException(status_code=400, detail="format must be json or markdown")
    return _get_playbook_service().audit_export(
        date=date,
        symbol=symbol,
        event_type=event_type,
        output_format="json",
    )


def _csv_query(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@router.get("/simulation/state")
async def get_funding_lab_simulation_state() -> dict[str, Any]:
    return _get_simulation_service().state()


@router.post("/simulation/session")
async def post_funding_lab_simulation_session(
    request: FundingLabSimulationSessionRequest,
) -> dict[str, Any]:
    return _get_simulation_service().create_session(
        request.model_dump(mode="json", exclude_none=True)
    )


@router.post("/simulation/order")
async def post_funding_lab_simulation_order(
    request: FundingLabSimulationOrderRequest,
) -> dict[str, Any]:
    return _get_simulation_service().create_order(
        request.model_dump(mode="json", exclude_none=True)
    )


@router.post("/simulation/mark-to-market")
async def post_funding_lab_simulation_mark_to_market(
    request: FundingLabSimulationMarkRequest,
) -> dict[str, Any]:
    return _get_simulation_service().mark_to_market(session_id=request.session_id)


@router.get("/simulation/report")
async def get_funding_lab_simulation_report(
    session_id: str | None = Query(default=None),
    date: str | None = Query(default=None),
) -> dict[str, Any]:
    return _get_simulation_service().report(session_id=session_id, date=date)


@router.get("/simulation/export")
async def get_funding_lab_simulation_export(
    session_id: str | None = Query(default=None),
    date: str | None = Query(default=None),
    format: str = Query(default="json"),
) -> Any:
    output_format = format.lower()
    if output_format == "markdown":
        markdown = _get_simulation_service().export_report(
            session_id=session_id,
            date=date,
            output_format="markdown",
        )
        return Response(content=markdown, media_type="text/markdown")
    if output_format != "json":
        raise HTTPException(status_code=400, detail="format must be json or markdown")
    return _get_simulation_service().export_report(
        session_id=session_id,
        date=date,
        output_format="json",
    )
