# 🏦 DEEP TRADING TERMINAL
## Sistema de Control para Vibecoding — v3.0

> **Tu mapa de navegación:** Leer esto al comenzar cada sesión de desarrollo con IA.

---

## 🎯 ¿Qué es este repositorio?

Este repositorio contiene el **sistema nervioso de control** para desarrollar una terminal de trading cuantitativo profesional usando 100% IA (Vibecoding).

Son las reglas, configuraciones y la constitución que hacen que la IA (Claude Code, Cursor, VS Code Copilot) se comporte como un desarrollador senior de software financiero — en lugar de generar código spaghetti que no sirve en producción.

| Sin estas reglas | Con estas reglas |
|-----------------|-----------------|
| ❌ Código desorganizado y entrelazado | ✅ Arquitectura clara en capas |
| ❌ API keys en el código fuente | ✅ Secrets siempre en variables de entorno |
| ❌ Float para cálculos de dinero | ✅ Decimal para toda operación financiera |
| ❌ Memory leaks en WebSockets | ✅ Lifecycle correcto con cleanup |
| ❌ Sin tests = sin confianza | ✅ Tests obligatorios para lógica financiera |
| ❌ Cambios destructivos sin aviso | ✅ Git como red de seguridad permanente |

---

## 📁 ESTRUCTURA DEL SISTEMA

```
deep-trading-terminal/
│
├── 🤖 CONSTITUCIÓN DEL AGENTE (cargar siempre al inicio)
│   ├── CLAUDE.md                    ← Reglas maestras: la "mente" de la IA
│   ├── PROJECT_CONFIG.md            ← Estado actual: módulos y progreso
│   └── WORKFLOW_STATE.md            ← Tarea exacta donde quedamos
│
├── 🎯 REGLAS PARA CURSOR (.cursor/rules/)
│   ├── 000-master.mdc               ← Comportamiento base (siempre activa)
│   ├── 010-architecture.mdc         ← Anti-spaghetti, capas
│   ├── 020-security.mdc             ← Seguridad financiera y secrets
│   ├── 030-realtime-data.mdc        ← WebSockets y feeds de mercado
│   ├── 040-workflow.mdc             ← Blueprint→Construct→Validate
│   ├── 050-testing.mdc              ← Tests obligatorios
│   ├── 060-git.mdc                  ← Control de versiones
│   ├── 070-ui-components.mdc        ← React para interfaces de trading
│   └── 080-python-backend.mdc       ← FastAPI, SQLAlchemy, Pydantic
│
├── 💻 VS CODE / GITHUB COPILOT
│   ├── .vscode/settings.json        ← Editor + instrucciones para Copilot
│   └── .vscode/extensions.json      ← Extensiones recomendadas
│
├── 🔧 CONFIGURACIÓN DEL PROYECTO
│   ├── .env.local.example           ← Template de variables de entorno
│   ├── pyproject.toml               ← Config Python (black, ruff, mypy)
│   ├── .pre-commit-config.yaml      ← Quality checks automáticos antes de commit
│   └── docker-compose.yml           ← PostgreSQL + Redis para desarrollo
│
├── 📚 DOCUMENTACIÓN
│   ├── GUIA-REFERENCIA-RAPIDA.md    ← Para el usuario (sin jerga técnica)
│   ├── ARCHITECTURE.md              ← Diseño del sistema en detalle
│   └── CHANGELOG.md                 ← Historial de todo lo construido
│
├── backend/                         ← Código Python (FastAPI)
└── frontend/                        ← Código TypeScript (Next.js)
```

---

## 🚀 INSTALACIÓN EN UN PROYECTO NUEVO

### Paso 1: Copiar los archivos de control
```bash
cp CLAUDE.md        /ruta/de/tu/proyecto/
cp PROJECT_CONFIG.md /ruta/de/tu/proyecto/
cp -r .cursor/      /ruta/de/tu/proyecto/
cp -r .vscode/      /ruta/de/tu/proyecto/
```

### Paso 2: Instalar herramientas de calidad (una sola vez)
```bash
pip install pre-commit
pre-commit install

cp .env.local.example .env.local
# Editar .env.local con tus API keys reales del exchange
```

### Paso 3: Levantar la base de datos de desarrollo
```bash
docker-compose up -d postgres redis
```

### Paso 4: Primera sesión con la IA
```
"Lee CLAUDE.md y PROJECT_CONFIG.md.
¿Cuántos módulos están completos? ¿Cuál es el primer paso?"
```

---

## 🤖 GUÍA DE SESIÓN CON IA

### Para iniciar cada sesión
```
"Lee CLAUDE.md. Mi tarea de hoy es: [UNA SOLA ORACIÓN]."
```

### Según el tipo de trabajo (cargar el rule book correspondiente)
```
Para módulos nuevos o arquitectura:
  "También lee .cursor/rules/040-workflow.mdc"

Para APIs del exchange o datos de mercado:
  "También lee .cursor/rules/030-realtime-data.mdc"

Para seguridad, .env o autenticación:
  "También lee .cursor/rules/020-security.mdc"

Para componentes de UI del dashboard:
  "También lee .cursor/rules/070-ui-components.mdc"

Para código Python del backend:
  "También lee .cursor/rules/080-python-backend.mdc"

Para tests:
  "También lee .cursor/rules/050-testing.mdc"
```

### Checkpoints durante la sesión
```
Cada 10 intercambios con la IA:
  "Checkpoint: resumí qué construimos y qué sigue."

Al terminar la sesión:
  "Listá los TODO, FIXME y CHECKPOINT que agregamos hoy."

Antes de cada commit:
  "Corremos los tests y hacemos el commit."
```

