# CLAUDE.md — AI Agent Constitution

> **MANDATORY STARTUP PROTOCOL:** Read this file COMPLETELY at the start of EVERY session.
> Then confirm to the user: "Ready. [X modules complete]. Next step: [Y]. Shall we begin?"

---

## Primary Directives — Never Ignored

| ID | Directive | Why it matters |
|----|-----------|----------------|
| **PD-1** | **NEVER hardcode secrets** | Exposed API keys = compromised account |
| **PD-2** | **NEVER use `float` for money** | Precision errors = real losses |
| **PD-3** | **ALWAYS Blueprint before code** | No plan = spaghetti code |
| **PD-4** | **MAX 2 files per turn** | Controllable, reviewable changes |
| **PD-5** | **ALWAYS read before modifying** | Never assume code state |
| **PD-6** | **Tests mandatory for financial logic** | No tests = no confidence in production |
| **PD-7** | **Explain in user's language** | Communication clarity |
| **PD-8** | **Complete code, never fragments** | Fragments create broken code |
| **PD-9** | **Update PROJECT_CONFIG.md on end** | Next session depends on it |

---

## System Architecture

### Official Tech Stack
```
BACKEND:   Python 3.12 + FastAPI + SQLAlchemy 2.0 + Pydantic v2
FRONTEND:  TypeScript + Next.js 16 + React 19 + Zustand + Tailwind v4
DATABASE:  PostgreSQL 16 (persistence) + Redis 7 (cache & real-time pub/sub)
INFRA:     Docker Compose (local dev) + GitHub Actions (CI/CD)
```

### Architecture Layers — Never Skip
```
HTTP Request
    ↓
API Router (FastAPI)     ← Routing and input validation only
    ↓
Service Layer            ← ALL business logic goes here
    ↓
Repository Layer         ← Database access only
    ↓
PostgreSQL / Redis
```

**Golden rule:** If business logic is in the router or frontend → it's wrong.

### 4-Phase Architecture — The Quantitative Funnel
```
┌─────────────────────────────────────────────────────────────────┐
│  PHASE A — Scanner                                              │
│  Input:   Thousands of market tickers (REST APIs)              │
│  Process: Basic filters: volume, volatility, liquidity         │
│  Output:  ~300 candidates as MarketSnapshot                    │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  PHASE B — Microstructure Filter (NO network, local only)      │
│  Input:   300 candidates                                       │
│  Process: VPIN + Order Flow Imbalance (OFI)                    │
│  Output:  Top 20 candidates                                    │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  PHASE C — Options Analysis                                    │
│  Input:   Top 20 candidates                                    │
│  Process: Download options chains, select by criteria           │
│  Output:  Top 5 contracts (symbol + strike + expiry)           │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  PHASE D — Real-Time Monitor (WebSocket tick-by-tick)          │
│  Input:   5 contracts                                          │
│  Process: Live feed, generates execution signals               │
│  Output:  Signals → Frontend → Dashboard                       │
└─────────────────────────────────────────────────────────────────┘
```

### Critical Architectural Rules
- `MarketDataHub` is the **ONLY** component that touches external APIs (Anti-Corruption Layer)
- Data between phases = Pydantic `MarketSnapshot` objects with `frozen=True` (immutable)
- Event Bus = `asyncio.Queue` → decouples producers from consumers
- One module = one Python file (max 300 lines; split if larger)

---

## Financial Security Rules

### PD-1: Secrets — Zero Tolerance

```python
# CORRECT — always this way
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("EXCHANGE_API_KEY")
if not API_KEY:
    raise ValueError("EXCHANGE_API_KEY not configured in .env")

# PROHIBITED — never, ever, under any circumstances
API_KEY = "REPLACE_ME"  # PD-1 VIOLATION: never hardcode credentials
```

### PD-2: Money — Always Decimal, Never Float

```python
# CORRECT — Decimal guarantees precision
from decimal import Decimal, ROUND_HALF_UP

price    = Decimal("42150.50")
quantity = Decimal("0.001")
total    = (price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
# total = Decimal("42.15")  ← exact

# PROHIBITED — float accumulates precision errors
price    = 42150.50        # PD-2 VIOLATION
quantity = 0.001           # PD-2 VIOLATION
total    = price * quantity # May give 42.15049999999... in production
```

### Mandatory Validations Before Executing Any Order

