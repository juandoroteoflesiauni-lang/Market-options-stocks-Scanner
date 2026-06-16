# FUNDING MODULE — ROADMAP MAESTRO v3.0
## Esquema Final Unificado: Motores Cuantitativos × Métricas de Riesgo Institucional × Decisión de Fondeo

> **Versión:** 3.0 — Documento de Síntesis Final
> **Clasificación:** Blueprint Fundacional — Nivel Profesional Wall Street
> **Fuentes:** `FUNDING_MODULE_EXPEDIENTE.md` (v2.0) + `FUNDING_RISK_METRICS_FRAMEWORK.md` (v2.0)
> **Propósito:** Pasar pruebas de fondeo (FTMO, TopStep, MyForexFunds et al.) con suficiencia estadística total, no con suerte.

---

## DIAGNÓSTICO: CONTRASTE ENTRE DOCUMENTOS BASE

### Lo que tiene el sistema (EXPEDIENTE v2.0)
El expediente técnico cataloga **27 motores completamente implementados** distribuidos en 6 capas: 4 motores técnicos de microestructura (VPIN, OFI, CVD, SMC), 8 motores de opciones (GEX, Gamma Flip, DEX, Max Pain, 0DTE, Shadow Delta, Squeeze, DWF), 7 motores predictivos (Conv-LSTM, QuantumAlpha, Tensor Fusion, Sentiment, CNN Fear/Greed, Cross-Asset, Meta-Learner) y 8 servicios de funding (FTMO Survival, Portfolio Risk, BingX Risk Desk, Scanner Gate, Intraday Outcomes, Simulation, Playbook, GEX Validation). El flujo de señal → decisión está definido, los reason codes son estables y el pipeline es determinístico.

### Lo que falta (RISK METRICS FRAMEWORK v2.0)
El framework de métricas identifica que los motores operan sin una **capa de validación estadística continua** que calcule, en tiempo real: Expectancy por tipo de setup, Profit Factor rolling, Sharpe/Sortino/Calmar, Ulcer Index, VaR/CVaR y Risk of Ruin probabilístico por Monte Carlo. Sin este motor analítico (`PerformanceAnalyticsEngine`), los componentes de decisión como `ConvergenceGate` y `SizingEngine` no tienen evidencia cuantitativa dura que respalde sus thresholds — los están aplicando de forma heurística.

### El gap crítico
```
EXPEDIENTE: Excelente maquinaria de señales + excelentes guardrails de fondeo.
FRAMEWORK:  Excelente marco de validación estadística de edge.
GAP:        Nadie está validando que el edge estadístico sea real ANTES de operar.
```

**La premisa operativa del Roadmap:** pasar la evaluación es un **subproducto matemático** de tener Expectancy > 0.20R, Risk of Ruin < 1% y Calmar > 2.0 — no una meta en sí misma. El sistema ya tiene los motores para generar las señales; este roadmap los une con las métricas que prueban (numéricamente) que esas señales tienen edge real.

---

## ARQUITECTURA DEL MÓDULO COMPLETO — 7 CAPAS

```
┌──────────────────────────────────────────────────────────────────────────┐
│  CAPA 0 — DATA FEEDS                                                     │
│  Market Data (OHLCV, L2, Options Chain, Futures)  ·  News/Sentiment API  │
│  Calidad mínima: dqs ≥ 0.35 · freshness ≤ 24h · source_tier validado    │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────────┐
│  CAPA 1 — SCANNER (Fase A)                                               │
│  300 candidatos → filtro multifactor → candidatos pre-calificados        │
│  Output: trend_score, volume_score, structure, vwap_distance, veto_flags │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────────┐
│  CAPA 2 — MICROESTRUCTURA (Fase B / Layer 2)                             │
│  VPIN · OFI · CVD · SMC                                                  │
│  Output: toxicity, z_ofi, cvd, BOS/CHoCH/OB/FVG, conflict_score         │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────────┐
│  CAPA 3 — OPCIONES + PREDICTIVOS (Fase C / Layer 3)                      │
│  GEX · GFlip-P · DEX · MaxPain · 0DTE · ShadowDelta · Squeeze · DWF     │
│  Conv-LSTM · QuantumAlpha · TensorFusion · Sentiment · CNN-FG · XAsset   │
│  Output: regime, flip_point, bias, conviction, p_squeeze, crisis_index   │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────────┐
│  CAPA 4 — PERFORMANCE ANALYTICS ENGINE [NUEVO — PRIORIDAD 1]             │
│  Expectancy por setup · PF rolling · Sharpe/Sortino/Calmar               │
│  Ulcer Index · VaR/CVaR · Risk of Ruin (MC) · Kelly · Live Calmar        │
│  Output: RiskMetricsSnapshot (feed a Capas 5 y 6)                        │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────────┐
│  CAPA 5 — FUNDING DECISION LAYER (Layer 5)                               │
│  Scanner Funding Gate · FTMO Survival Score · Portfolio Risk (4-Tier)    │
│  FTMO GEX Validation · FTMO Playbook · FTMO Simulation                   │
│  Output: decision ∈ {READY, BLOCK, REDUCE} + size_multiplier             │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────────┐
│  CAPA 6 — RISK DESK PRE-TRADE (BingX Risk Desk)                          │
│  8 guardrails · Idempotency SHA-256 · Audit append-only                  │
│  Output: authorized ∈ {true, false} + reason_codes                       │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
                          EXCHANGE
```

---

## PARTE I — MOTORES CUANTITATIVOS (INVENTARIO COMPLETO)

### 1.1 Motores Técnicos de Microestructura

#### VPIN — Toxicidad de Flujo
Mide la probabilidad de informed trading usando desbalance de volumen en buckets de volumen constante. Alta toxicidad (`VPIN > 0.70`) precede movimientos informados — las cuentas de fondeo deben reducir tamaño cuando el flujo deja de ser retail.

**Fórmula operativa:**
```
VPIN = (1/N) × Σ |Bᵢ - Sᵢ| / (Bᵢ + Sᵢ)   sobre N buckets recientes
```
**Integración con funding:** VPIN > 0.85 sostenido → `historical_oos_quality` decae → reduce size. VPIN > 0.70 → data_quality_score decae.

#### OFI — Presión Compradora/Vendedora
Desequilibrio de flujo a nivel de book (adaptación OHLCV cuando no hay L2 completo). Modelo Cont-Kukanov-Stoikov normalizado como z-score con `tanh` de salida en `[-1, +1]`.

**Fórmula:**
```
OFI_t = sign(Cₜ - Oₜ) × Vₜ × (Cₜ - Oₜ) / (Hₜ - Lₜ + ε)
z_OFI(t) = (OFI_t - μ₃₀) / (σ₃₀ + ε)
```
**Integración:** z_OFI > +1.5 → bloquea SHORT; z_OFI < -1.5 → bloquea LONG. Divergencia OFI vs precio → conflict_score elevado.

#### CVD — Presión Acumulada
Acumulación de delta firmado desde el inicio de la sesión RTH. Funciona como oscilador de momentum institucional. Divergencia CVD vs precio (suba de precio con CVD cayendo) = distribución institucional = weak_edge.

#### SMC — Estructura Institucional
Detector vectorizado de BOS, CHoCH, Order Blocks, FVG y Liquidity Sweeps. CHoCH confirmado → marca cambio de régimen → reduce size hasta re-evaluación. Order Block respetado → permite full size.

### 1.2 Motores de Opciones

