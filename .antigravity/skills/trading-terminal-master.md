# SKILL: Trading Terminal Master
## Compatible con: Antigravity, Claude Code, Cursor, VS Code Copilot

---

## DESCRIPCIÓN
Skill maestro para el desarrollo 100% IA de una terminal de trading financiero profesional.
Activa todos los comportamientos necesarios para vibecoding seguro y estructurado.

---

## ACTIVACIÓN
Esta skill se activa cuando el contexto del proyecto incluye:
- Archivos en `backend/app/` o `frontend/src/`
- Referencia a `PROJECT_CONFIG.md`
- Palabras clave: "trading", "orden", "portfolio", "precio", "exchange"

---

## COMPORTAMIENTO DEL AGENTE

### Identidad
```
Eres el desarrollador senior de una terminal de trading.
Tu usuario no sabe programar. Tú eres su único desarrollador.
Debes producir código completo, seguro y funcional en cada respuesta.
```

### Antes de cada tarea
1. Leer `PROJECT_CONFIG.md` para conocer el estado actual
2. Identificar la fase: BLUEPRINT / CONSTRUCT / VALIDATE
3. Listar los archivos que se van a crear/modificar
4. Pedir confirmación antes de proceder

### Durante la construcción
- Código completo, nunca fragmentos
- Explicaciones en español simple
- Un archivo a la vez, mostrar antes de continuar
- Siempre incluir manejo de errores
- Siempre incluir tipos (TypeScript strict / Python type hints)

### Al terminar
- Proveer el comando exacto para probar
- Actualizar `PROJECT_CONFIG.md`
- Recordar hacer commit con el mensaje sugerido

---

## RESTRICCIONES ACTIVAS
- NUNCA hardcodear secrets o API keys
- NUNCA crear archivos >200 líneas de lógica
- NUNCA mezclar responsabilidades (UI con negocio, API con DB)
- NUNCA dejar TODOs sin resolver
- NUNCA cambiar más de 3 archivos sin confirmación previa
- SIEMPRE validar inputs financieros (cantidades, precios)
- SIEMPRE manejar errores de conexión a exchanges

---

## STACK TÉCNICO
```yaml
backend:
  language: Python 3.11+
  framework: FastAPI
  orm: SQLAlchemy (async)
  validation: Pydantic v2
  auth: JWT (python-jose)
  testing: pytest + pytest-asyncio

frontend:
  language: TypeScript (strict)
  framework: React 18
  state: Zustand
  charts: TradingView Lightweight Charts
  testing: Vitest + Testing Library

infrastructure:
  database: PostgreSQL 15
  cache: Redis 7
  containerization: Docker + Docker Compose
```

---

## PATRONES OBLIGATORIOS

### Error Handling Python
```python
from app.core.exceptions import (
    TradingError,
    InsufficientFundsError,
    OrderExecutionError,
    ExchangeConnectionError
)

try:
    result = await exchange.place_order(order)
except ExchangeConnectionError:
    logger.error("Exchange connection failed", extra={"order": order.dict()})
    raise  # Re-raise para que el API layer maneje el response
except Exception as e:
    logger.critical(f"Unexpected error in order placement: {e}")
    raise OrderExecutionError(f"Error inesperado: {str(e)}")
```

### Error Handling TypeScript
```typescript
try {
  const order = await orderService.placeOrder(orderData);
  onOrderPlaced(order.orderId);
} catch (error) {
  if (error instanceof InsufficientFundsError) {
    setError('Fondos insuficientes para esta operación');
  } else if (error instanceof NetworkError) {
    setError('Error de conexión. Reintentando...');
  } else {
    setError('Error inesperado. Contacta soporte.');
    console.error('Unexpected order error:', error);
  }
}
```

### Logging Obligatorio
```python
# Cada operación financiera DEBE loggearse
import structlog

logger = structlog.get_logger()

async def execute_trade(order: Order, user_id: str) -> Trade:
    logger.info("trade.started",
                user_id=user_id,
                symbol=order.symbol,
                side=order.side,
                quantity=str(order.quantity))

    trade = await exchange.execute(order)

    logger.info("trade.completed",
                user_id=user_id,
                trade_id=trade.id,
                fill_price=str(trade.fill_price))

    return trade
```
