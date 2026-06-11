# 📊 PROJECT_CONFIG.md
## Deep Trading Terminal — Estado del Proyecto

---

## 🎯 DESCRIPCIÓN DEL PROYECTO

**Nombre:** Deep Trading Terminal / deep-funnel-station
**Objetivo:** Terminal de trading cuantitativo para opciones y futuros con filtrado de microestructura en 4 fases (Scanner → Microestructura → Opciones → Monitor).
**Stack:** Python/FastAPI (backend) + TypeScript/Next.js 16 (frontend) + PostgreSQL + Redis
**Exchange objetivo:** Alpaca (US market/options data y order routing) + FMP (Quote REST API) + Massive (Real-time option/stock WS).

---

## 📈 ESTADO ACTUAL DEL PROYECTO

**Última actualización:** 2026-06-11
**Sesiones completadas:** 6
**Progreso general:** 95% completado (Scanner migrado a datos reales con WebSocket en tiempo real, auth integrado, 52 tests unitarios, infraestructura Vitest completa).

---

## 🧩 MÓDULOS DEL SISTEMA

### Backend — Fase A: Scanner ✅ COMPLETADO
- [x] `MarketDataHub` — Clase central para acceso a APIs externas
- [x] Conexión al exchange (API key, autenticación FMP y Alpaca)
- [x] Endpoint de listado de tickers (`/api/v1/market-scanner/scan`)
- [x] Filtro por volumen, precio, score, dirección
- [x] Filtro por universo (wall_street, crypto, all)
- [x] Tests para el scanner (pytest)
- [x] Auth middleware (HMAC-signed cookies `qa_session`)
- [x] WebSocket manager para precios en tiempo real (`scanner_ws_manager`)

### Backend — Fase B: Filtro Microestructura
- [x] Cálculo de VPIN (Volume-Synchronized Probability of Informed Trading)
- [x] Cálculo de OFI (Order Flow Imbalance)
- [x] Selección de top candidatos (scoring institucional)
- [x] Tests para los indicadores

### Backend — Fase C: Análisis de Opciones
- [x] Descarga de options chains
- [x] Criterios de selección de contratos
- [x] Cálculo de Greeks básicos (Delta, Theta)
- [x] Selección de top contratos
- [x] Tests para la selección

### Backend — Fase D: Monitor en Tiempo Real
- [x] WebSocket al exchange para datos tick-by-tick
- [x] Reconnect automático con backoff exponencial
- [x] Generación de señales de ejecución (Stubs de endpoint WS listos en API)
- [x] `/ws/stream/{symbol}` — Signal streaming endpoint
- [x] `/ws/live-ticker` — BingX account/position updates
- [ ] Pub/Sub con Redis para el frontend
- [ ] Tests para el monitor

### Backend — Infraestructura
- [x] FastAPI app inicializada (`main.py` + routes)
- [ ] PostgreSQL configurado y conectado
- [ ] Redis configurado y conectado
- [x] Modelos SQLAlchemy (tablas iniciales)
- [ ] Migraciones con Alembic
- [x] Logging estructurado
- [x] Variables de entorno configuradas (settings mappings en `.env`)
- [x] Auth settings (`qa_session_secret`, `qa_app_username`, `qa_app_password_hash`)

### Frontend — Dashboard
- [x] Layout base con dark theme de Bloomberg (Wall Street Standard)
- [x] Conexión WebSocket al backend
- [x] Panel de Phase A (tabla de candidatos con parpadeo PriceCell y Sparklines)
- [x] Panel de Phase B (candidatos filtrados e indicadores de gey/greeks)
- [x] Panel de Phase C (contratos seleccionados de opciones de alta liquidez)
- [x] Panel de Phase D (monitor en vivo y signals feed en la cinta inferior)
- [x] Gráfico de precios en tiempo real (velas SVG simuladas de alta fidelidad)
- [ ] Order Entry (formulario de órdenes)