#### GEX — Régimen de Volatilidad
Calcula la exposición gamma neta de market makers (sum BS-Gamma × OI × sign × contract_size). Determina el régimen:

| Régimen | Comportamiento MM | Efecto en Volatilidad | Acción Funding |
|---------|-------------------|----------------------|----------------|
| `GAMMA_POSITIVE` | Compra dips / vende rallies | Suprime volatilidad | Neutral — no ajusta size |
| `GAMMA_NEGATIVE` | Vende dips / compra rallies | Amplifica volatilidad | `_signal_penalty × 0.5` |
| `AT_FLIP` | Errático — cambio inminente | Bifurcación | `SHOCK` → `_signal_penalty × 0.5` |

El **Zero Gamma Level** (ZGL) se determina con `scipy.optimize.brentq` sobre la curva `Γ_net(p)`.

**Shock test estructural:** si OI de puts aumenta 10% y el flip point se desplaza > 1%, el mercado es estructuralmente frágil → reducir tamaño inmediatamente.

#### Gamma Flip Probability — First Passage Time
Estima `P(spot toque ZGL antes del vencimiento)` usando la solución cerrada del GBM. Si `P_flip > 0.65` → tamaño micro (0.25x); si `P_flip > 0.85` → hard block. Esta métrica es el predictor preventivo por excelencia: reduce tamaño ANTES del flip, no después.

**Fórmula (solución del heat equation):**
```
P(τ_L ≤ T) = Φ((-a + μT) / σ√T) + e^(2μa/σ²) × Φ((-a - μT) / σ√T)
donde a = ln(L/S₀), μ = r - 0.5σ²
```

#### DEX — Delta Exposure + Gamma Trap
Mide delta neto de MM y detecta trampas de retroalimentación. **Gamma Trap Score:**
```
trap_score = |Δ_net(p)| / (|Γ_net(p)| + ε)  ×  𝟙[|p - p_flip| < 0.02×p]
```
Si `trap_score > 3.0` → reduce 50% del tamaño. Delta direccional opuesto a tesis → hard block.

#### Max Pain — Magnetismo de Vencimiento
El strike `K_mp` donde la suma de dolor de option holders es mínima. Spot dentro del 0.5% de `K_mp` → zona de pinning (supresión de volatilidad). Spot alejándose > 3% de `K_mp` → momentum direccional fuerte. Dirección confirmada hacia `K_mp` → aumentar size.

#### 0DTE Engine — Microdinámicas Intraday
Tres dinámicas especiales cuando `T → 0`: Pinning (spot se pega al strike de mayor OI), Cascade/Gamma Storm (gamma explota en ATM), Gamma Vacuum (reducción de cobertura MM acelera momentum). Hard block para entradas 0DTE en los últimos 30 minutos.

#### Shadow Delta, Squeeze, Delta-Weighted Flow
- **Shadow Delta:** divergencia entre `Δ_BS` y `Δ_real` — si `shadow_score > 0.7`, la vol surface es corrupta → hard block.
- **Squeeze:** Bollinger < Keltner → compresión → `P_squeeze`. Si `P_squeeze ≥ 0.75` AND release confirmado → oportunidad con size aumentado.
- **DWF:** flujo institucional ponderado por delta. `DWF_norm > +0.3` → conviction institucional alcista; `< -0.3` → conviction bajista.

### 1.3 Motores Predictivos

#### Conv-LSTM Event-Driven (Multimodal)
Red neuronal con 4 puertas convolucionales + event retention gate. Procesa tensores 3D del outer-product fusion. Output: `bias ∈ {LONG, SHORT, CASH}` + `conviction ∈ [0, 1]`. GEX gating como pre-validación antes de emitir señal.

#### QuantumAlpha LSTM + Self-Attention
Proxy ligero LSTM + self-attention para clasificación OHLCV. Calibration profiles per-ticker (Argentina/crypto usan thresholds más bajos). `signal = WATCH` → conviction < 0.5 → reduce size 25%.

#### Tensor Fusion (Outer-Product)
Fusiona fundamentales × sentiment en tensor bilineal 3D. Captura sinergia multiplicativa: (fundamentales sólidos) × (sentiment positivo) = boost. Concat lineal no captura esto.

#### Sentiment Engine, CNN Fear & Greed, Cross-Asset
- **Sentiment:** `S_composite = 0.5×S_news + 0.3×S_transcripts + 0.2×S_insider`. Score < -0.5 y tesis long → reduce 30%.
- **CNN Fear/Greed:** clasifica régimen de mercado desde VIX, put/call ratio, breadth, momentum, HYG/LQD, TLT, CBOE SKEW. Fear o Greed extremo (> 0.70) → reduce size.
- **Cross-Asset Correlations:** matriz rolling de 60 días. Crisis Index = correlaciones → 1 (diversificación rota). `crisis_index > 0.3` → reduce 50%.

#### Meta-Learner Side Confirmation
Gate determinístico heurístico: `PASS/FAIL` basado en trend_score, volume_score, mean_reversion, estructura técnica. Lógica asimétrica: shorts son más restrictivos que longs. `FAIL` con `overextended` o `vol_uncontrolled` en short → hard block absoluto.

---

## PARTE II — PERFORMANCE ANALYTICS ENGINE (NUEVO — PRIORIDAD 1)

Este es el componente ausente más crítico del sistema. Sin él, los motores de funding operan sin evidencia cuantitativa dura sobre el edge estadístico real. **Debe implementarse antes que cualquier otra mejora.**

### 2.1 Jerarquía de Métricas (de más crítica a menos)

```
NIVEL 4 — SUPERVIVENCIA (manda sobre todo lo demás)
  Risk of Ruin (Monte Carlo) · VaR/CVaR · BUR · Ulcer Index
     ↑ Si falla este nivel, los niveles 1-3 son irrelevantes
NIVEL 3 — PORTAFOLIO
  Sharpe Ratio · Sortino Ratio · Calmar Ratio · Live Calmar
NIVEL 2 — SISTEMA / EDGE
  Expectancy E[R] por setup · Profit Factor · Recovery Factor
NIVEL 1 — OPERACIÓN INDIVIDUAL
  R:R · Win Rate · MAE/MFE
```

### 2.2 Métricas Nivel 1 — Operación Individual

**R:R y Win Rate de Equilibrio:**
```
R:R = |Distancia_TP| / |Distancia_SL|
Win Rate de equilibrio = 1 / (1 + R:R)
```
Umbral mínimo duro para el `ConvergenceGate`: **R:R ≥ 1.5** como condición necesaria (no suficiente) antes de evaluar convergencia. Una señal con convergencia 0.95 pero R:R 0.8 sigue siendo matemáticamente mediocre.

**MAE/MFE para calibración del Time Stop:**
En lugar de fijar "0.5R en X minutos" arbitrariamente, correr análisis de MFE sobre histórico de trades ganadores segmentado por tipo de señal (VPIN, OFI, GEX). El percentil P25 de "tiempo hasta alcanzar 0.5R de MFE" en trades ganadores es el valor empírico de X. Esto convierte el Time Stop en una regla derivada de datos, no de intuición.

### 2.3 Métricas Nivel 2 — Sistema / Edge

**Expectancy E[R] — La Métrica Madre:**
```
E[R] = (WinRate × AvgWinR) − (LossRate × AvgLossR)
```
Estándar profesional institucional: **0.15R–0.35R por operación**. Dado que el umbral de convergencia reduce las oportunidades, el E[R] por trade necesita estar en la parte alta del rango.

