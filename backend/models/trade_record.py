from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class TradeRecord(BaseModel):
    """
    Represents a historical trade for performance analytics.
    Frozen for immutability.
    """

    model_config = ConfigDict(frozen=True)

    trade_id: str
    setup_type: str
    symbol: str
    direction: str
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: Decimal
    risk_r: Decimal
    realized_r: Decimal | None
    pnl: Decimal | None
    opened_at: datetime
    closed_at: datetime | None
    equity_after: Decimal | None
    mode: str
