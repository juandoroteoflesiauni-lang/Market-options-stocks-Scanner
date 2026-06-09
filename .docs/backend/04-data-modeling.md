# 📖 Rule Book: Data Modeling & Schema Governance
## `.docs/backend/04-data-modeling.md` — v2.0

> **Agent Load Instruction:** Load this file when creating or modifying Pydantic
> models, data schemas, or anything related to cross-phase data contracts.
> This is the FINOS CDM-aligned standard for this system.

---

## 1. MISSION: A SINGLE SOURCE OF TRUTH

Every datum that moves through this system must be traceable back to a
canonical, immutable model. No more dicts. No more ad-hoc dataclasses.
No more `"price"` string keys in random places.

The `MarketSnapshot` is the system's **Common Domain Model (CDM)** — inspired
by the FINOS/ISDA standard used by Goldman Sachs, Morgan Stanley, Deutsche Bank.

---

## 2. THE CANONICAL MODEL: `MarketSnapshot`

```python
# backend/models/market_snapshot.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataLineage(BaseModel):
    """Tracks the provenance of every MarketSnapshot.

    A snapshot without lineage is an "orphan" and will be rejected
    by any Phase B/C engine.
    """
    model_config = ConfigDict(frozen=True)

    source: str               # Provider name: "fmp", "massive", "local"
    ingestion_latency_ms: int = Field(ge=0)  # Milliseconds from fetch to model
    raw_field_count: int = Field(ge=0)       # How many fields the raw response had


class MarketSnapshot(BaseModel):
    """Canonical, immutable market data object. The inter-phase contract.

    Aligned with FINOS CDM standards for financial instrument data.
    All fields are validated at construction time. Any invalid snapshot
    must be discarded at Phase A — never propagated.

    Attributes:
        ticker            : Uppercase ticker symbol (e.g., "AAPL", "SPY").
        exchange          : Exchange identifier (e.g., "NASDAQ", "NYSE").
        price             : Last trade price — Decimal for precision safety.
        volume            : Cumulative volume — must be non-negative.
        exchange_timestamp: The timestamp from the exchange, in UTC.
        data_lineage      : Provenance metadata — mandatory, never None.
    """
    model_config = ConfigDict(frozen=True)

    ticker: str
    exchange: str
    price: Decimal = Field(ge=Decimal("0"))
    volume: int = Field(ge=0)
    exchange_timestamp: datetime
    data_lineage: DataLineage

    @field_validator("ticker")
    @classmethod
    def ticker_must_be_uppercase(cls, value: str) -> str:
        """Ensures ticker is uppercase. Rejects empty strings."""
        if not value.strip():
            raise ValueError("Ticker cannot be empty or whitespace.")
        return value.upper().strip()

    @field_validator("exchange_timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        """Ensures exchange timestamp is timezone-aware UTC."""
        if value.tzinfo is None:
            raise ValueError(
                "exchange_timestamp must be timezone-aware (UTC). "
                f"Received naive datetime: {value}"
            )
        return value
```

---

## 3. THE OPTIONS CONTRACT MODEL

```python
# backend/models/option_contract.py
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OptionType(StrEnum):
    """Strict enum — no string literals in business logic."""
    CALL = "call"
    PUT = "put"


class Greeks(BaseModel):
    """Option Greeks — all as Decimal for precision."""
    model_config = ConfigDict(frozen=True)

    delta: Decimal = Field(ge=Decimal("-1"), le=Decimal("1"))
    gamma: Decimal = Field(ge=Decimal("0"))
    theta: Decimal                    # Typically negative — no lower bound constraint
    vega: Decimal = Field(ge=Decimal("0"))


class OptionContract(BaseModel):
    """A single options contract node selected by Phase C.

    Uses model composition (not flat dicts) as required by the architecture.

    Attributes:
        underlying_ticker: The asset the option is written on.
        strike           : Strike price — Decimal precision.
        expiration       : Expiration date (not datetime — date precision sufficient).
        option_type      : CALL or PUT enum — no raw strings.
        open_interest    : Current open interest in contracts.
        greeks           : Composed Greeks sub-model.
        snapshot_at_selection: The MarketSnapshot that triggered Phase C selection.
    """
    model_config = ConfigDict(frozen=True)

    underlying_ticker: str
    strike: Decimal = Field(ge=Decimal("0"))
    expiration: date
    option_type: OptionType
    open_interest: int = Field(ge=0)
    greeks: Greeks
    snapshot_at_selection: MarketSnapshot   # Full lineage preserved
```

