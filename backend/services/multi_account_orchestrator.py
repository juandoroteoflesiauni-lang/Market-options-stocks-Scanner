from collections.abc import Sequence
from typing import Any

from backend.config.funding_accounts_loader import FundingAccountsLoader
from backend.domain.portfolio_risk_models import TradeCandidate, PortfolioRiskRequest
from backend.models.trade_record import TradeRecord
from backend.services.funding_orchestrator import FundingOrchestrator, OrchestrationResult


class MultiAccountOrchestrator:
    """
    Evaluates a trade candidate across multiple independent funded accounts.
    Allows a single Master signal to be mapped and sized differently
    for each prop firm challenge or funded tier.
    """

    def __init__(
        self,
        orchestrator: FundingOrchestrator,
        loader: FundingAccountsLoader | None = None,
    ) -> None:
        self.loader = loader or FundingAccountsLoader()
        self.orchestrator = orchestrator

    def evaluate_across_accounts(
        self,
        candidate: TradeCandidate,
        trades: Sequence[TradeRecord],
        context_data: dict[str, Any],
        portfolio_requests: dict[str, PortfolioRiskRequest],
    ) -> dict[str, OrchestrationResult]:
        """
        Runs the End-to-End FundingOrchestrator pipeline for each configured account.

        Args:
            candidate: A single trade candidate from Scanner/RiskDesk.
            trades: Historical trade records.
            context_data: Context data for the GlobalContextEngine.
            portfolio_requests: Mapping of account_id to its specific PortfolioRiskRequest.

        Returns:
            Dictionary mapping account `id` to its specific `OrchestrationResult`.
        """
        config = self.loader.load()
        results: dict[str, OrchestrationResult] = {}

        for account_config in config.accounts:
            account_id = account_config.id
            account_state = account_config.to_account_state()

            # Use specific portfolio request if provided, else create a dummy one
            if account_id in portfolio_requests:
                req = portfolio_requests[account_id]
            else:
                req = PortfolioRiskRequest(
                    account_state=account_state,
                    preset=account_config.preset,
                    candidates=[candidate],
                )

            # Run the single-account orchestrator
            response = self.orchestrator.evaluate_candidate(
                candidate=candidate,
                account=account_state,
                trades=trades,
                context_data=context_data,
                portfolio_request=req,
            )

            results[account_id] = response

        return results
