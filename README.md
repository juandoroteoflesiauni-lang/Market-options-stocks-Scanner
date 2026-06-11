# Deep Funnel Station

> An AI-governed quantitative trading terminal with a 4-phase asymmetric data funnel architecture.

[![Backend CI](https://github.com/juandoroteoflesiauni-lang/Market-options-stocks-Scanner/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/juandoroteoflesiauni-lang/Market-options-stocks-Scanner/actions/workflows/backend-ci.yml)
[![Frontend CI](https://github.com/juandoroteoflesiauni-lang/Market-options-stocks-Scanner/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/juandoroteoflesiauni-lang/Market-options-stocks-Scanner/actions/workflows/frontend-ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-black)](https://docs.astral.sh/ruff/)
[![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy--strict-brightgreen)](https://mypy-lang.org/)

---

## What is this?

Deep Funnel Station processes **thousands of market tickers** down to **5 high-liquidity options contracts** for real-time execution. It's a quantitative trading system built with an **AI-native governance framework** — a set of rules (`CLAUDE.md` + `AGENTS.md`) that instruct AI coding assistants to behave as senior financial software developers.

The core insight: AI assistants will generate code that *looks* correct but contains subtle, dangerous bugs in financial systems — floating-point precision errors, hardcoded secrets, missing error handling. This project solves that by encoding domain-specific rules directly into the AI's context.

### The 4-Phase Funnel

```
Phase A — Scanner
  Input:  Thousands of tickers (REST APIs)
  Output: ~300 candidates as MarketSnapshot objects

Phase B — Microstructure Filter (zero network, isolated)
  Input:  300 candidates
  Process: VPIN + Order Flow Imbalance calculations
  Output: Top 20 assets

Phase C — Derivatives Engine
  Input:  Top 20 candidates
  Process: Options chain analysis, strike/expiration selection
  Output: Top 5 OptionContracts

Phase D — Real-Time Monitor (WebSocket tick-by-tick)
  Input:  5 contracts
  Process: Live feed, execution signal generation
  Output: Signals → Frontend Dashboard
```

### Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| `MarketDataHub` as sole API gateway | Single Anti-Corruption Layer — circuit breaker, retries, normalization in one place |
| Pydantic `frozen=True` for all cross-phase models | Prevents mutation bugs across async tasks |
| `asyncio.Queue` as Event Bus | Avoids external broker dependency |
| `ProcessPoolExecutor` for Phase B/C | Prevents CPU-bound math from blocking the event loop |
| `Result[T]` monad over raw exceptions | Forces callers to handle errors explicitly |
| `Decimal` for all price fields | Float precision errors are unacceptable in trading systems |

---

## AI Governance Framework

The `CLAUDE.md` and `AGENTS.md` files are **reusable across any financial software project**. They encode 9 Primary Directives:

| ID | Directive | Why it matters |
|----|-----------|----------------|
| PD-1 | Never hardcode secrets | Exposed API keys = compromised account |
| PD-2 | Never use `float` for money | Precision errors = real losses |
| PD-3 | Always Blueprint before code | No plan = spaghetti code |
| PD-4 | Max 2 files per turn | Controllable, reviewable changes |
| PD-5 | Always read before modifying | Never assume code state |
| PD-6 | Tests mandatory for financial logic | No tests = no confidence |
| PD-7 | Explain in user's language | Communication clarity |
| PD-8 | Complete code, never fragments | Fragments create broken code |
| PD-9 | Update config on session end | Next session depends on it |

The framework also enforces a **Blueprint → Construct → Validate** workflow and defines prohibited actions (e.g., no `float` for money, no `eval()`, no secrets in code).

---

## Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Backend | Python + FastAPI | 3.12+ |
| Data Validation | Pydantic v2 | 2.x (frozen models) |
| Async Runtime | asyncio + uvloop | latest |
| HTTP Client | httpx | latest |
| Config | pydantic-settings | 2.x |
| Testing | pytest + pytest-asyncio | latest |
| Type Checking | mypy --strict | latest |
| Linting | ruff + black + isort | latest |
| Security | bandit + gitleaks + pip-audit | latest |
| Frontend | Next.js + React | 16.x |
| Styling | Tailwind CSS | 4.x |
| Components | shadcn/ui | latest |
| Database | PostgreSQL | 16 |
| Cache | Redis | 7 |

---

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+
- PostgreSQL 16 (or Docker)
- Redis 7 (or Docker)

### Installation

```bash
# Clone the repository
git clone https://github.com/juandoroteoflesiauni-lang/Market-options-stocks-Scanner.git
cd Market-options-stocks-Scanner

# Backend setup
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install poetry
poetry install

# Frontend setup
cd frontend
npm install
cd ..

# Environment variables
cp .env.example .env
# Edit .env with your API keys (NEVER commit this file)

# Start infrastructure
docker-compose up -d postgres redis

# Run quality checks
pre-commit install
pre-commit run --all-files
```

### Running

```bash
# Backend
cd backend
uvicorn main:app --reload

# Frontend
cd frontend
npm run dev
```

### Testing

```bash
# Backend tests
pytest tests/ -v --cov=backend --cov-fail-under=80

# Frontend tests
cd frontend
npm run test
```

---

## CI/CD Pipeline

Every PR automatically runs through 7 quality gates:

| Gate | Tool | Purpose |
|------|------|---------|
| Secret Scan | gitleaks | Detect leaked API keys/tokens |
| Format | black + isort | Code formatting |
| Lint | ruff | Code quality |
| Type Check | mypy --strict | Type safety |
| SAST | bandit | Security vulnerabilities |
| Dependency Audit | pip-audit | CVE scanning |
| Tests | pytest | Coverage ≥ 80% |

---

## Project Structure

```
deep-funnel-station/
├── CLAUDE.md                    # AI agent constitution
├── AGENTS.md                    # Universal agent rules
├── ARCHITECTURE.md              # System design document
├── backend/
│   ├── config/                  # Pydantic Settings (loads .env)
│   ├── hub/                     # Anti-Corruption Layer (sole API gateway)
│   │   ├── market_data_hub.py   # The ONLY file that calls external APIs
│   │   ├── circuit_breaker.py   # Resilience patterns
│   │   └── normalizers/         # Per-provider data normalization
│   ├── models/                  # Pydantic schemas (frozen, immutable)
│   ├── phases/                  # The 4 processing engines
│   │   ├── phase_a/             # Scanner (polling, worker pool)
│   │   ├── phase_b/             # Microstructure (VPIN/OFI, isolated)
│   │   ├── phase_c/             # Derivatives (options chain analysis)
│   │   └── phase_d/             # Real-time monitor (WebSocket)
│   ├── bus/                     # Event infrastructure (asyncio.Queue)
│   └── tests/                   # Unit + integration tests
├── frontend/
│   ├── app/                     # Next.js 16 App Router
│   ├── components/              # React components
│   ├── hooks/                   # Client-side logic
│   └── lib/                     # Utilities
└── .github/
    └── workflows/               # CI/CD pipelines
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

### Quick Start for Contributors

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Read `CLAUDE.md` — understand the Primary Directives
4. Make your changes following the Blueprint → Construct → Validate workflow
5. Ensure all CI gates pass
6. Submit a pull request using the provided template

---

## Security

- **Zero secrets in code:** API keys go in `.env` only (gitignored)
- **Zero float for money:** All financial calculations use `Decimal` (Python) or `string` (TypeScript)
- **Mandatory validation:** All external inputs validated with Pydantic
- **Rate limiting:** Exponential backoff on all exchange API calls
- **Pre-commit hooks:** Code passes quality checks before reaching the repo

To report vulnerabilities: Use [GitHub Security Advisories](https://github.com/juandoroteoflesiauni-lang/Market-options-stocks-Scanner/security/advisories/new) (private).

---

## License

This project is licensed under the Apache License 2.0 — see [LICENSE](LICENSE) for details.

---

## Disclaimer

This terminal handles **real money**. Before operating in production:

1. Test 100% on testnet/sandbox first
2. All tests passing at 100%
3. Review every risk validation manually
4. Audit security code (especially API key handling)
5. Backup database before each deploy
6. Start with minimum position sizes

**AI can make mistakes. ALWAYS review code before executing in production.**
**Never trade with money you cannot afford to lose.**

---

## Acknowledgments

Built with inspiration from:

- [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) — Deterministic, event-driven trading engine
- [cursor-rule-framework](https://github.com/fbrbovic/cursor-rule-framework) — Blueprint/Construct/Validate structure
- [cursor-security-rules](https://github.com/matank001/cursor-security-rules) — Financial API security rules
- [Prompt Engineering Guide](https://github.com/dair-ai/Prompt-Engineering-Guide) — Effective prompts for development