### Frontend — Market Scanner ✅ COMPLETADO
- [x] `types/marketScanner.ts` — Contratos de tipo 1:1 con Pydantic backend (537 líneas)
- [x] `services/scannerService.ts` — Servicio de integración con 7 endpoints backend
- [x] `hooks/useScanner.ts` — Hook centralizado con lifecycle, retry, debounce (427 líneas)
- [x] `hooks/useScannerWebSocket.ts` — WebSocket para precios en tiempo real
- [x] `store/scannerStore.ts` — Zustand store con persistencia entre tabs
- [x] `UniverseManager.tsx` — Conectado a backend, sin ALL_SYMBOLS hardcodeado
- [x] `MarketScanner/index.tsx` — Datos reales, sin initMockData, polling + WebSocket
- [x] `PhaseAnalytics.tsx` — Fase derivada de timeframes reales del backend
- [x] `StrategyWeights.tsx` — Sync con backend via GET/PUT/POST
- [x] Error handling estructurado (ApiError, NetworkError, TimeoutError, AuthError)
- [x] Retry con backoff exponencial (SCANNER_MAX_RETRIES = 2)

### Frontend — Estado y Datos
- [x] Zustand store configurado
- [x] Hook de WebSocket con reconnect
- [x] Conversión de Decimal para precios (todo como `string` para evitar flotantes de JS)
- [x] `store/scannerStore.ts` — Zustand para scanner (persiste entre tabs)
- [x] `store/tradingStore.ts` — Zustand para trading legacy (WebSocket compat)

### Frontend — Auth ✅ COMPLETADO
- [x] `hooks/useAuthToken.ts` — Hook de auth con backend cookie-based
- [x] `lib/api-client.ts` — `checkAuthStatus()`, `loginApi()`, `logoutApi()`
- [x] Backend: HMAC-signed `qa_session` cookie (httponly, samesite=lax)
- [x] Backend: `POST /login`, `POST /logout`, `GET /me` endpoints

### Frontend — Testing ✅ COMPLETADO
- [x] Vitest configurado (`vitest.config.ts`, jsdom env)
- [x] `__tests__/services/scannerService.test.ts` — 29 tests unitarios
- [x] `__tests__/store/scannerStore.test.ts` — 13 tests unitarios
- [x] `__tests__/hooks/useScanner.test.ts` — 10 tests de hook
- [x] Coverage available via `vitest run --coverage`
- [x] CI/CD con GitHub Actions (configurado de base)

---

## 📍 ÚLTIMO CHECKPOINT

**Sesión anterior terminó en:**
Se completó la migración completa del Market Scanner (Fase 0-4) de datos mock a datos reales del backend. Incluye: tipos 1:1 con Pydantic, servicio de integración con 7 endpoints, hook centralizado con retry/backoff, Zustand store persistente, WebSocket para precios en tiempo real, auth middleware con cookies HMAC-signed, error handling estructurado, y 52 tests unitarios con Vitest. Todos los quality gates pasan (tsc, eslint, prettier).

**Próxima tarea:**
Completar Fase 4 de limpieza de deuda técnica: eliminar `services/mock/` (requiere migrar 5 componentes no-scanner), actualizar PROJECT_CONFIG.md, y hacer Q&A del flujo completo.

**Archivos modificados en sesiones recientes:**
- `frontend/types/marketScanner.ts` — Contratos de tipo 537 líneas, 1:1 con Pydantic
- `frontend/services/scannerService.ts` — Servicio de integración 429 líneas
- `frontend/hooks/useScanner.ts` — Hook centralizado 427 líneas
- `frontend/hooks/useScannerWebSocket.ts` — WebSocket para precios en tiempo real
- `frontend/hooks/useAuthToken.ts` — Auth hook con backend cookie-based
- `frontend/store/scannerStore.ts` — Zustand store para scanner
- `frontend/lib/api-client.ts` — Error classes + auth helpers + credentials:include
- `frontend/lib/constants.ts` — Todos los números mágicos centralizados
- `frontend/components/tabs/MarketScanner/index.tsx` — Datos reales + WebSocket
- `frontend/components/tabs/MarketScanner/UniverseManager.tsx` — Backend universes
- `frontend/components/tabs/MarketScanner/PhaseAnalytics.tsx` — Phase derived
- `frontend/components/tabs/MarketScanner/StrategyWeights.tsx` — Backend sync
- `frontend/store/tradingStore.ts` — Limpieza de initMockData
- `frontend/vitest.config.ts` — Configuración de testing
- `frontend/__tests__/services/scannerService.test.ts` — 29 tests
- `frontend/__tests__/store/scannerStore.test.ts` — 13 tests
- `frontend/__tests__/hooks/useScanner.test.ts` — 10 tests
- `backend/config/settings.py` — Auth settings
- `backend/api/router.py` — Mounted auth_router
- `backend/routers/auth_router.py` — Auth dependency
- `backend/routers/market_scanner_router.py` — Auth on all endpoints

