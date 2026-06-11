"""
Domain contracts for backtesting, macro liquidity and smart-money flows.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BacktestInput(BaseModel):
    """Validated backtest input series."""

    model_config = ConfigDict(frozen=True)

    close: tuple[float, ...]
    signal: tuple[int, ...]
    initial_capital: float = 10_000.0
    risk_free_rate: float = 0.0

    @model_validator(mode="after")
    def validate_lengths(self: BacktestInput) -> BacktestInput:
        if len(self.close) != len(self.signal):
            raise ValueError("close/signal length mismatch")
        if len(self.close) < 10:
            raise ValueError("At least 10 data points required.")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be > 0.")
        bad = [value for value in self.signal if value not in (-1, 0, 1)]
        if bad:
            raise ValueError("signal values must be in {-1, 0, 1}")
        return self

    @classmethod
    def from_lists(
        cls: type[BacktestInput],
        close: list[float],
        signal: list[int],
        initial_capital: float = 10_000.0,
        risk_free_rate: float = 0.0,
    ) -> BacktestInput:
        return cls(
            close=tuple(float(x) for x in close),
            signal=tuple(int(x) for x in signal),
            initial_capital=float(initial_capital),
            risk_free_rate=float(risk_free_rate),
        )


class TradeRecord(BaseModel):
    """Executed trade summary."""

    model_config = ConfigDict(frozen=True)

    trade_id: int
    entry_price: float
    exit_price: float
    return_pct: float
    is_winner: bool


class BacktestResult(BaseModel):
    """Vectorized backtest result envelope."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    total_return_pct: float | None = None
    buy_and_hold_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    sharpe_ratio: float | None = None
    trades_count: int | None = None
    win_rate_pct: float | None = None
    avg_trade_return_pct: float | None = None
    periods_in_market_pct: float | None = None
    n_periods: int = 0
    trade_log: tuple[TradeRecord, ...] = Field(default_factory=tuple)
    equity_curve: tuple[float, ...] = Field(default_factory=tuple)


class MacroLiquidityInput(BaseModel):
    """Validated macro-liquidity time series."""

    model_config = ConfigDict(frozen=True)

    walcl: tuple[float, ...]
    wtregen: tuple[float, ...]
    rrpontsyd: tuple[float, ...]
    nfci: tuple[float | None, ...]
    trend_weeks: int = 4

    @model_validator(mode="after")
    def validate_lengths(self: MacroLiquidityInput) -> MacroLiquidityInput:
        lengths = {len(self.walcl), len(self.wtregen), len(self.rrpontsyd), len(self.nfci)}
        if len(lengths) != 1:
            raise ValueError("All series must have equal length.")
        if len(self.walcl) < self.trend_weeks + 1:
            raise ValueError(f"Need at least {self.trend_weeks + 1} observations.")
        return self

    @classmethod
    def from_lists(
        cls: type[MacroLiquidityInput],
        walcl: list[float],
        wtregen: list[float],
        rrpontsyd: list[float],
        nfci: list[float | None],
        trend_weeks: int = 4,
    ) -> MacroLiquidityInput:
        return cls(
            walcl=tuple(float(x) for x in walcl),
            wtregen=tuple(float(x) for x in wtregen),
            rrpontsyd=tuple(float(x) for x in rrpontsyd),
            nfci=tuple(None if x is None else float(x) for x in nfci),
            trend_weeks=trend_weeks,
        )


class NetLiquidityMetrics(BaseModel):
    """Net liquidity result contract."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    walcl: float | None = None
    wtregen: float | None = None
    rrpontsyd: float | None = None
    net_liquidity: float | None = None
    net_liquidity_nw: float | None = None
    net_liquidity_chg_pct: float | None = None
    nfci: float | None = None
    nfci_regime: str = "N/D"
    liquidity_drain: bool = False
    drain_severity: str = "NONE"


class RawTransaction(BaseModel):
    """Canonical insider transaction record."""

    model_config = ConfigDict(frozen=True)

    insider_name: str
    transaction_type: str
    shares: float = 0.0
    transaction_price: float = 0.0
    transaction_date: str | None = None

    @field_validator("transaction_type")
    @classmethod
    def validate_transaction_type(cls: type[RawTransaction], value: str) -> str:
        if value not in ("Purchase", "Sale"):
            raise ValueError("transaction_type must be 'Purchase' or 'Sale'")
        return value


class InsiderFlowProfile(BaseModel):
    """Aggregated insider-flow metrics."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    score: float = 0.0
    bias: str = "NEUTRAL"
    buy_shares: int = 0
    sell_shares: int = 0
    net_shares: int = 0
    buy_transactions: int = 0
    sell_transactions: int = 0
    total_transactions: int = 0
    buy_value_usd: float = 0.0
    sell_value_usd: float = 0.0
    net_value_usd: float = 0.0
    insiders_buying: tuple[str, ...] = Field(default_factory=tuple)
    insiders_selling: tuple[str, ...] = Field(default_factory=tuple)
    latest_transaction_date: str | None = None


class InstitutionalHolder(BaseModel):
    """Institutional holder row."""

    model_config = ConfigDict(frozen=True)

    holder: str
    shares: int | None = None
    pct_held: float | None = None
    value_usd: float | None = None
    date_reported: str | None = None


class InstitutionalFlowProfile(BaseModel):
    """Aggregated institutional-ownership metrics."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    inst_ownership_pct: float | None = None
    insider_pct: float | None = None
    top_holders: tuple[InstitutionalHolder, ...] = Field(default_factory=tuple)
    total_institutions: int | None = None


class DIXResult(BaseModel):
    """Institutional Dark Pool Index (DIX) result contract."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    error: str | None = None
    dix_value: float = 0.0
    dix_avg_20: float = 0.0
    dix_bias: str = "NEUTRAL"  # BULLISH | BEARISH | NEUTRAL
    dix_divergence: bool = False
    short_volume_ratio: float = 0.0
    total_volume: float = 0.0
    short_volume: float = 0.0
    as_of: str = ""


__all__ = [
    "BacktestInput",
    "BacktestResult",
    "DIXResult",
    "InsiderFlowProfile",
    "InstitutionalFlowProfile",
    "InstitutionalHolder",
    "MacroLiquidityInput",
    "NetLiquidityMetrics",
    "RawTransaction",
    "TradeRecord",
]


# ─────────────────────────────────────────────────
# MIGRATION AUDIT — SECTOR: FUNDAMENTALES
# Archivo: flow_models.py
# Eliminado: referencias del sistema anterior en encabezado y ruido no contractual
# Preservado: contratos de backtest/liquidez/flows y validators completos
# Pendientes: ninguno
# ─────────────────────────────────────────────────
