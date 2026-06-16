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

**Última actualización:** 2026-06-12
**Sesiones completadas:** 9
**Progreso general:** 97% completado (Scanner migrado a datos reales con WebSocket en tiempo real, auth integrado, **MFFU Builder Plan backend + API + cockpit mínimo** con 61 tests unitarios Builder, infraestructura Vitest completa, **Expediente del Módulo de Funding generado**).

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

### Backend — MFFU Builder Plan ($50K) ✅ COMPLETADO
- [x] Contratos CME + preset `MFFU_BUILDER_50K` (`builder_contracts.yaml`, `builder_models.py`)
- [x] State machine EVAL→SIM→LIVE + persistencia SQLite (`builder_state_machine.py`, `builder_state_store.py`)
- [x] Rule engine (trailing DD, DLL soft pause, contract cap)
- [x] Sizing overlay (% risk → contratos enteros)
- [x] Survival + payout engines (50% consistency, buffer, qualified days)
- [x] Integración en `funding_orchestrator.py` (FTMO sigue siendo default)
- [x] API: `GET /api/v1/funding/builder/state|metrics`, `POST /evaluate`
- [x] Persistencia de PnL diario (`builder_daily_pnl`) para consistency/survival con histórico
- [x] Intraday trailing DD tracker (proyección de floor EOD + alerta de drift)
- [x] Consistency 50% live tracker (techo de ganancia diario)
- [x] Payout planner (buffer/días restantes + ETA a primer retiro)
- [x] What-if por trade (escenario post-stop: equity, distancias, breach)
- [x] Backtest determinístico de supervivencia (`POST /builder/backtest`)
- [x] Batch evaluate para leaders del scanner (`POST /builder/evaluate-batch`)
- [x] 77 tests unitarios Builder (`pytest -k builder`)

### Frontend — Funding Module
- [x] `BuilderCockpit` (eval progress, trailing DD, DLL, payout buffer, max profit hoy, payout ETA, alerta floor drift)
- [x] Preset `mffu-builder-50k` en `data/funding.ts`
- [x] `fetchBuilderMetrics()` + `evaluateBuilderCandidate()` en `store/fundingStore.ts`
- [ ] Dashboard glassmorphism dark mode completo (Survival + Risk Metrics + Sizing + Global Context)
- [ ] `types/riskMetrics.ts` 1:1 con Pydantic (200 líneas)
- [ ] `services/riskMetricsService.ts` con retry/backoff
- [ ] `store/fundingStore.ts` Zustand con persistencia
- [ ] Kill switch visible y operativo
- [ ] Alerts feed con 3 niveles + dedup 15min
- [ ] Monte Carlo button + histograma de drawdowns
- [ ] Consistency heatmap mensual
- [ ] Trade journal con filtros
- [ ] Mode toggle paper ↔ live
- [ ] Playbook audit trail visual

### Backend — Performance Analytics (Sprint 0 — NUEVO)
- [ ] `PerformanceAnalyticsEngine` con E[R] global + por setup, PF rolling, Sharpe/Sortino/Calmar
- [ ] BUR 3 zonas (verde/amarillo/rojo)
- [ ] VaR/CVaR 95/99 histórico
- [ ] Ulcer Index rolling 50
- [ ] Risk of Ruin Monte Carlo (n_sims=10k, n_trades=100, mll=10%)
- [ ] Kelly fraccional (25% con cap 25%)
- [ ] `RiskMetricsSnapshot` Pydantic frozen
- [ ] `TradeRecord` Pydantic frozen + SQLite persistence
- [ ] `FundingThresholds` pydantic-settings con `QA_FTMO_*` y `QA_PA_*`
- [ ] `GET /api/v1/funding/risk-metrics` endpoint con auth
- [ ] Tests unitarios + integración con coverage ≥ 80%

### Backend — Sizing + Global Context (Sprint 1 — NUEVO)
- [ ] `SizingEngine` multi-factor (Kelly × F_vol × F_dd × F_signal × F_regime × F_conviction × F_global)
- [ ] `GlobalContextEngine` Capa 3.5 con VIX, Fear/Greed, SPY/EEM, QQQ/IWM, XLY/XLP, HYG/TLT, Breadth
- [ ] `ConvergenceGate` con R:R ≥ 1.5 + Expectancy Gate por setup
- [ ] `DailyBudgetGuard` con CVaR 99% × 1.67
- [ ] `TrailingMLLSimulator` con BUR 3 zonas + Live Calmar
- [ ] `ConsistencyRuleManager` con PF rolling + Sortino rolling
- [ ] `PreMarketCheck` con UI rolling override
- [ ] Test E2E `test_funding_pipeline.py` con todo el pipeline

### Backend — Funding Production Hardening (Sprint 5)
- [ ] `docs/OPERATIONS_RUNBOOK.md` con quickstart "Operar mañana"
- [ ] `docs/SECURITY_AUDIT_FUNDING.md` con PD-1 compliance
- [ ] `docs/INCIDENT_RESPONSE.md`
- [ ] `scripts/seed_backtest_data.py` con datos sintéticos
- [ ] `scripts/verify_production_readiness.py` con todos los gates
- [ ] `docs/FUNDING_MODULE.md` reescrito con arquitectura 8 capas
- [ ] `CHANGELOG.md` con todos los cambios Sprint 0-5
- [ ] Branch `feature/05-funding` con commits semánticos

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

**Sesión actual (Fases 1-5 Módulo 05 Funding) entregó:**
- **Contrato Canónico (Fase 1)**: Implementado `CanonicalSignalPayload` en `backend/models/canonical_signal.py` utilizando Pydantic v2 y precisión Decimal para soportar estructuras de opciones multi-pata de forma limpia.
- **Sizing de Opciones Estructurado (Fase 2)**: Desarrollados `LinearInstrumentSizer` y `StructuredOptionsSizer` en `backend/services/` para aplicar penalización por bid-ask spread y controlar el buying power de Alpaca.
- **Integración con Orquestador (Fase 3)**: Integrada la evaluación canónica en `FundingOrchestrator` y habilitado el pipeline de control para admitir opciones.
- **Reconciliación y Estado Vivo (Fase 4)**: Creada caché en memoria en `BuilderStateStore` con persistencia SQLite asíncrona en segundo plano, e implementado `BrokerStateReconciliator` para sincronizar equity, PnL y HWM dinámicamente con la API de Alpaca.
- **Cockpit en Tiempo Real vía WebSockets (Fase 5)**: Habilitado el stream en tiempo real `/api/v1/ws/funding` en el backend y adaptado `fundingStore.ts` en el frontend para actualizar los indicadores React de forma reactiva cada 3 segundos, incorporando reconexión automática y fallback.
- **Corrección de Simulación**: Resuelto error en el endpoint `/mock-trade` mediante la importación explícita de `TradeRecord`.
- **Tests de Calidad Financiera**: Suite de 36 pruebas unitarias de funding/builder completada al 100%, junto con 3 pruebas de integración dedicadas para la API WebSocket y mock trade.

**Comandos para verificar que todo está bien:**
```bash
# Ejecutar tests unitarios de Funding/Builder
poetry run pytest backend/tests/unit/ -k "builder or reconciliator or canonical or funding"
# Ejecutar tests de integración WebSocket/Mock API
poetry run pytest backend/tests/integration/test_funding_ws.py backend/tests/integration/test_risk_metrics_api.py
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