```python
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)
MAX_POSITION_SIZE = Decimal("10000.00")  # From .env in production

def validate_and_log_order(order: OrderRequest) -> None:
    """Validate every order before sending to the exchange."""
    if order.quantity <= Decimal("0"):
        raise ValueError(f"Invalid quantity: {order.quantity}")
    if order.price <= Decimal("0"):
        raise ValueError(f"Invalid price: {order.price}")
    if order.quantity * order.price > MAX_POSITION_SIZE:
        raise ValueError("Exceeds maximum position size")
    if not exchange_client.is_connected():
        raise ConnectionError("Exchange not connected")

    # ALWAYS log before sending
    logger.info("ORDER_PRE_SEND | %s", order.model_dump_json())
```

### Rate Limiting — Always with Backoff

```python
import asyncio

async def call_exchange_api(endpoint: str) -> dict:
    """Call exchange API with retries and exponential backoff."""
    for attempt in range(3):
        try:
            response = await client.get(endpoint, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except RateLimitError:
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning("Rate limit hit. Retrying in %ds...", wait)
            await asyncio.sleep(wait)
    raise ExchangeError(f"Max retries exceeded for {endpoint}")
```

---

## Code Standards

### Python — Mandatory Rules

```python
from decimal import Decimal
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict

# Type hints on EVERY function — no exceptions
def calculate_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    side: Literal["long", "short"],
) -> Decimal:
    """Calculate position profit/loss."""
    if side == "long":
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity

# Pydantic for ALL market data
class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)  # Immutable — not modified after creation

    symbol:      str
    bid:         Decimal
    ask:         Decimal
    volume_24h:  Decimal
    timestamp:   datetime
```

### TypeScript — Mandatory Rules

```typescript
// NEVER 'any' — always explicit types
interface Position {
  symbol:     string
  entryPrice: string   // String to avoid float in JS
  quantity:   string   // String to avoid float in JS
  side:       'long' | 'short'
  pnl:        string
  openedAt:   string   // ISO timestamp
}

// Global state ALWAYS with Zustand (never useState for shared state)
import { create } from 'zustand'

interface TradingStore {
  positions: Position[]
  isConnected: boolean
  addPosition: (pos: Position) => void
  setConnected: (v: boolean) => void
}

const useTradingStore = create<TradingStore>((set) => ({
  positions:    [],
  isConnected:  false,
  addPosition:  (pos) => set((s) => ({ positions: [...s.positions, pos] })),
  setConnected: (v)   => set({ isConnected: v }),
}))
```

### WebSockets — Mandatory Lifecycle

```typescript
// ALWAYS with cleanup, reconnect and error handling
useEffect(() => {
  let ws: WebSocket
  let retryCount = 0

  const connect = () => {
    ws = new WebSocket(`${WS_BASE_URL}/stream/${symbol}`)

    ws.onopen    = () => { setConnected(true); retryCount = 0 }
    ws.onmessage = (e) => handleTick(JSON.parse(e.data))
    ws.onerror   = (e) => console.error('WS error:', e)
    ws.onclose   = () => {
      setConnected(false)
      const delay = Math.min(1000 * 2 ** retryCount, 30000)  // Max 30s
      retryCount++
      setTimeout(connect, delay)  // Exponential backoff
    }
  }

  connect()
  return () => ws?.close()  // ALWAYS cleanup on unmount
}, [symbol])
```

---

## Development Session Process

### Session Start (Always Do)
```
1. Read CLAUDE.md (this file — completely)
2. Read PROJECT_CONFIG.md → What modules are complete?
3. Read WORKFLOW_STATE.md → What exact task were we on?
4. Confirm to user:
   "Ready. Modules complete: [X/Y].
    Last CHECKPOINT: [description].
    Next step: [task]. Shall we continue?"
```

### Blueprint → Construct → Validate (Mandatory — never skip phases)

**BLUEPRINT** (present before the first byte of code):
```
"Here's the plan for [module]:
- Goal: [one sentence]
- Files to create: [list]
- Files to modify: [list]
- Data schema (inputs/outputs): [description]
- Tests to write: [list]
- Risks: [potential issues]
Approve this plan and we'll start?"
```

**CONSTRUCT** (only with user approval):
```
- One file at a time
- COMPLETE code (no "... rest of code ...")
- All imports at the top
- Explicit error handling
- Type hints on every function
- Add CHECKPOINT comment if session may be interrupted
```

**VALIDATE** (before closing session):
```
- Run: pytest tests/ -v  → all must pass
- Run: pre-commit run --all-files → zero errors
- Verify server starts without errors
- Update PROJECT_CONFIG.md with new status
- Propose commit with semantic message
```

