# 📖 Rule Book: Data Hub & API Governance
## `.docs/backend/02-data-hub.md` — v2.0

> **Agent Load Instruction:** Load this file when working on API integration,
> data normalization, error handling for external calls, or secret management.

---

## 1. MISSION: ANTI-CORRUPTION LAYER (ACL)

The `MarketDataHub` is a **wall between external chaos and internal order**.
External APIs return inconsistent field names, wrong types, missing fields, and
unexpected errors. The Hub translates all of that into a single trusted contract:
the `MarketSnapshot`.

**Core principle (NautilusTrader-derived):** The engine layer should never
know that an external API exists. It only knows `MarketSnapshot`.

---

## 2. WHAT THE HUB IS (AND IS NOT)

| It IS | It IS NOT |
|-------|----------|
| The sole gateway to all external APIs | A business logic engine |
| Responsible for retries and circuit breaking | A data store or cache |
| The normalizer of all provider formats | A direct pass-through |
| The validator of data lineage | A queue or event bus |
| The reader of API secrets from environment | A configuration UI |

---

## 3. ARCHITECTURAL PATTERN

```python
# The ONLY valid call pattern from an engine:
snapshot: MarketSnapshot = await hub.get_market_snapshot(ticker="AAPL")

# The Hub handles internally:
# 1. Selects the correct provider (FMP / Massive)
# 2. Manages API key rotation
# 3. Applies exponential backoff on failure
# 4. Checks circuit breaker state
# 5. Normalizes response to MarketSnapshot
# 6. Attaches data_lineage metadata
# 7. Returns Result[MarketSnapshot]
```

---

## 4. RESILIENCE IMPLEMENTATION

### 4.1 Exponential Backoff
```python
from backend.hub.backoff import exponential_backoff

@exponential_backoff(
    max_retries=3,
    base_delay_seconds=1.0,
    max_delay_seconds=30.0,
    jitter=True,
)
async def _call_fmp_api(self, endpoint: str, params: dict[str, str]) -> dict:
    """Calls FMP REST API with backoff protection.

    Args:
        endpoint: The API endpoint path.
        params  : Query parameters dict.

    Returns:
        Raw API response as dict.

    Raises:
        ExternalAPIError: If all retries are exhausted.
    """
    ...
```

### 4.2 Circuit Breaker
```python
# State machine: CLOSED → OPEN → HALF-OPEN → CLOSED
# When OPEN: Hub returns Result.failure() immediately (no network call)
# When HALF-OPEN: Hub tries one probe request
# Threshold: 5 consecutive failures within 60 seconds → trips to OPEN

circuit_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout_seconds=60,
    provider_name="fmp",
)
```

### 4.3 The `Result[T]` Return Pattern
```python
# Hub NEVER raises raw exceptions to callers.
# It always returns a Result object.

result: Result[MarketSnapshot] = await hub.get_market_snapshot("AAPL")

# Callers handle it explicitly:
if result.is_failure:
    logger.warning("Hub returned failure: %s", result.reason)
    # Handle gracefully — discard this ticker, do not propagate exception
    return

snapshot = result.unwrap()  # Safe — only called after is_success check
```

---

## 5. SECRET MANAGEMENT

### Rules
- `ZERO` API keys in source code. Not even in test files.
- `ZERO` hardcoded URLs that contain tokens (e.g. `?apikey=abc123`).
- All secrets load via `pydantic-settings` from environment variables.
- The Hub validates ALL required keys at startup before any engine initializes.

### Pattern
```python
# config/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, field_validator

class MarketDataSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    fmp_api_key: SecretStr          # Loaded from FMP_API_KEY env var
    massive_api_key: SecretStr      # Loaded from MASSIVE_API_KEY env var
    massive_ws_url: str             # Loaded from MASSIVE_WS_URL env var
    alpaca_api_key: SecretStr       # Loaded from ALPACA_API_KEY env var
    alpaca_secret_key: SecretStr    # Loaded from ALPACA_SECRET_KEY env var
    alpaca_base_url: str            # Loaded from ALPACA_BASE_URL env var

    @field_validator("fmp_api_key", "massive_api_key", "alpaca_api_key", "alpaca_secret_key")
    @classmethod
    def validate_key_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key cannot be empty")
        return value
```

