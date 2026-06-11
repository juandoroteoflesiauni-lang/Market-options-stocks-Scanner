# Deep Funnel Station — Sistema de Trading Cuantitativo

> **Versión del documento:** 1.0
> **Fecha:** Junio 2026
> **Clasificación:** Interno — Fundación Módulo de Funding
> **Dependencias:** `ARCHITECTURE.md`, `CLAUDE.md`, `AGENTS.md`

---

## Índice

1. [Visión General del Sistema](#1-visión-general-del-sistema)
2. [El Scanner (Phase A) — Filtro y Selección de Universo](#2-el-scanner-phase-a--filtro-y-selección-de-universo)
3. [Motor de Opciones (Phase C) — Derivatives Engine](#3-motor-de-opciones-phase-c--derivatives-engine)
4. [Motor Técnico (Phase B) — Microstructure Engine](#4-motor-técnico-phase-b--microstructure-engine)
5. [Motor Predictivo — IA Probabilística Multimodal](#5-motor-predictivo--ia-probabilística-multimodal)
6. [Motor Predictivo — IA Probabilística Multimodal](#5-motor-predictivo--ia-probabilística-multimodal)
7. [Matriz de Capacidad para el Módulo de Funding](#6-matriz-de-capacidad-para-el-módulo-de-funding)
8. [Hoja de Ruta Técnica](#7-hoja-de-ruta-técnica)
9. [Conclusión](#8-conclusión)

---

## 1. Visión General del Sistema

`deep-funnel-station` es una **estación de trading cuantitativo** diseñada para filtrar miles de tickers del mercado global hasta converger en un conjunto crítico de **5 contratos de opciones de alta liquidez** para ejecución en tiempo real. El sistema opera bajo una **arquitectura de embudo asimétrico de 4 fases**, donde cada fase existe para reducir el ruido y amplificar la calidad probabilística de los candidatos.

### Topología del Funnel

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                  EXTERNAL UNIVERSE (~5,000 tickers)                              │
│        FMP (REST) · Alpaca (US Market) · Massive (WebSocket)                     │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  PHASE A — Scanner / Filter                                                       │
│  Input:    Universo de ~5,000 tickers globales líquidos                           │
│  Técnica:  WorkerPool con sharding de API keys                                   │
│  Output:   ≤ 300 MarketSnapshot candidatos                                        │
│  Reglas:   Validación Pydantic → Hard Vetoes → 6 filtros técnicos → Fast-Track   │
└───────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  PHASE B — Microstructure Engine                                                  │
│  Input:    300 MarketSnapshot                                                      │
│  Técnica:  VPIN + OFI + SMC (sin red, solo local)                                 │
│  Output:   Top 20 activos con mayor probabilidad de ejecución                    │
│  Reglas:   Cálculos CPU-bound en ProcessPoolExecutor                              │
└───────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  PHASE C — Derivatives Engine                                                     │
│  Input:    Top 20 activos                                                          │
│  Técnica:  8 motores quant + análisis de cadenas de opciones                      │
│  Output:   Top 5 OptionContract (símbolo + strike + vencimiento)                   │
│  Reglas:   Zero network imports, datos inyectados por Hub                         │
└───────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  PHASE D — Real-Time Monitor (WebSocket)                                         │
│  Input:    5 contratos críticos                                                    │
│  Técnica:  Suscripción tick-by-tick via Massive WS                               │
│  Output:   ExecutionSignal → EventBus → Frontend                                 │
│  Reglas:   Cola de prioridad en el Bus, solo monitorea y emite señales         │
└───────────────────────────────────────────────────────────────────────────────────┘
```

**Fundamentos de diseño críticos:**

- **Aislamiento total:** Phase B y Phase C tienen **zero network imports**. Todos los datos fluyen desde Phase A → Phase B → Phase C → EventBus → Phase D.
- **Modelos inmutables:** Todo objeto cruzando fronteras de fase es un Pydantic `frozen=True` con trazabilidad `data_lineage`.
- **Procesamiento paralelo:** Cálculos CPU-intensive (VPIN, matrices, PyTorch) se ejecutan en `ProcessPoolExecutor` sin bloquear el event loop async.
- **Anti-corruption:** `MarketDataHub` es la **única** capa que toca APIs externas, encapsulando backoff exponencial, circuit breakers y normalización.

---

## 2. El Scanner (Phase A) — Filtro y Selección de Universo

El Scanner es la **primera línea de defensa** del sistema. Su misión es descartar ruido y entregar al pipeline posterior únicamente activos que cumplan con criterios estrictos de calidad, liquidez y coherencia técnica.

### 2.1 Arquitectura del Scanner

```
┌─────────────────────────────────────────────────────────────────┐
│  SCANNER PIPELINE                                                │
│  1. Regime Proxy — Ajusta umbrales según VIX antes del scan    │
│  2. WorkerPool — Concurrent fetch en chunks de 50 tickers        │
│  3. Pydantic Validation → MarketSnapshot                        │
│  4. Hard Veto (cortocircuito: NO_DATA, ILLIQUID, EXHAUSTION)    │
│  5. PhaseAGlobalFilter — 6 filtros técnicos con early-exit     │
│  6. Fast-Track (quality_score > 90 + volumen anómalo)          │
│  7. Return ≤ 300 MarketSnapshot de alta calidad                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Hard Vetoes — Cortocircuitos Rápidos

Evaluados **antes** de los filtros técnicos para evitar gasto computacional innecesario:

| Veto | Trigger | Acción |
|------|---------|--------|
| **VETO_NO_DATA** | Precio ≤ 0 o Volumen ≤ 0 | Rechazo inmediato |
| **VETO_ILLIQUID** | Volumen < min_volume configurable | Rechazo inmediato |
| **VETO_EXTREME_EXHAUSTION** | Cambio de barra > max_spread_pct | Rechazo por exceso de volatilidad sin consolidación |

### 2.3 Filtros Técnicos (6 Filtros Clásicos)

Ordenados por peso descendente y costo computacional ascendente para maximizar early-exits:

| # | Filtro | Peso | Propósito |
|---|--------|------|-----------|
| 1 | **EMA Cluster Alignment** | 20% | Validar alineación multitemporal (9, 21, 50, 200 EMAs). Al menos 3/4 alineadas |
| 2 | **ATR Volatility Gate** | 20% | Filtrar tickers sin volatilidad suficiente o con exceso de volatilidad |
| 3 | **RSI Extreme Filter** | 15% | Descartar condiciones extremas sin señales de confluence |
| 4 | **VWAP Distance Z-Score** | 15% | Descartar desviaciones del VWAP sin catalizador fundamental |
| 5 | **Shannon Entropy** | 15% | Descartar mercados con exceso de ruido/aleatoriedad |
| 6 | **SuperTrend Regime** | 15% | Validar consistencia direccional (sin cambios frecuentes) |

### 2.4 Regime Proxy — Adaptación Dinámica por VIX

Antes del escaneo, se consulta el VIX como proxy de régimen de mercado:

- **VIX > 30 (Alta volatilidad):** Umbrales de ATR se relajan, RSI extreme se amplía
- **VIX < 15 (Bulo):** Umbrales se tensan, exige mayor alineación EMA
- **VIX normal (15-30):** Pesos estándar activos

### 2.5 Fast-Track — Priorización de Alta Calidad

Tickers con `quality_score >= 90` y `volumen_actual >= 1.5x volumen_promedio` reciben flag `high_priority=True`, acelerando su paso por Phase B y Phase C.

---

## 3. Motor de Opciones (Phase C) — Derivatives Engine

El Derivatives Engine es el **cerebro cuantitativo del sistema** para el análisis de opciones. No toca redes externas; todos los datos de cadenas de opciones son inyectados por `MarketDataHub` desde Phase A.

### 3.1 Arquitectura Multi-Motor

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  DERIVATIVES ENGINE                                                              │
│  Input:  Top 20 EnrichedSnapshot desde Phase B                                   │
│  Output: Top 5 OptionContract con scoring compuesto                              │
├──────────────────────────────────────────────────────────────────────────────────┤
│  Motores Institucionales Ejecutados:                                             │
│  ├─ OptionsEngine        → GEX/VEX/CEX, Max Pain, Squeeze                        │
│  ├─ GammaFlipEngine      → Gamma flip point, régimen de volatilidad            │
│  ├─ DeltaExposureEngine  → Exposición delta MM, gamma trap                       │
│  ├─ OptionsFlowSignal    → Flujo institucional anómalo                          │
│  ├─ ZeroDayEngine        → 0DTE: pinning, cascades                            │
│  ├─ ShadowDeltaEngine    → Shadow delta, position sizing                        │
│  └─ DeltaWeightedFlow    → Capitulación por delta flow                          │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Scoring Compuesto por Contrato

Cada contrato recibe un score que combina:

| Componente | Peso | Descripción |
|------------|------|-------------|
| **Métricas Básicas** | 40% | Liquidez (volumen/OI), delta target, IV, DTE |
| **Promedio de Motores** | 60% | Score compuesto de los 8 motores institucionales |

**Sub-pesos de Métricas Básicas:**
- Liquidez: 37.5 likes (volumen + OI + spread)
- Delta: 25 likes (proximidad a delta target 0.35)
- IV: 20 likes (rango óptimo 10%-40%)
- DTE: 17.5 likes (óptimo 35 días, rango 14-60)

### 3.3 Pesos de los 8 Motores en Phase C (Total = 100%)

| Motor | Peso | Señal Dominante |
|-------|------|-----------------|
| **GEX Score** | 20% | Concentración gamma, zonas de pinning |
| **Gamma Flip** | 12% | Proximidad al gamma flip point |
| **DEX Exposure** | 15% | Exposición delta de market makers |
| **Flow Signal** | 12% | Actividad institucional anómala |
| **Zero Day (0DTE)** | 10% | Dinámica de vencimiento inmediato |
| **Shadow Delta** | 10% | Gap entre delta nominal y real |
| **Delta Flow** | 8% | Señales de capitulación/extensión |
| **Phase B Momentum** | 13% | Momentum desde OFI + SMC |

### 3.4 Selección Final

```python
# Pseudocódigo del algoritmo de selección
for candidate in top_20:
    chain = hub.get_options_chain(candidate.ticker)
    engine_results = run_all_8_engines(chain)
    scores = compute_engine_scores(engine_results, chain, candidate)

    scored_contracts = []
    for contract in chain.contracts:
        score = score_contract(contract, chain.spot_price, candidate, scores)
        if score >= min_composite_score:
            scored_contracts.append((contract, score))

    scored_contracts.sort(key=lambda x: x[1], reverse=True)
    top_5 = scored_contracts[:5]  # Top N configurables (default: 5)
```

---

## 4. Motor Técnico (Phase B) — Microstructure Engine

El Microstructure Engine es un **motor de cálculo puro** sin acceso a red. Recibe `MarketSnapshot` de Phase A, ejecuta cálculos CPU-intensivos, y devuelve `EnrichedSnapshot` con métricas de microestructura.

### 4.1 Métricas Computadas

| Métrica | Descripción | Implementación |
|---------|-------------|----------------|
| **VPIN** (Volume-Synchronized PIN) | Probabilidad de información privada | Buckets de volumen + estimación de pines |
| **OFI** (Order Flow Imbalance) | Desequilibrio entre compras y ventas | Modelo Cont-Kukanov-Stoikov (2014) adaptado a OHLCV |
| **SMC** (Smart Money Concepts) | Bias direccional institucional | Detección vectorizada de BOS, CHoCH, Order Blocks, FVG |

### 4.2 Arquitectura SMC

El motor SMC detecta patrones de "dinero inteligente" con precisión institucional:

- **BOS (Break of Structure):** Confirmación de ruptura estructural
- **CHoCH (Change of Character):** Cambio de régimen de mercado
- **Order Blocks:** Zonas de acumulación/distribución institucional
- **Fair Value Gaps (FVG):** Ineficiencias de precio no retrateadas
- **Liquidity Sweeps:** Barrido de liquidez con desplazamiento confirmado

### 4.3 Paralelización

Todos los cálculos CPU-bound corren en `ProcessPoolExecutor` para no bloquear el event loop async principal:

```python
# Ejecución concurrente por snapshot
tasks = [
    loop.run_in_executor(self._executor, _enrich_single, snap)
    for snap in snapshots
]
results = await asyncio.gather(*tasks)
```

---

## 5. Motor Predictivo — IA Probabilística Multimodal

El motor predictivo es la **capa de inteligencia artificial** del sistema, fusionando señales de fundamentales, sentimiento y microestructura para producir convicciones direccionales con probabilidad asignada.

### 5.1 Arquitectura del Motor

```
┌─────────────────────────────────────────────────────────────────┐
│  MULTIMODAL PREDICTIVE ENGINE                                    │
├─────────────────────────────────────────────────────────────────┤
│  Input Channels:                                                  │
│  ├─ Datos Fundamentales (OHLCV, métricas financieras)          │
│  ├─ Sentimiento (news, earnings transcripts, insider activity)    │
│  └─ GEX Data (gamma exposure, vanna flow)                       │
├─────────────────────────────────────────────────────────────────┤
│  Fusion Core:                                                     │
│  ├─ Outer-Product Tensor Fusion (np.einsum)                      │
│  ├─ Event-Driven Conv-LSTM (PyTorch)                            │
│  └─ QuantumAlpha Engine (kernel de inferencia profunda)        │
├─────────────────────────────────────────────────────────────────┤
│  Gating:                                                          │
│  └─ Probabilistic GEX Gating (seguridad antes de emisión)        │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Event-Driven Conv-LSTM

Una red neuronal **Conv-LSTM con mecanismos de retención de eventos** que procesa secuencias temporales de tensores 3D:

- **Input:** Secuencia de tensores outer-product (fundamentales × sentimiento)
- **Event Retention Gate:** Gated mechanism para retener información de eventos de mercado
- **Output:** Clasificación direccional con probabilidad asignada

```python
class EventDrivenLSTMCell(nn.Module):
    """Celula LSTM convolucional con retención de eventos"""
    # ... proyecciones de input, hidden, evento
    # ... gates: forget, input, output, candidate
    # ... event_retention_gate para persistencia de información
```

### 5.3 Outer-Product Tensor Fusion

Fusiona datos fundamentales y de sentimiento mediante producto exterior para capturar interacciones no lineales:

```python
# Tensores 2D se combinan en tensor 3D via outer-product
tensor_3d = np.einsum("ij,ik->ijk", fund_arr, news_arr, optimize="optimal")
```

### 5.4 GEX Gating — Control de Riesgo Predictivo

Antes de emitir cualquier señal predictiva, se verifica la seguridad del entorno gamma:

```python
is_safe = calculate_probabilistic_gex_gating(
    current_gex=gex_data["total_gex"],
    vanna_flow=gex_data["net_vanna_flow"],
    regime_confidence=0.8
)
# Si is_safe = False, la señal se descarta o se reduce tamaño
```

### 5.5 Orquestación del Motor en el Funnel

El motor predictivo opera en dos niveles:

1. **Phase C (Derivatives):** Alimenta el scoring de contratos con `conviction` y `bias` direccional
2. **Phase D (Real-Time):** Modula la confianza de las señales de ejecución en vivo basándose en convergencia predictiva

---

## 6. Matriz de Capacidad para el Módulo de Funding

La fundación de capital (funding) requiere un enfoque sistémico que combine los motores existentes con nuevas capacidades de gestión de riesgo y aceleración de capital.

### 6.1 Arquitectura Conceptual del Módulo de Funding

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  MÓDULO DE FUNDING v1.0 (Propuesta)                                              │
├──────────────────────────────────────────────────────────────────────────────────┤
│  Layer 1: Capital Base Management                                                  │
│  ├─ Segregación de capital por tramos de riesgo                                   │
│  ├─ Drawdown protection con circuit breakers dinámicos                           │
│  ├─ Reinversión progresiva basada en multiplicador de Kelly fraccionado         │
│  └─ Funding Lab: métricas intraday (1h, 4h, EOD)                                │
├──────────────────────────────────────────────────────────────────────────────────┤
│  Layer 2: Multi-Signal Confluence Engine                                          │
│  ├─ Convergence Score: alineación Scanner + Técnico + Opciones + Predictivo    │
│  ├─ Agreement Matrix: correlación de direcciones entre motores                   │
│  ├─ Confidence Weighting: peso dinámico según régimen VIX                         │
│  └─ Veto Override: veto de predictivo si GEX gating es inseguro                 │
├──────────────────────────────────────────────────────────────────────────────────┤
│  Layer 3: Risk-Adjusted Position Sizing                                           │
│  ├─ Kelly Fractional sizing con bounded max (ej. 2% del capital por trade)       │
│  ├─ Volatility-adjusted sizing: menor tamaño en alta volatilidad                 │
│  ├─ Correlation-aware portfolio construction                                      │
│  ├─ Maximum drawdown circuit breaker (ej. -5% diario)                            │
│  └─ Anti-martingale: incremento progresivo post-ganancias                         │
├──────────────────────────────────────────────────────────────────────────────────┤
│  Layer 4: Execution & Monitoring                                                  │
│  ├─ Dry-run validation con backtesting en datos reales                          │
│  ├─ Paper trading con métricas de funding simuladas                             │
│  ├─ Live execution con kill-switch automático                                    │
│  └─ Post-trade analysis con funding metrics (Sharpe, Profit Factor, Sortino)    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Integración con Motores Existentes

| Componente Existente | Capacidad Aprovechada | Contribución al Funding |
|---------------------|----------------------|------------------------|
| **Scanner (Phase A)** | Selección de universo de alta calidad | Reduce el riesgo de selección en mercados de baja calidad |
| **Microstructure (Phase B)** | OFI/SMC para timing de entrada | Mejora el timing preciso de entradas, reduciendo slippage |
| **Derivatives (Phase C)** | 8 motores de análisis de opciones | Identificación de contratos con alto reward/risk |
| **Predictivo** | Bias + convicción direccional | Aumenta la probabilidad de trades con alta convicción |
| **GEX Gating** | Seguridad gamma pre-trade | Previene entrar en zonas de mercado gamma-inestables |
| **Funding Lab** | Métricas intraday (1h, 4h, EOD) | Proporciona feedback loop de rendimiento para ajustar pesos |

### 6.3 Estrategia de Crecimiento Expansivo con Mínimo Riesgo

**Principio central:** Capital preservation > Capital growth.

El sistema propone una estrategia de **capital compounding con protección asimétrica**:

#### Fase 1: Validación (Mes 1-2)

- **Capital asignado:** 5-10% del capital total
- **Operaciones:** Paper trading / micro-cuentas
- **Métricas clave:** Win rate > 55%, Profit Factor > 1.5, Max DD < 3%
- **Feedback loop:** Ajustar pesos de StrategyWeights según resultados del Funding Lab

#### Fase 2: Crecimiento Controlado (Mes 3-6)

- **Capital asignado:** 25-50% del capital total
- **Operaciones:** Live con sizing fraccionario (Kelly 0.25x - 0.5x)
- **Circuit breakers:** Max daily loss -3%, max drawdown circuit breaker -5%
- **Aceleración:** Solo tras 3 semanas consecutivas de profit

#### Fase 3: Expansión (Mes 7+)

- **Capital asignado:** 100% del capital total
- **Sizing:** Kelly fraccionado hasta máximo 2% por trade
- **Diversificación:** Múltiples cuentas de fondeo con correlación controlada
- **Anti-martingale:** Incremento de exposición solo tras períodos rentables

### 6.4 Métricas de Control para Cada Fase

| Fase | Tramo Capital | Win Rate Target | Profit Factor | Max Drawdown | Sharpe Target |
|------|--------------|-----------------|---------------|--------------|---------------|
| Validación | 5-10% | > 55% | > 1.5 | < 3% | > 1.0 |
| Crecimiento | 25-50% | > 58% | > 1.8 | < 5% | > 1.5 |
| Expansión | 100% | > 60% | > 2.0 | < 7% | > 2.0 |

---

## 7. Hoja de Ruta Técnica

### Milestones para el Módulo de Funding

| # | Milestone | Status | Dependencias |
|---|-----------|--------|--------------|
| 1 | **StrategyWeights API** — Modificación en caliente de pesos | ✅ Existente | None |
| 2 | **Funding Lab Metrics** — Outcomes intraday (1h, 4h, EOD) | ✅ Existente | None |
| 3 | **Convergence Scoring** — Unificación de scores cross-phase | 🔧 Propuesto | Motores A, B, C, Predictivo |
| 4 | **Risk Desk Integration** — Kill-switches y circuit breakers | 🔧 Parcial (BingX) | RiskDesk, StateManager |
| 5 | **Meta-Learner** — Ajuste automático de pesos por outcome | 🔧 Propuesto | Funding Lab, Backtesting |
| 6 | **Multi-Account Orchestrator** — Gestión de cuentas de fondeo | 📝 Pendiente | Capital Management Layer |
| 7 | **Funding Audit Trail** — Traza completa de decisiones de capital | 📝 Pendiente | Observability, Audit Hooks |

### Stack Técnico Recomendado para Funding

| Capa | Tecnología | Justificación |
|------|-----------|---------------|
| Capital State | `pydantic` + `sqlite` (metric lake) | ACID, trazable, ligero |
| Risk Circuit Breakers | `asyncio` + `state_manager` | Respuesta en milisegundos |
| Kelly Sizing | `scipy.optimize` | Optimización fraccionada robusta |
| Drawdown Monitoring | Streaming `pandas` | Cálculo continuo de métricas |
| Audit & Compliance | `structlog` + `json` | Traza inmutable de decisiones |
| Multi-Account | `asyncio` + `httpx` | Orquestación concurrente de cuentas |

---

## 8. Conclusión

El sistema `deep-funnel-station` ya posee una **arquitectura institucional robusta** con:

- ✅ **Scanner** de alta calidad con filtros técnicos y regime adaptation
- ✅ **Motor de Opciones** con 8 motores cuantitativos especializados
- ✅ **Motor Técnico** con VPIN, OFI y SMC de nivel institucional
- ✅ **Motor Predictivo** con IA multimodal (Conv-LSTM + Tensor Fusion)
- ✅ **Risk Framework** con GEX gating, kill-switches y circuit breakers
- ✅ **Funding Lab** con métricas intraday

**Para el Módulo de Funding**, la ruta es clara:

1. **Unificar** los scores de los 4 motores en un `ConvergenceScore` compuesto
2. **Implementar** sizing con Kelly fraccionado + circuit breakers de drawdown
3. **Validar** con paper trading + Funding Lab metrics durante 4-8 semanas
4. **Escalar** progresivamente con anti-martingale y multi-cuentas

La arquitectura actual proporciona los cimientos técnicos necesarios para pasar **cualquier cuenta de fondeo con suficiencia**, siempre que el capital se gestione con la misma rigurosidad que el procesamiento de datos: preservación asimétrica del capital, compounding controlado, y feedback loops cerrados entre decisión, ejecución y métrica.

---

> *"El objetivo no es ganar más rápido, sino perder más lento que el mercado. El compounding hace el resto."*