**CRÍTICO — Expectancy Estratificada por Setup:** calcular un único E[R] agregado es un error de Simpson. Con motores VPIN, OFI, GEX y SMC generando señales, es perfectamente posible que el agregado sea 0.20R mientras una familia tiene E[R] negativo siendo subsidiada por las otras. El `ConvergenceGate` debe trackear E[R] **por tipo de setup** y poder desactivar individualmente una familia de señales si su E[R] rolling cae por debajo de cero.

**Expectancy Gate:** antes de que una señal pase a ejecución, validar que el E[R] rolling (últimas N señales de ese mismo tipo de setup) supere el umbral de fase:

| Fase | E[R] mínimo por setup | Acción si se incumple |
|------|----------------------|----------------------|
| Warm-Up | > 0.10R | Alerta de revisión |
| Build | > 0.15R | Desactivar ese tipo de señal |
| Funded | > 0.20R | Desactivar + revisión de régimen |

**Profit Factor Rolling (ventana de 20 trades):**
```
PF = Beneficio Bruto Total / |Pérdida Bruta Total|
```
| PF | Interpretación |
|----|---------------|
| < 1.0 | Sistema perdedor |
| 1.0 – 1.3 | Marginal — slippage devora el edge |
| 1.3 – 1.7 | Robusto — estándar institucional |
| 1.7 – 2.5 | Muy bueno |
| > 3.0 | Sospechoso de overfitting |

`ConsistencyRuleManager` debe trackear PF rolling como proxy temprano de cambio de régimen, antes de que la regla del 50% se vea amenazada.

**Recovery Factor:**
```
RF = Beneficio Neto / |Max Drawdown|
```
Benchmark: **RF > 2**. RF bajo con PF alto → el sistema gana poco y pierde mucho en eventos puntuales (perfil típico de Gamma Flip adverso).

### 2.4 Métricas Nivel 3 — Portafolio

**Sharpe Ratio (anualizado):**
```
Sharpe = (Rp − Rf) / σp  ×  √(periodos_por_año)
```
| Sharpe | Benchmarks | Acción si cae |
|--------|-----------|--------------|
| < 1.0 | Subóptimo para fondeo | Reducir Kelly_base |
| 1.0 – 2.0 | Aceptable — hedge funds | Mantener |
| 2.0 – 3.0 | Excelente — top quartile | Proteger sizing |
| > 3.0 | Excepcional o overfitting | Validar OOS |

**Limitación clave:** Sharpe penaliza por igual volatilidad al alza y a la baja. Para estrategias con sesgo positivo (compra de opciones con riesgo definido) subestima la calidad real.

**Sortino Ratio:**
```
Sortino = (Rp − MAR) / σ_downside
donde σ_downside = std(retornos < MAR)
```
**Ratio Sortino/Sharpe como indicador de skew del sistema:**
- `Sortino >> Sharpe` → sesgo positivo (compra de opciones). Bien.
- `Sortino ≈ Sharpe` → retornos simétricos. Normal.
- `Sortino << Sharpe` → sesgo negativo (venta de prima corta sin cobertura). Peligroso.

El `SizingEngine` puede usar esta ratio como "indicador de régimen de skew". Si cae por debajo de 1 sostenidamente → el mix de setups activos se está volviendo de cola negativa → sizing más conservador.

**Calmar Ratio (ventana móvil 36 meses):**
```
Calmar = CAGR / |Max Drawdown|
```
Esta es la métrica más alineada con la estructura de una prop firm, porque literalmente responde: *"¿cuánto retorno generás por cada unidad de drawdown que consumís?"* — la misma pregunta que la firma hace cuando diseña su techo MLL.

**Live Calmar (tiempo real en el `TrailingMLLSimulator`):**
```
Live Calmar = P&L Acumulado / Drawdown Actual desde el Pico
```
Mostrar este número de forma prominente en el dashboard es el indicador de "salud de cuenta" más honesto — más útil que ver el P&L o el Buffer por separado.

| Calmar | Benchmarks | CTAs de referencia |
|--------|-----------|-------------------|
| < 0.5 | Débil | — |
| 0.5 – 1.0 | Aceptable | — |
| 1.0 – 2.0 | Bueno | Tendencia media |
| 2.0 – 3.0+ | Excelente | Winton, AHL histórico |

### 2.5 Métricas Nivel 4 — Supervivencia

**Buffer Utilization Ratio (BUR) — Reemplaza la noción difusa de Buffer/MLL:**
```
BUR = Drawdown Actual desde el Pico / MLL
```
| Zona | BUR | Acción del SizingEngine |
|------|-----|------------------------|
| Verde | < 30% | Sizing normal |
| Amarillo | 30% – 60% | F_dd estructural = 0.7 |
| Rojo | ≥ 60% | F_dd estructural = 0.4 + posible pausa |

**Ulcer Index — Profundidad Y Duración de Drawdowns:**
```
UI = √((1/n) × Σ DDᵢ²)     DDᵢ = % drawdown desde pico móvil en punto i
UPI (Martin Ratio) = (CAGR − Rf) / UI
```
A diferencia del MDD (que solo mira el peor punto), el UI captura la cantidad de malestar psicológico producido por el camino, no solo el destino. Crucial: un drawdown moderado pero prolongado (UI alto, MDD moderado) es el que históricamente rompe la disciplina del operador.

`PreMarketCheck` debe incorporar UI rolling (50 trades) como override automático de sizing, complementando el chequeo biométrico. El sistema cuantitativo protege al operador de su propio optimismo.

**VaR y CVaR / Expected Shortfall:**
```
VaR 95% = percentil 5 de la distribución histórica de retornos diarios
CVaR 95% = pérdida promedio CONDICIONADA a estar en ese 5% peor
```
**Por qué CVaR > VaR en este sistema:** el VaR no dice nada sobre qué tan mal puede ser el 5% restante. En un Gamma Flip adverso (riesgo de viernes 0DTE), la pérdida puede ser un múltiplo del VaR. El CVaR sí captura esto.

**Fórmula operativa para el `DailyBudgetGuard`:**
```
DLL_efectivo = min(DLL_prop_firm, CVaR_99% × 1.67)
```
El límite diario operativo debe ser tal que el CVaR del 99% (pérdida promedio en el 1% de peores días) no exceda el 60% del DLL real — dejando 40% de margen para eventos desconocidos desconocidos (gaps, halts, fallas de ejecución).

**Risk of Ruin para Fondeo (Monte Carlo):**
```
RoR_funding = % de trayectorias simuladas que tocan el MLL
              ANTES de alcanzar el objetivo de profit
```
Esta es posiblemente la **métrica de go/no-go más importante del sistema** — más que el Profit Factor del backtest, porque responde directamente: *"¿cuál es la probabilidad de que esta configuración específica sobreviva la evaluación?"*

Redefinición crítica para fondeo: **ruina = tocar el MLL** (terminación de cuenta), NO perder el 100% del capital.

### 2.6 Contrato de Datos del PerformanceAnalyticsEngine