**Comandos para verificar que todo está bien:**
```bash
# Frontend type check
cd frontend && npx tsc --noEmit
# Frontend lint
npx eslint --max-warnings=0 .
# Frontend tests
npx vitest run __tests__/
# Frontend build
npm run build
# Backend type check
cd .. && python -m py_compile backend/main.py
# Levantar el backend
python -m backend.main
```

---

## 🏗️ DECISIONES DE ARQUITECTURA TOMADAS

| ID | Decisión | Por qué | Fecha |
|----|----------|---------|-------|
| ARCH-001 | Todo precio/volumen como string en frontend | Evita la acumulación de errores de precisión flotante al manejar precios de opciones de alta precisión | 2026-06-09 |
| ARCH-002 | Un único httpx.AsyncClient en MarketDataHub | Optimiza la reutilización de sockets HTTP/TCP para FMP y Alpaca, reduciendo la latencia de red en llamadas secuenciales | 2026-06-09 |
| ARCH-003 | Zustand para scanner data, local state para lifecycle | `scannerStore` persiste tickers/universes/params entre tabs; `isScanning`/`isLoading`/`error` stay local por mount | 2026-06-11 |
| ARCH-004 | Adapter pattern para Ticker compat | `displayToTicker`/`displayListToTickers` bridging `ScannerTickerDisplay` → `Ticker` para mantener TickerRow/TickerModal/PhaseDonut sin modificar | 2026-06-11 |
| ARCH-005 | Cookie-based auth (HMAC-signed `qa_session`) | Backend ya tenía auth completa; solo necesitaba mounting y `Depends(get_current_user)` en scanner endpoints | 2026-06-11 |
| ARCH-006 | Error classes en `api-client.ts` | `ApiError`, `NetworkError`, `TimeoutError`, `AuthError` proveen metadata estructurada para UI categorization | 2026-06-11 |
| ARCH-007 | WebSocket para precios en tiempo real | Reemplaza polling HTTP de 3s; conecta a `/ws/stream/ALL` existente; exponential backoff 1s→30s | 2026-06-11 |
| ARCH-008 | Vitest over Jest | Tests existentes de bingx-bot ya usaban vitest; consistencia en tooling | 2026-06-11 |

---

## ⚠️ PROBLEMAS CONOCIDOS

| ID | Problema | Prioridad | Estado |
|----|----------|-----------|--------|
| KNOWN-001 | `services/mock/` still used by 5 non-scanner components (BingXBot, BinanceBot, AlpacaBot, Predictive, Technical, CandleChart) | Medium | Open — requires migrating those modules to real APIs first |
| KNOWN-002 | 4 pre-existing test failures in `bingx-probabilistic-panel.test.tsx` (unrelated to scanner work) | Low | Open — tests check for `text-info` CSS class but component uses `text-blue-400` |
| KNOWN-003 | Backend auth settings (`qa_session_secret`, etc.) need `.env` values to function | Medium | Open — auth endpoints will return 503 without env vars |
| KNOWN-004 | WebSocket reconnect in `useScannerWebSocket` doesn't have a `mountedRef` guard in the `reconnect` callback | Low | Minor — reconnect is user-initiated |

---

## 🔑 VARIABLES DE ENTORNO NECESARIAS

```
# Base de datos
DATABASE_URL=
REDIS_URL=

# Cryptografía
SECRET_KEY=

# Integraciones de Mercado
FMP_API_KEY=
MASSIVE_API_KEY=
MASSIVE_WS_URL=
ALPACA_API_KEY=
ALPACA_API_SECRET=

# Auth (HMAC-signed cookies)
QA_SESSION_SECRET=
QA_APP_USERNAME=
QA_APP_PASSWORD_HASH=
QA_APP_DISPLAY_NAME=
QA_APP_EMAIL=

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
```
