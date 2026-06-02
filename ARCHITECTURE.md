# ARCHITECTURE.md — Deep Funnel Station
## System Design & Module Map v2.0

> **Agent Onboarding Guide:** Paste this file into your AI IDE context at the
> start of any architectural session. It provides the bird's-eye view needed
> to make decisions that respect module boundaries.

---

## 1. SYSTEM OVERVIEW

`deep-funnel-station` is a **4-phase asymmetric data funnel** for quantitative
trading. It processes thousands of market tickers down to a critical set of
high-liquidity contracts suitable for real-time execution.

The architecture is **event-driven, async-first and strictly layered**.
No phase may bypass the next in sequence. No engine may touch the network directly.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL UNIVERSE                            │
│   REST: FMP API · Massive API          WebSocket: Massive Advanced  │
└──────────────┬──────────────────────────────────────┬──────────────┘
               │                                      │
               ▼                                      │
┌──────────────────────────────────┐                  │
│         MARKET DATA HUB          │  Anti-Corruption │
│  (Anti-Corruption Layer / ACL)   │       Layer      │
│  • Exponential Backoff            │                  │
│  • Circuit Breaker               │                  │
│  • Normalizes → MarketSnapshot   │                  │
│  • Validates lineage + schema    │                  │
└──────────────┬───────────────────┘                  │
               │ MarketSnapshot (frozen Pydantic)     │
               ▼                                      │
┌──────────────────────────────────┐                  │
│            PHASE A               │                  │
│   Scanner / Filter (Polling)     │                  │
│  • WorkerPool + API key sharding │                  │
│  • Processes thousands of tickers│                  │
│  • Output: ≤ 300 candidates      │                  │
└──────────────┬───────────────────┘                  │
               │ DataFrame[MarketSnapshot]            │
               ▼                                      │
┌──────────────────────────────────┐                  │
│            PHASE B               │                  │
│  Microstructure Engine (Local)   │                  │
│  • VPIN / OFI matrix processing  │                  │
│  • Zero network calls (isolated) │                  │
│  • Output: Top 20 assets         │                  │
└──────────────┬───────────────────┘                  │
               │ List[MarketSnapshot]                 │
               ▼                                      │
┌──────────────────────────────────┐                  │
│            PHASE C               │                  │
│  Derivatives Engine (Selective)  │                  │
│  • Options chains via Hub only   │                  │
│  • Strike/Expiration selection   │                  │
│  • Output: Top 5 OptionContracts │                  │
└──────────────┬───────────────────┘                  │
               │                                      │
               ▼                                      │
┌──────────────────────────────────┐                  │
│          EVENT BUS               │◄─────────────────┘
│   (asyncio.Queue / Pub-Sub)      │  WebSocket feeds
│  • Decouples producers/consumers │  injected directly
│  • Backpressure: Drop Oldest     │  into Bus for Phase D
│  • Priority lane for Phase D     │
└──────────────┬───────────────────┘
               │ Critical signals (Priority Queue)
               ▼
┌──────────────────────────────────┐
│            PHASE D               │
│  Real-Time Monitor (WebSocket)   │
│  • Tick-by-tick subscription     │
│  • Execution signal generation   │
│  • Output → Frontend via SSE/WS  │
└──────────────┬───────────────────┘
               │
               ▼
┌──────────────────────────────────┐
│           FRONTEND               │
│   Next.js 16 / React 19          │
│  • Dark-mode glassmorphism UI    │
│  • Server Components by default  │
│  • Receives signals from Phase D │
└──────────────────────────────────┘
```

---

## 2. MODULE MAP

### 2.1 Backend (`/backend/`)

```
backend/
├── config/                   # All config files — NO magic numbers in code
│   ├── settings.py           # Pydantic Settings (loads .env)
│   ├── vpin_params.py        # VPIN/OFI calculation parameters
│   └── phase_thresholds.py   # Candidate counts, priority thresholds
│
├── hub/                      # Anti-Corruption Layer
│   ├── market_data_hub.py    # The ONLY file that calls external APIs
│   ├── circuit_breaker.py    # Resilience: circuit breaker implementation
│   ├── backoff.py            # Exponential backoff decorator
│   └── normalizers/          # Per-provider normalization to MarketSnapshot
│       ├── fmp_normalizer.py
│       └── massive_normalizer.py
│
├── models/                   # FINOS-compliant Pydantic schemas
│   ├── market_snapshot.py    # MarketSnapshot — the canonical data object
│   ├── option_contract.py    # OptionContract with Greeks
│   ├── execution_signal.py   # Signal emitted to Phase D / Frontend
│   └── result.py             # Result[T] monad for error handling
│
├── phases/                   # The 4 processing engines
│   ├── phase_a/
│   │   ├── scanner.py        # WorkerPool + API key sharding
│   │   └── worker_pool.py
│   ├── phase_b/
│   │   ├── microstructure_engine.py  # VPIN/OFI (NO network imports)
│   │   └── matrix_processor.py
│   ├── phase_c/
│   │   ├── derivatives_engine.py     # Options chain analysis (NO network)
│   │   └── greeks_calculator.py
│   └── phase_d/
│       ├── realtime_monitor.py       # WebSocket tick consumer
│       └── signal_emitter.py
│
├── bus/                      # Event infrastructure
│   ├── event_bus.py          # asyncio.Queue Pub/Sub
│   └── priority_queue.py     # High-priority lane for Phase D
│
└── tests/                    # Mirrors src structure
    ├── unit/
    ├── integration/
    └── conftest.py