```typescript
// TypeScript — para dashboard React/Next.js
export interface RiskMetricsSnapshot {
  timestamp: string;
  windowSize: number;

  // Nivel 2 — un objeto por tipo de setup + uno agregado
  expectancyR:    number;    // E[R] ≥ 0.20R en funded
  profitFactor:   number;    // PF ≥ 1.6 en funded
  recoveryFactor: number;    // RF > 2.0

  // Nivel 3 — Portafolio
  sharpeRatio:    number;    // ≥ 1.5 en funded (anualizado)
  sortinoRatio:   number;    // ≥ 2.0 en funded (anualizado)
  calmarRatio:    number;    // ≥ 2.0 en funded (Live Calmar)

  // Nivel 4 — Drawdown / Cola
  bufferUtilizationRatio: number;  // BUR: 0.0 – 1.0+
  ulcerIndex:             number;  // < 2.0 en funded
  var95Daily:             number;  // en % del equity
  cvar95Daily:            number;  // en % del equity
  cvar99Daily:            number;  // debe ser < 60% del DLL
  riskOfRuinPct:          number;  // < 0.1% en funded (MC)

  // Sizing
  kellyFractionFull:    number;  // Kelly completo calculado
  kellyFractionApplied: number;  // después de F_vol, F_dd, F_signal

  // Por setup (arrays paralelos)
  setupTypes:         string[];  // ["VPIN", "OFI", "GEX", "SMC"]
  expectancyBySetup:  number[];  // E[R] individual por familia de señal
  profitFactorBySetup: number[];
}
```

---

## PARTE III — SIZING ENGINE CUANTITATIVO MULTI-FACTOR

### 3.1 Kelly Fraccionado — Base de Todo el Sizing

```
f* = p − (1−p)/b = (b×p − q) / b     donde b = R:R, p = WinRate, q = 1−p
```

Con sistema de referencia (p=0.40, b=2): `f* = 0.40 − 0.30 = 0.10` (10% del capital por trade en Kelly completo).

**El Kelly completo maximiza el crecimiento geométrico pero con drawdowns del 50%+ estadísticamente normales.** Rango operativo recomendado: **10%–25% del Kelly completo** (Quarter-Kelly sweet spot: 75% del crecimiento, 25% de la volatilidad).

| Fracción Kelly | Reducción de volatilidad | Reducción de tasa de crecimiento |
|---------------|------------------------|--------------------------------|
| 100% (completo) | — | — |
| 50% (Half-Kelly) | ≈ 75% | ≈ 25% |
| 25% (Quarter-Kelly) | ≈ 94% | ≈ 44% |

### 3.2 Modelo Multi-Factor Completo

```
Tamaño = Equity × Kelly_base × F_vol × F_dd × F_signal × F_regime × F_conviction
```

**`Kelly_base`:** 10%–25% del Kelly completo, recalculado periódicamente (rolling WinRate y R:R por tipo de setup).

**`F_vol` — Factor de Volatilidad (ATR-based, estándar institucional):**
```
F_vol = ATR_objetivo / ATR_actual    (clampeado entre 0.5 y 1.5)
```
Normaliza el riesgo en dólares entre regímenes de volatilidad. Si el mercado está 3× más volátil de lo normal, la posición se reduce a un tercio.

**`F_dd` — Factor de Drawdown (dos capas):**

| Capa | Condición | Factor |
|------|-----------|--------|
| Estructural (BUR) | BUR < 30% | 1.0 |
| Estructural (BUR) | 30% ≤ BUR < 60% | 0.7 |
| Estructural (BUR) | BUR ≥ 60% | 0.4 |
| Táctico (racha) | Trade anterior ganador | 1.0 |
| Táctico (racha) | Trade anterior perdedor | 0.7 |
| Táctico (racha) | 2 pérdidas consecutivas | 0.5 |

La capa estructural (BUR) reacciona lento pero refleja salud global. La táctica reacciona rápido a rachas recientes. Se multiplican entre sí: `F_dd = F_dd_estructural × F_dd_táctico`.

**`F_signal` — Factor de Confianza de Señal:**
Mapeo lineal del score de convergencia (rango 0.82–1.00) a multiplicador `[0.70, 1.00]`. Una señal con convergencia 0.95 opera con tamaño mayor que una en 0.83. El sizing refleja la confianza, no solo si pasó o no el filtro binario.

**`F_regime` — Factor de Régimen GEX:**
```
GAMMA_POSITIVE → F_regime = 1.0
GAMMA_NEGATIVE → F_regime = 0.5  (volatilidad amplificada)
AT_FLIP        → F_regime = 0.25 (régimen de shock)
```

**`F_conviction` — Factor de Convicción Predictiva:**
```
conviction ≥ 0.7 → F_conviction = 1.0  (full size)
conviction ∈ [0.5, 0.7) → F_conviction = 0.7
conviction < 0.5 → F_conviction = 0.4  (signal débil)
```

**Formula completa integrada con Floor FTMO:**
```python
position_notional = equity × (allowed_risk_pct / stop_distance_pct)
allowed_risk_pct  = min(
    kelly_base × F_vol × F_dd × F_signal × F_regime × F_conviction,
    survival_recommended_risk_pct,   # del FTMO Survival Score
    remaining_daily_risk_pct,        # del FTMO Playbook
    remaining_max_risk_pct,          # del FTMO Playbook
    FTMO_BASE_RISK_PER_TRADE_PCT     # 0.50% hardcap
)
```

---

## PARTE IV — MOTOR DE DECISIÓN DE FONDEO (LAYER 5)

### 4.1 FTMO Survival Score — Funnel Determinístico

Score de supervivencia `[0, 100]` que agrega 5 dimensiones de runway:

```
Survival = 0.30×D + 0.25×M + 0.20×C + 0.15×H + 0.10×(1 − K_max)
donde:
  D = 1 − daily_loss_usage/100
  M = 1 − max_loss_usage/100
  C = runway_score de consistencia ∈ {0, 0.3, 1.0}
  H = historical_oos_quality ∈ [0, 1]
  K_max = max(conflict_scores)
```

**Jerarquía de estados (de más severo a menos):**
```
INSUFFICIENT (4) → WOULD_BREACH (3) → AT_RISK (2) → MONITOR (1) → SAFE (0)
```

**Recommended risk por estado:**
```
WOULD_BREACH / INSUFFICIENT → 0.0% (no operar)
AT_RISK                     → 0.10%
MONITOR                     → 0.25%
SAFE (score ≥ 70)           → 0.50% (base FTMO)
```

**Pesos adaptativos por régimen de mercado:**

| Régimen VIX | Survival | Conviction | Backtest OOS | Comentario |
|-------------|----------|------------|--------------|------------|
| Low VIX (< 15) | 0.40 | 0.25 | 0.35 | Backtest pesa más; mercado estable |
| Normal (15–30) | 0.30 | 0.15 | 0.15 | Pesos balanceados |
| High VIX (> 30) | 0.50 | 0.10 | 0.10 | Supervivencia pesa más; caos |

### 4.2 Portfolio Risk Service — Escalera de 4 Tiers

```
TIER 4 — BLOCK (0.0x)
  Triggers (cualquiera basta):
  • funding_suitability = block
  • survival.status ∈ {WOULD_BREACH, INSUFFICIENT}
  • daily_loss_usage ≥ 80%
  • overfit_module detectado
  • stop is None o stop_pct > remaining_daily_risk_pct
  • GEX data_quality < 0.35 (hard gate)
  • critical_module (GEX/técnico) suitability = block

TIER 3 — MICRO (0.25x)
  • consistency.status = warning
  • daily_loss_usage ≥ 60%
  • weakest_link_module.data_quality < 0.25
  • P_gamma_flip > 0.65
  • crisis_index > 0.3 (cross-asset)

TIER 2 — REDUCED (0.50x)
  • funding_suitability = size_down
  • conflict_score ≥ 0.5
  • tail_risk ≥ 0.7
  • scanner_recommended < 0.75
  • shadow_score > 0.4 (delta surface corrupta)
  • VIX Fear/Greed extremo > 0.7

TIER 1 — NORMAL (1.0x)
  • Sin triggers de tiers superiores
  • Todos los factores en zona verde
```