### Startup Validation
```python
# hub/market_data_hub.py
class MarketDataHub:
    def __init__(self, settings: MarketDataSettings) -> None:
        # Validate settings at construction time — fail fast before any engine starts
        self._settings = settings
        self._validate_connectivity()  # Optional: probe APIs on startup

    def _validate_connectivity(self) -> None:
        """Verifies that all required secrets are present and non-empty.
        Blocks system initialization if any key is missing or malformed.
        """
        # pydantic-settings already validated at parse time via @field_validator
        logger.info("MarketDataHub initialized. Providers: FMP, Massive, Alpaca.")
```

---

## 6. NORMALIZER PATTERN (Per Provider)

Each API provider gets its own normalizer class. The Hub calls the correct
one and the engine layer never sees raw API responses.

```python
# hub/normalizers/fmp_normalizer.py
from datetime import datetime, timezone
from decimal import Decimal
from backend.models.market_snapshot import MarketSnapshot, DataLineage
import time

class FmpNormalizer:
    """Transforms FMP API raw response into a canonical MarketSnapshot.

    Raises:
        ValidationError: If the raw response is missing required fields.
    """

    PROVIDER_NAME: str = "fmp"

    def normalize(self, raw: dict, ingestion_start_ns: int) -> MarketSnapshot:
        """Converts an FMP ticker response to MarketSnapshot.

        Args:
            raw             : The raw dict from FMP REST API.
            ingestion_start_ns: nanosecond timestamp when the fetch started.

        Returns:
            A validated, frozen MarketSnapshot.

        Raises:
            KeyError    : If a required FMP field is absent.
            ValidationError: If types fail Pydantic validation.
        """
        ingestion_latency_ms = (time.time_ns() - ingestion_start_ns) // 1_000_000

        return MarketSnapshot(
            ticker=raw["symbol"].upper(),
            exchange=raw.get("exchange", "UNKNOWN"),
            price=Decimal(str(raw["price"])),      # str conversion avoids float drift
            volume=int(raw["volume"]),
            exchange_timestamp=datetime.fromtimestamp(
                raw["timestamp"], tz=timezone.utc
            ),
            data_lineage=DataLineage(
                source=self.PROVIDER_NAME,
                ingestion_latency_ms=ingestion_latency_ms,
                raw_field_count=len(raw),
            ),
        )
```

---

## 7. ANTI-PATTERNS TO REJECT

| Anti-Pattern | Example | Rejection Reason |
|-------------|---------|-----------------|
| Direct API call in engine | `httpx.get(...)` inside `phase_b/` | Violates ACL — REJECT |
| Raw dict as inter-phase object | `return {"price": 100}` across boundary | No schema validation — REJECT |
| Key in source code | `API_KEY = "abc123xyz"` | Security violation — REJECT |
| Silent failure | `except: return None` | Hides errors — REJECT |
| Float for price | `price: float = 100.05` | Precision risk in trading — REJECT, use `Decimal` |
| Bare exception to caller | `raise Exception(...)` from Hub | Must use `Result.failure()` — REJECT |
| Missing data_lineage | `MarketSnapshot(lineage=None)` | Orphan data — REJECTED by any engine |

---

## 8. PROVIDER FAILOVER STRATEGY

```
Primary REST (Global/Financials):    FMP REST API
Secondary REST (US Market Data):     Alpaca REST API
Exclusive Streaming (Phase D WS):    Massive WebSocket
Tertiary Failover/Degradation:       Graceful degradation (log + skip ticker for this cycle)

Failover/routing logic lives ENTIRELY in MarketDataHub.
Engines receive a MarketSnapshot regardless of which provider served it.
```
