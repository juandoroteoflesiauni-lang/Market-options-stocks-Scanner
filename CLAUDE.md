# 🤖 CLAUDE.md — CONSTITUCIÓN DEL AGENTE IA
## Deep Trading Terminal — v3.0

> **PROTOCOLO DE INICIO OBLIGATORIO:** Leer este archivo COMPLETO al comenzar CADA sesión.
> Luego confirmar al usuario: "Listo. [X módulos completados]. Próximo paso: [Y]. ¿Arrancamos?"

---

## ⚡ DIRECTIVAS PRIMARIAS — NUNCA SE IGNORAN

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

## 🏗️ ARQUITECTURA DEL SISTEMA

### Stack Tecnológico Oficial
```
BACKEND:   Python 3.12 + FastAPI + SQLAlchemy 2.0 + Pydantic v2
FRONTEND:  TypeScript + Next.js 14 + React 19 + Zustand + Tailwind v4
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

## 🔒 REGLAS DE SEGURIDAD FINANCIERA

### PD-1: Secrets — Cero Tolerancia

```python
# ✅ CORRECTO — siempre así
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("EXCHANGE_API_KEY")
if not API_KEY:
    raise ValueError("EXCHANGE_API_KEY no configurada en .env")

# ❌ PROHIBIDO — jamás, nunca, bajo ninguna circunstancia
API_KEY = "sk-abc123xyz789"  # PD-1 VIOLATION: expone credenciales
```

### PD-2: Dinero — Siempre Decimal, Nunca Float

```python
# ✅ CORRECTO — Decimal garantiza precisión
from decimal import Decimal, ROUND_HALF_UP

price    = Decimal("42150.50")
quantity = Decimal("0.001")
total    = (price * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
# total = Decimal("42.15")  ← exacto

# ❌ PROHIBIDO — float acumula errores de precisión
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

## 📐 ESTÁNDARES DE CÓDIGO

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

## 🔄 PROCESO DE SESIÓN DE DESARROLLO

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

## 🚫 ACCIONES ABSOLUTAMENTE PROHIBIDAS

```
❌ Modificar más de 3 archivos en un solo turno sin aprobación explícita
❌ Usar float para cualquier cálculo de dinero, precio, cantidad o P&L
❌ Hardcodear API keys, passwords, tokens o cualquier credencial
❌ Comentar o deshabilitar tests para hacerlos pasar
❌ Usar eval(), exec() o deserialización sin validación de fuentes externas
❌ Proponer cambio arquitectural sin Blueprint separado y aprobación
❌ Avanzar con tests fallando
❌ Ignorar errores de tipo ("lo arreglo después" no existe)
❌ Poner lógica de negocio en API routers o en el frontend
❌ Crear archivos de más de 300 líneas sin discutirlo primero
❌ Terminar una sesión sin actualizar PROJECT_CONFIG.md y hacer commit
```

---

## 📋 CHECKLIST DE FIN DE SESIÓN

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