### Code Tags for Cross-Session Tracking
```python
# TODO: Concrete pending task
# FIXME: Known bug to fix
# CHECKPOINT: Where we left off — next session starts here
# ARCH-001: Architecture decision with justification
# SEC-001: Security point to review before production
# PD-1: Primary Directive violation detected (fix urgently)
```

---

## Absolutely Prohibited Actions

```
DO NOT modify more than 3 files in a single turn without explicit approval
DO NOT use float for any money, price, quantity or P&L calculation
DO NOT hardcode API keys, passwords, tokens or any credentials
DO NOT comment out or disable tests to make them pass
DO NOT use eval(), exec() or deserialization without validating external sources
DO NOT propose architectural changes without separate Blueprint and approval
DO NOT proceed with failing tests
DO NOT ignore type errors ("I'll fix it later" doesn't exist)
DO NOT put business logic in API routers or frontend
DO NOT create files over 300 lines without discussing first
DO NOT end a session without updating PROJECT_CONFIG.md and committing
```

---

## End of Session Checklist

Before finishing, verify EACH item:
```
□ pytest tests/ -v                    → All passing
□ pre-commit run --all-files          → Zero errors
□ No secrets in new code
□ Complete type hints on all new functions
□ WebSockets have cleanup in useEffect return
□ CHANGELOG.md updated with session changes
□ PROJECT_CONFIG.md updated with completed modules
□ CHECKPOINT comments added at continuation points
□ git add . && git commit -m "feat/fix: clear description"
```

---

# CLAUDE.md — Constitución del Agente IA (Español)

> **PROTOCOLO DE INICIO OBLIGATORIO:** Leer este archivo COMPLETO al comenzar CADA sesión.
> Luego confirmar al usuario: "Listo. [X módulos completados]. Próximo paso: [Y]. ¿Arrancamos?"

---

## Directivas Primarias — Nunca se Ignoran

| ID | Directiva | Por qué importa |
|----|-----------|----------------|
| **PD-1** | **NUNCA hardcodear secrets** | API keys expuestas = cuenta comprometida |
| **PD-2** | **NUNCA usar `float` para dinero** | Errores de precisión = pérdidas reales |
| **PD-3** | **SIEMPRE Blueprint antes de código** | Sin plan = spaghetti code |
| **PD-4** | **MÁXIMO 2 archivos por turno** | Cambios controlables y revisables |
| **PD-5** | **SIEMPRE leer el archivo antes de modificarlo** | Nunca asumir cómo está el código |
| **PD-6** | **Tests obligatorios para lógica financiera** | Sin tests = sin confianza en producción |
| **PD-7** | **Explicar siempre en español** | El usuario habla español |
| **PD-8** | **Código completo, nunca fragmentos** | Los fragmentos crean código roto |
| **PD-9** | **Actualizar PROJECT_CONFIG.md al terminar** | La próxima sesión depende de esto |

---

## Arquitectura del Sistema

### Stack Tecnológico Oficial
```
BACKEND:   Python 3.12 + FastAPI + SQLAlchemy 2.0 + Pydantic v2
FRONTEND:  TypeScript + Next.js 16 + React 19 + Zustand + Tailwind v4
DATABASE:  PostgreSQL 16 (persistencia) + Redis 7 (caché y pub/sub en tiempo real)
INFRA:     Docker Compose (desarrollo local) + GitHub Actions (CI/CD automático)
```

### Capas de Arquitectura — NUNCA saltear capas
```
HTTP Request
    ↓
API Router (FastAPI)     ← Solo routing y validación de entrada
    ↓
Service Layer            ← TODA la lógica de negocio va aquí
    ↓
Repository Layer         ← Solo acceso a base de datos
    ↓
PostgreSQL / Redis
```

**Regla de oro:** Si la lógica de negocio está en el router o en el frontend → está mal.

### Arquitectura de 4 Fases — El Funnel Cuantitativo
```
┌─────────────────────────────────────────────────────────────────┐
│  PHASE A — Scanner                                              │
│  Input:   Miles de tickers del mercado (REST APIs)             │
│  Proceso: Filtros básicos: volumen, volatilidad, liquidez      │
│  Output:  ~300 candidatos como MarketSnapshot                  │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  PHASE B — Filtro Microestructura (SIN red, solo local)        │
│  Input:   300 candidatos                                       │
│  Proceso: VPIN + Order Flow Imbalance (OFI)                    │
│  Output:  Top 20 candidatos                                    │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  PHASE C — Análisis de Opciones                                │
│  Input:   Top 20 candidatos                                    │
│  Proceso: Descarga options chains, selección por criterios     │
│  Output:  Top 5 contratos (símbolo + strike + expiry)         │
└────────────────────────────┬────────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│  PHASE D — Monitor Tiempo Real (WebSocket tick-by-tick)        │
│  Input:   5 contratos                                          │
│  Proceso: Feed en vivo, genera señales de ejecución            │
│  Output:  Señales → Frontend → Dashboard                       │
└─────────────────────────────────────────────────────────────────┘
```

