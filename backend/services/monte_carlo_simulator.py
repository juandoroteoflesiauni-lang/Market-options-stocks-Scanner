from collections.abc import Sequence

import numpy as np


class MonteCarloSimulator:
    """
    Monte Carlo simulator based on Bootstrap Resampling (with replacement)
    over R-multiples. It decouples edge from nominal sizing.
    """

    def simulate_r_paths(
        self,
        historical_rs: Sequence[float],
        num_simulations: int,
        sim_length: int,
    ) -> np.ndarray:
        """
        Runs `num_simulations` paths, each of `sim_length` trades.
        Returns a 2D numpy array of shape (num_simulations, sim_length)
        containing the individual trade R-multiples for each step.

        Args:
            historical_rs: Sequence of realized R values from past trades.
            num_simulations: Number of paths to generate.
            sim_length: Number of trades per path.

        Returns:
            np.ndarray: Simulated R values.
        """
        if not historical_rs:
            return np.zeros((num_simulations, sim_length))

        # Use numpy for fast vectorized resampling
        rs_array = np.array(historical_rs, dtype=np.float64)

        # Random choice with replacement
        simulated_trades_r = np.random.choice(
            rs_array, size=(num_simulations, sim_length), replace=True
        )

        return simulated_trades_r