```

### 2.2 Frontend (`/frontend/`)

```
frontend/
├── app/                      # Next.js 16 App Router
│   ├── layout.tsx            # Root layout + Top Nav
│   └── page.tsx              # Entry point
│
├── components/
│   ├── navigation/
│   │   └── TopNavigationBar.tsx   # Glassmorphism nav (Phase 1 only)
│   └── [future phases only when unlocked]
│
├── hooks/                    # Client-side logic separation
│   └── useAuthToken.ts       # Token management (isolated)
│
└── lib/
    └── env.ts                # Environment variable validation
```

---

## 3. CRITICAL BOUNDARIES (MUST NOT CROSS)

| From | To | Status | Reason |
|------|----|--------|--------|
| Phase A | Phase D | ❌ FORBIDDEN | Must go through B → C → Bus |
| Phase B | External API | ❌ FORBIDDEN | Isolation rule — no network imports |
| Phase C | External API | ❌ FORBIDDEN | Isolation rule — data injected by Hub |
| Any engine | Secrets/env | ❌ FORBIDDEN | Only `MarketDataHub` reads config |
| Frontend | Python backend direct | ❌ FORBIDDEN | Only via defined API contract |

---

## 4. DATA FLOW CONTRACTS

### Cross-Phase Object: `MarketSnapshot`
```python
# Canonical object — frozen, validated, lineage-tracked
class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str                    # Uppercase symbol
    exchange: str
    price: Decimal                 # High-precision decimal
    volume: int                    # Cumulative volume ≥ 0
    exchange_timestamp: datetime   # ISO 8601 UTC
    data_lineage: DataLineage      # Source + ingestion latency metadata
```

### Error Handling Contract: `Result[T]`
```python
# Never raise raw exceptions across boundaries
result = await hub.fetch(ticker)
if result.is_failure:
    logger.error("Fetch failed: %s", result.reason)
    return  # Never propagate the raw exception
value = result.unwrap()
```

---

## 5. KEY DESIGN DECISIONS (Architecture Decision Records)

| ADR | Decision | Rationale |
|-----|----------|-----------|
| ADR-001 | Pydantic `frozen=True` for all cross-phase models | Prevents mutation bugs across async tasks |
| ADR-002 | `MarketDataHub` as sole API gateway | Enables circuit breaker, retries, normalization in one place |
| ADR-003 | `asyncio.Queue` as Event Bus | Avoids external broker dependency for Phase 1 |
| ADR-004 | `ProcessPoolExecutor` for Phase B/C | Prevents CPU-bound math from blocking the event loop |
| ADR-005 | `Result[T]` monad over raw exceptions | Forces callers to handle errors explicitly |
| ADR-006 | Config via Pydantic `BaseSettings` | Validates env vars at startup, fails fast if missing |
| ADR-007 | `Decimal` for price fields | Float precision errors are unacceptable in trading systems |

---

## 6. ENTRY POINTS (Where to Start Reading)

| Task | Start Here |
|------|-----------|
| Understanding data flow | `backend/models/market_snapshot.py` |
| API integration | `backend/hub/market_data_hub.py` |
| Adding a scanning strategy | `backend/phases/phase_a/scanner.py` |
| Adding a VPIN calculation | `backend/phases/phase_b/microstructure_engine.py` |
| Understanding the event system | `backend/bus/event_bus.py` |
| Frontend navigation | `frontend/app/layout.tsx` |

---

## 7. TECHNOLOGY DECISIONS

| Layer | Technology | Version | Reason |
|-------|-----------|---------|--------|
| Python | Python | 3.12+ | Required for performance improvements |
| Data validation | Pydantic v2 | 2.x | FINOS CDM-compatible, frozen models |
| Async runtime | asyncio + uvloop | latest | Event-driven architecture |
| HTTP client | httpx | latest | Async-native, supports retries |
| WebSocket | websockets | latest | Pure-Python, production-grade |
| Config | pydantic-settings | 2.x | Validated env vars at startup |
| Testing | pytest + pytest-asyncio | latest | Async test support |
| Type checking | mypy --strict | latest | Wall Street-grade type safety |
| Linting | ruff + black + isort | latest | Zero-config, fast |
| Frontend | Next.js | 16 | App Router, Server Components |
| Styling | Tailwind CSS | 4 | Dark-mode-first utility CSS |
| Components | shadcn/ui | latest | Accessible, composable primitives |
| Charts | Apache ECharts | 5.x | Financial chart capability (Phase 2+) |