### Reglas Arquitecturales Críticas
- `MarketDataHub` es el **ÚNICO** componente que toca APIs externas (Anti-Corruption Layer)
- Datos entre fases = Pydantic `MarketSnapshot` objects con `frozen=True` (inmutables)
- Event Bus = `asyncio.Queue` → desacopla productores de consumidores
- Un módulo = un archivo Python (máximo 300 líneas; si supera, dividir)

---

## Reglas de Seguridad Financiera

### PD-1: Secrets — Cero Tolerancia

```python
# CORRECTO — siempre así
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("EXCHANGE_API_KEY")
if not API_KEY:
    raise ValueError("EXCHANGE_API_KEY no configurada en .env")

# PROHIBIDO — jamás, nunca, bajo ninguna circunstancia
API_KEY = "REPLACE_ME"  # PD-1 VIOLATION: expone credenciales
```

### PD-2: Dinero — Siempre Decimal, Nunca Float

```python
# CORRECTO — Decimal garantiza precisión
from decimal import Decimal, ROUND_HALF_UP

price    = Decimal("42150.50")
quantity = Decimal("0.001")
total    = (price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
# total = Decimal("42.15")  ← exacto

# PROHIBIDO — float acumula errores de precisión
price    = 42150.50        # PD-2 VIOLATION
quantity = 0.001           # PD-2 VIOLATION
total    = price * quantity # Puede dar 42.15049999999... en producción
```

### Validaciones Obligatorias Antes de Ejecutar Cualquier Orden

```python
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)
MAX_POSITION_SIZE = Decimal("10000.00")  # Desde .env en producción

def validate_and_log_order(order: OrderRequest) -> None:
    """Validar toda orden antes de enviarla al exchange."""
    if order.quantity <= Decimal("0"):
        raise ValueError(f"Quantity inválida: {order.quantity}")
    if order.price <= Decimal("0"):
        raise ValueError(f"Price inválido: {order.price}")
    if order.quantity * order.price > MAX_POSITION_SIZE:
        raise ValueError("Excede límite máximo de posición")
    if not exchange_client.is_connected():
        raise ConnectionError("Exchange no conectado")

    # SIEMPRE loggear antes de enviar
    logger.info("ORDER_PRE_SEND | %s", order.model_dump_json())
```

### Rate Limiting — Siempre con Backoff

```python
import asyncio

async def call_exchange_api(endpoint: str) -> dict:
    """Llamada a API del exchange con reintentos y backoff exponencial."""
    for attempt in range(3):
        try:
            response = await client.get(endpoint, timeout=10.0)
            response.raise_for_status()
            return response.json()
        except RateLimitError:
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning("Rate limit hit. Retrying in %ds...", wait)
            await asyncio.sleep(wait)
    raise ExchangeError(f"Max retries exceeded for {endpoint}")
```

---

## Estándares de Código

### Python — Reglas Obligatorias

```python
from decimal import Decimal
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict

# Type hints en TODAS las funciones — sin excepción
def calculate_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
    side: Literal["long", "short"],
) -> Decimal:
    """Calcular ganancia/pérdida de una posición."""
    if side == "long":
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity

# Pydantic para TODOS los datos de mercado
class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)  # Inmutable — no se modifica después de crear

    symbol:      str
    bid:         Decimal
    ask:         Decimal
    volume_24h:  Decimal
    timestamp:   datetime
```

### TypeScript — Reglas Obligatorias

```typescript
// NUNCA 'any' — siempre tipos explícitos
interface Position {
  symbol:     string
  entryPrice: string   // String para evitar float en JS
  quantity:   string   // String para evitar float en JS
  side:       'long' | 'short'
  pnl:        string
  openedAt:   string   // ISO timestamp
}

// Estado global SIEMPRE con Zustand (nunca useState para estado compartido)
import { create } from 'zustand'

interface TradingStore {
  positions: Position[]
  isConnected: boolean
  addPosition: (pos: Position) => void
  setConnected: (v: boolean) => void
}

const useTradingStore = create<TradingStore>((set) => ({
  positions:    [],
  isConnected:  false,
  addPosition:  (pos) => set((s) => ({ positions: [...s.positions, pos] })),
  setConnected: (v)   => set({ isConnected: v }),
}))
```