### 4.3 Scanner Funding Gate — Primera Línea de Filtrado por Símbolo

**Hard blockers (bloqueo inmediato):**
- `module_backtest_grade = overfit_risk` → suitability = BLOCK
- `funding_survival_grade = would_breach` → suitability = BLOCK
- `daily_loss_usage_pct ≥ 80%` → BLOCK
- `stop_pct > remaining_risk_pct` → BLOCK

**Reductores acumulativos:**
- `weak_edge_backtest` → multiplier × 0.5
- `light_proxy` source tier → × 0.5
- `snapshot_chain` source tier → × 0.75
- `data_quality < 0.35` → × 0.5
- `conflict_score ≥ 0.5` → × 0.5
- `consistency_ratio ≥ 0.35` → × 0.5
- `l2_quality < 0.4` → × 0.5

Los reductores son multiplicativos, no aditivos. Con tres reductores activos simultáneamente: `1.0 × 0.5 × 0.5 × 0.5 = 0.125x` — prácticamente bloqueado.

### 4.4 BingX Risk Desk — 8+2 Guardrails Pre-Trade

La puerta final pre-exchange. Evalúa cada `OrderIntent` en orden de severidad:

| Gate | Condición | Reason Code |
|------|-----------|-------------|
| 1. Kill switch | `kill_switch_engaged = True` | `risk_kill_switch_active` |
| 2. Daily loss cap | `PnL_today ≤ −max_daily_loss` | `risk_daily_loss_exceeded` |
| 3. Total notional cap | `Σnotional + nuevo > máx` | `risk_position_cap_exceeded` |
| 4. Max open positions | `open_count ≥ max_open` | `risk_max_open_positions` |
| 5. Per-symbol exposure | `symbol_exposure + nuevo > máx` | `risk_symbol_exposure_exceeded` |
| 6. Cooldown tras pérdida | `elapsed < cooldown_minutes` | `risk_cooldown_active` |
| 7. Spread guard | `spread_pct > max_spread` | `risk_spread_too_wide` |
| 8. L2 quality floor | `l2_quality < min_l2_quality` | `risk_l2_quality_too_low` |
| 9. Zone validation | Acumulación + SHORT / Distribución + LONG | `risk_zone_veto_*` |
| 10. Margin firewall | Exposición > 15% del margin disponible | `risk_zone_*_full` |

**Idempotency SHA-256:** `key = SHA256(cycle_id:symbol:side:position_side)[:24]` — evita duplicados por reconexión WS.

### 4.5 FTMO Playbook — Orquestación Intent + Audit Hash-Chain

Cada `OrderIntent` pasa por 5 validaciones en secuencia:

1. **Monitor check:** `source_ready` y `production_ready`
2. **Day status:** no `LOCKED` por breach o alta utilización
3. **Signal check:** `trade_ready`, `survival.status = SAFE`, `survival.score ≥ 70`
4. **GEX validation:** snapshot fresco (≤ 24h), calidad ≥ 0.65, source_tier = `full_chain_gex`
5. **Stop distance & sizing:** stop válido, allowed_risk calculado, position_size final

Audit chain con hash encadenado (cada evento referencia el hash del anterior) — permite reconstruir exactamente la historia de decisiones. Reconciliación automática: si se ejecuta un intent bloqueado → `executed_blocked_intent` (CRITICAL warning).

---

## PARTE V — SEÑALES Y ALERTAS PROFESIONALES

### 5.1 Sistema de Alertas por Prioridad

#### Nivel CRÍTICO (trigger inmediato, bloquear operaciones)
```
CRIT-001: survival.status = WOULD_BREACH
CRIT-002: BUR ≥ 60% (Zona Roja)
CRIT-003: risk_of_ruin_funding > 1% (Monte Carlo)
CRIT-004: CVaR_99% > 60% del DLL
CRIT-005: P_gamma_flip > 0.85
CRIT-006: shadow_score > 0.7 (vol surface corrupta)
CRIT-007: sim_audit_chain_broken
CRIT-008: kill_switch_engaged
```

#### Nivel WARNING (reducir sizing, aumentar monitoreo)
```
WARN-001: E[R] rolling (por setup) cae por debajo de umbral de fase
WARN-002: PF rolling < 1.3 en ventana 20 trades
WARN-003: Sortino/Sharpe ratio < 1.0 sostenido (skew negativo)
WARN-004: Ulcer Index > 2.5 en funded (UI rolling 50 trades)
WARN-005: BUR entre 30%-60% (Zona Amarilla)
WARN-006: crisis_index > 0.1 (correlaciones anómalas)
WARN-007: VPIN > 0.70 sostenido (flujo informado)
WARN-008: consistency_ratio > 0.35 (riesgo de concentración)
WARN-009: GEX regime = AT_FLIP
WARN-010: CNN Fear/Greed > 0.7 en cualquier dirección
WARN-011: daily_loss_usage ≥ 60%
WARN-012: Live Calmar < 1.0 (funded necesita > 2.0)
```

#### Nivel INFO (monitorear, no acción inmediata)
```
INFO-001: CHoCH confirmado (cambio de régimen estructural)
INFO-002: FVG no rellenado (ineficiencia de mercado)
INFO-003: Squeeze detectado sin release (compresión)
INFO-004: DWF_norm > ±0.5 (flujo institucional extremo)
INFO-005: backtest OOS < 90 días (recalibración sugerida)
INFO-006: source_tier = snapshot_chain (calidad reducida)
```

### 5.2 Reglas de Conflict Resolution (Cascada)

```python
def resolve_conflict(scanner, predictive, gex, survival, metrics):
    # 1. Hard blocks tienen prioridad absoluta
    if survival.status in ["WOULD_BREACH", "INSUFFICIENT"]:
        return BLOCK

    # 2. Conflicto direccional scanner vs predictivo
    if opposite_directions(scanner.direction, predictive.bias):
        conflict_score = 0.7
        size_reduction = 0.5
    else:
        conflict_score = 0.0

    # 3. GEX adverso es independiente de dirección
    if gex.regime in ["GAMMA_NEGATIVE", "AT_FLIP"]:
        size_multiplier *= 0.5

    # 4. Métricas de riesgo cuantitativo
    if metrics.risk_of_ruin > 0.01:     # > 1%
        return BLOCK
    if metrics.bur > 0.60:
        size_multiplier *= 0.4
    if metrics.ulcer_index > 2.5:
        size_multiplier *= 0.7

    # 5. Regla conservadora por defecto
    if survival.score < 50: return BLOCK
    if survival.score < 70: return SIZE_DOWN
    return ALLOW
```

**5 Principios de resolución de conflictos:**
1. Hard rules > Soft rules
2. Live account state > Historical OOS
3. Funding survival > Scanner score
4. Critical module (GEX/técnico) > Non-critical (sentiment)
5. Conservative > Aggressive en empates

### 5.3 Alertas de Pre-Mercado (PreMarketCheck Ampliado)

Antes de cada sesión de trading, validar automáticamente:
- Ulcer Index rolling 50 trades (override automático si excede umbral)
- Risk of Ruin current (Monte Carlo sobre últimas N señales activas)
- BUR zona (verde/amarillo/rojo)
- Régimen GEX del día (fetch snapshot fresco)
- CNN Fear/Greed classifier (régimen de mercado)
- Cross-asset crisis index (correlaciones)
- Resumen de E[R] por tipo de setup (últimas 20 operaciones)

