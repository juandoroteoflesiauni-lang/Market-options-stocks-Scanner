# 📖 CI/CD & Automation Rules
## `.docs/CICD.md` — v2.0

> **Agent Load Instruction:** Load this file when setting up or modifying
> GitHub Actions workflows, pre-commit hooks, or quality gates.

---

## 1. PHILOSOPHY: AUTOMATE THE AUDIT

As a non-programmer using vibe-coding, the most important protection against
code degradation is **automation that catches problems before they reach main**.

The goal: every push is reviewed by machines before humans ever see it.
Human review is for architecture and business logic — not formatting or typos.

```
Developer (or AI) pushes code
          │
          ▼
┌─────────────────────────────────────────────┐
│           AUTOMATED QUALITY GATES            │
│                                             │
│  1. Secret scan      (gitleaks)     < 30s  │
│  2. Format check     (black/isort)  < 60s  │
│  3. Lint             (ruff)         < 60s  │
│  4. Type check       (mypy)         < 90s  │
│  5. Security scan    (bandit)       < 60s  │
│  6. Tests            (pytest)       < 5min │
│  7. Coverage gate    (≥80%)         inline  │
│  8. Dep audit        (pip-audit)    < 30s  │
│  9. Frontend lint    (eslint)       < 60s  │
│ 10. Frontend types   (tsc)          < 90s  │
└─────────────────────────────────────────────┘
          │
          ▼ (All gates pass)
      Code is mergeable
```

---

## 2. GITHUB ACTIONS: BACKEND CI

```yaml
# .github/workflows/backend-ci.yml
name: Backend CI

on:
  push:
    branches: [main, develop]
    paths: ["backend/**", "pyproject.toml"]
  pull_request:
    branches: [main, develop]
    paths: ["backend/**", "pyproject.toml"]

jobs:
  quality:
    name: Code Quality
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install poetry
          poetry install --with dev

      - name: Scan for secrets
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Format check (black)
        run: poetry run black --check backend/ tests/

      - name: Import order (isort)
        run: poetry run isort --check-only backend/ tests/

      - name: Lint (ruff)
        run: poetry run ruff check backend/ tests/

      - name: Type check (mypy --strict)
        run: poetry run mypy backend/ --strict

      - name: Security scan (bandit)
        run: poetry run bandit -r backend/ -ll -ii -f json -o bandit-report.json
        continue-on-error: false

      - name: Dependency audit (pip-audit)
        run: poetry run pip-audit --strict

      - name: Run tests with coverage
        run: |
          poetry run pytest tests/ \
            --cov=backend \
            --cov-report=term-missing \
            --cov-report=xml \
            --cov-fail-under=80 \
            -v

      - name: Upload coverage report
        uses: codecov/codecov-action@v4
        with:
          file: coverage.xml
          fail_ci_if_error: true
```

---

## 3. GITHUB ACTIONS: FRONTEND CI

```yaml
# .github/workflows/frontend-ci.yml
name: Frontend CI

on:
  push:
    branches: [main, develop]
    paths: ["frontend/**", "package.json"]
  pull_request:
    branches: [main, develop]
    paths: ["frontend/**", "package.json"]

jobs:
  quality:
    name: Frontend Quality
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Node.js 22
        uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: "npm"

      - name: Install dependencies
        run: npm ci
        working-directory: ./frontend

      - name: Format check (prettier)
        run: npm run format:check
        working-directory: ./frontend

      - name: Lint (eslint)
        run: npm run lint
        working-directory: ./frontend

      - name: Type check (tsc)
        run: npm run type-check
        working-directory: ./frontend

      - name: Dependency audit
        run: npm audit --audit-level=moderate
        working-directory: ./frontend

      - name: Build check
        run: npm run build
        working-directory: ./frontend
        env:
          NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000"
          NEXT_PUBLIC_WS_URL: "ws://localhost:8001"
```

---

## 4. BRANCH PROTECTION RULES

Configure these in GitHub → Settings → Branches → Branch protection rules
for the `main` branch:

```
✅ Require a pull request before merging
✅ Require status checks to pass before merging
   Required checks:
   - backend-ci / quality
   - frontend-ci / quality
✅ Require branches to be up to date before merging
✅ Do not allow bypassing the above settings
❌ Allow force pushes (DISABLED)
❌ Allow deletions (DISABLED)
```

---

## 5. PRE-COMMIT HOOKS (Local Development)

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.2
    hooks:
      - id: gitleaks

  - repo: https://github.com/psf/black
    rev: 24.4.2
    hooks:
      - id: black
        language_version: python3.12

  - repo: https://github.com/PyCQA/isort
    rev: 5.13.2
    hooks:
      - id: isort
        args: ["--profile", "black"]

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.4
    hooks:
      - id: ruff
        args: [--fix]

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: check-merge-conflict
      - id: check-yaml
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: no-commit-to-branch
        args: [--branch, main]
```

**Setup command for any new contributor:**
```bash
pip install pre-commit
pre-commit install
```

---

## 6. `pyproject.toml` TOOL CONFIGURATION

```toml
# pyproject.toml — all tool configs in one place

[tool.black]
line-length = 100
target-version = ["py312"]

[tool.isort]
profile = "black"
line_length = 100

[tool.ruff]
line-length = 100
target-version = "py312"
select = [
  "E",    # pycodestyle errors
  "W",    # pycodestyle warnings
  "F",    # pyflakes
  "I",    # isort
  "B",    # flake8-bugbear
  "C4",   # flake8-comprehensions
  "UP",   # pyupgrade
  "SIM",  # flake8-simplify
  "TID",  # flake8-tidy-imports
]
ignore = ["E501"]  # black handles line length

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = false
disallow_untyped_defs = true
disallow_any_generics = true
warn_return_any = true
warn_unused_configs = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --tb=short"

[tool.coverage.run]
source = ["backend"]
omit = ["**/tests/**", "**/__init__.py"]

[tool.coverage.report]
fail_under = 80
show_missing = true
```

---

## 7. `package.json` SCRIPTS (Frontend)

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint . --max-warnings=0",
    "lint:fix": "eslint . --fix",
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "type-check": "tsc --noEmit",
    "validate": "npm run type-check && npm run lint && npm run format:check"
  }
}
```

**`npm run validate`** is the single command that mirrors CI locally.
Run it before every commit.

---

## 8. DEPENDABOT CONFIGURATION

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
    labels: ["dependencies", "security"]
    
  - package-ecosystem: "npm"
    directory: "/frontend"
    schedule:
      interval: "weekly"
      day: "monday"
    labels: ["dependencies", "security"]
    
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```

---

## 9. QUICK REFERENCE: LOCAL VALIDATION COMMANDS

```bash
# Backend — run all checks locally
poetry run black backend/ tests/
poetry run isort backend/ tests/
poetry run ruff check backend/ tests/
poetry run mypy backend/ --strict
poetry run bandit -r backend/ -ll
poetry run pytest tests/ --cov=backend --cov-fail-under=80

# Frontend — run all checks locally
cd frontend && npm run validate

# Full system check (mirrors CI exactly)
pre-commit run --all-files
```
