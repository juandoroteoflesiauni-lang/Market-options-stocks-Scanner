# 🏦 TRADING TERMINAL — PROJECT CONFIGURATION MASTER
> **Archivo de configuración raíz. LEER ANTES DE CUALQUIER ACCIÓN.**

---

## 🎯 IDENTIDAD DEL PROYECTO

```
Nombre:        Trading Terminal Pro
Tipo:          Aplicación de escritorio/web para trading financiero
Stack:         Python (FastAPI backend) + React/TypeScript (frontend)
Base de datos: PostgreSQL + Redis (caché en tiempo real)
APIs:          Binance, MT5/MT4, TradingView Webhooks
Autores IA:    Claude (Cursor / VS Code / Antigravity)
Nivel Vibe:    100% IA — Sin programador humano en el loop de código
```

---

## 🧠 REGLA MAESTRA DE VIBECODING

> El usuario NO sabe programar. La IA es el 100% del desarrollador.
> Esto significa que la IA debe:
> 1. Escribir código COMPLETO, no fragmentos.
> 2. Explicar CADA decisión técnica en español simple.
> 3. Nunca asumir que el usuario puede "completar el resto".
> 4. Siempre proveer pasos de instalación/ejecución.
> 5. Validar que el código funciona antes de entregarlo.

---

## 🏗️ ARQUITECTURA DEL SISTEMA

```
trading-terminal/
├── backend/
│   ├── app/
│   │   ├── api/           # Endpoints REST
│   │   ├── core/          # Config, seguridad, DB
│   │   ├── models/        # Modelos de datos
│   │   ├── services/      # Lógica de negocio
│   │   ├── strategies/    # Estrategias de trading
│   │   └── websockets/    # Feeds en tiempo real
│   ├── tests/
│   └── main.py
├── frontend/
│   ├── src/
│   │   ├── components/    # Componentes UI
│   │   ├── hooks/         # Custom hooks
│   │   ├── pages/         # Páginas/vistas
│   │   ├── services/      # Llamadas API
│   │   ├── store/         # Estado global (Zustand)
│   │   └── types/         # TypeScript types
│   └── package.json
├── docs/
│   ├── architecture.md
│   ├── api-reference.md
│   └── deployment.md
├── .cursor/rules/
├── .antigravity/skills/
├── .vscode/
└── PROJECT_CONFIG.md      ← ESTE ARCHIVO
```

---

## 📋 MÓDULOS DEL SISTEMA

| Módulo | Estado | Prioridad |
|--------|--------|-----------|
| Dashboard principal | 🔴 Pendiente | ALTA |
| Feed de precios en tiempo real | 🔴 Pendiente | ALTA |
| Gestión de órdenes | 🔴 Pendiente | ALTA |
| Gráficos (TradingView) | 🔴 Pendiente | ALTA |
| Alertas y notificaciones | 🔴 Pendiente | MEDIA |
| Backtesting | 🔴 Pendiente | MEDIA |
| Gestión de riesgo | 🔴 Pendiente | ALTA |
| Autenticación / 2FA | 🔴 Pendiente | CRÍTICA |
| Logs y auditoría | 🔴 Pendiente | MEDIA |

---

## ⚡ FASES DE DESARROLLO

### FASE 1 — BLUEPRINT (Planificación)
- Definir requerimientos del módulo
- Diseñar arquitectura y estructura de archivos
- Identificar dependencias externas
- NADA DE CÓDIGO AÚN

### FASE 2 — CONSTRUCT (Construcción)
- Escribir código modular y limpio
- Un archivo = una responsabilidad
- Tests unitarios incluidos
- Comentarios en español

### FASE 3 — VALIDATE (Validación)
- Revisar que el código no rompe nada existente
- Ejecutar tests
- Verificar seguridad
- Documentar cambios en CHANGELOG.md

---

## 🔒 REGLAS DE ORO (NUNCA VIOLAR)

1. **NUNCA hardcodear** API keys, passwords, o secrets → siempre `.env`
2. **NUNCA** modificar más de 3 archivos por tarea sin confirmación
3. **NUNCA** eliminar código sin mostrar qué se elimina y por qué
4. **SIEMPRE** hacer commit antes de un refactor grande
5. **SIEMPRE** preguntar antes de cambiar la arquitectura base
6. **NUNCA** código spaghetti: máximo 200 líneas por archivo
7. **SIEMPRE** tipado estricto en TypeScript (no `any`)
8. **NUNCA** lógica de negocio en componentes UI

---

## 🌐 VARIABLES DE ENTORNO REQUERIDAS

```env
# Backend
DATABASE_URL=postgresql://user:pass@localhost:5432/trading_db
REDIS_URL=redis://localhost:6379
SECRET_KEY=<generado-con-openssl-rand-hex-32>
JWT_EXPIRE_MINUTES=60

# APIs de Trading
BINANCE_API_KEY=
BINANCE_API_SECRET=
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=

# Frontend
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws
```

---

## 📝 WORKFLOW STATE
> Actualizar este campo en cada sesión

```
Última sesión: 2026-06-05
Fase actual:   CONSTRUCT
Módulo activo: MÓDULO 3 (Correlation Analyzer Engine refactoring)
Bloqueadores:  Ninguno
Próximo paso:  Integrar los motores de correlación y análisis en el pipeline principal.
```
