# SKILL: Order Management
## Compatible con: Antigravity, Claude Code, Cursor, VS Code

---

## DESCRIPCIÓN
Skill especializado para implementar gestión de órdenes de trading:
creación, cancelación, historial y validación de riesgo.

---

## ACTIVACIÓN
Se activa cuando el contexto incluye:
- Archivos: `order_service.py`, `OrderForm.tsx`, `order_repo.py`
- Palabras clave: "orden", "BUY", "SELL", "MARKET", "LIMIT", "cancelar orden"
- Imports de: `OrderCreate`, `OrderResult`, `RiskService`

---

## FLUJO OBLIGATORIO DE CREACIÓN DE ORDEN

```
Usuario envía formulario
    ↓
Pydantic valida el schema (tipos, rangos, campos requeridos)
    ↓
RiskService.validate_order() — valida límites financieros
    ↓ (solo si pasa validación)
ExchangeAdapter.place_order() — envía al exchange
    ↓
OrderRepository.create() — guarda en PostgreSQL
    ↓
Cache.invalidate_portfolio() — invalida caché del usuario
    ↓
AuditLogger.order_created() — log de auditoría obligatorio
    ↓
Response al cliente con OrderResult
```

**NUNCA saltar pasos. NUNCA enviar al exchange sin validar riesgo primero.**

---

## VALIDACIONES MÍNIMAS DEL RISK SERVICE

| Regla | Valor default | Configurable en |
|-------|--------------|----------------|
| Máximo por orden | $10,000 USD | settings.MAX_ORDER_VALUE_USD |
| Mínimo por orden | $10 USD | settings.MIN_ORDER_VALUE_USD |
| Máx posición (% portfolio) | 25% | settings.MAX_POSITION_SIZE_PCT |
| Máx volumen diario | $50,000 USD | settings.MAX_DAILY_VOLUME_USD |

---

## ESTADOS DE ORDEN

```
PENDING → OPEN → FILLED (completada)
                 ↘ CANCELLED (cancelada por usuario)
                 ↘ REJECTED (rechazada por exchange)
         ↘ REJECTED (rechazada inmediatamente)
```

---

## COMPONENTE FORMULARIO — REQUISITOS UI

```
□ Selector BUY/SELL con colores: verde/rojo
□ Campo cantidad — solo números positivos, máximo 8 decimales
□ Campo precio — solo visible para órdenes LIMIT
□ Resumen de la orden antes de confirmar
□ Loading state durante el envío
□ Error message claro en español si falla
□ Confirmación de éxito con ID de orden
□ Limpiar formulario después de orden exitosa
```

---

## CHECKLIST ANTES DE COMPLETAR

```
□ ¿El RiskService valida ANTES de llamar al exchange?
□ ¿Los errores de fondos insuficientes tienen mensaje claro?
□ ¿La orden se guarda en DB con el estado correcto?
□ ¿El log de auditoría registra la orden?
□ ¿El caché del portfolio se invalida después de la orden?
□ ¿El formulario valida en frontend Y en backend?
□ ¿El formulario tiene loading state para evitar doble submit?
□ ¿Los valores monetarios usan Decimal (Python) / string (JSON)?
```
