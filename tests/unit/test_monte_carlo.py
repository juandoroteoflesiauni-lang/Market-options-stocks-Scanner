from decimal import Decimal
import numpy as np

from backend.domain.portfolio_risk_models import AccountState
from backend.services.monte_carlo_simulator import MonteCarloSimulator
from backend.services.risk_of_ruin_engine import RiskOfRuinEngine


def test_monte_carlo_simulator():
    sim = MonteCarloSimulator()
    historical_rs = [1.0, -1.0, 2.0, -0.5, 3.0]
    paths = sim.simulate_r_paths(historical_rs, num_simulations=10, sim_length=5)
    
    assert paths.shape == (10, 5)
    # Check if elements are from historical_rs
    unique_vals = set(np.unique(paths))
    assert unique_vals.issubset(set(historical_rs))


def test_risk_of_ruin_engine():
    engine = RiskOfRuinEngine()
    
    # Simulate a very bad strategy: always loses 1R
    historical_rs = [-1.0, -1.0, -1.0]
    
    account_state = AccountState(
        initial_capital=100_000.0,
        current_equity=100_000.0,
        start_of_day_balance=100_000.0,
    )
    
    # 10% max loss limit => ruin threshold = $90,000
    # 1% risk per trade => $1,000 risk
    # It takes 10 losses to reach $90,000. If sim_length=15, ruin should be 100%.
    
    result = engine.evaluate_risk_of_ruin(
        historical_rs=historical_rs,
        account_state=account_state,
        max_loss_limit_pct=10.0,
        risk_per_trade_pct=1.0,
        num_simulations=100,
        sim_length=15,
    )
    
    assert result["ror_pct"] == 100.0
    assert result["p50_equity"] < 90_000.0

    # Simulate a winning strategy
    historical_rs = [1.0, 1.0, 1.0]
    result_win = engine.evaluate_risk_of_ruin(
        historical_rs=historical_rs,
        account_state=account_state,
        max_loss_limit_pct=10.0,
        risk_per_trade_pct=1.0,
        num_simulations=100,
        sim_length=15,
    )
    assert result_win["ror_pct"] == 0.0
    assert result_win["p50_equity"] == 100_000.0 + (15 * 1000.0)
