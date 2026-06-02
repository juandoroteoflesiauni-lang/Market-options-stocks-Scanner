# 📖 Rule Book: Python Standards & Clean Code
## `.docs/backend/03-python-standards.md` — v2.0

> **Agent Load Instruction:** Load this file for ALL Python code generation
> or review tasks. These standards apply to every `.py` file in the repo.
> Non-compliance causes automated CI failures.

---

## 1. TOOLCHAIN (Non-Negotiable)

Every Python file must pass these tools with ZERO warnings before commit:

| Tool | Purpose | Config File |
|------|---------|-------------|
| `black` | Auto-format | `pyproject.toml` |
| `isort` | Import ordering (compatible with black) | `pyproject.toml` |
| `ruff` | Lint (replaces flake8, pylint, pyupgrade) | `pyproject.toml` |
| `mypy --strict` | Static type checking | `pyproject.toml` |
| `bandit` | Security vulnerability scanner | `pyproject.toml` |
| `pip-audit` | Dependency CVE scanner | CI only |

The agent **must generate code that would pass all of these** before
presenting it. Do not present code with known type errors or lint issues.

---

## 2. TYPE SYSTEM (Mandatory)

### 2.1 Every Function Must Be Fully Typed
```python
# CORRECT
async def calculate_vpin(
    snapshot: MarketSnapshot,
    bucket_size: int,
    window_count: int,
) -> float:
    ...

# FORBIDDEN — missing return type and parameter types
def calculate_vpin(snapshot, bucket_size, window_count):
    ...
```

### 2.2 No `Any` — Use Specific Types
```python
# FORBIDDEN
from typing import Any
def process(data: Any) -> Any: ...

# CORRECT
def process(data: MarketSnapshot) -> EnrichedSnapshot: ...
```

### 2.3 Use Precise Types from `collections.abc`
```python
from collections.abc import Sequence, Mapping, AsyncGenerator
# NOT from typing import List, Dict — those are deprecated
```

### 2.4 Decimal for All Price/Financial Values
```python
from decimal import Decimal

# FORBIDDEN — float causes precision errors in trading
price: float = 100.05

# CORRECT
price: Decimal = Decimal("100.05")
```

---

## 3. NAMING CONVENTIONS

| Category | Convention | Example |
|----------|-----------|---------|
| Classes | `PascalCase` | `MarketDataHub`, `VpinCalculator` |
| Functions / Methods | `snake_case` | `calculate_vpin`, `fetch_snapshot` |
| Constants | `UPPER_CASE` | `MAX_CANDIDATES = 300` |
| Private members | `_single_underscore` | `_circuit_breaker` |
| Type variables | `T`, `TResult` | `T = TypeVar("T")` |
| Single-letter variables | **FORBIDDEN** | Use `ticker` not `t`, `snapshot` not `s` |
| Generic names | **FORBIDDEN** | Never use `data`, `val`, `obj`, `tmp` |

---

## 4. FUNCTION DESIGN

### 4.1 Single Responsibility Rule
- One function = one logical task.
- **Hard limit: 30 lines of code.** If it exceeds this, refactor.
- A function that both fetches AND normalizes data violates SRP.
  Split into `_fetch_raw()` and `_normalize()`.

### 4.2 Docstrings (Google Style — Mandatory for Complex Functions)
```python
async def fetch_option_chain(
    ticker: str,
    expiration_date: date,
) -> Result[list[OptionContract]]:
    """Downloads and validates a complete option chain for a single expiration.

    Args:
        ticker         : The uppercase ticker symbol (e.g., "AAPL").
        expiration_date: The options expiration date to fetch.

    Returns:
        A Result containing a list of OptionContract objects on success,
        or a Result.failure with an error message if the API is unavailable.

    Raises:
        ValidationError: If the API response fails schema validation.
    """
```

### 4.3 No `print()` — Use Structured Logging
```python
import logging

logger = logging.getLogger(__name__)

# CORRECT
logger.info("Phase A: Scan complete", extra={"candidate_count": len(candidates)})
logger.error("Hub: API call failed", extra={"ticker": ticker}, exc_info=True)
logger.debug("VPIN calculation", extra={"ticker": ticker, "vpin": vpin_score})

# FORBIDDEN
print(f"Scan complete: {len(candidates)} candidates")
```

---

## 5. ERROR HANDLING

