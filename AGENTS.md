# AGENTS.md — deep-funnel-station
## Universal Agent Rules v3.0
### Compatible con: Claude Code · Cursor · GitHub Copilot · Windsurf · Codex

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
⚠️ INCERTIDUMBRE: No estoy seguro de [X].
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
