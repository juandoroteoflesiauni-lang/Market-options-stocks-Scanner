# 🔄 WORKFLOW STATE — TRADING TERMINAL
> Actualizar en CADA sesión de trabajo. Este archivo es la memoria del proyecto.

---

## 📊 ESTADO ACTUAL

```yaml
fecha_ultima_sesion: "2026-06-05"
fase_global: "CONSTRUCT"
modulo_activo: "MÓDULO 3 (Catalyst NLP Engine refactoring)"
completado_pct: 100%
```

---

## ✅ MÓDULOS COMPLETADOS

```
- Módulo 3 (Catalyst NLP Engine refactoring) - Completado
- Módulo 3 (Sentiment Engine refactoring) - Completado
- Módulo 3 (Feedback Calibration Engine refactoring) - Completado
- Módulo 3 (Regime Weights Engine refactoring) - Completado
- Módulo 3 (Cross-Asset Correlation Engine refactoring) - Completado
- Módulo 3 (Correlation Analyzer Engine refactoring) - Completado
- Módulo 1 (Delta Exposure (DEX) Engine refactoring) - Completado
- Módulo 1 (Options Volume/OI Dynamics refactoring) - Completado
- Módulo 3 (Fear & Greed Engine refactoring) - Completado
```

---

## 🟡 EN PROGRESO

```
[ ] Setup del entorno de desarrollo
    - [ ] Instalar Python 3.11+
    - [ ] Instalar Node 18+
    - [ ] Instalar Docker Desktop
    - [ ] Crear estructura de directorios
    - [ ] Crear .env con variables vacías
    - [ ] Inicializar git repository
```

---

## 🔴 PENDIENTE (En orden de prioridad)

```
CRÍTICO:
[ ] MÓDULO-001: Autenticación (JWT + registro de usuario)
[ ] MÓDULO-002: Estructura base FastAPI + conexión DB

ALTA:
[ ] MÓDULO-003: Feed de precios en tiempo real (WebSocket)
[ ] MÓDULO-004: Order Book en tiempo real
[ ] MÓDULO-005: Formulario de órdenes (buy/sell)
[ ] MÓDULO-006: Gestión de riesgo (validaciones)

MEDIA:
[ ] MÓDULO-007: Portfolio y posiciones
[ ] MÓDULO-008: Historial de operaciones
[ ] MÓDULO-009: Gráficos TradingView
[ ] MÓDULO-010: Alertas de precio

BAJA:
[ ] MÓDULO-011: Backtesting básico
[ ] MÓDULO-012: Dashboard de estadísticas
[ ] MÓDULO-013: Configuración de usuario
```

---

## 🐛 BUGS CONOCIDOS

```
Ninguno aún
```

---

## ⚠️ DEUDA TÉCNICA

```
Ninguna aún
```

---

## 📝 NOTAS DE LA ÚLTIMA SESIÓN

```
Se completó la migración del motor FearGreedEngine a un diseño síncrono, stateless e inmutable con Pydantic. Se creó el archivo backend/engine/metrics/fear_greed.py y se validó con un set exhaustivo de 13 pruebas unitarias en backend/tests/unit/test_fear_greed.py, sumando 34 pruebas exitosas en total para la suite del backend.
```

---

## 🔑 DECISIONES ARQUITECTÓNICAS TOMADAS

| Decisión | Alternativas consideradas | Razón |
|----------|--------------------------|-------|
| FastAPI para backend | Django, Flask | Async nativo, OpenAPI auto, tipado |
| SQLAlchemy async | Tortoise ORM | Estándar, mejor soporte mypy |
| Zustand para estado | Redux, Context | Más simple, sin boilerplate |
| TradingView LWC | Recharts, Chart.js | Hecho para trading, libre |
| PostgreSQL | MySQL, SQLite | Soporte Decimal nativo, performance |

---

## 📦 DEPENDENCIAS EXTERNAS A CONFIGURAR

```
[ ] Cuenta en Binance Testnet (para desarrollo sin dinero real)
    URL: https://testnet.binance.vision/

[ ] PostgreSQL corriendo localmente o en Docker
[ ] Redis corriendo localmente o en Docker

OPCIONAL:
[ ] Cuenta MT5/MT4 demo (para Forex)
[ ] TradingView webhook URL (para señales)
```

---

## 🚀 PASOS PARA INICIAR EL PROYECTO

```bash
# 1. Clonar/crear el repositorio
git init trading-terminal
cd trading-terminal

# 2. Copiar todos los archivos de reglas a su lugar

# 3. Crear entorno Python
python -m venv venv
source venv/bin/activate  # Mac/Linux
# venv\Scripts\activate    # Windows

# 4. Crear proyecto React
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install

# 5. Copiar .env.example a .env y completar variables

# 6. Iniciar bases de datos con Docker
docker-compose up -d

# 7. Decirle a la IA: "Inicia el MÓDULO-001: Autenticación"
```