---

## 📋 REGLAS ACTIVAS POR CONTEXTO

| Regla | Se activa cuando... | Qué controla |
|-------|---------------------|-------------|
| `000-master` | **Siempre** | Comportamiento base del agente |
| `010-architecture` | Archivos .py o .ts | Capas, anti-spaghetti, SRP |
| `020-security` | Backend, configs, auth | Secrets, validaciones, rate limiting |
| `030-realtime-data` | WebSockets, stores | Feeds de mercado, lifecycle, pub/sub |
| `040-workflow` | **Siempre** | Blueprint→Construct→Validate |
| `050-testing` | Archivos de tests | Tests financieros obligatorios |
| `060-git` | Archivos git, changelog | Commits semánticos, branching |
| `070-ui-components` | Componentes React | Dashboard, gráficos, order entry |
| `080-python-backend` | Archivos Python | FastAPI, SQLAlchemy, Pydantic |

---

## 🧠 ARQUITECTURA EN UN PÁRRAFO

El sistema es un **funnel cuantitativo de 4 fases**. La Fase A escanea miles de tickers del mercado vía REST APIs y produce hasta 300 candidatos. La Fase B ejecuta análisis de microestructura (VPIN/OFI) localmente sin red y selecciona los 20 mejores. La Fase C descarga options chains y selecciona los 5 mejores contratos. La Fase D monitorea esos 5 contratos vía WebSocket tick-by-tick y emite señales de ejecución al frontend. Todos los datos entre fases se intercambian como Pydantic `MarketSnapshot` objects inmutables. Un `MarketDataHub` actúa como Anti-Corruption Layer — es el único componente que toca APIs externas. Un Event Bus basado en `asyncio.Queue` desacopla productores de consumidores.

---

## 🏗️ STACK TECNOLÓGICO

| Capa | Tecnología | Por qué |
|------|-----------|---------|
| Backend | Python 3.12 + FastAPI | Async nativo, tipado, performance |
| ORM | SQLAlchemy 2.0 + Alembic | Migrations, queries type-safe |
| Validación | Pydantic v2 | Validación automática, serialización |
| Frontend | Next.js 14 + TypeScript | SSR, tipos estrictos, ecosistema |
| Estado | Zustand | Simple y performante para real-time |
| Estilos | Tailwind v4 | Utilidades, dark theme nativo |
| DB | PostgreSQL 16 | ACID, JSON nativo, confiable |
| Cache | Redis 7 | Pub/Sub para WebSocket, caché tickers |
| CI/CD | GitHub Actions + pre-commit | Quality gates automáticos |

---

## 🔒 POLÍTICA DE SEGURIDAD

- **Cero secrets en código:** API keys, passwords y tokens van exclusivamente en `.env.local` (gitignoreado)
- **Cero float en dinero:** Toda operación financiera usa `Decimal` en Python y `string` en TypeScript
- **Validación antes de órdenes:** Quantity > 0, Price > 0, Total ≤ límite de posición, Exchange conectado
- **Rate limiting obligatorio:** Backoff exponencial en todas las llamadas a exchanges
- **Pre-commit obligatorio:** Todo el código pasa los quality checks antes de subir al repositorio

Para reportar vulnerabilidades: GitHub Security Advisories (privado).

---

## 🔄 ESTADO DEL BUILD

| Gate | Estado |
|------|--------|
| Backend CI | [![Backend CI](https://github.com/TU_ORG/deep-trading-terminal/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/TU_ORG/deep-trading-terminal/actions) |
| Frontend CI | [![Frontend CI](https://github.com/TU_ORG/deep-trading-terminal/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/TU_ORG/deep-trading-terminal/actions) |
| Coverage | [![Coverage](https://codecov.io/gh/TU_ORG/deep-trading-terminal/graph/badge.svg)](https://codecov.io/gh/TU_ORG/deep-trading-terminal) |

*(Reemplazar TU_ORG con tu usuario de GitHub)*

---

## 📐 FILOSOFÍA DE DISEÑO

Este sistema se construye con los estándares usados por firmas de trading cuantitativo.

**Fuentes de inspiración:**
- [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) — Motor de trading determinístico y event-driven
- [cursor-rule-framework](https://github.com/fbrbovic/cursor-rule-framework) — Estructura Blueprint/Construct/Validate
- [cursor-security-rules](https://github.com/matank001/cursor-security-rules) — Reglas de seguridad para APIs financieras
- [agent-rules-books](https://github.com/ciembor) — Gobernanza de agentes IA desde libros de programación
- [Prompt Engineering Guide](https://github.com/dair-ai/Prompt-Engineering-Guide) — Prompts efectivos para desarrollo

---

## ⚠️ AVISO CRÍTICO — LEER ANTES DE OPERAR

Esta terminal maneja **dinero real**. Antes de operar en producción:

1. ✅ Probar 100% en testnet/sandbox del exchange (nunca saltear este paso)
2. ✅ Todos los tests pasando al 100%
3. ✅ Revisar cada validación de riesgo manualmente
4. ✅ Auditar el código de seguridad (especialmente manejo de API keys)
5. ✅ Backup de la base de datos antes de cada deploy
6. ✅ Empezar con posiciones mínimas del exchange

**La IA puede cometer errores. SIEMPRE revisar el código antes de ejecutar en producción.**
**Nunca operar con dinero que no puedas permitirte perder.**
