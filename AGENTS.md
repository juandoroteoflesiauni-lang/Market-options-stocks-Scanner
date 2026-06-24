# AGENTS.md — Universal Agent Rules

## Universal Agent Rules v3.0
### Compatible with: Claude Code · Cursor · GitHub Copilot · Windsurf · Codex

> This file is the universal standard for all AI agents.
> CLAUDE.md takes precedence for Claude Code.
> Cursor additionally uses `.cursor/rules/*.mdc`.

---

## 1. Project in One Sentence

`deep-funnel-station` is a quantitative trading terminal that filters
thousands of tickers down to 5 high-liquidity options contracts for
real-time execution. Stack: Python 3.12 (backend) + Next.js 16 (frontend).

---

## 2. Repository Structure

```
deep-funnel-station/
├── CLAUDE.md               ← Master constitution (read first always)
├── AGENTS.md               ← This file
├── ARCHITECTURE.md         ← System map for onboarding
│
├── .cursor/rules/          ← Cursor rules (.mdc) loaded automatically
│   ├── 00-master.mdc       ← Always active
│   ├── 01-backend-python.mdc
│   ├── 02-data-models.mdc
│   ├── 03-frontend-nextjs.mdc
│   ├── 04-data-hub.mdc
│   └── 05-async-events.mdc
│
├── .antigravity/skills/    ← Claude Code custom skills
│
├── .docs/                  ← Complete reference rule books
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── CICD.md
│   ├── backend/
│   │   ├── 01-deep-funnel.md
│   │   ├── 02-data-hub.md
│   │   ├── 03-python-standards.md
│   │   ├── 04-data-modeling.md
│   │   └── 05-async-event-engine.md
│   └── frontend/
│       ├── 01-scope.md
│       ├── 02-design-system.md
│       └── 03-clean-code.md
│
├── .github/
│   ├── workflows/
│   │   ├── backend-ci.yml
│   │   └── frontend-ci.yml
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── dependabot.yml
│
├── backend/                ← Python trading engine
├── frontend/               ← Next.js UI shell
├── .pre-commit-config.yaml
└── pyproject.toml
```

---

## 3. Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Python | Python | 3.12+ |
| Validation | Pydantic v2 | 2.x (frozen models) |
| Async | asyncio + uvloop | latest |
| HTTP | httpx | latest |
| Config | pydantic-settings | 2.x |
| Tests | pytest + pytest-asyncio | latest |
| Types | mypy --strict | latest |
| Lint | ruff + black + isort | latest |
| Frontend | Next.js | 16.x |
| Styles | Tailwind CSS | 4.x |
| Components | shadcn/ui | latest |

---

## 4. Agent Behavior Rules

### 4.1 Before Writing Code
1. Identify the phase/layer of work
2. Load the specific rule (max 2 per session)
3. Declare your plan — no code until confirmation on destructive changes

### 4.2 During Writing
- Tag applied rules in comments: `# [PD-3][TH][IM]`
- One function = one task. Max 30 lines.
- If you find spaghetti code: **propose refactor before adding features**

### 4.3 When Presenting Code
Declare explicitly:
- Which files are created/modified
- Which rules are applied
- Which CI gates must pass

### 4.4 When Uncertain
```
UNCERTAINTY: I'm not sure about [X].
   Option A: [description]
   Option B: [description]
   Recommendation: A, because [reason].
   Waiting for confirmation before proceeding.
```

---

## 5. Commit Standards

```
<type>(<scope>): <short description in present tense>

Valid types:
  feat     → new feature
  fix      → bug fix
  refactor → restructuring without behavior change
  docs     → documentation only
  test     → tests only
  ci       → pipeline / configuration
  sec      → security

Examples:
  feat(phase-b): add VPIN calculation with ProcessPoolExecutor
  fix(hub): handle FMP API timeout with exponential backoff
  refactor(models): convert MarketSnapshot to frozen Pydantic v2
  sec(hub): replace plain str with SecretStr for API keys
```

---

## 6. Test Standards

```python
# Naming conventions:
# tests/unit/     → no network, no DB, mocked
# tests/integration/ → may use network/DB

# Test naming:
def test_market_snapshot_rejects_negative_price():
    """Name = test_{what}_{condition}."""

# AAA Pattern:
def test_phase_a_discards_invalid_ticker():
    # ARRANGE
    invalid_raw = {"symbol": "", "price": -1}
    # ACT
    result = normalizer.normalize(invalid_raw, time.time_ns())
    # ASSERT
    assert result.is_failure

# Mocks for external APIs — never real calls in unit tests
@pytest.fixture
def mock_fmp_client(mocker):
    return mocker.patch("backend.hub.market_data_hub.httpx.AsyncClient")
```

---

## 7. Universal Pre-Commit Checklist

