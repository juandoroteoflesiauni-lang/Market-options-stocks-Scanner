#!/usr/bin/env python
import logging
import sys
from pathlib import Path

# Add project root to path so we can import backend
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.domain.portfolio_risk_models import AccountState
from backend.infrastructure.repositories.trade_history_repository import TradeHistoryRepository
from backend.services.monte_carlo_simulator import MonteCarloSimulator
from backend.services.risk_of_ruin_engine import RiskOfRuinEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("CalibrationLoop")


def run_calibration() -> None:
    """
    Monthly Calibration Loop.
    1. Fetches historical trades.
    2. Runs Heavy Monte Carlo to recalculate exact Risk of Ruin.
    3. Analyzes Correlation edge breakdown (VPIN vs OFI vs SMC).
    4. Outputs new recommended Kelly fraction to logs/DB.
    """
    logger.info("Starting Calibration Loop...")

    repo = TradeHistoryRepository()
    # Fetch a large window for monthly retrain
    trades = repo.get_recent(window=500)
    if len(trades) < 30:
        logger.warning(f"Not enough trades for calibration (found {len(trades)}). Min 30 required.")
        sys.exit(0)

    logger.info(f"Loaded {len(trades)} trades for calibration.")

    # 1. Edge correlation check
    # We group trades by setup_type and check if performance is deteriorating
    setup_pnl: dict[str, list[float]] = {}
    for t in trades:
        if t.realized_r is not None:
            setup_pnl.setdefault(t.setup_type, []).append(float(t.realized_r))

    logger.info("=== Setup Edge Analysis ===")
    for setup, rs in setup_pnl.items():
        win_rate = len([r for r in rs if r > 0]) / len(rs) * 100
        avg_r = sum(rs) / len(rs)
        logger.info(f" - {setup}: N={len(rs)}, WinRate={win_rate:.1f}%, Avg_R={avg_r:.2f}")
        if avg_r < 0.1:
            logger.warning(
                f" [!] Setup {setup} is showing edge decay. Consider reducing its allocation."
            )

    # 1.5 Empirical Time Stop
    winning_trades = [t for t in trades if t.realized_r is not None and t.realized_r > 0]
    if winning_trades:
        durations = []
        for t in winning_trades:
            if t.opened_at and t.closed_at:
                dur_min = (t.closed_at - t.opened_at).total_seconds() / 60.0
                durations.append(dur_min)

        if durations:
            import numpy as np

            p25_dur = float(np.percentile(durations, 25))
            p50_dur = float(np.percentile(durations, 50))
            logger.info("=== Empirical Time Stop ===")
            logger.info(f" Winning Trades N={len(durations)}")
            logger.info(f" Median duration of winning trades (P50): {p50_dur:.1f} minutes")
            logger.info(f" Fast duration of winning trades (P25): {p25_dur:.1f} minutes")
            logger.info(
                f" Recommended Time Stop: If a trade hasn't advanced in {p50_dur:.1f} min, "
                "the edge is decaying. Consider force-closing or tightening stop."
            )

    # 2. Monte Carlo validation
    historical_rs = [float(t.realized_r) for t in trades if t.realized_r is not None]
    engine = RiskOfRuinEngine(MonteCarloSimulator())

    account_state = AccountState(
        initial_capital=100_000.0,
        current_equity=100_000.0,
        start_of_day_balance=100_000.0,
        phase="calibration",
    )

    logger.info("=== Heavy Monte Carlo Validation (10,000 curves) ===")
    res = engine.evaluate_risk_of_ruin(
        historical_rs=historical_rs,
        account_state=account_state,
        max_loss_limit_pct=10.0,
        risk_per_trade_pct=0.5,
        num_simulations=10000,
        sim_length=100,  # simulate 100 trades into the future
    )

    ror = res["ror_pct"]
    logger.info(f"Risk of Ruin (10% Max Loss, 0.5% Risk): {ror:.2f}%")
    logger.info(f"Median Expected Equity (100 trades): ${res['p50_equity']:.2f}")
    logger.info(f"P5 (Pessimistic) Equity: ${res['p5_equity']:.2f}")

    # 3. Kelly Recommendation
    if ror > 5.0:
        logger.warning("Risk of Ruin is too high! Recommended action: REDUCE Kelly Base Fraction.")
    elif ror == 0.0:
        logger.info(
            "Risk of Ruin is near 0. System is extremely stable. "
            "Optimal Kelly Base Fraction can be safely maintained."
        )
    else:
        logger.info("System is within acceptable risk parameters.")

    logger.info("Calibration Loop finished successfully.")


if __name__ == "__main__":
    run_calibration()