### 5.1 Specific Exception Types Only
```python
# CORRECT
try:
    snapshot = await hub.fetch_snapshot(ticker)
except httpx.TimeoutException as exc:
    logger.error("API timeout for %s", ticker, exc_info=True)
    return Result.failure(reason=f"Timeout: {exc}")
except ValidationError as exc:
    logger.error("Schema validation failed for %s", ticker, exc_info=True)
    return Result.failure(reason=f"Validation: {exc}")

# FORBIDDEN — all of these
except:          # Too broad
    pass         # Silent failure
except Exception as e:
    print(e)     # print + generic exception
```

### 5.2 Never Propagate Raw Exceptions Across Phase Boundaries
```python
# Across boundaries: always use Result[T]
return Result.failure(reason="API unavailable")

# Within a module: specific exceptions are acceptable
raise VpinCalculationError(f"Insufficient data for ticker {ticker}")
```

### 5.3 Clean Shutdown (Signal Handling)
```python
import asyncio
import signal

async def shutdown(signal_received: signal.Signals, loop: asyncio.AbstractEventLoop) -> None:
    """Handles SIGINT/SIGTERM for graceful shutdown.
    
    Args:
        signal_received: The OS signal that triggered shutdown.
        loop           : The running event loop.
    """
    logger.info("Shutdown initiated by signal: %s", signal_received.name)
    
    # Cancel all running tasks
    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()
    logger.info("Clean shutdown complete.")
```

---

## 6. CONCURRENCY RULES

### 6.1 Async for I/O — Always
```python
# CORRECT — non-blocking
async def fetch_all_snapshots(tickers: list[str]) -> list[MarketSnapshot]:
    tasks = [hub.fetch_snapshot(ticker) for ticker in tickers]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r.value for r in results if isinstance(r, Result) and r.is_success]

# FORBIDDEN — blocks event loop
def fetch_all_snapshots(tickers):
    return [requests.get(url).json() for url in urls]  # sync HTTP in async context
```

### 6.2 CPU-Bound Work — ProcessPoolExecutor
```python
import asyncio
from concurrent.futures import ProcessPoolExecutor

# CORRECT — offloads to separate process, does not block event loop
async def run_vpin_calculation(
    snapshots: list[MarketSnapshot],
    executor: ProcessPoolExecutor,
) -> list[float]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        executor,
        _calculate_vpin_sync,   # Pure sync function, no async inside
        snapshots,
    )

# FORBIDDEN — runs heavy math in the event loop
async def run_vpin_calculation(snapshots):
    return [expensive_math(s) for s in snapshots]  # Blocks event loop
```

### 6.3 Never Use `time.sleep()` in Async Code
```python
# FORBIDDEN
time.sleep(1.0)             # Blocks event loop thread

# CORRECT
await asyncio.sleep(1.0)    # Yields control to event loop
```

---

## 7. CONFIGURATION MANAGEMENT

All tunable parameters must live in `config/` files, loaded via
`pydantic-settings`. No exceptions.

```python
# config/phase_thresholds.py
from pydantic import BaseModel, PositiveInt, PositiveFloat

class PhaseThresholds(BaseModel):
    """Configurable thresholds for the Deep Funnel phases."""
    
    phase_a_max_candidates: PositiveInt = 300
    phase_b_top_n_assets: PositiveInt = 20
    phase_c_top_n_contracts: PositiveInt = 5
    event_bus_max_queue_size: PositiveInt = 10_000
    vpin_bucket_size: PositiveInt = 50
    ofi_window_ticks: PositiveInt = 100

# FORBIDDEN — magic numbers in source
if len(candidates) > 300:     # Where does 300 come from?
    candidates = candidates[:300]

# CORRECT
if len(candidates) > thresholds.phase_a_max_candidates:
    candidates = candidates[:thresholds.phase_a_max_candidates]
```

---

## 8. MODULE STRUCTURE

```python
# Standard import order (isort enforces this automatically):
# 1. Standard library
import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

# 2. Third-party
import httpx
from pydantic import ValidationError

# 3. Internal (absolute imports only — no relative imports across modules)
from backend.models.market_snapshot import MarketSnapshot
from backend.hub.market_data_hub import MarketDataHub

# FORBIDDEN
from ..models import snapshot    # Relative imports cause refactoring pain
import *                         # Wildcard imports hide dependencies
```