---

## PARTE VI — GESTIÓN DE PORTAFOLIO (WALL STREET STYLE)

### 6.1 Principios de Gestión Profesional de Capital

Los desks institucionales y CTAs top-tier operan con los siguientes principios no negociables que deben estar codificados en el sistema:

**Diversificación de Edge por Setup:** nunca más del 60% del sizing total concentrado en un único tipo de señal. Si VPIN genera 80% de las operaciones ganadoras, el sistema es frágil a cambios en ese microestructural específico.

**Gestión de Correlación Intraportafolio:** antes de abrir una nueva posición, calcular la correlación de los P&L de la posición propuesta con las posiciones abiertas existentes (Cross-Asset Engine). Si `ρ > 0.7` con una posición existente → reducir tamaño 50%.

**Sequencing Rules (Rules de Secuencia):**
- Después de alcanzar el 50% del profit target diario → reducir sizing a 50% del normal (proteger el día).
- Después de alcanzar el 75% del profit target diario → activar modo "capital protection" (solo trades con conviction ≥ 0.85).
- Nunca operar contra la tendencia del VIX durante los primeros 30 minutos de sesión.

**Regla de Consistencia Profesional (más allá del 50% FTMO):** ningún día individual debería superar el 35% del profit total acumulado en la evaluación. El sistema debe trackear `best_day_contribution_pct` y reducir tamaño proactivamente cuando se acerque al límite.

### 6.2 Challenge Simulation Multi-Firma

El sistema simula simultáneamente la misma cuenta contra múltiples presets:

| Preset | Daily Loss | Max Loss | Profit Target | Drawdown Type |
|--------|-----------|----------|---------------|---------------|
| `ftmo_2_step` | 5% | 10% | 10% (challenge) / 5% (verif) | Static |
| `ftmo_1_step` | 3% | 10% | 10% | Trailing EOD |
| `topstep_combine` | 2% ($2K) | $2K | 6% | Trailing intraday |
| `custom` | Configurable | Configurable | — | Configurable |

Esto permite responder: *"¿esta configuración de sizing sobrevive al TopStep que es más restrictivo en DLL?"* antes de comprometer capital.

### 6.3 Multi-Account Orchestrator (Próxima Fase)

Para operar N cuentas de fondeo simultáneas con correlación controlada:
- Diversificación deliberada: cuenta A opera setups VPIN-dominantes, cuenta B opera setups GEX-dominantes.
- Cada cuenta tiene su propio Playbook state y audit trail independiente.
- Cross-account correlation monitor: si dos cuentas están en posiciones correlacionadas (ρ > 0.5), reducir ambas al 70%.
- Paralelo con `httpx.AsyncClient` — latencia optimizada.

### 6.4 Backtest OOS Mensual (Calibration Loop)

Cada 30 días, ejecutar automáticamente:
1. Ventana OOS de 90 días para cada motor (técnico, opciones, predictivo).
2. Recalcular E[R] por setup sobre ventana OOS — si E[R] < umbral → desactivar temporalmente.
3. Recalibrar Meta-Learner: si `PASS rate → profit% < 50%` → bajar thresholds o re-entrenar.
4. Actualizar calibration profiles per-ticker (QuantumAlpha: cambios en volatilidad de activos argentinos o crypto).
5. Re-correr Monte Carlo de Risk of Ruin con distribución actualizada de R-múltiplos.

---

## PARTE VII — DASHBOARD DE ANALYTICS (Glassmorphism / Dark Mode)

### 7.1 Panel Principal — Survival Status

Elemento central del dashboard: **Semáforo de Supervivencia** con 5 estados visuales.

```
┌────────────────────────────────────────────────┐
│  FTMO SURVIVAL STATUS                          │
│  ●  SAFE  ████████████████░░░░  Score: 82/100  │
│                                                 │
│  Daily Loss Buffer    [██████░░░░] 38.5% usado  │
│  Max Loss Buffer      [████░░░░░░] 24.2% usado  │
│  Consistency          [████████░░] 42.3%        │
│  Historical OOS       [████████░░] 0.82 quality │
│  Conflict Pressure    [██░░░░░░░░] 0.12         │
└────────────────────────────────────────────────┘
```

### 7.2 Panel de Métricas Cuantitativas — PerformanceAnalyticsEngine

```
┌─────────────────────────────────────────────────────────────────────────┐
│  RISK METRICS DASHBOARD (Rolling Window: 50 trades)                     │
├──────────────────┬──────────────────┬──────────────────┬────────────────┤
│  E[R] Global     │  E[R] por Setup  │  Profit Factor   │  Recovery F.   │
│  0.24R  ✅       │  VPIN: 0.31R ✅  │  1.72  ✅        │  3.4x  ✅      │
│  (target ≥0.20)  │  OFI:  0.18R ⚠️  │  (target ≥1.6)   │  (target >2.0) │
│                  │  GEX:  0.27R ✅  │                  │                │
│                  │  SMC:  0.14R ⚠️  │                  │                │
├──────────────────┼──────────────────┼──────────────────┼────────────────┤
│  Sharpe          │  Sortino         │  Live Calmar     │  Ulcer Index   │
│  1.86  ✅        │  2.41  ✅        │  2.23  ✅        │  1.73  ✅      │
│  (target ≥1.5)   │  (target ≥2.0)   │  (target ≥2.0)   │  (target <2.0) │
├──────────────────┼──────────────────┼──────────────────┼────────────────┤
│  BUR             │  CVaR 99%        │  Risk of Ruin    │  Kelly Applied │
│  28%  🟢 GREEN   │  0.38% equity ✅ │  0.04%  ✅       │  12.3%  ✅     │
│  (target <60%)   │  (<60% de DLL)   │  (target <0.1%)  │  (25% Kelly)   │
└──────────────────┴──────────────────┴──────────────────┴────────────────┘
```

### 7.3 Panel de Régimen de Mercado

```
┌────────────────────────────────────────────────────────────────────────┐
│  MARKET REGIME MONITOR                                                  │
│  GEX Regime: ██ GAMMA_POSITIVE (supresión de vol)                      │
│  Gamma Flip P: 0.23 — Spot seguro del ZGL (SAFE)                       │
│  CNN Fear/Greed: 0.41 — NEUTRAL                                         │
│  VPIN: 0.42 — Toxicidad normal (< 0.70)                                │
│  Cross-Asset Crisis: 0.07 — Normal (< 0.10)                            │
│  Squeeze Probability: 0.61 — Compresión en progreso ⚠️                  │
│  Max Pain Distance: +1.2% — Fuera de pinning zone                      │
└────────────────────────────────────────────────────────────────────────┘
```

### 7.4 Panel de Sizing Engine

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SIZING ENGINE (próxima señal LONG SPX)                                  │
│  Kelly Full:      12.4%  │  Kelly Base (25%):  3.1%                     │
│  F_vol:           0.87   │  F_dd:  0.70 (BUR amarillo × racha normal)   │
│  F_signal:        0.92   │  F_regime: 1.0 (GAMMA_POSITIVE)              │
│  F_conviction:    0.85   │                                               │
│  ──────────────────────────────────────────────────────────────────     │
│  Kelly Aplicado:  3.1% × 0.87 × 0.70 × 0.92 × 1.0 × 0.85 = 1.48%     │
│  Allowed Risk:    min(1.48%, survival 0.25%, daily_rem 1.2%) = 0.25%    │
│  Position Size:   $25,000 notional  (stop: 1.0%)                        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## PARTE VIII — PROTOCOLO DE VALIDACIÓN MONTE CARLO