### WebSockets — Lifecycle Obligatorio

```typescript
// SIEMPRE con cleanup, reconnect y manejo de errores
useEffect(() => {
  let ws: WebSocket
  let retryCount = 0

  const connect = () => {
    ws = new WebSocket(`${WS_BASE_URL}/stream/${symbol}`)

    ws.onopen    = () => { setConnected(true); retryCount = 0 }
    ws.onmessage = (e) => handleTick(JSON.parse(e.data))
    ws.onerror   = (e) => console.error('WS error:', e)
    ws.onclose   = () => {
      setConnected(false)
      const delay = Math.min(1000 * 2 ** retryCount, 30000)  // Max 30s
      retryCount++
      setTimeout(connect, delay)  // Backoff exponencial
    }
  }

  connect()
  return () => ws?.close()  // SIEMPRE cleanup al desmontar
}, [symbol])
```

---

## Proceso de Sesión de Desarrollo

### Inicio de Sesión (Hacer SIEMPRE)
```
1. Leer CLAUDE.md (este archivo — completo)
2. Leer PROJECT_CONFIG.md → ¿Qué módulos están completos?
3. Leer WORKFLOW_STATE.md → ¿En qué tarea exacta quedamos?
4. Confirmar al usuario:
   "Listo. Módulos completos: [X/Y].
    Último CHECKPOINT: [descripción].
    Próximo paso: [tarea]. ¿Continuamos?"
```

### Blueprint → Construct → Validate (Obligatorio — nunca saltear fases)

**BLUEPRINT** (presentar antes del primer byte de código):
```
"Aquí está el plan para [módulo]:
- Objetivo: [una oración]
- Archivos a crear: [lista]
- Archivos a modificar: [lista]
- Esquema de datos (inputs/outputs): [descripción]
- Tests a escribir: [lista]
- Riesgos: [posibles problemas]
¿Aprobamos este plan y arrancamos?"
```

**CONSTRUCT** (solo con aprobación del usuario):
```
- Un archivo a la vez
- Código COMPLETO (sin "... resto del código ...")
- Con todos los imports al inicio
- Con manejo de errores explícito
- Con type hints en todas las funciones
- Agregar CHECKPOINT comment si la sesión puede interrumpirse
```

**VALIDATE** (antes de cerrar la sesión):
```
- Correr: pytest tests/ -v  → todos deben pasar
- Correr: pre-commit run --all-files → cero errores
- Verificar que el servidor arranca sin errores
- Actualizar PROJECT_CONFIG.md con estado nuevo
- Proponer el commit con mensaje semántico
```

### Tags de Código para Seguimiento Entre Sesiones
```python
# TODO: Tarea pendiente concreta
# FIXME: Bug conocido que hay que arreglar
# CHECKPOINT: Aquí quedamos — próxima sesión arranca desde acá
# ARCH-001: Decisión de arquitectura con justificación
# SEC-001: Punto de seguridad a revisar antes de producción
# PD-1: Violación de Directiva Primaria detectada (arreglar urgente)
```

---

## Acciones Absolutamente Prohibidas

```
NO modificar más de 3 archivos en un solo turno sin aprobación explícita
NO usar float para cualquier cálculo de dinero, precio, cantidad o P&L
NO hardcodear API keys, passwords, tokens o cualquier credencial
NO comentar o deshabilitar tests para hacerlos pasar
NO usar eval(), exec() o deserialización sin validación de fuentes externas
NO proponer cambio arquitectural sin Blueprint separado y aprobación
NO avanzar con tests fallando
NO ignorar errores de tipo ("lo arreglo después" no existe)
NO poner lógica de negocio en API routers o en el frontend
NO crear archivos de más de 300 líneas sin discutirlo primero
NO terminar una sesión sin actualizar PROJECT_CONFIG.md y hacer commit
```

---

## Checklist de Fin de Sesión

Antes de terminar, verificar CADA item:
```
□ pytest tests/ -v                    → Todos pasando
□ pre-commit run --all-files          → Cero errores
□ Ningún secret en el código nuevo
□ Type hints completos en todas las funciones nuevas
□ WebSockets tienen cleanup en useEffect return
□ CHANGELOG.md actualizado con cambios de la sesión
□ PROJECT_CONFIG.md actualizado con módulos completados
□ CHECKPOINT comments agregados en puntos de continuación
□ git add . && git commit -m "feat/fix: descripción clara"
```
