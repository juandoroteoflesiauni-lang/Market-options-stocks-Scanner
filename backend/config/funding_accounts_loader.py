import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from backend.domain.portfolio_risk_models import AccountState, FundingRulePreset


class AccountConfigItem(BaseModel):
    id: str
    name: str
    phase: str
    initial_capital: float
    current_equity: float
    start_of_day_balance: float
    preset: FundingRulePreset

    def to_account_state(self) -> AccountState:
        return AccountState(
            initial_capital=self.initial_capital,
            current_equity=self.current_equity,
            start_of_day_balance=self.start_of_day_balance,
            phase=self.phase,
        )


class FundingAccountsConfig(BaseModel):
    accounts: list[AccountConfigItem] = Field(default_factory=list)


class FundingAccountsLoader:
    def __init__(self, filepath: str | None = None) -> None:
        if filepath is None:
            # Default to adjacent yaml file
            base_dir = Path(__file__).parent
            self.filepath = str(base_dir / "funding_accounts.yaml")
        else:
            self.filepath = filepath

    def load(self) -> FundingAccountsConfig:
        if not os.path.exists(self.filepath):
            return FundingAccountsConfig(accounts=[])

        with open(self.filepath, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return FundingAccountsConfig.model_validate(data)