---

## 4. THE EXECUTION SIGNAL MODEL

```python
# backend/models/execution_signal.py
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SignalStrength(StrEnum):
    """Typed signal strength — prevents typo-based bugs."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExecutionSignal(BaseModel):
    """The output of Phase D — sent to the Frontend.

    This is the final product of the entire funnel pipeline.
    """
    model_config = ConfigDict(frozen=True)

    contract: OptionContract
    signal_strength: SignalStrength
    emitted_at: datetime             # UTC timestamp of signal emission
    phase_d_latency_ms: int          # Time from WebSocket tick to signal emit
    rationale: str                   # Human-readable summary for the UI
```

---

## 5. THE RESULT MONAD

```python
# backend/models/result.py
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    """Type-safe wrapper for all Hub return values.

    Enforces explicit error handling at every call site.
    The Hub never raises raw exceptions — it returns Result objects.

    Usage:
        result = await hub.fetch_snapshot("AAPL")
        if result.is_failure:
            logger.warning("Failed: %s", result.reason)
            return
        snapshot = result.unwrap()
    """
    model_config = ConfigDict(frozen=True)

    _value: T | None = None
    _reason: str | None = None
    is_success: bool

    @classmethod
    def success(cls, value: T) -> "Result[T]":
        instance = cls(is_success=True)
        object.__setattr__(instance, "_value", value)
        return instance

    @classmethod
    def failure(cls, reason: str) -> "Result[T]":
        instance = cls(is_success=False)
        object.__setattr__(instance, "_reason", reason)
        return instance

    @property
    def is_failure(self) -> bool:
        return not self.is_success

    @property
    def reason(self) -> str:
        if self._reason is None:
            raise RuntimeError("Cannot access reason on a successful Result.")
        return self._reason

    def unwrap(self) -> T:
        """Returns the value. Raises RuntimeError if called on a failure."""
        if not self.is_success or self._value is None:
            raise RuntimeError(
                f"Cannot unwrap a failed Result. Reason: {self._reason}"
            )
        return self._value
```

---

## 6. VALIDATION RULES

| Field | Rule | Rejection Message |
|-------|------|------------------|
| `price` | `>= 0` | "Price cannot be negative — source: {provider}" |
| `volume` | `>= 0` | "Volume cannot be negative — source: {provider}" |
| `ticker` | Uppercase, non-empty | "Invalid ticker format" |
| `exchange_timestamp` | Timezone-aware UTC | "Naive datetime rejected — must be UTC" |
| `data_lineage` | Not None, source non-empty | "Orphan snapshot rejected by Phase B/C" |
| `greeks.delta` | `-1 ≤ delta ≤ 1` | "Delta out of valid range" |
| `greeks.gamma` | `>= 0` | "Gamma cannot be negative" |
| Any price field | `Decimal`, NOT `float` | Auto-enforced by type system |

---

## 7. FORBIDDEN PATTERNS

```python
# ❌ FORBIDDEN: Generic dict as inter-phase object
def process(data: dict) -> dict: ...

# ❌ FORBIDDEN: Mutable dataclass
@dataclass
class Snapshot:
    price: float    # mutable, no validation

# ❌ FORBIDDEN: Optional lineage
class Snapshot(BaseModel):
    data_lineage: dict | None = None   # Orphan data

# ❌ FORBIDDEN: Float for prices
price: float = 100.055              # Precision loss in calculation

# ❌ FORBIDDEN: String literals for types
option_type: str = "call"           # Typo-prone, no IDE support

# ✅ CORRECT: Frozen Pydantic + Decimal + Enum + Mandatory lineage
class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    price: Decimal
    option_type: OptionType
    data_lineage: DataLineage         # Never Optional
```

---

## 8. MODEL INHERITANCE RULES

```python
# ALLOWED: Extension for enriched data
class EnrichedSnapshot(MarketSnapshot):
    """Adds computed analytics while preserving the base contract."""
    vpin_score: float
    ofi_score: float
    # frozen=True is inherited from MarketSnapshot

# FORBIDDEN: Overriding base field types
class BadSnapshot(MarketSnapshot):
    price: float    # Weakening the type contract — REJECTED

# FORBIDDEN: Removing mandatory fields
class IncompleteSnapshot(MarketSnapshot):
    data_lineage: DataLineage | None = None  # Weakening contract — REJECTED
```
