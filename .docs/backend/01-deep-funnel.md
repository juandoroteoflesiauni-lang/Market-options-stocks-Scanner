# 📖 Rule Book: Deep Funnel Architecture
## `.docs/backend/01-deep-funnel.md` — v2.0

> **Agent Load Instruction:** Load this file when working on phase routing,
> inter-phase data flow, or the WorkerPool/scanner. Do NOT load it for
> pure API or modeling tasks (use `02-data-hub.md` or `04-data-modeling.md`).

---

## 1. SYSTEM MISSION

Process asymmetric market data: filter thousands of tickers down to a
critical high-liquidity execution set. The system must never short-circuit
the funnel — each phase exists to reduce noise for the next.

**The funnel ratio:** ~5,000 tickers → 300 → 20 → 5 → real-time execution.

---

## 2. THE 4-PHASE TOPOLOGY (Inviolable)

The signal routing MUST follow this exact sequence. Any shortcut is an
architectural violation and will be rejected.

### Phase A — Scanner / Filter (Polling)
```
Source   : REST API (FMP / Massive) via MarketDataHub
Technique: WorkerPool with API key sharding
Input    : Universe of market tickers (~5,000)
Output   : DataFrame of ≤ 300 candidate MarketSnapshot objects
Rules    :
  - Phase A owns "dirty data." It must discard any ticker that fails
    MarketSnapshot schema validation BEFORE passing to Phase B.
  - The WorkerPool must shard API keys to stay within rate limits.
  - Each worker processes an isolated key shard (no key sharing).
  - Concurrency: asyncio tasks, not threads.
```

### Phase B — Microstructure Engine (Local Analysis)
```
Source   : MarketSnapshot objects injected by MarketDataHub (NOT fetched directly)
Technique: Matrix-based local processing — VPIN / OFI
Input    : Up to 300 MarketSnapshot candidates
Output   : List of 20 assets with highest execution probability
Rules    :
  - Phase B has ZERO network imports. It is a pure computation engine.
  - All data arrives via dependency injection from Phase A pipeline.
  - CPU-bound calculations run in ProcessPoolExecutor (never block event loop).
  - Parameters (VPIN bucket size, OFI window) live in config/, not here.
```

### Phase C — Derivatives Engine (Selective)
```
Source   : Top 20 MarketSnapshot objects from Phase B
Technique: Full options chain download (Open Interest, Greeks)
Input    : 20 candidate assets
Output   : Top 5 OptionContract objects (definitively selected)
Rules    :
  - Phase C has ZERO network imports. Same isolation as Phase B.
  - Options data is fetched by MarketDataHub and INJECTED into Phase C.
  - Strike / Expiration selection uses configured liquidity node rules.
  - OptionContract model must include: strike, expiration, option_type,
    delta, gamma, theta, vega. No flat lists — use model composition.
```

### Phase D — Real-Time Monitor (WebSocket)
```
Source   : Exclusive WebSocket connections (Massive Advanced)
Technique: Low-latency tick-by-tick subscription
Input    : Top 5 contracts selected by Phase C
Output   : ExecutionSignal objects published to Priority Queue → Frontend
Rules    :
  - Phase D is the ONLY consumer of WebSocket feeds.
  - Phase D is a HIGH-PRIORITY consumer on the Event Bus.
  - Signals must be emitted via the Priority Queue, not the standard queue.
  - Phase D does NOT perform analysis — it monitors and emits signals only.
```

---

## 3. THE ABSOLUTE PROHIBITION

```
Phase A ────────────────────────────────────────► Phase D
                          ❌ FORBIDDEN

Correct flow:
Phase A → [MarketSnapshot] → Phase B → [MarketSnapshot] → Phase C
       → [OptionContract] → EventBus → Phase D → [ExecutionSignal]
```

If any code path attempts to connect Phase A output directly to Phase D
consumers, it must be REJECTED and refactored.

---

## 4. DATA CONTRACTS AT EACH BOUNDARY

Every object crossing a phase boundary must:
1. Be a frozen Pydantic model (`model_config = ConfigDict(frozen=True)`)
2. Pass schema validation at the ingress of the receiving phase
3. Carry `data_lineage` metadata (source, timestamp, ingestion latency)
4. Be **discarded, not mutated** if a calculation adds new fields
   (create a new derived model instead)

```python
# CORRECT: Create a derived model for enriched data
class EnrichedSnapshot(MarketSnapshot):
    vpin_score: float
    ofi_score: float
    # model_config inherited from parent → still frozen

# FORBIDDEN: Mutating an existing object
snapshot.price = new_price  # Will raise FrozenInstanceError — intended
```

---

## 5. WORKERPOOOL — PHASE A IMPLEMENTATION PATTERN

```python
class ApiKeyPool:
    """Manages API key rotation across concurrent workers.
    
    Args:
        api_keys: List of valid API keys from environment.
    """
    def __init__(self, api_keys: list[str]) -> None:
        self._keys: list[str] = api_keys
        self._index: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire_key(self) -> str:
        async with self._lock:
            key = self._keys[self._index % len(self._keys)]
            self._index += 1
            return key


async def scan_ticker_batch(
    ticker_batch: list[str],
    hub: MarketDataHub,
    key_pool: ApiKeyPool,
) -> list[MarketSnapshot]:
    """Scans a batch of tickers and returns validated snapshots.
    
    Args:
        ticker_batch: Subset of tickers assigned to this worker.
        hub: The MarketDataHub instance for data fetching.
        key_pool: Shared API key pool for rate-limit management.
    
    Returns:
        List of valid MarketSnapshot objects. Invalid tickers are discarded.
    """
    api_key = await key_pool.acquire_key()
    results: list[MarketSnapshot] = []
    
    for ticker in ticker_batch:
        result = await hub.fetch_snapshot(ticker=ticker, api_key=api_key)
        if result.is_success:
            results.append(result.value)
        else:
            logger.warning(
                "Phase A: Discarding invalid ticker [PD-1]",
                extra={"ticker": ticker, "reason": result.reason},
            )
    
    return results
```

---

## 6. PHASE TRANSITION CHECKLIST

Before passing data from one phase to the next, verify:

```
[ ] Object is an instance of the expected Pydantic model
[ ] `data_lineage` field is populated (not None, not empty dict)
[ ] `exchange_timestamp` is a valid UTC datetime
[ ] `price` and `volume` are ≥ 0
[ ] The object was NOT mutated (it is frozen — Pydantic enforces this)
[ ] Log the phase transition at INFO level with ticker + timestamp
```

---

## 7. COMMON VIOLATIONS TO REJECT

| Violation | Symptom | Action |
|-----------|---------|--------|
| Phase bypass | Direct call from Phase A to Phase D logic | REJECT, refactor to use EventBus |
| Dirty data propagation | Non-validated dict passed between phases | REJECT, add Pydantic validator at boundary |
| Network import in Phase B/C | `import httpx` in engine file | REJECT, move to Hub |
| Mutable inter-phase object | Dict or plain dataclass used as contract | REJECT, replace with frozen Pydantic |
| Synchronous sleep in Phase A worker | `time.sleep()` for rate limiting | AUTO-FIX with `await asyncio.sleep()` |
