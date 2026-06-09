# 📊 PROJECT_CONFIG.md
## Deep Trading Terminal — Estado del Proyecto

> **INSTRUCCIÓN PARA LA IA:** Leer este archivo al inicio de cada sesión.
> Actualizar la sección "Estado Actual" y "Último Checkpoint" al terminar cada sesión.

---

## 🎯 DESCRIPCIÓN DEL PROYECTO

**Nombre:** Deep Trading Terminal
**Objetivo:** Terminal de trading cuantitativo para opciones y futuros con análisis de microestructura
**Stack:** Python/FastAPI (backend) + TypeScript/Next.js (frontend) + PostgreSQL + Redis
**Arquitectura:** Funnel de 4 fases (Scanner → Microestructura → Opciones → Monitor)
**Exchange objetivo:** [Nombre del exchange — ej: Binance, Interactive Brokers, etc.]

---

## 📈 ESTADO ACTUAL DEL PROYECTO

**Última actualización:** [Fecha — la IA actualiza esto]
**Sesiones completadas:** [Número]
**Progreso general:** [X]% completado

---

## 🧩 MÓDULOS DEL SISTEMA

### Backend — Fase A: Scanner
- [ ] `MarketDataHub` — Clase central para acceso a APIs externas
- [ ] Conexión al exchange (API key, autenticación)
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
- [ ] Generación de señales de ejecución
- [ ] Pub/Sub con Redis para el frontend
- [ ] Tests para el monitor

### Backend — Infraestructura
- [ ] FastAPI app inicializada
- [ ] PostgreSQL configurado y conectado
- [ ] Redis configurado y conectado
- [ ] Modelos SQLAlchemy (tablas)
- [ ] Migraciones con Alembic
- [ ] Logging estructurado
- [ ] Variables de entorno configuradas

### Frontend — Dashboard
- [ ] Layout base con dark theme
- [ ] Conexión WebSocket al backend
- [ ] Panel de Phase A (tabla de candidatos)
- [ ] Panel de Phase B (candidatos filtrados)
- [ ] Panel de Phase C (contratos seleccionados)
- [ ] Panel de Phase D (monitor en vivo)
- [ ] Gráfico de precios en tiempo real
- [ ] Order Entry (formulario de órdenes)

### Frontend — Estado y Datos
- [ ] Zustand store configurado
- [ ] Hook de WebSocket con reconnect
- [ ] Conversión de Decimal para precios

### Testing
- [ ] pytest configurado
- [ ] Tests de unidad para cálculos financieros
- [ ] Tests de integración para APIs
- [ ] Tests de frontend (Vitest)
- [ ] CI/CD con GitHub Actions

---

## 📍 ÚLTIMO CHECKPOINT

**Sesión anterior terminó en:**
```
[La IA escribe aquí dónde quedó exactamente]
```

**Próxima tarea:**
```
[La IA escribe aquí cuál es el siguiente paso concreto]
```

**Archivos modificados en última sesión:**
```
- [Archivo 1]: [Qué se hizo]
- [Archivo 2]: [Qué se hizo]
```

**Comandos para verificar que todo está bien:**
```bash
[La IA escribe aquí los comandos para confirmar que el estado es correcto]
```

---

## 🏗️ DECISIONES DE ARQUITECTURA TOMADAS

| ID | Decisión | Por qué | Fecha |
|----|----------|---------|-------|
| ARCH-001 | [Decisión tomada] | [Justificación] | [Fecha] |

---

## ⚠️ PROBLEMAS CONOCIDOS

| ID | Problema | Prioridad | Estado |
|----|----------|-----------|--------|
| BUG-001 | [Descripción del bug] | Alta/Media/Baja | Abierto/Resuelto |

---

## 🔑 VARIABLES DE ENTORNO NECESARIAS

*(Nunca poner los valores reales aquí — solo los nombres)*

```
EXCHANGE_API_KEY=
EXCHANGE_API_SECRET=
DATABASE_URL=
REDIS_URL=
ENVIRONMENT=development
MAX_POSITION_SIZE=
LOG_LEVEL=INFO
```

---

## 📝 NOTAS DE DESARROLLO

*(La IA agrega aquí notas importantes sobre decisiones y contexto)*

```
[Espacio para notas libres de la IA entre sesiones]
```