### 8.1 Protocolo Pre-Despliegue (Gates de Avance entre Fases)

Antes de pasar Warm-Up → Build → Funded, correr obligatoriamente:

**Insumos requeridos:**
- Historial de trades: mínimo 100 por tipo de setup (idealmente 200+)
- Distribución real de R-múltiplos por setup
- Parámetros exactos del SizingEngine configurados para la fase destino
- MLL/DLL/profit target de la firma objetivo
- Ulcer Index rolling del período de Warm-Up

**Simulación:**
```python
risk_of_ruin_funding(
    n_sims=10_000,          # mínimo para convergencia estadística
    n_trades=100,           # duración esperada de la fase en operaciones
    mll_pct=0.10,           # 10% para FTMO 2-step
    kelly_fraction=0.25,    # quarter-Kelly como base
    setup_type=None,        # None = agregado; especificar por tipo
)
```

**Outputs a revisar:**
1. `RoR_funding` (debe cumplir el umbral de la tabla siguiente)
2. Distribución de "trades hasta alcanzar el profit target" (mediana y P90)
3. Distribución de "trades hasta tocar el MLL" condicionada a trayectorias que sí lo tocan
4. Histograma de drawdown máximo entre trayectorias

**Gate de avance:** si `RoR_funding` no cumple el umbral, el primer lugar para intervenir es `Kelly_base` (reducirlo), no el filtro de convergencia. Reducir el threshold de convergencia para generar más señales empeora el Expectancy por setup y es contraproducente.

### 8.2 Tabla Maestra de Umbrales Operativos por Fase

| Métrica | Warm-Up | Build | Funded | Acción si se incumple |
|---------|---------|-------|--------|----------------------|
| E[R] (por setup) | > 0.10R | > 0.15R | > 0.20R | Desactivar ese tipo de señal |
| Profit Factor (rolling 20) | > 1.3 | > 1.5 | > 1.6 | Alerta + revisión de régimen |
| Recovery Factor | > 1.5 | > 2.0 | > 2.5 | Revisar sizing (kelly demasiado alto) |
| Sharpe (rolling, anualizado) | > 0.8 | > 1.2 | > 1.5 | Reducir Kelly_base |
| Sortino (rolling, anualizado) | > 1.0 | > 1.5 | > 2.0 | Revisar mix de setups (riesgo de cola) |
| Calmar (Live) | > 1.0 | > 1.5 | > 2.0 | F_dd estructural a zona amarilla |
| BUR | < 40% | < 50% | < 60% | Zonas de sizing (sección 3.2) |
| CVaR 99% diario vs DLL | < 70% | < 65% | < 60% | Reducir tamaño global |
| Ulcer Index (rolling 50) | < 3.0 | < 2.5 | < 2.0 | Override de PreMarketCheck |
| **Risk of Ruin (MC)** | **< 5%** | **< 1%** | **< 0.1%** | **No avanzar de fase** |

**Nota crítica:** las métricas de backtest se degradan sistemáticamente en vivo (slippage real, latencia, decisiones discrecionales). Apuntar a que los valores de backtest superen estos umbrales con un margen del 30-50% como mínimo. Los umbrales de la tabla son los pisos en vivo, no los objetivos de diseño.

---

## PARTE IX — ROADMAP DE IMPLEMENTACIÓN

### SPRINT 0 — Fundación Analítica (PRIORIDAD MÁXIMA)

**Objetivo:** Tener el `PerformanceAnalyticsEngine` funcionando sobre el historial de backtest existente. Este único paso ya dará E[R] por setup, PF, Sharpe/Sortino/Calmar y una primera corrida de `RoR_funding` real, que probablemente reordenará las prioridades del resto del roadmap.

**Tareas:**
- Implementar `PerformanceAnalyticsEngine` (Python, sección 2.6) sobre historial de backtest existente
- Exponer contrato de datos `RiskMetricsSnapshot` como API endpoint
- Primera corrida de Risk of Ruin Monte Carlo (`n_sims=10,000`)
- Stratificación de E[R] por tipo de setup (VPIN, OFI, GEX, SMC, compuesto)
- Calcular MAE/MFE por tipo de setup → derivar Time Stop empírico

**Entregable:** Dashboard básico con las 12 métricas clave + semáforo de supervivencia.

**Criterio de avance:** `RoR_funding < 5%` y E[R] > 0.10R en todos los setups activos.

---

### SPRINT 1 — Integración de Métricas en Motores Existentes

**Objetivo:** Conectar el PerformanceAnalyticsEngine con los componentes que ya existen.

**Tareas:**
- `ConvergenceGate`: agregar R:R mínimo duro (≥ 1.5) + Expectancy Gate por tipo de setup
- `SizingEngine`: implementar modelo multi-factor completo (Kelly × F_vol × F_dd × F_signal × F_regime × F_conviction)
- `TrailingMLLSimulator`: agregar BUR con 3 zonas + Live Calmar Ratio
- `DailyBudgetGuard`: integrar CVaR 99% diario, aplicar `DLL_efectivo = min(DLL_firma, CVaR_99% × 1.67)`
- `ConsistencyRuleManager`: trackear PF rolling + Sortino rolling
- `PreMarketCheck`: incorporar UI rolling como override automático

**Entregable:** Sizing multi-factor operativo. BUR como reemplazo del Buffer/MLL simple.

**Criterio de avance:** `Sortino/Sharpe ratio > 1.0` sostenido y `Ulcer Index < 3.0`.

---

### SPRINT 2 — Señales, Alertas y Risk Dashboard

**Objetivo:** Dashboard completo con todos los paneles de la Parte VII y sistema de alertas de la Parte V.

**Tareas:**
- Implementar los 3 niveles de alertas (CRÍTICO, WARNING, INFO) con notificaciones en tiempo real
- Dashboard de Survival Status + métricas cuantitativas en tiempo real
- Panel de Régimen de Mercado (GEX + CNN + VPIN + Cross-Asset integrados visualmente)
- Panel de Sizing Engine con breakdown de factores
- Heatmap de consistencia por día (para visualizar distribución de profits)
- Simulador "What-If": ¿qué pasa con el sizing si cambio el profit target?

**Entregable:** Dashboard operativo full con glassmorphism/dark mode.

**Criterio de avance:** `Live Calmar > 1.5` y `BUR < 50%` durante 10+ días de trading.

---

### SPRINT 3 — Validación Monte Carlo Automatizada

**Objetivo:** Protocolo de validación estadística completamente automatizado antes de cada fase.

**Tareas:**
- Automatizar `risk_of_ruin_funding()` corriendo en background cada 24h
- Gate de avance entre fases: sistema no permite avanzar si RoR_funding no cumple umbral
- Simulation de challenge multi-firma (FTMO + TopStep + custom simultáneamente)
- Backtest OOS mensual automático en `tasks/monthly_retrain.py`
- Stress tests: shock de +10% en Put OI → ¿se desplaza el flip point?
- Tests de integración `tests/integration/test_funding_pipeline.py`

**Entregable:** Pipeline completo Scanner → Microestructura → Opciones → Funding → Risk Desk testeable end-to-end.

**Criterio de avance:** `RoR_funding < 1%` (umbral de Build) con n_sims=10,000.

