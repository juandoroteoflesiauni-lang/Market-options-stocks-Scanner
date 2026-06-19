"""Alpaca live risk metrics API (mirrors Funding risk-metrics pattern). # [PD-3][TH]"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.portfolio_risk_models import AccountState
from backend.infrastructure.repositories.trade_history_repository import TradeHistoryRepository
from backend.models.risk_metrics_snapshot import RiskMetricsSnapshot
from backend.services.alpaca_bot_service import AlpacaBotService
from backend.services.alpaca_pre_trade_risk_gate import PreTradeRiskGate
from backend.services.performance_analytics_engine import PerformanceAnalyticsEngine

router = APIRouter(prefix="/api/v1/alpaca", tags=["alpaca", "risk-metrics"])

_service: AlpacaBotService | None = None


def get_alpaca_service() -> AlpacaBotService:
    global _service
    if _service is None:
        _service = AlpacaBotService()
    return _service


def get_performance_engine() -> PerformanceAnalyticsEngine:
    return PerformanceAnalyticsEngine()


def get_trade_repo() -> TradeHistoryRepository:
    return TradeHistoryRepository()


@router.get("/risk-metrics", response_model=RiskMetricsSnapshot)
async def get_alpaca_risk_metrics(
    window: int = 100,
    engine: PerformanceAnalyticsEngine = Depends(get_performance_engine),  # noqa: B008
    repo: TradeHistoryRepository = Depends(get_trade_repo),  # noqa: B008
    svc: AlpacaBotService = Depends(get_alpaca_service),  # noqa: B008
) -> RiskMetricsSnapshot:
    """Live risk metrics for Alpaca dual-route positions."""
    thresholds = FundingThresholds()
    try:
        balance = await svc._client.fetch_account_balance()
        equity = float(balance.get("equity") or balance.get("portfolio_value") or 0)
        buying_power = float(balance.get("buying_power") or equity)
    except Exception:
        equity = float(thresholds.ftmo_initial_capital)
        buying_power = equity

    gate = PreTradeRiskGate.instance()
    account_state = AccountState(
        initial_capital=float(thresholds.ftmo_initial_capital),
        current_equity=equity or float(thresholds.ftmo_initial_capital),
        start_of_day_balance=buying_power or equity,
    )
    trades = [t for t in repo.get_recent(window=window) if t.mode in {"paper", "live", "alpaca"}]
    snapshot = engine.compute_snapshot(trades, account_state, window=window)
    gate.update_bur(snapshot.bur)
    return snapshot


__all__ = ["router"]
