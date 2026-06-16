import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from backend.config.funding_thresholds import FundingThresholds
from backend.domain.portfolio_risk_models import AccountState
from backend.models.risk_metrics_snapshot import RiskMetricsSnapshot
from backend.models.trade_record import TradeRecord
from backend.services.risk_of_ruin_engine import RiskOfRuinEngine


class PerformanceAnalyticsEngine:
    """Engine for computing aggregate performance and risk metrics."""

    def __init__(self, thresholds: FundingThresholds | None = None) -> None:
        self.thresholds = thresholds or FundingThresholds()
        self.min_trades = 10  # Arbitrary small number for testing if not specified

    def compute_snapshot(
        self,
        trades: Sequence[TradeRecord],
        account_state: AccountState,
        window: int,
    ) -> RiskMetricsSnapshot:
        """Compute RiskMetricsSnapshot for the given trades."""
        w = trades[-window:] if window > 0 else trades
        sample_size = len(w)

        if sample_size < self.min_trades:
            return self._empty_snapshot(sample_size)

        # Basic sets
        realized_rs = [float(t.realized_r) for t in w if t.realized_r is not None]
        if not realized_rs:
            return self._empty_snapshot(sample_size)

        wins = [r for r in realized_rs if r > 0]
        losses = [r for r in realized_rs if r < 0]

        # Expectancy
        expectancy_r = Decimal(str(statistics.mean(realized_rs)))

        # By Setup
        per_setup: dict[str, list[float]] = defaultdict(list)
        for t in w:
            if t.realized_r is not None:
                per_setup[t.setup_type].append(float(t.realized_r))

        expectancy_by_setup = {
            s: Decimal(str(statistics.mean(rs))) for s, rs in per_setup.items() if rs
        }

        # Profit Factor
        sum_wins = sum(wins)
        sum_losses = abs(sum(losses))
        profit_factor = 99.0 if sum_losses == 0 else min(sum_wins / sum_losses, 99.0)

        # Sharpe / Sortino (simplified, daily/trade frequency assumed)
        mean_r = statistics.mean(realized_rs)
        std_r = statistics.stdev(realized_rs) if len(realized_rs) > 1 else 0.0
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0

        downside = [r for r in realized_rs if r < 0]
        down_dev = statistics.stdev(downside) if len(downside) > 1 else 0.0
        sortino = (mean_r / down_dev * math.sqrt(252)) if down_dev > 0 else 0.0

        calmar = 0.0  # Simplified

        # BUR
        max_loss_limit = (
            float(account_state.initial_capital)
            * float(self.thresholds.ftmo_max_loss_limit_pct)
            / 100.0
        )
        loss_used = float(account_state.initial_capital) - float(account_state.current_equity)
        bur = max(0.0, loss_used / max_loss_limit) if max_loss_limit > 0 else 0.0

        if bur < 0.5:
            buffer_zone = "GREEN"
        elif bur < 0.8:
            buffer_zone = "YELLOW"
        else:
            buffer_zone = "RED"

        # Risk tail metrics
        sorted_r = sorted(realized_rs)
        idx_95 = max(0, int(len(sorted_r) * 0.05))
        idx_99 = max(0, int(len(sorted_r) * 0.01))

        var95 = abs(sorted_r[idx_95]) if sorted_r else 0.0
        cvar95_vals = sorted_r[: idx_95 + 1]
        cvar95 = abs(statistics.mean(cvar95_vals)) if cvar95_vals else 0.0
        cvar99_vals = sorted_r[: idx_99 + 1]
        cvar99 = abs(statistics.mean(cvar99_vals)) if cvar99_vals else 0.0

        # Kelly
        p = len(wins) / len(realized_rs)
        if losses and sum_losses > 0:
            b = (sum_wins / len(wins)) / abs(sum_losses / len(losses))
        else:
            b = 1.0
        if mean_r <= 0:
            kelly_applied = 0.0
        else:
            kelly_full = p - ((1 - p) / b) if b > 0 else 0.0
            base_fraction = self.thresholds.sizing_kelly_base_fraction
            cap = self.thresholds.sizing_kelly_cap
            kelly_applied = max(0.0, min(kelly_full * base_fraction, cap))

        # Risk of ruin (Fast MC for dashboard: 1000 curves, 50 trades)
        risk_of_ruin_pct = 0.0
        if len(realized_rs) >= self.min_trades:
            ror_engine = RiskOfRuinEngine()
            # Default to 0.5% risk per trade. 
            # In a fully dynamic system, this could read from settings.
            ror_res = ror_engine.evaluate_risk_of_ruin(
                historical_rs=realized_rs,
                account_state=account_state,
                max_loss_limit_pct=float(self.thresholds.ftmo_max_loss_limit_pct),
                risk_per_trade_pct=0.5,
                num_simulations=1000,
                sim_length=50,
            )
            risk_of_ruin_pct = ror_res["ror_pct"]

        return RiskMetricsSnapshot(
            sample_size=sample_size,
            expectancy_r=expectancy_r,
            expectancy_by_setup=expectancy_by_setup,
            profit_factor=profit_factor,
            sharpe=sharpe,
            sortino=sortino,
            calmar=calmar,
            bur=bur,
            buffer_zone=buffer_zone,
            ulcer=0.0,
            var95=Decimal(str(var95)),
            cvar95=Decimal(str(cvar95)),
            cvar99=Decimal(str(cvar99)),
            kelly_applied=kelly_applied,
            risk_of_ruin_pct=risk_of_ruin_pct,
        )

    def _empty_snapshot(self, sample_size: int) -> RiskMetricsSnapshot:
        return RiskMetricsSnapshot(
            sample_size=sample_size,
            expectancy_r=Decimal("0.0"),
            expectancy_by_setup={},
            profit_factor=0.0,
            sharpe=0.0,
            sortino=0.0,
            calmar=0.0,
            bur=0.0,
            buffer_zone="GREEN",
            ulcer=0.0,
            var95=Decimal("0.0"),
            cvar95=Decimal("0.0"),
            cvar99=Decimal("0.0"),
            kelly_applied=0.0,
            risk_of_ruin_pct=0.0,
        )
