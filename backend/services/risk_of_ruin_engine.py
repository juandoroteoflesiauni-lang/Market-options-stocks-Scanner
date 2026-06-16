
import numpy as np

from backend.domain.portfolio_risk_models import AccountState
from backend.services.monte_carlo_simulator import MonteCarloSimulator


class RiskOfRuinEngine:
    """
    Computes exact probabilities of ruin and equity percentiles
    by applying sizing rules to R-multiple simulations.
    """

    def __init__(self, simulator: MonteCarloSimulator | None = None):
        self.simulator = simulator or MonteCarloSimulator()

    def evaluate_risk_of_ruin(
        self,
        historical_rs: list[float],
        account_state: AccountState,
        max_loss_limit_pct: float,
        risk_per_trade_pct: float,
        num_simulations: int = 1000,
        sim_length: int = 50,
    ) -> dict[str, float]:
        """
        Evaluates the Risk of Ruin (RoR) applying nominal dollar sizing
        to the R-multiple simulations.

        Args:
            historical_rs: List of historical R-multiples.
            account_state: Current account state (equity, balance).
            max_loss_limit_pct: E.g. 10.0 for 10% max drawdown from initial.
            risk_per_trade_pct: E.g. 0.5 for 0.5% risk per trade.
            num_simulations: 1000 for fast UI, 10000 for deep analysis.
            sim_length: Number of trades into the future.

        Returns:
            dict containing ror_pct, median_drawdown, etc.
        """
        if not historical_rs:
            return {"ror_pct": 0.0, "p5_equity": float(account_state.current_equity)}

        # 1. Simulate R paths
        # Shape: (num_simulations, sim_length)
        r_paths = self.simulator.simulate_r_paths(historical_rs, num_simulations, sim_length)

        # 2. Convert to Dollars
        initial_cap = float(account_state.initial_capital)
        ruin_threshold = initial_cap * (1.0 - (max_loss_limit_pct / 100.0))

        # Sizing model: Fixed fractional based on initial capital for FTMO challenges,
        # or compounding. We use fixed nominal based on current equity for safety,
        # but standard is fixed % of initial to avoid death spiral math complexity in fast UI.
        # Let's use fixed risk amount per trade based on current equity:
        risk_usd = float(account_state.current_equity) * (risk_per_trade_pct / 100.0)

        # Multiply R matrix by risk_usd to get PnL matrix
        pnl_paths = r_paths * risk_usd

        # Cumulative PnL paths
        cum_pnl_paths = np.cumsum(pnl_paths, axis=1)

        # Equity paths
        equity_paths = float(account_state.current_equity) + cum_pnl_paths

        # 3. Calculate Risk of Ruin
        # A path is ruined if its minimum equity drops below the ruin_threshold
        min_equities = np.min(equity_paths, axis=1)
        ruined_paths = np.sum(min_equities <= ruin_threshold)

        ror_pct = (ruined_paths / num_simulations) * 100.0

        # 4. Calculate percentiles of final equity
        final_equities = equity_paths[:, -1]
        p5 = np.percentile(final_equities, 5)
        p50 = np.percentile(final_equities, 50)
        p95 = np.percentile(final_equities, 95)

        return {
            "ror_pct": float(ror_pct),
            "p5_equity": float(p5),
            "p50_equity": float(p50),
            "p95_equity": float(p95),
            "ruin_threshold": float(ruin_threshold),
            "simulated_trades": sim_length,
            "simulations_count": num_simulations,
        }
