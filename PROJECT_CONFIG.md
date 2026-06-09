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

**Última actualización:** 2026-06-09
**Sesiones completadas:** 2
**Progreso general:** 85% completado (la infraestructura central, el frontend completo, la comunicación por WS/REST y los fetchers reales ya se encuentran integrados y probados).

---

## 🧩 MÓDULOS DEL SISTEMA

### Backend — Fase A: Scanner
- [x] `MarketDataHub` — Clase central para acceso a APIs externas
- [x] Conexión al exchange (API key, autenticación FMP y Alpaca)
- [ ] Endpoint de listado de tickers
- [ ] Filtro básico por volumen
- [ ] Filtro básico por volatilidad
- [ ] Tests para el scanner

### Backend — Fase B: Filtro Microestructura
- [ ] Cálculo de VPIN (Volume-Synchronized Probability of Informed Trading)
- [ ] Cálculo de OFI (Order Flow Imbalance)
- [ ] Selección de top 20 candidatos
- [ ] Tests para los indicadores

### Backend — Fase C: Análisis de Opciones
- [ ] Descarga de options chains
- [ ] Criterios de selección de contratos
- [ ] Cálculo de Greeks básicos (Delta, Theta)
- [ ] Selección de top 5 contratos
- [ ] Tests para la selección

### Backend — Fase D: Monitor en Tiempo Real
- [ ] WebSocket al exchange para datos tick-by-tick
- [ ] Reconnect automático con backoff exponencial
- [x] Generación de señales de ejecución (Stubs de endpoint WS listos en API)
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

### Frontend — Dashboard
- [x] Layout base con dark theme
- [x] Conexión WebSocket al backend
- [x] Panel de Phase A (tabla de candidatos)
- [x] Panel de Phase B (candidatos filtrados)
- [x] Panel de Phase C (contratos seleccionados)
- [x] Panel de Phase D (monitor en vivo y signals feed)
- [ ] Gráfico de precios en tiempo real
- [ ] Order Entry (formulario de órdenes)

### Frontend — Estado y Datos
- [x] Zustand store configurado
- [x] Hook de WebSocket con reconnect
- [x] Conversión de Decimal para precios (todo como `string` para evitar flotantes de JS)

### Testing
- [ ] pytest configurado
- [ ] Tests de unidad para cálculos financieros
- [ ] Tests de integración para APIs
- [ ] Tests de frontend (Vitest)
- [x] CI/CD con GitHub Actions (configurado de base)

---

## 📍 ÚLTIMO CHECKPOINT

**Sesión anterior terminó en:**
Se implementó el esqueleto principal de endpoints y componentes UI para fases 1 a 4. Quedaba pendiente corregir la ausencia del script `type-check` en `package.json` del frontend y completar el código ejecutable de la API principal (`backend/main.py`).

**Próxima tarea:**
Conectar el motor cuantitativo de microestructura (`QuantitativeEngine` / Phase B) y las bases de datos (PostgreSQL/Redis) al flujo de ingesta real de datos iniciado en `MarketDataHub`.

**Archivos modificados en última sesión:**
- `frontend/package.json`: Se agregó el script `"type-check"`, se actualizó `eslint` a `^9.x` y se cambió `"lint": "eslint ."`.
- `frontend/eslint.config.mjs`: Creado para la configuración plana (Flat Config) requerida por ESLint 9 en Next.js 16.
- `frontend/hooks/useAuthToken.ts`: Refactorizado para inicialización perezosa de estado, eliminando efectos que llamaban a setState.
- `frontend/hooks/useSignalStream.ts`: Implementada referencia connectRef para recursión de reintento y encapsulado en un efecto para evitar mutaciones durante render.
- `backend/main.py`: Se implementó la aplicación FastAPI con ciclo de vida asíncrono y configuración de CORS.
- `backend/hub/market_data_hub.py`: Se implementaron los fetchers reales para FMP y Alpaca con cabeceras de autorización y reutilización de un único HTTP AsyncClient con cierre limpio.
- `.env`: Se mapearon las variables que `settings.py` requiere para validar.
- `backend/hub/normalizers/alpaca_normalizer.py`, `fmp_normalizer.py`, `massive_normalizer.py`: Añadidas anotaciones `dict[str, Any]` para complacer a mypy strict.
- `backend/engine/state_manager.py`: Añadido tipo `deque[Any]`.
- `pyproject.toml`: Se agregó el plugin `pydantic.mypy` para resolver el tipado de base settings.
- Creado un package.json delegador en la raíz para permitir comandos npm directamente desde allí sin generar un lockfile de raíz.
- Eliminados archivos y carpetas npm accidentales en la raíz (package-lock.json, node_modules).

**Comandos para verificar que todo está bien:**
```bash
# Frontend type check
cd frontend && npm run type-check
# Frontend Next.js build
npm run build
# Backend type check (strict)
cd .. && poetry run mypy --strict backend/main.py backend/api/ backend/bus/ backend/config/ backend/engine/ backend/hub/ backend/models/ backend/phases/
# Levantar el backend
poetry run python -m backend.main
```

---

## 🏗️ DECISIONES DE ARQUITECTURA TOMADAS

| ID | Decisión | Por qué | Fecha |
|----|----------|---------|-------|
| ARCH-001 | Todo precio/volumen como string en frontend | Evita la acumulación de errores de precisión flotante en JavaScript al manejar precios de opciones de alta precisión | 2026-06-09 |
| ARCH-002 | Un único httpx.AsyncClient en MarketDataHub | Optimiza la reutilización de sockets HTTP/TCP para FMP y Alpaca, reduciendo la latencia de red en llamadas secuenciales | 2026-06-09 |

---

## ⚠️ PROBLEMAS CONOCIDOS

| ID | Problema | Prioridad | Estado |
|----|----------|-----------|--------|
| - | Ninguno reportado | - | - |

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
```