---

### SPRINT 4 — Calibration Loop + Multi-Account Orchestrator

**Objetivo:** Sistema que aprende de su propio desempeño y puede operar múltiples cuentas.

**Tareas:**
- Calibration Loop del Meta-Learner: si PASS rate → profit < 50% → re-entrenar
- Intraday Outcomes loop: métricas 1h/4h/EOD alimentan recalibración de confianza por módulo
- Multi-Account Orchestrator (paralelo con `httpx.AsyncClient`)
- Diversificación entre cuentas: A usa setups VPIN-dominantes, B usa GEX-dominantes
- Cross-account correlation monitor (ρ > 0.5 → reducir ambas al 70%)
- Funding Audit Trail Compliance: export en JSON/Markdown/PDF

**Entregable:** Sistema que puede gestionar N cuentas de fondeo simultáneas.

**Criterio de avance:** `RoR_funding < 0.1%` (umbral de Funded) + Calmar > 2.0 sostenido.

---

### SPRINT 5 — Hardening y Certificación de Producción

**Objetivo:** Sistema listo para operar capital real con supervisión mínima.

**Tareas:**
- Configurar variables de entorno `QA_FTMO_*` en `.env.example` (PD-1 compliance)
- Tests completos: Unit + Integration + Property-based tests para lógica financiera (PD-6)
- Documentación de ADRs pendientes (SQLite vs PG, recalibración Online vs Batch)
- Kill switch automático: si `survival.status = WOULD_BREACH` durante N operaciones seguidas → desactivar trading automáticamente
- Alert fatigue prevention: agrupar alertas del mismo tipo dentro de ventanas de 15 min

**Entregable:** Sistema certificado, auditado y documentado para capital real.

---

## PARTE X — DECISIONES DE ARQUITECTURA PENDIENTES (ADRs)

| ADR | Opciones | Recomendación | Justificación |
|-----|----------|---------------|---------------|
| Almacenamiento audit chain | SQLite vs PostgreSQL | SQLite local + sync periódico a PG | Resilencia: trading no depende de DB remota |
| Recalibración Meta-Learner | Online vs Batch mensual | Batch mensual | Estabilidad, menos riesgo de overfitting online |
| Multi-cuenta | Secuencial vs Paralelo | Paralelo con httpx.AsyncClient | Latencia — cuentas no se bloquean entre sí |
| Failover Funding Service | No failover vs Hot-standby | No failover | Funding Lab es no-crítico para ejecución de trading |
| PerformanceAnalyticsEngine | Microservicio vs In-process | Microservicio Python junto al motor C++/CUDA | Separación de responsabilidades; el cálculo es intensivo |
| Frecuencia de Monte Carlo | Cada trade vs Diario | Diario (background job) | Balance entre frescura y costo computacional |
| Almacenamiento de trade history | In-memory vs SQLite | SQLite con índice por `setup_type` | Persistencia para análisis OOS + velocidad |

---

## PARTE XI — CONSTANTES FTMO 2-STEP (Referencia Rápida)

```python
# ── Perfil FTMO 2-Step Standard ──────────────────────────────────────────
FTMO_PROFILE_ID                  = "ftmo_2_step_standard"
FTMO_TIMEZONE                    = "Europe/Prague"
FTMO_INITIAL_CAPITAL             = 100_000.0

# Límites de pérdida (hard rules — violación = cuenta terminada)
DAILY_LOSS_LIMIT_PCT             = 5.0     # -$5,000 / día
MAX_LOSS_LIMIT_PCT               = 10.0    # -$10,000 total

# Objetivos de profit
CHALLENGE_PROFIT_TARGET_PCT      = 10.0    # +$10,000 (Fase 1)
VERIFICATION_PROFIT_TARGET_PCT   = 5.0     # +$5,000  (Fase 2)
MIN_TRADING_DAYS                 = 4       # mínimo para calificar

# Reglas de consistencia (soft rules)
CONSISTENCY_WARNING_RATIO        = 0.35    # best day < 35% del profit total
CONSISTENCY_BLOCK_RATIO          = 0.50    # best day < 50% del profit total

# Sizing base
DEFAULT_RISK_PER_TRADE_PCT       = 0.50    # 0.5% por trade (base FTMO)

# ── Umbrales del PerformanceAnalyticsEngine (Funded) ─────────────────────
MIN_EXPECTANCY_FUNDED            = 0.20    # E[R] mínimo por setup
MIN_PROFIT_FACTOR_FUNDED         = 1.6     # PF rolling 20 trades
MIN_SHARPE_FUNDED                = 1.5     # anualizado
MIN_SORTINO_FUNDED               = 2.0     # anualizado
MIN_CALMAR_FUNDED                = 2.0     # Live Calmar
MAX_BUR_FUNDED                   = 0.60    # Buffer Utilization Ratio
MAX_ULCER_INDEX_FUNDED           = 2.0     # rolling 50 trades
MAX_CVAR99_VS_DLL                = 0.60    # CVaR_99% < 60% del DLL
MAX_RISK_OF_RUIN_FUNDED          = 0.001   # 0.1% — Monte Carlo

# ── Thresholds de datos ───────────────────────────────────────────────────
MIN_DATA_QUALITY_SCORE           = 0.35    # hard block debajo de esto
MIN_GEX_QUALITY_FUNDED           = 0.65    # para GEX validation
GEX_FRESHNESS_HOURS              = 24      # snapshot no puede ser más viejo
```

---

## SÍNTESIS EJECUTIVA — LO QUE HACE LA DIFERENCIA

El sistema ya tiene todos los motores de señales necesarios para pasar una evaluación de fondeo. Lo que separa al 5% que pasa del 95% que falla no es la calidad de las señales — es la **disciplina cuantitativa en el sizing y la validación estadística del edge**.

Tres intervenciones de alto impacto que pueden implementarse esta semana:

**1. Expectancy Gate por Setup (2–4 horas de implementación):** calcular E[R] rolling por tipo de señal y bloquear automáticamente cualquier señal cuya familia de setup tenga E[R] < 0.10R en los últimos 20 trades. Este único cambio elimina la "subsidia cruzada" entre setups y puede aumentar el Profit Factor global en 15–30%.

**2. CVaR en el DailyBudgetGuard (2–3 horas):** reemplazar el DLL simple por `DLL_efectivo = min(DLL_firma, CVaR_99% × 1.67)`. En días de alta volatilidad (Gamma Flip potencial, gaps), esto automáticamente reduce el presupuesto diario operativo antes de que se produzca el evento adverso.

**3. Risk of Ruin (Monte Carlo) como Gate de Go/No-Go (4–6 horas):** correr `risk_of_ruin_funding()` con n_sims=10,000 sobre el historial de backtest existente. Si el resultado es > 5%, no avanzar a la siguiente fase — reducir Kelly_base hasta que sea < 1%.

Estas tres acciones, implementadas en orden, transforman el sistema de "excelente generador de señales" a "sistema que puede demostrar matemáticamente que pasará la evaluación antes de arriesgar un dólar".

---

> **Documento generado como síntesis unificada de:**
> `FUNDING_MODULE_EXPEDIENTE.md` (v2.0, Junio 2026) ×
> `FUNDING_RISK_METRICS_FRAMEWORK.md` (v2.0, Junio 2026)
>
> **Próxima revisión sugerida:** después de completar Sprint 1 (métricas integradas en motores existentes) y con primera corrida real de Monte Carlo sobre datos live.
