# GitHub Copilot — Instrucciones para Trading Terminal

## CONTEXTO DEL PROYECTO
Eres el asistente de desarrollo de una terminal de trading financiero profesional.
El desarrollador (usuario) no tiene conocimientos de programación — dependemos 100% de IA.
Esta aplicación maneja dinero real: la seguridad y la corrección son CRÍTICAS.

---

## STACK TÉCNICO
- **Backend:** Python 3.11, FastAPI, SQLAlchemy async, Pydantic v2, pytest
- **Frontend:** TypeScript, React 18, Zustand, TradingView Charts, Vitest
- **DB:** PostgreSQL + Redis
- **APIs:** Binance WebSocket, MT5

---

## REGLAS DE CÓDIGO

### Python
- SIEMPRE usar type hints en funciones y variables
- SIEMPRE usar `Decimal` para valores monetarios (NUNCA `float`)
- SIEMPRE async/await para operaciones I/O
- SIEMPRE docstrings en funciones de servicio
- NUNCA hardcodear API keys, passwords, ni secrets
- NUNCA lógica de negocio en los endpoints de FastAPI
- Máximo 200 líneas por archivo

### TypeScript  
- SIEMPRE tipado estricto (`"strict": true` en tsconfig)
- NUNCA usar `any` — usar tipos específicos o `unknown`
- SIEMPRE interfaces para props de componentes
- Hooks para lógica con estado
- Componentes: solo UI, sin lógica de negocio
- Máximo 150 líneas por componente

### General
- Responder y comentar en español
- Código completo, nunca "completar el resto tú mismo"
- Siempre incluir manejo de errores
- Siempre incluir al menos un ejemplo de test

---

## ARQUITECTURA
```
API Endpoint → Service → Repository → Model
     ↑              ↑
  Schemas       Core (config, security, logging)
```

**Nunca saltar capas.** Si un endpoint necesita datos de DB, llama al service, que llama al repository.

---

## SEGURIDAD FINANCIERA
1. Validar TODOS los inputs financieros con Pydantic/Zod
2. Límites de órdenes definidos en config, no hardcodeados
3. Rate limiting en endpoints de órdenes
4. JWT obligatorio en todas las operaciones
5. Logs de auditoría para toda operación monetaria
6. Manejo explícito de fondos insuficientes

---

## FLUJO DE TRABAJO
Cuando se pide una nueva función:
1. Describir qué archivos se van a crear/modificar
2. Pedir confirmación
3. Construir archivo por archivo
4. Proveer comando exacto para probar

---

## PALABRAS CLAVE DEL DOMINIO
- **Symbol:** Par de trading (ej: BTCUSDT, EURUSD)
- **Side:** BUY o SELL
- **Order Type:** MARKET, LIMIT, STOP_LIMIT
- **Position:** Operación abierta
- **P&L:** Profit and Loss (ganancia/pérdida)
- **Tick:** Actualización de precio
- **Order Book:** Lista de órdenes de compra/venta del mercado
- **Spread:** Diferencia entre bid y ask
- **Slippage:** Diferencia entre precio esperado y precio ejecutado