```
BACKEND:
[ ] black + isort + ruff → 0 errors
[ ] mypy --strict → 0 type errors
[ ] bandit → no HIGH/CRITICAL
[ ] pytest --cov-fail-under=80 → passes
[ ] 0 secrets in code (gitleaks)

FRONTEND:
[ ] prettier --check → 0 errors
[ ] eslint --max-warnings=0 → 0 warnings
[ ] tsc --noEmit → 0 errors
[ ] npm audit --audit-level=moderate → clean
[ ] next build → build succeeds

UNIVERSAL:
[ ] No print() in Python
[ ] No console.log in TypeScript
[ ] No any in TypeScript
[ ] No magic numbers in code
[ ] No hardcoded secrets
```

---

# AGENTS.md — Reglas Universales para Agentes (Español)

> Este archivo es el estándar universal para todos los agentes de IA.
> CLAUDE.md tiene precedencia para Claude Code.
> Cursor usa adicionalmente `.cursor/rules/*.mdc`.

---

## 1. PROYECTO EN UNA ORACIÓN

`deep-funnel-station` es una estación de trading cuantitativo que filtra
miles de tickers hasta 5 contratos de opciones de alta liquidez para
ejecución en tiempo real. Stack: Python 3.12 (backend) + Next.js 16 (frontend).

---

## 2. ESTRUCTURA DEL REPOSITORIO

```
deep-funnel-station/
├── CLAUDE.md               ← Constitución maestra (leer siempre primero)
├── AGENTS.md               ← Este archivo
├── ARCHITECTURE.md         ← Mapa del sistema para onboarding
│
├── .cursor/rules/          ← Reglas Cursor (.mdc) cargadas automáticamente
│   ├── 00-master.mdc       ← Siempre activo
│   ├── 01-backend-python.mdc
│   ├── 02-data-models.mdc
│   ├── 03-frontend-nextjs.mdc
│   ├── 04-data-hub.mdc
│   └── 05-async-events.mdc
│
├── .antigravity/skills/    ← Claude Code custom skills
│
├── .docs/                  ← Rule books de referencia completos
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── CICD.md
│   ├── backend/
│   │   ├── 01-deep-funnel.md
│   │   ├── 02-data-hub.md
│   │   ├── 03-python-standards.md
│   │   ├── 04-data-modeling.md
│   │   └── 05-async-event-engine.md
│   └── frontend/
│       ├── 01-scope.md
│       ├── 02-design-system.md
│       └── 03-clean-code.md
│
├── .github/
│   ├── workflows/
│   │   ├── backend-ci.yml
│   │   └── frontend-ci.yml
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── dependabot.yml
│
├── backend/                ← Python trading engine
├── frontend/               ← Next.js UI shell
├── .pre-commit-config.yaml
└── pyproject.toml
```

---

## 3. STACK TÉCNICO

| Capa | Tecnología | Versión |
|------|-----------|---------|
| Python | Python | 3.12+ |
| Validación | Pydantic v2 | 2.x (frozen models) |
| Async | asyncio + uvloop | latest |
| HTTP | httpx | latest |
| Config | pydantic-settings | 2.x |
| Tests | pytest + pytest-asyncio | latest |
| Tipos | mypy --strict | latest |
| Lint | ruff + black + isort | latest |
| Frontend | Next.js | 16.x |
| Estilos | Tailwind CSS | 4.x |
| Componentes | shadcn/ui | latest |

---

## 4. REGLAS DE COMPORTAMIENTO DEL AGENTE

### 4.1 Antes de escribir código
1. Identifica la fase/capa del trabajo
2. Carga la regla específica (máximo 2 por sesión)
3. Declara tu plan — sin código hasta confirmación en cambios destructivos

### 4.2 Durante la escritura
- Etiqueta reglas aplicadas en comentarios: `# [PD-3][TH][IM]`
- Una función = una tarea. Máx. 30 líneas.
- Si encuentras código espagueti: **propón refactor antes de agregar features**

### 4.3 Al presentar código
Declara explícitamente:
- Qué archivos se crean/modifican
- Qué reglas se aplican
- Qué gates de CI deben pasar

### 4.4 Cuando hay incertidumbre
```
INCERTIDUMBRE: No estoy seguro de [X].
   Opción A: [descripción]
   Opción B: [descripción]
   Recomendación: A, porque [razón].
   Esperando confirmación antes de proceder.
```

---

## 5. ESTÁNDARES DE COMMIT

```
<tipo>(<alcance>): <descripción corta en presente>

Tipos válidos:
  feat     → nueva funcionalidad
  fix      → corrección de bug
  refactor → restructuración sin cambio funcional
  docs     → solo documentación
  test     → solo tests
  ci       → pipeline / configuración
  sec      → seguridad

Ejemplos:
  feat(phase-b): add VPIN calculation with ProcessPoolExecutor
  fix(hub): handle FMP API timeout with exponential backoff
  refactor(models): convert MarketSnapshot to frozen Pydantic v2
  sec(hub): replace plain str with SecretStr for API keys
```

