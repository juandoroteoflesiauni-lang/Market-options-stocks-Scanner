# Contributing to Deep Funnel Station

Thank you for your interest in contributing to an AI-governed quantitative trading terminal.

## Prerequisites

- Python 3.12+
- Node.js 18+
- PostgreSQL 16 (or Docker)
- Redis 7 (or Docker)
- Git

## Development Setup

### 1. Fork and Clone

```bash
git clone https://github.com/YOUR_USERNAME/Market-options-stocks-Scanner.git
cd Market-options-stocks-Scanner
```

### 2. Backend (Python)

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

pip install poetry
poetry install

# Install pre-commit hooks
pip install pre-commit
pre-commit install
```

### 3. Frontend (Next.js)

```bash
cd frontend
npm install
cd ..
```

### 4. Environment Variables

```bash
cp .env.example .env
# Edit .env with your API keys
# NEVER commit this file — it's gitignored
```

### 5. Infrastructure

```bash
docker-compose up -d postgres redis
```

## Code Standards

### Python (Backend)

- **Type hints** on every function — no exceptions
- **Docstrings** in Google style
- **Pydantic v2** for all data models (`frozen=True` for cross-phase objects)
- **`Decimal`** for all financial calculations — never `float`
- **No `print()`** — use the `logging` module
- **No `except: pass`** — specific exceptions with logging
- **No hardcoded secrets** — everything in `.env`
- **Max 300 lines per file** — split if larger

### TypeScript (Frontend)

- **No `any` types** — always explicit types
- **Server Components** by default (no unnecessary `"use client"`)
- **Tailwind CSS** only — no inline styles
- **Zustand** for shared state — not `useState`
- **No `console.log`** — only `console.error` in catch blocks

### Financial Rules (Critical)

- `Decimal` for all money calculations (Python)
- `string` for all money values (TypeScript)
- Validate before execution: quantity > 0, price > 0, total ≤ position limit
- Rate limiting with exponential backoff on all external API calls

## Workflow: Blueprint → Construct → Validate

### 1. Blueprint (Before Writing Code)

Present your plan:

```
"What I'm building for [module]:
- Goal: [one sentence]
- Files to create: [list]
- Files to modify: [list]
- Data schema (inputs/outputs): [description]
- Tests to write: [list]
- Risks: [potential issues]
Approve this plan before I start?"
```

### 2. Construct (With Approval)

- One file at a time
- Complete code (no "... rest of code ...")
- All imports at the top
- Explicit error handling
- Type hints on every function
- CHECKPOINT comments if session may be interrupted

### 3. Validate (Before Closing)

```bash
pytest tests/ -v                          # All tests passing
pre-commit run --all-files                # Zero errors
mypy backend/ --strict                    # Zero type errors
bandit -r backend/ -ll                    # No high/critical issues
```

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

Types:
  feat     → New feature
  fix      → Bug fix
  refactor → Code restructuring (no behavior change)
  docs     → Documentation only
  test     → Adding/updating tests
  ci       → CI/CD configuration
  sec      → Security fix

Examples:
  feat(phase-b): add VPIN calculation with ProcessPoolExecutor
  fix(hub): handle FMP API timeout with exponential backoff
  refactor(models): convert MarketSnapshot to frozen Pydantic v2
```

## Pull Request Process

1. Create a feature branch from `main` or `develop`
2. Make your changes following the workflow above
3. Ensure all CI gates pass
4. Fill out the PR template completely
5. Request a review

### PR Checklist (auto-verified by CI)

**Backend:**
- [ ] All functions have type hints
- [ ] All functions have docstrings
- [ ] No `print()` statements
- [ ] No hardcoded secrets
- [ ] Models use `ConfigDict(frozen=True)`
- [ ] Tests with ≥ 80% coverage

**Frontend:**
- [ ] No `any` types
- [ ] Server Components by default
- [ ] No inline styles
- [ ] No `console.log`

**Security:**
- [ ] No secrets in code
- [ ] No `verify=False` in HTTP clients
- [ ] All inputs validated with Pydantic
- [ ] `gitleaks` scan passes

## Running Tests

```bash
# Backend
pytest tests/ -v --cov=backend --cov-fail-under=80

# Frontend
cd frontend
npm run test

# Type checking
mypy backend/ --strict

# Linting
ruff check backend/ tests/
black --check backend/ tests/
isort --check-only backend/ tests/

# Security
bandit -r backend/ -ll
pip-audit --strict
```

## AI-Assisted Development

This project includes AI governance files (`CLAUDE.md`, `AGENTS.md`) that configure AI assistants for financial software development. If you use Claude Code, Cursor, or similar tools:

1. Read `CLAUDE.md` at the start of each session
2. Follow the Blueprint → Construct → Validate workflow
3. Never violate the 9 Primary Directives
4. Update `PROJECT_CONFIG.md` when you finish

## Questions?

Open an issue or check the [Architecture Document](ARCHITECTURE.md) for system design details.
