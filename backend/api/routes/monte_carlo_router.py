from typing import Any

from fastapi import APIRouter, Depends

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.portfolio_risk_models import AccountState
from backend.infrastructure.repositories.trade_history_repository import TradeHistoryRepository
from backend.services.risk_of_ruin_engine import RiskOfRuinEngine

router = APIRouter(prefix="/api/v1/funding", tags=["funding", "monte-carlo"])


def get_trade_repo() -> TradeHistoryRepository:
    return TradeHistoryRepository()


def get_ror_engine() -> RiskOfRuinEngine:
    return RiskOfRuinEngine()


@router.get("/monte-carlo")
def run_monte_carlo(
    window: int = 100,
    simulations: int = 10000,
    sim_length: int = 50,
    risk_per_trade_pct: float = 0.5,
    engine: RiskOfRuinEngine = Depends(get_ror_engine),  # noqa: B008
    repo: TradeHistoryRepository = Depends(get_trade_repo),  # noqa: B008
) -> dict[str, Any]:
    """
    Run a heavy On-Demand Monte Carlo simulation over the historical trades.
    Uses R-multiples for resampling.
    """
    thresholds = FundingThresholds()
    trades = repo.get_recent(window=window)

    historical_rs = [float(t.realized_r) for t in trades if t.realized_r is not None]

    account_state = AccountState(
        initial_capital=float(thresholds.ftmo_initial_capital),
        current_equity=float(thresholds.ftmo_initial_capital),  # Can be retrieved from a real DB
        start_of_day_balance=float(thresholds.ftmo_initial_capital),
    )

    if len(historical_rs) < 10:
        return {
            "ror_pct": 0.0,
            "error": "Not enough historical trades to run Monte Carlo (min 10).",
            "historical_trades_found": len(historical_rs),
        }

    result = engine.evaluate_risk_of_ruin(
        historical_rs=historical_rs,
        account_state=account_state,
        max_loss_limit_pct=float(thresholds.ftmo_max_loss_limit_pct),
        risk_per_trade_pct=risk_per_trade_pct,
        num_simulations=simulations,
        sim_length=sim_length,
    )

    return result