---

## 6. ESTÁNDARES DE TESTS

```python
# Convenciones de naming:
# tests/unit/     → sin red, sin DB, mockeado
# tests/integration/ → puede usar red/DB

# Naming de tests:
def test_market_snapshot_rejects_negative_price():
    """Nombre = test_{qué}_{condición}."""

# Patrón AAA:
def test_phase_a_discards_invalid_ticker():
    # ARRANGE
    invalid_raw = {"symbol": "", "price": -1}
    # ACT
    result = normalizer.normalize(invalid_raw, time.time_ns())
    # ASSERT
    assert result.is_failure

# Mocks para APIs externas — nunca llamadas reales en unit tests
@pytest.fixture
def mock_fmp_client(mocker):
    return mocker.patch("backend.hub.market_data_hub.httpx.AsyncClient")
```

---

## 7. CHECKLIST UNIVERSAL PRE-COMMIT

```
BACKEND:
[ ] black + isort + ruff → 0 errores
[ ] mypy --strict → 0 errores de tipos
[ ] bandit → sin HIGH/CRITICAL
[ ] pytest --cov-fail-under=80 → pasa
[ ] 0 secrets en código (gitleaks)

FRONTEND:
[ ] prettier --check → 0 errores
[ ] eslint --max-warnings=0 → 0 warnings
[ ] tsc --noEmit → 0 errores
[ ] npm audit --audit-level=moderate → limpio
[ ] next build → build exitoso

UNIVERSAL:
[ ] Sin print() en Python
[ ] Sin console.log en TypeScript
[ ] Sin any en TypeScript
[ ] Sin números mágicos en código
[ ] Sin secrets hardcodeados
```

---

## Cursor Cloud specific instructions

The startup update script already runs `poetry install` (backend) and `npm install`
in `frontend/` on a fresh VM. Poetry is installed under `~/.local/bin` and that path is
added to the agent's `~/.bashrc`; the backend venv lives in-project at `.venv`. Standard
run/lint/test commands live in `README.md`, `pyproject.toml`, and `frontend/package.json`.
Notes below are non-obvious things only.

### Services
- **Backend** — FastAPI quant engine + 113 REST/WS routes. Entry `backend/main:app`, port 8000.
- **Frontend** — Next.js 16 terminal UI. Entry `frontend/app/page.tsx`, port 3000.
- No Docker/Postgres/Redis in the cloud VM. The backend runs fine without them: Redis
  caching fails open, and Postgres is declared in settings but not actually wired.

### Backend (works end-to-end)
- Run from the **repo root**, not from inside `backend/`, because all imports use the
  `backend.*` package prefix: `.venv/bin/python -m uvicorn backend.main:app --reload --port 8000`.
- Requires a `.env` at the repo root (gitignored). `backend/config/settings.py` validates that
  `SECRET_KEY`, `FMP_API_KEY`, `MASSIVE_API_KEY`, `ALPACA_API_KEY`, `ALPACA_API_SECRET` are
  **non-empty**, and also needs `DATABASE_URL`, `REDIS_URL`, `MASSIVE_WS_URL`. Placeholder
  values let the app boot; real market-data/broker keys are only needed for live data. With
  placeholders the startup logs show `401` errors from FMP/Alpaca/Massive — this is expected
  and gracefully handled (the HTTP server still serves).
- Operator auth (`/api/v1/auth/*`) needs `QA_SESSION_SECRET` and `QA_APP_PASSWORD_HASH`
  (`sha256:<hex>` or `pbkdf2_sha256$...`) in `.env`; default username is `admin`.
- Tests: run from repo root with `--asyncio-mode=auto` and mock keys, e.g.
  `FMP_API_KEY=x MASSIVE_API_KEY=x MASSIVE_WS_URL=ws://localhost:9999 .venv/bin/python -m pytest tests/ --asyncio-mode=auto`.
  ~193/197 pass; the ~4 remaining failures are pre-existing config/data drift (e.g. a config
  value vs. assertion mismatch, a pickled scikit-learn model version skew), not env issues.

### Frontend (currently blocked — needs source restored)
- `npm run dev` starts and the deps install, but pages 500 and `tsc`/`vitest` fail because
  **`frontend/lib/` is missing from the repo** (`api-client.ts`, `constants.ts`, `env.ts`,
  `utils.ts`, `bingx-bot-types.ts`, imported as `@/lib/*`). The directory was never committed
  because the broad `.gitignore` rule `lib/` matched it; that rule is now anchored to `/lib/`.
  The frontend cannot build/run until someone restores and commits `frontend/lib/*`. Only the
  one test file with no `@/lib` import passes (13 tests).

### Lint/type gates
- `ruff`, `mypy --strict`, `black`, and the frontend `eslint`/`tsc` tooling all run, but the
  repo currently has many pre-existing violations — these gates are not green on `main` today.
