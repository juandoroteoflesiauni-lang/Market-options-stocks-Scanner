# Deep Funnel Station — Expediente Técnico del Módulo de Funding
## Catálogo Exhaustivo de Motores, Funcionalidad, Lógica y Matemática

> **Versión del documento:** 2.0
> **Fecha:** Junio 2026
> **Clasificación:** Interno — Blueprint Fundacional del Módulo de Funding
> **Complementa a:** `docs/FUNDING_MODULE.md` (visión de alto nivel)
> **Aplica reglas:** `[PD-3]` (Blueprint antes de código) · `[ARCH-001]` (modelos frozen) · `[PD-7]` (idioma español) · `[PD-6]` (tests para lógica financiera)

---

## Índice General

### PARTE I — FUNDAMENTOS ARQUITECTÓNICOS
1. [Misión del Módulo de Funding](#parte-i--fundamentos-arquitectónicos)
2. [Mapa Relacional de los Motores](#2-mapa-relacional-de-los-motores)
3. [Convenciones Matemáticas y de Implementación](#3-convenciones-matemáticas-y-de-implementación)

### PARTE II — MOTORES TÉCNICOS (Phase B / Layer 2)
4. [VPIN — Volume-Synchronized Probability of Informed Trading](#4-vpin--volume-synchronized-probability-of-informed-trading)
5. [OFI — Order Flow Imbalance (Modelo Cont-Kukanov-Stoikov)](#5-ofi--order-flow-imbalance-modelo-cont-kukanov-stoikov)
6. [CVD — Cumulative Volume Delta](#6-cvd--cumulative-volume-delta)
7. [SMC — Smart Money Concepts (BOS, CHoCH, Order Blocks, FVG)](#7-smc--smart-money-concepts-bos-choch-order-blocks-fvg)

### PARTE III — MOTORES DE OPCIONES (Phase C / Layer 3)
8. [GEX — Gamma Exposure + Volatility Regime](#8-gex--gamma-exposure--volatility-regime)
9. [Gamma Flip Probability (First Passage Time sobre GBM)](#9-gamma-flip-probability-first-passage-time-sobre-gbm)
10. [DEX — Delta Exposure + Gamma Trap](#10-dex--delta-exposure--gamma-trap)
11. [Max Pain — Magnetismo de Vencimiento](#11-max-pain--magnetismo-de-vencimiento)
12. [Zero-Day (0DTE) Engine — Pinning, Cascades, Gamma Vacuum](#12-zero-day-0dte-engine--pinning-cascades-gamma-vacuum)
13. [Shadow Delta Engine — Gap entre Delta Nominal y Real](#13-shadow-delta-engine--gap-entre-delta-nominal-y-real)
14. [Squeeze Ignition Detection](#14-squeeze-ignition-detection)
15. [Delta-Weighted Flow](#15-delta-weighted-flow)

### PARTE IV — MOTORES PREDICTIVOS (Phase C / Layer 3)
16. [Multimodal Predictive Engine — Conv-LSTM Event-Driven](#16-multimodal-predictive-engine--conv-lstm-event-driven)
17. [QuantumAlpha LSTM + Self-Attention](#17-quantumalpha-lstm--self-attention)
18. [Outer-Product Tensor Fusion](#18-outer-product-tensor-fusion)
19. [Sentiment Engine + Catalyst NLP](#19-sentiment-engine--catalyst-nlp)
20. [CNN Fear & Greed Classifier](#20-cnn-fear--greed-classifier)
21. [Cross-Asset Correlation Engine](#21-cross-asset-correlation-engine)
22. [Funding Lab Side Meta-Learner (Heurística)](#22-funding-lab-side-meta-learner-heurística)

### PARTE V — MOTORES DE FUNDING / RIESGO (Layer 5)
23. [FTMO Survival Score — Funnel de Decisión Determinístico](#23-ftmo-survival-score--funnel-de-decisión-determinístico)
24. [Portfolio Risk Service — 4-Tier Ladder + Kelly Fraccional](#24-portfolio-risk-service--4-tier-ladder--kelly-fraccional)
25. [BingX Risk Desk — 8 Guardrails + Idempotency + Audit](#25-bingx-risk-desk--8-guardrails--idempotency--audit)
26. [Scanner Funding Gate — Suitability + Reason Codes Estables](#26-scanner-funding-gate--suitability--reason-codes-estables)
27. [Intraday Outcomes (Funding Lab)](#27-intraday-outcomes-funding-lab)
28. [FTMO Simulation Service — Backtest Determinístico](#28-ftmo-simulation-service--backtest-determinístico)
29. [FTMO Playbook Service — Estado Manual + Audit Hash-Chain](#29-ftmo-playbook-service--estado-manual--audit-hash-chain)
30. [FTMO GEX Validation — Data Lineage Check](#30-ftmo-gex-validation--data-lineage-check)

### PARTE VI — SÍNTESIS Y MATRIZ DE DECISIÓN
31. [Matriz de Composición: Cómo los Motores Alimentan el Funding](#31-matriz-de-composición-cómo-los-motores-alimentan-el-funding)
32. [Pesos Recomendados y Conflict Resolution](#32-pesos-recomendados-y-conflict-resolution)
33. [Hoja de Ruta de Integración](#33-hoja-de-ruta-de-integración)

---

# PARTE I — FUNDAMENTOS ARQUITECTÓNICOS

## 1. Misión del Módulo de Funding

El **Módulo de Funding** es la capa que toma las señales de los 4 motores cuantitativos existentes en el sistema (Scanner, Microestructura, Opciones, Predictivo) y las traduce en **decisiones de capital** que satisfacen simultáneamente:

| Restricción | Tipo | Origen |
|-------------|------|--------|
| No violar límites diarios de pérdida (FTMO: 5%) | Hard rule | `FTMO_DAILY_LOSS_LIMIT_PCT` |
| No violar drawdown máximo total (FTMO: 10%) | Hard rule | `FTMO_MAX_LOSS_LIMIT_PCT` |
| No concentrar profit en un solo día (Consistency ≤ 50%) | Soft rule | `FTMO_CONSISTENCY_BLOCK` |
| Mantener supervivencia histórica (backtest OOS) | Statistical | `_historical_metrics` |
| Coherencia con predictivo (bias + convicción) | Conviction | `MultimodalPredictiveEngine` |
| Calidad de datos mínima (`dqs ≥ 0.35`) | Quality gate | `L2_QUALITY_SIZE_DOWN_THRESHOLD` |

**Definición formal de "supervivencia" del funding:**

Sea:
- $C_0$ = capital inicial
- $C_t$ = equity en el tiempo $t$
- $\mathrm{dd}_{\max} = \max_{s \le t}(C_s - C_t)$ = drawdown máximo realizado
- $\mathrm{dd}_{t}^{day} = \max(0, \mathrm{SoD}_t - C_t)$ = drawdown desde inicio de día

El sistema está **vivo** en $t$ si y solo si:

$$
C_t \ge C_0 - L_{\max} \quad \wedge \quad \mathrm{dd}_{t}^{day} \le L_{day}
$$

donde $L_{\max} = 0.10 \cdot C_0$ y $L_{day} = 0.05 \cdot C_0$ en el perfil FTMO 2-Step. Cualquier violación es **terminal** (cuenta quemada).

## 2. Mapa Relacional de los Motores

```
                            ┌──────────────────────────────────────────┐
                            │     MOTORES DE ENTRADA (FASE A-C)        │
                            │  Scanner · Microestructura · Opciones ·  │
                            │  Predictivo · GEX · L2                  │
                            └────────────────┬─────────────────────────┘
                                             │
                  ┌──────────────────────────┼──────────────────────────┐
                  │                          │                          │
                  ▼                          ▼                          ▼
       ┌──────────────────┐      ┌──────────────────┐       ┌──────────────────┐
       │ SCANNER FUNDING  │      │  FTMO SURVIVAL   │       │  FTMO PLAYBOOK   │
       │      GATE        │      │      SCORE       │       │     SERVICE      │
       │ (per-symbol)     │      │  (cross-symbol)  │       │  (intent-level)  │
       └────────┬─────────┘      └────────┬─────────┘       └────────┬─────────┘
                │                         │                          │
                │  suitability ∈          │  status ∈                │  decision ∈
                │  {allow,size_down,      │  {SAFE,MONITOR,          │  {PLAYBOOK_READY,
                │   block,informational}  │   AT_RISK,WOULD_BREACH}  │   PLAYBOOK_BLOCK,
                │                         │                          │   PLAYBOOK_REDUCE}
                ▼                         ▼                          ▼
       ┌──────────────────────────────────────────────────────────────────────┐
       │              PORTFOLIO RISK SERVICE (Layer 5)                         │
       │  • 4-Tier Ladder  • Kelly Fraccional  • VaR Histórico                │
       │  • Stress Tests  • Challenge Simulation  • Action Plan               │
       └──────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
       ┌──────────────────────────────────────────────────────────────────────┐
       │                  BINGX RISK DESK (Pre-trade)                          │
       │  8 guardrails  •  Idempotency SHA-256  •  Audit append-only          │
       │  •  Zone Validation  •  Margin Firewall  •  Precision Rounding       │
       └──────────────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
                                    ORDER → EXCHANGE
```

## 3. Convenciones Matemáticas y de Implementación

- **Precisión:** Todo precio/quantity usa `Decimal` en el dominio y `string` en la frontera (PD-2). Internamente los motores quant usan `numpy.float64`.
- **Inmutabilidad:** Los outputs de motor son `Pydantic frozen=True` o `@dataclass(frozen=True)`.
- **Trazabilidad:** Cada score lleva `data_lineage` con `source_tier` ∈ {`full_chain_gex`, `snapshot_chain`, `light_proxy`}.
- **Determinismo:** Todos los engines de funding son **puros** (sin estado, sin I/O) para ser testeables y reproducibles.
- **Reason codes:** Strings estables consumidos por UI y Risk Desk. No renombrar sin coordinar.
- **Tolerancias numéricas:** `xtol=1e-6` en búsquedas de raíces, `1e-9` como épsilon de seguridad en divisiones.

---

# PARTE II — MOTORES TÉCNICOS (Phase B / Layer 2)

## 4. VPIN — Volume-Synchronized Probability of Informed Trading

### 4.1 Funcionalidad

Mide la **toxicidad del flujo** (probabilidad de que los traders informados estén activos) usando el desbalance entre volumen de compra y volumen de venta agregado en *buckets de volumen constante* (no de tiempo). Es un proxy de informed trading asimilable a PIN (Probabilidad de Informed Trading) sin requerir modelado de llegada de órdenes.

### 4.2 Lógica

El algoritmo particiona la cinta de trades en buckets cuyo tamaño se determina por el volumen total promedio de los N buckets anteriores. Dentro de cada bucket se acumula:

$$
B_i = \sum_{j=1}^{n_i} \mathbb{1}_{\text{buy}}(\text{trade}_j) \cdot V_j
$$
$$
S_i = \sum_{j=1}^{n_i} \mathbb{1}_{\text{sell}}(\text{trade}_j) \cdot V_j
$$

donde $\mathbb{1}_{\text{buy}}$ se infiere con el **Lee-Ready algorithm** o con `price > mid` y `price < mid`. La toxicidad del bucket es:

$$
\tau_i = \frac{|B_i - S_i|}{B_i + S_i}
$$

El VPIN es la media móvil de la toxicidad sobre los últimos $N$ buckets:

$$
\mathrm{VPIN} = \frac{1}{N} \sum_{i=t-N+1}^{t} \tau_i
$$

### 4.3 Matemática (implementación real)

```python
def compute_vpin_from_signed_volume(
    buy_volumes: list[float],     # [B_1, B_2, ...]
    sell_volumes: list[float],    # [S_1, S_2, ...]
    *,
    bucket_target: float | None = None,
) -> dict:
    # 1. Volumen total y target de bucket
    total_buy  = sum(buy_volumes)
    total_sell = sum(sell_volumes)
    total      = total_buy + total_sell
    if total <= 0:
        return {"vpin": None, ...}

    # 2. Imbalance global (proxy si no hay buckets completos)
    imb = (total_buy - total_sell) / total

    # 3. Tamaño de bucket por defecto: V / N_buckets
    if bucket_target is None or bucket_target <= 0:
        bucket_target = total / max(len(buy_volumes), 1)

    # 4. Acumulación por bucket
    toxicity = []
    b_acc = s_acc = v_acc = 0.0
    for b, s in zip(buy_volumes, sell_volumes):
        b_acc += b
        s_acc += s
        v_acc += b + s
        if v_acc >= bucket_target:
            toxicity.append(abs(b_acc - s_acc) / max(v_acc, 1e-9))
            b_acc = s_acc = v_acc = 0.0

    # 5. VPIN = mean(toxicity), clipped a [0, 1]
    vpin = float(sum(toxicity) / len(toxicity)) if toxicity else abs(imb)
    return {
        "vpin":             round(min(1.0, max(0.0, vpin)), 4),
        "volume_imbalance": round(max(-1.0, min(1.0, imb)), 4),
        "bucket_count":     len(toxicity),
        "method":           "vpin_trade_tape_v1",
    }
```

### 4.4 Integración con Funding

| Señal | Efecto en Funding |
|-------|-------------------|
| `VPIN > 0.7` | `data_quality_score` decae → reduce `size_multiplier` |
| `VPIN > 0.85` sostenido | `signal_conflict_pressure` ↑ → reduce `historical_oos_quality` |
| `volume_imbalance > ±0.4` | Marca `weak_edge_backtest` si la dirección se sostiene en OOS |

**Razonamiento:** Alta toxicidad de flujo precede movimientos informados; las cuentas de fondeo deben reducir tamaño cuando el flujo se vuelve informativo (no retail).

### 4.5 Tests Unitarios Clave

- `test_vpin_zero_balance_returns_none`
- `test_vpin_clipping_to_unit_interval`
- `test_vpin_with_single_dominant_bucket`
- `test_vpin_handles_empty_inputs`

---

## 5. OFI — Order Flow Imbalance (Modelo Cont-Kukanov-Stoikov)

### 5.1 Funcionalidad

Mide el **desequilibrio de presión compradora vs vendedora** en el book de órdenes. La versión Cont-Kukanov-Stoikov (2014) descompone el mejor bid/ask y la profundidad L2 en una señal direccional de alta frecuencia. Adaptado a OHLCV + volumen, se usa para timing de entrada.

### 5.2 Lógica

El modelo original CKS mide el order flow imbalance sobre microestructura L1/L2:

$$
\mathrm{OFI}_t = \Delta P_t^{\text{bid}} \cdot Q_t^{\text{bid}} - \Delta P_t^{\text{ask}} \cdot Q_t^{\text{ask}}
$$

Adaptación a OHLCV (sin acceso a book completo) — usa la posición del cierre dentro del rango:

$$
\mathrm{OFI}_t = \mathrm{sign}(C_t - O_t) \cdot V_t \cdot \frac{C_t - O_t}{H_t - L_t + \epsilon}
$$

donde $C_t$ = close, $O_t$ = open, $H_t$ = high, $L_t$ = low, $V_t$ = volumen, $\epsilon$ = épsilon de seguridad. La idea: si el cierre está en la mitad superior del rango con volumen alto, el flujo neto fue comprador.

### 5.3 Matemática de Salida

OFI se normaliza por desviación estándar móvil para producir z-score:

$$
z_{\mathrm{OFI}}(t) = \frac{\mathrm{OFI}_t - \mu_{30}(t)}{\sigma_{30}(t) + \epsilon}
$$

Esto se mapea a un `[-1, +1]` con `tanh(z)` para usar como `bias` direccional.

### 5.4 Integración con Funding

| Señal | Efecto en Funding |
|-------|-------------------|
| `OFI z-score > +1.5` | Alinea con `long`; bloquea `short` por zona |
| `OFI z-score < -1.5` | Alinea con `short`; bloquea `long` por zona |
| Divergencia OFI vs precio | Marca `conflict_score` elevado → reduce size |

**Razonamiento:** Cuando el flujo real contradice la tesis direccional del Scanner, el trade tiene menor edge. La cuenta de fondeo no puede permitirse ese lujo.

---

## 6. CVD — Cumulative Volume Delta

### 6.1 Funcionalidad

Acumula el delta firmado de volumen (compras - ventas) desde un punto de reset. Es la **presión acumulada** del tape y funciona como un oscilador de momentum institucional.

### 6.2 Lógica

$$
\mathrm{CVD}_t = \sum_{i=1}^{t} \mathrm{sign}_i \cdot V_i
$$

donde $\mathrm{sign}_i \in \{-1, 0, +1\}$ según Lee-Ready. Se resetea diariamente o al inicio de la sesión RTH.

### 6.3 Implementación

```python
def compute_cvd_from_trades(signed_volumes: list[float]) -> dict:
    cvd = 0.0
    for vol in signed_volumes:
        if math.isfinite(vol):
            cvd += vol
    return {
        "cvd":           round(cvd, 4),
        "period_delta":  round(signed_volumes[-1], 4) if signed_volumes else 0.0,
        "trade_count":   len(signed_volumes),
    }
```

### 6.4 Integración con Funding

| Señal | Efecto en Funding |
|-------|-------------------|
| `CVD` divergence vs precio (suba de precio con CVD cayendo) | Marca `weak_edge`; posible distribución institucional |
| `CVD` confirma dirección | Refuerza conviction del Meta-Learner lateral |

---

## 7. SMC — Smart Money Concepts (BOS, CHoCH, Order Blocks, FVG)

### 7.1 Funcionalidad

Detecta **patrones de "dinero inteligente"** con precisión vectorizada sobre series de precios. El motor SMC emite 5 tipos de eventos:

1. **BOS (Break of Structure):** Ruptura confirmada de un swing high/low previo.
2. **CHoCH (Change of Character):** Cambio de régimen (de alcista → bajista o viceversa).
3. **Order Blocks:** Zonas de acumulación/distribución institucional (la vela anterior a un impulso fuerte).
4. **Fair Value Gaps (FVG):** Ineficiencias de 3 velas (low[i] > high[i-2]) no rellenas.
5. **Liquidity Sweeps:** Barridos de stops con desplazamiento direccional.

### 7.2 Lógica

Para BOS, se detecta con una **ventana de swing detection**:

$$
\text{swing high}_t = \max_{i \in [t-w, t+w]} H_i \iff H_t > H_{t-1} \land H_t > H_{t+1}
$$

donde $w$ = ventana configurable (default 5). Un BOS alcista es:

$$
\mathrm{BOS}^{\uparrow}_t = \left[ C_t > \max_{i < t} \text{swing high}_i \right] \land \left[ V_t > \bar{V}_{20} \right]
$$

CHoCH ocurre cuando un BOS rompe un swing en dirección opuesta al último BOS.

**Order Blocks** se identifican como la **vela bearish previa a un BOS alcista** (o viceversa), con cuerpo significativo:

$$
\mathrm{OB}^{\uparrow} = \left\{ t : \mathrm{BOS}^{\uparrow}_{t+k} \land C_{t+k} > H_t \land |C_t - O_t| > 0.5 \cdot \mathrm{ATR}_{14}(t) \right\}
$$

**FVG** se detecta como gaps de 3 velas:

$$
\mathrm{FVG}^{\uparrow}_t = \left[ L_t > H_{t-2} \right] \land \left[ V_t > \bar{V}_{20} \right]
$$

### 7.3 Paralelización

Todos los cálculos se ejecutan en `ProcessPoolExecutor` con `loop.run_in_executor(...)` para no bloquear el event loop async.

### 7.4 Integración con Funding

| Señal | Efecto en Funding |
|-------|-------------------|
| `CHoCH` confirmado | Marca cambio de régimen; reduce size hasta re-evaluación |
| Order Block respetado | Refuerza `best_supporting_module`; permite full size |
| FVG no rellenado | Aterriza en zona de ineficiencia; `tail_risk ↑` |
| Liquidity Sweep confirmado | Reversión institucional probable; alinear con sweep |

---

# PARTE III — MOTORES DE OPCIONES (Phase C / Layer 3)

## 8. GEX — Gamma Exposure + Volatility Regime

### 8.1 Funcionalidad

Calcula la **exposición gamma neta de los market makers** (dealer gamma) sumando gamma Black-Scholes por strike, ponderada por Open Interest, con signo invertido (los dealers están cortos en opciones que los clientes compran). Determina el **régimen de volatilidad**:

- **GAMMA_POSITIVE:** MM compra dips / vende rallies → supresión de volatilidad.
- **GAMMA_NEGATIVE:** MM vende dips / compra rallies → amplificación de volatilidad.
- **AT_FLIP:** Spot cerca del Zero Gamma Level → comportamiento errático.

### 8.2 Lógica

**Paso 1: Gamma Black-Scholes vectorial:**

$$
\Gamma_{\mathrm{BS}}(S, K, T, r, \sigma) = \frac{\phi(d_1)}{S \cdot \sigma \sqrt{T}}
$$

donde $d_1 = \frac{\ln(S/K) + (r + 0.5\sigma^2)T}{\sigma \sqrt{T}}$ y $\phi$ es la PDF normal estándar.

**Paso 2: Net Gamma en un spot hipotético $p$:**

$$
\Gamma_{\text{net}}(p) = \sum_{i=1}^{N} \Gamma_{\mathrm{BS}}(p, K_i, T, r, \sigma) \cdot \mathrm{OI}_i \cdot \mathrm{sign}_i \cdot \mathrm{contract\_size}
$$

donde $\mathrm{sign}_i = +1$ si `is_call` (dealer short puts / long calls) y $-1$ si `is_put`. En realidad los dealers están cortos en lo que los clientes compran, por lo que la convención es:

$$
\mathrm{sign}_i = \begin{cases} -1 & \text{si } \mathrm{is\_call}_i = 1 \text{ (dealer short call)} \\ +1 & \text{si } \mathrm{is\_call}_i = 0 \text{ (dealer short put)} \end{cases}
$$

**Paso 3: Perfil gamma en rango:**

Se muestrea $\Gamma_{\text{net}}(p)$ en $N$ puntos del rango $[p_{\text{spot}} \cdot (1 - 0.15), p_{\text{spot}} \cdot (1 + 0.15)]$ para construir la curva.

### 8.3 Matemática: Zero-Crossing (Gamma Flip)

El **Gamma Flip Point** es el spot donde $\Gamma_{\text{net}}(p) = 0$. Se busca con `scipy.optimize.brentq` sobre los sub-intervalos donde hay cambio de signo:

$$
p_{\text{flip}} = \min \{ p : \Gamma_{\text{net}}(p) = 0 \}
$$

Si $brentq` falla, se usa interpolación lineal como fallback:

$$
p_{\text{flip}} \approx p_{\text{lo}} - \frac{\Gamma_{\text{net}}(p_{\text{lo}})}{\Gamma_{\text{net}}(p_{\text{hi}}) - \Gamma_{\text{net}}(p_{\text{lo}})} \cdot (p_{\text{hi}} - p_{\text{lo}})
$$

### 8.4 Implementación

```python
def bs_gamma(spot, strike, tte, rate, sigma):
    """Gamma Black-Scholes vectorial."""
    if tte <= 0 or sigma <= 0 or spot <= 0:
        return np.zeros_like(strike)
    valid = strike > 0
    gamma = np.zeros_like(strike)
    k_val = strike[valid]
    d1 = (np.log(spot / k_val) + (rate + 0.5 * sigma**2) * tte) / (sigma * np.sqrt(tte))
    pdf = np.exp(-0.5 * d1**2) / np.sqrt(2.0 * np.pi)
    gamma[valid] = pdf / (spot * sigma * np.sqrt(tte))
    return gamma


def _get_net_gamma(price, strikes, is_call, open_interest, tte, rate, sigma, contract_size):
    gammas = bs_gamma(price, strikes, tte, rate, sigma)
    direction = np.where(is_call == 1.0, 1.0, -1.0)
    return float(np.sum(gammas * open_interest * direction) * contract_size)


# Find flip with brentq
sign_changes = np.where(np.diff(np.sign(gamma_profile)))[0]
if len(sign_changes) > 0:
    idx = sign_changes[0]
    p_lo, p_hi = price_range[idx], price_range[idx + 1]
    flip_point = brentq(
        lambda p: _get_net_gamma(p, strikes, is_call, oi, tte, rate, sigma, cs),
        p_lo, p_hi, xtol=1e-6, maxiter=200,
    )
```

### 8.5 Sensitivity Analysis

Shock sobre Put OI del 10% para medir fragilidad estructural:

$$
\mathrm{OI}^{\text{shock}}_i = \mathrm{OI}_i \cdot (1 + 0.10) \quad \text{si } \mathrm{is\_call}_i = 0
$$

Si el nuevo flip point se desplaza más del 1%, el mercado es estructuralmente frágil.

### 8.6 Integración con Funding

| Señal GEX | Efecto en Funding |
|-----------|-------------------|
| `regime = AT_FLIP` | `gamma_regime = SHOCK` → `_signal_penalty *= 0.5` |
| `regime = GAMMA_NEGATIVE` | `_signal_penalty *= 0.5`; `tail_risk` ↑ |
| `regime = GAMMA_POSITIVE` | Neutral; no ajusta size |
| `data_quality_score < 0.35` | **Hard block** en Portfolio Risk |
| `data_quality_score < 0.75` | `_signal_penalty *= 0.5` |

### 8.7 Tests

- `test_gamma_regime_positive_suppresses_vol`
- `test_gamma_regime_negative_amplifies_vol`
- `test_flip_point_falls_on_zero_crossing`
- `test_shock_moves_flip_in_expected_direction`
- `test_data_quality_below_threshold_blocks`

---

## 9. Gamma Flip Probability (First Passage Time sobre GBM)

### 9.1 Funcionalidad

Estima la **probabilidad de que el spot toque el Zero Gamma Level antes del vencimiento**, asumiendo un Movimiento Browniano Geométrico (GBM). Es la **probabilidad de primera llegada** (first passage time) a una barrera, que es la primitiva teórica fundamental para knockout options y análisis de gamma risk.

### 9.2 Lógica

Sea $S_t$ el spot siguiendo GBM:

$$
dS_t = \mu S_t \, dt + \sigma S_t \, dW_t
$$

Queremos $P(\min_{0 \le t \le T} S_t \le L) = 1$ si $L < S_0$ (barrera inferior) o equivalentemente $P(\max_{0 \le t \le T} S_t \ge U) = 1$ si $U > S_0$ (barrera superior).

La fórmula cerrada de primera llegada para GBM es la **solución de la ecuación del calor**:

$$
P(\tau_L \le T) = \Phi\left(\frac{-a + \mu T}{\sigma \sqrt{T}}\right) + e^{2\mu a / \sigma^2} \cdot \Phi\left(\frac{-a - \mu T}{\sigma \sqrt{T}}\right)
$$

donde $a = \ln(L/S_0)$ (negativo si $L < S_0$).

### 9.3 Implementación

```python
def estimate_gamma_flip_probability(
    spot: float,
    zgl: float,
    iv: float,         # σ anualizado
    dte_days: float,
    r: float = 0.04,   # risk-free
) -> float:
    if spot <= 0 or zgl <= 0 or dte_days <= 0 or iv <= 0:
        return 0.0

    T = dte_days / 365.0
    if math.isclose(spot, zgl, rel_tol=1e-5):
        return 1.0

    a = math.log(zgl / spot)             # log-distance al ZGL
    mu = r - 0.5 * iv**2                 # drift de GBM
    sigma_sqrt_T = iv * math.sqrt(T)     # √T * σ

    if sigma_sqrt_T < 1e-8:
        return 0.0

    # Cálculo de CDF normal estándar
    if a > 0:    # ZGL > spot (barrera arriba)
        d1 = (-a + mu * T) / sigma_sqrt_T
        d2 = (-a - mu * T) / sigma_sqrt_T
    else:        # ZGL < spot (barrera abajo)
        d1 = (a - mu * T) / sigma_sqrt_T
        d2 = (a + mu * T) / sigma_sqrt_T

    term1 = norm_cdf(d1)                  # Φ(d1)

    # term2 puede explotar numéricamente: clampeamos
    exponent = (2.0 * mu * a) / (iv**2)
    if exponent > 700:
        term2_factor = float("inf")
    elif exponent < -700:
        term2_factor = 0.0
    else:
        term2_factor = math.exp(exponent)
    term2 = term2_factor * norm_cdf(d2)

    prob = float(term1 + term2)
    if math.isnan(prob) or math.isinf(prob):
        return 0.0
    return max(0.0, min(prob, 1.0))
```

### 9.4 Integración con Funding

| Probabilidad | Acción |
|--------------|--------|
| `P(flip) > 0.65` | Tamaño **micro (0.25x)** porque el régimen puede cambiar |
| `P(flip) > 0.85` | **Hard block** — probabilidad crítica de cambio de régimen |
| `P(flip) < 0.30` | Tamaño **normal** (1.0x) — régimen estable |

**Razonamiento:** Si hay alta probabilidad de tocar el flip point, el régimen gamma cambiará. El tamaño debe reducirse *preventivamente*, no reactivamente.

---

## 10. DEX — Delta Exposure + Gamma Trap

### 10.1 Funcionalidad

Mide la **exposición delta neta de los market makers**. Complementa a GEX (que es gamma) con la primera derivada. Identifica **Gamma Traps**: zonas donde el dealer hedging crea feedback loops explosivos (generalmente cerca del flip point con delta direccional elevado).

### 10.2 Lógica

**Delta Black-Scholes:**

$$
\Delta_{\mathrm{BS}}(S, K, T, r, \sigma) = \Phi(d_1) \quad \text{(call)}; \quad \Phi(d_1) - 1 \quad \text{(put)}
$$

**Net Delta Exposure:**

$$
\Delta_{\text{net}}(p) = \sum_{i=1}^{N} \Delta_{\mathrm{BS}}(p, K_i, T, r, \sigma) \cdot \mathrm{OI}_i \cdot \mathrm{sign}_i
$$

**Gamma Trap Detection:**

Un gamma trap ocurre si simultáneamente:

1. $|\Delta_{\text{net}}(p)| > \Delta_{\text{threshold}}$ (delta direccional fuerte)
2. $|\Gamma_{\text{net}}(p)| < \Gamma_{\text{threshold}}$ (gamma débil, sin amortiguación)
3. $|p - p_{\text{flip}}| < \delta_{\text{threshold}}$ (cerca del flip)

$$
\mathrm{trap\_score} = \frac{|\Delta_{\text{net}}(p)|}{|\Gamma_{\text{net}}(p)| + \epsilon} \cdot \mathbb{1}[|p - p_{\text{flip}}| < 0.02 \cdot p]
$$

### 10.3 Integración con Funding

| Señal DEX | Efecto en Funding |
|-----------|-------------------|
| `trap_score > 3.0` | **Reduce 50% del tamaño**; warning de gamma trap |
| `\|Δ_net\| > percentile 95` | Marca `gamma_regime = BEARISH` o `BULLISH` direccional |
| Delta direccional opuesto a tesis | **Hard block**; el MM va en contra |

---

## 11. Max Pain — Magnetismo de Vencimiento

### 11.1 Funcionalidad

Calcula el strike de **menor dolor** para los option holders al vencimiento: el precio donde la mayor cantidad de opciones expiran *out-of-the-money* (mínimo payout agregado de los clientes).

### 11.2 Lógica

Para cada strike candidato $K_c$:

$$
\mathrm{Pain}(K_c) = \sum_{i=1}^{N} \mathrm{OI}_i \cdot \max(0, K_c - K_i) \cdot \mathbb{1}_{\text{call}} + \mathrm{OI}_i \cdot \max(0, K_i - K_c) \cdot \mathbb{1}_{\text{put}}
$$

Max Pain:

$$
K_{\text{mp}} = \arg\min_{K_c} \mathrm{Pain}(K_c)
$$

### 11.3 Integración con Funding

| Distancia a Max Pain | Efecto |
|----------------------|--------|
| `|S - K_mp| / S < 0.5%` | **Zona de pinning** — supresión de volatilidad, ajustar size |
| `|S - K_mp| / S > 3%` | Mercados rotando hacia nuevo dolor → momentum fuerte |
| Dirección confirmada a K_mp | **alinear tesis**; aumentar size |

---

## 12. Zero-Day (0DTE) Engine — Pinning, Cascades, Gamma Vacuum

### 12.1 Funcionalidad

Especializado en opciones que vencen **hoy mismo (0 days to expiration)**. Tres dinámicas dominantes:

1. **Pinning:** El spot se "pega" al strike con mayor OI 0DTE (Max Pain intraday).
2. **Cascade / Gamma Storm:** Movimiento violento cuando gamma intraday se acerca a 0 (theta burn deja gamma inestable).
3. **Gamma Vacuum:** Cuando el spot se aleja del strike 0DTE, el MM reduce cobertura, lo que produce aceleración de momentum.

### 12.2 Lógica

Para opciones 0DTE, la gamma Black-Scholes **explota** cuando $S \approx K$ y $T \to 0$:

$$
\Gamma_{\mathrm{BS}} \xrightarrow{T \to 0} \frac{\delta(S - K)}{S^2 \cdot \sigma \sqrt{2\pi T}} \to \infty
$$

El 0DTE engine opera en sub-escalas: usa $T_{\text{frac}} = T_{\text{day}} / N_{\text{bars}}$ (fracciones del día) para muestrear.

**Pinning detection:**

$$
\mathrm{pin\_strength} = \frac{\mathrm{OI}(K_{\text{closest}})}{\sum_i \mathrm{OI}(K_i)} \cdot \exp\left(-\frac{(S - K_{\text{closest}})^2}{2 \cdot \sigma_{\text{rolling}}^2}\right)
$$

Si `pin_strength > 0.5`, se está en zona de pinning.

### 12.3 Integración con Funding

| Estado 0DTE | Efecto |
|-------------|--------|
| `pinning` | Reducir tamaño; spread ajustado |
| `cascade` potencial | **Hard block** 0DTE entries en los últimos 30 min |
| `gamma_vacuum` | Alinear con momentum (no contra) |

---

## 13. Shadow Delta Engine — Gap entre Delta Nominal y Real

### 13.1 Funcionalidad

Detecta el **gap entre el delta reportado** (Black-Scholes) y el **delta real** (calculado desde el movimiento del subyacente). Esta divergencia es una señal de **manipulación de surface de volatilidad** o **hedge ineficiente del market maker**.

### 13.2 Lógica

**Delta nominal:** $\Delta_{\mathrm{BS}}$ como siempre.
**Delta realizado** (ventana móvil de 30 min):

$$
\Delta_{\text{real}}(t) = \frac{\Delta S_t}{\sigma_{\text{impl}}} \cdot \frac{1}{\sqrt{T_{\text{frac}}}}
$$

**Shadow delta score:**

$$
\mathrm{shadow\_score} = \frac{|\Delta_{\text{real}} - \Delta_{\mathrm{BS}}|}{\Delta_{\mathrm{BS}} + \epsilon}
$$

Si `shadow_score > 0.4`, hay divergencia material.

### 13.3 Integración con Funding

| Shadow Score | Efecto |
|--------------|--------|
| `> 0.4` | Marca `data_quality` bajo; reduce 50% size |
| `> 0.7` | **Hard block** — surface corrupta, no se puede confiar en Greeks |

---

## 14. Squeeze Ignition Detection

### 14.1 Funcionalidad

Detecta **compresiones de volatilidad seguidas de expansión** (Volatility Squeeze) y la **ignición** del movimiento resultante. Combina Bollinger Bands con Keltner Channels (TTM Squeeze).

### 14.2 Lógica

**Squeeze ON:** BB width < KC width (volatilidad real < volatilidad implícita)

$$
\mathrm{squeeze\_on} = \left[ (H_{20} - L_{20}) / \mathrm{MA}_{20} \right] < \left[ 1.5 \cdot \mathrm{ATR}_{20} \right]
$$

**Squeeze Release:** Squeeze estaba ON en $t-1$ y OFF en $t$ (release moment).

**Direction:** Momentum linear regression slope en las últimas 20 velas.

**Squeeze Probability (0-1):**

$$
P_{\text{squeeze}} = \sigma\left(\alpha \cdot \text{slope} + \beta \cdot \text{momentum} + \gamma \cdot \text{volume\_z}\right)
$$

donde $\sigma$ es la función sigmoide.

### 14.3 Integración con Funding

| Squeeze Probability | Efecto |
|--------------------|--------|
| `P_squeeze ≥ 0.75` | `_signal_penalty *= 0.75` (warning de riesgo de expansión) |
| `P_squeeze ≥ 0.75` AND release confirmado | **Aumentar size** si tesis alineada (oportunidad) |

---

## 15. Delta-Weighted Flow

### 15.1 Funcionalidad

Pondera el flujo de opciones (compra/venta) por la **delta** de cada contrato, para detectar **flujo institucional con convicción direccional**. Diferencia entre comprar calls OTM (baja delta, retail) vs comprar calls ATM (alta delta, institucional cubriendo posición).

### 15.2 Lógica

**Para cada trade de opción:**

$$
w_i = |\Delta_{\mathrm{BS}}(K_i, T_i)| \cdot \mathrm{notional}_i
$$

**Sign convention:** $\mathrm{sign}_i = +1$ si call comprada / put vendida; $-1$ si call vendida / put comprada.

**Delta-weighted flow (rolling 1h):**

$$
\mathrm{DWF}_t = \sum_{i \in [t-1h, t]} \mathrm{sign}_i \cdot w_i
$$

Normalizado por OI total:

$$
\mathrm{DWF\_norm} = \frac{\mathrm{DWF}_t}{\sum_i \mathrm{OI}_i \cdot |\Delta_i|}
$$

### 15.3 Integración con Funding

| `DWF_norm` | Efecto |
|------------|--------|
| `> +0.3` | Conviction alcista institucional → `best_supporting_module` |
| `< -0.3` | Conviction bajista → alinear short |
| `\|DWF\| > 0.5` | **Reduce 25%** (extensión; posible capitulación) |

---

# PARTE IV — MOTORES PREDICTIVOS (Phase C / Layer 3)

## 16. Multimodal Predictive Engine — Conv-LSTM Event-Driven

### 16.1 Funcionalidad

Red neuronal **Conv-LSTM con mecanismos de retención de eventos** que procesa secuencias temporales de tensores 3D resultado del outer-product fusion. Produce clasificación direccional con probabilidad asignada (3 clases: BEARISH, NEUTRAL, BULLISH).

### 16.2 Arquitectura

**Celda `_EventDrivenLSTMCell`:**

Cada celda tiene 4 puertas convolucionales + 1 event retention gate:

1. **Forget gate:** $f_t = \sigma(W_{xf} * x_t + W_{hf} * h_{t-1} + W_{ef} \cdot e_t)$
2. **Input gate:** $i_t = \sigma(W_{xi} * x_t + W_{hi} * h_{t-1} + W_{ei} \cdot e_t)$
3. **Candidate:** $\tilde{c}_t = \tanh(W_{xc} * x_t + W_{hc} * h_{t-1} + W_{ec} \cdot e_t)$
4. **Output gate:** $o_t = \sigma(W_{xo} * x_t + W_{ho} * h_{t-1} + W_{eo} \cdot e_t)$
5. **Event retention gate:** $r_t = \sigma(W_{er} \cdot e_t)$

**Ecuación de estado (con event retention):**

$$
c_t = f_t \odot c_{t-1} + i_t \odot \tilde{c}_t
$$
$$
c_t' = \tanh(c_t)
$$
$$
c_t^{\text{final}} = c_t + r_t \odot c_t' - c_t' \quad \text{(event-augmented)}
$$
$$
h_t = o_t \odot \tanh(c_t^{\text{final}})
$$

La innovación está en el **sustraendo** $-c_t'$: cuando un evento es relevante, la retención amplifica el state, pero se resta la parte "neutra" para evitar que la memoria se sature.

### 16.3 Stack completo

```
Input (B, T, H, W) → Conv2D projection → ConvLSTM Cells × n_layers
   → Event retention injection en cada step
   → Self-Attention
   → GlobalAvgPool over H,W
   → Linear → logits (3 clases)
```

### 16.4 Output

```python
class FusionReport(BaseModel):
    symbol: str
    bias: str          # "LONG" | "CASH" | "SHORT"
    conviction: float  # [0, 1]
    fusion_metadata: FusionMetadata
```

### 16.5 GEX Gating (pre-emission)

Antes de emitir la señal, se valida con `calculate_probabilistic_gex_gating`:

```python
is_safe = calculate_probabilistic_gex_gating(
    current_gex=gex_data.get("total_gex", 0.0),
    vanna_flow=gex_data.get("net_vanna_flow", 0.0),
    regime_confidence=0.8,
)
if not is_safe:
    return Result.failure(reason="GEX gating blocked emission")
```

### 16.6 Integración con Funding

| Salida del Motor | Efecto en Funding |
|------------------|-------------------|
| `bias = LONG` con `conviction > 0.7` | Permite `ALLOW` con size completo |
| `bias = LONG` con `conviction < 0.4` | **SIZE_DOWN** o **BLOCK** |
| `bias ≠ thesis del Scanner` | `conflict_score ≥ 0.5` → reduce 50% |
| GEX gate unsafe | **Hard block** predictivo (override) |

### 16.7 Tests

- `test_lstm_cell_event_retention_modifies_state`
- `test_fusion_tensor_outer_product_shape`
- `test_gating_blocks_unsafe_emission`
- `test_calibration_profile_vol_scaler_assets`

---

## 17. QuantumAlpha LSTM + Self-Attention

### 17.1 Funcionalidad

Versión simplificada del multimodal engine: LSTM vanilla con self-attention para clasificación OHLCV en 3 clases (BEARISH, NEUTRAL, BULLISH). Sirve como **proxy ligero** cuando no hay sentiment disponible.

### 17.2 Arquitectura

**Input:** `(B, T=20, I=5)` donde $I=5$ son las features OHLCV normalizadas (z-score).

**LSTM:**

$$
h_t, c_t = \mathrm{LSTM\_cell}(x_t, h_{t-1}, c_{t-1})
$$

**Self-Attention:**

$$
Q = h \cdot W_Q, \quad K = h \cdot W_K, \quad V = h \cdot W_V
$$
$$
\mathrm{attn} = \mathrm{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)
$$
$$
\mathrm{context} = \mathrm{attn} \cdot V
$$

**Output:** `context.mean(dim=1)` → Linear → logits (3 clases).

### 17.3 Normalización

```python
@staticmethod
@lru_cache(maxsize=64)
def _normalize_features_cached(features_tuple):
    features_array = np.asarray(features_tuple, dtype=np.float64)
    mean = np.mean(features_array, axis=0)
    std = np.std(features_array, axis=0)
    return (features_array - mean) / (std + 1e-9)
```

Z-score por feature, con épsilon para evitar división por cero.

### 17.4 Calibration Profile

Per-ticker thresholds para mapear probabilidad → signal:

```python
def get_profile(ticker: str) -> dict:
    if ticker.endswith(".BA") or ticker in ["GGAL", "YPF", "PAMP", "BTC", "ETH"]:
        return {"long_threshold": 0.60, "cash_threshold": 0.45, "vol_scaler": 1.5}
    return {"long_threshold": 0.65, "cash_threshold": 0.40, "vol_scaler": 1.0}
```

Lógica: activos volátiles (Argentina, crypto) necesitan umbrales más bajos para LONG y más altos para CASH.

### 17.5 Integración con Funding

Idéntica a §16.6. Adicionalmente, cuando `signal = WATCH` (no se clasifica), se considera `conviction < 0.5` → reduce size 25%.

---

## 18. Outer-Product Tensor Fusion

### 18.1 Funcionalidad

Fusiona dos modalidades (e.g., fundamentales × sentiment) en un **tensor 3D** vía producto exterior para capturar interacciones bilineales no lineales.

### 18.2 Matemática

Sean $F \in \mathbb{R}^{T \times d_f}$ (fundamentales) y $N \in \mathbb{R}^{T \times d_n}$ (news/sentiment). El tensor fusionado es:

$$
\mathcal{T} \in \mathbb{R}^{T \times d_f \times d_n}, \quad \mathcal{T}_{t, i, j} = F_{t, i} \cdot N_{t, j}
$$

Implementación vectorizada con `np.einsum`:

```python
tensor_3d = np.einsum("ij,ik->ijk", fund_arr, news_arr, optimize="optimal")
```

### 18.3 Por qué outer-product (vs concatenación)

Concatenación $\mathbf{c} = [F_t \| N_t]$ produce dimensión $d_f + d_n$ y solo captura **separabilidad lineal** (la red aprende $W \cdot c = w_F F + w_N N$).

Outer-product produce $d_f \cdot d_n$ parámetros de interacción **bilineales**:

$$
y = \sum_{i,j} W_{i,j} \cdot F_i \cdot N_j
$$

Esto captura **sinergia**: e.g., "fundamentales sólidos" × "sentiment positivo" = `boost`, vs "fundamentales sólidos" × "sentiment negativo" = `riesgo de reversal`.

### 18.4 Complejidad

Para $T=20$, $d_f = d_n = 32$, el tensor es $20 \times 32 \times 32 = 20480$ valores. Se cachea con `@lru_cache(maxsize=128)` por tupla de features para evitar recomputación.

---

## 19. Sentiment Engine + Catalyst NLP

### 19.1 Funcionalidad

Combina **tres fuentes de sentiment** en un score agregado en $[-1, +1]$:

1. **News sentiment:** Análisis NLP de titulares (FinGPT, FinRobot).
2. **Earnings transcripts:** Tone analysis sobre calls trimestrales.
3. **Insider activity:** Compras/ventas de insiders como proxy de sentiment.

### 19.2 Lógica

$$
S_{\text{news}} = \frac{1}{N} \sum_{k=1}^{N} \mathrm{BERT\_polarity}(\text{headline}_k)
$$

$$
S_{\text{transcripts}} = \alpha \cdot \mathrm{positivity} + \beta \cdot \mathrm{certainty} + \gamma \cdot \mathrm{forward\_looking}
$$

$$
S_{\text{insider}} = \frac{V_{\text{buy}} - V_{\text{sell}}}{V_{\text{buy}} + V_{\text{sell}} + \epsilon}
$$

**Score compuesto:**

$$
S_{\text{composite}} = w_1 S_{\text{news}} + w_2 S_{\text{transcripts}} + w_3 S_{\text{insider}}
$$

con defaults $w_1 = 0.5$, $w_2 = 0.3$, $w_3 = 0.2$.

### 19.3 Integración con Funding

| `S_composite` | Efecto |
|---------------|--------|
| `> +0.5` y tesis long | Boost a `conviction` del Meta-Learner |
| `< -0.5` y tesis long | **Reduce 30%**; warning de sentiment adverso |
| `S = 0` (sin data) | Neutral; no afecta |

---

## 20. CNN Fear & Greed Classifier

### 20.1 Funcionalidad

Red neuronal convolucional 1D que clasifica el **régimen de mercado** (Fear, Greed, Neutral) desde features de mercado (VIX, put/call ratio, breadth, momentum).

### 20.2 Arquitectura

```
Input (B, T=30, F=8)
   → Conv1D(64, kernel=3) + ReLU
   → MaxPool1D(2)
   → Conv1D(128, kernel=3) + ReLU
   → AdaptiveAvgPool1D(1)
   → Linear(128, 3)  # 3 clases
```

### 20.3 Features de Entrada

1. VIX (volatilidad implícita)
2. Put/Call ratio (5-day MA)
3. NYSE breadth (% advancing)
4. Momentum (SPX 30-day return)
5. Junk bond demand (HYG/LQD ratio)
6. Safe haven demand (TLT return)
7. Market volatility (VIX 30d std)
8. Skew index (CBOE SKEW)

### 20.4 Integración con Funding

| Régimen | Efecto |
|---------|--------|
| `Fear` extremo (>0.7) | **Reduce size global** 30% (modo defensivo) |
| `Greed` extremo (>0.7) | **Reduce size** 20% (mercado extendido) |
| `Neutral` | Sin ajuste |

---

## 21. Cross-Asset Correlation Engine

### 21.1 Funcionalidad

Calcula la **matriz de correlaciones rolling** entre activos y detecta **descorrelaciones anómalas** que indican regímenes de crisis o contagio.

### 21.2 Lógica

**Matriz de correlación rolling (60 días):**

$$
\rho_{i,j}(t) = \frac{\sum_{k=t-60}^{t} (r_{i,k} - \bar{r}_i)(r_{j,k} - \bar{r}_j)}{\sqrt{\sum (r_i - \bar{r}_i)^2 \sum (r_j - \bar{r}_j)^2}}
$$

**Detección de crisis (correlación → 1):**

Cuando todas las correlaciones inter-asset se acercan a 1, hay "correlations go to 1" → diversificación inefectiva → reducir tamaño total.

**Indicador de crisis:**

$$
\mathrm{crisis\_index} = \frac{2}{N(N-1)} \sum_{i<j} \max(0, \rho_{i,j} - 0.7)
$$

### 21.3 Integración con Funding

| `crisis_index` | Efecto |
|----------------|--------|
| `> 0.3` | **Reduce 50%** del tamaño (diversificación rota) |
| `0.1 - 0.3` | Warning; activar stop-losses trailing |
| `< 0.1` | Normal |

---

## 22. Funding Lab Side Meta-Learner (Heurística)

### 22.1 Funcionalidad

**Confirmación de alta convicción** sobre la dirección propuesta (long/short). Es un gate determinístico derivado de features del scanner. Decisión final: `PASS` o `FAIL`.

### 22.2 Lógica — Long

```python
LONG_MIN_TREND_SCORE = 0.75
MIN_VOLUME_SCORE    = 0.60
MAX_ABS_MEAN_REVERSION = 0.80
MAX_LONG_RETURN_5D = 0.08

def long_reasons(features):
    reasons = []
    trend = features.get("vsa_forecast__trend_score")
    volume = features.get("vsa_forecast__volume_score")
    mean_rev = features.get("price__mean_rev_signal")
    return_5d = features.get("price__return_5d")

    if trend is None or trend < 0.75:
        reasons.append("trend_score_too_low")
    if volume is None or volume < 0.60:
        reasons.append("volume_score_too_low")
    if (mean_rev and abs(mean_rev) > 0.80) or (return_5d and return_5d > 0.08):
        reasons.append("overextended")
    return reasons
```

**Long score:**

$$
\mathrm{score}_{\text{long}} = \mathrm{clip}\left( 0.65 \cdot \mathrm{trend} + 0.35 \cdot \mathrm{volume} - 0.15 \cdot \min\left(\frac{|\mathrm{mean\_rev}|}{0.80}, 1.0\right), 0, 1 \right)
$$

### 22.3 Lógica — Short

Más restrictivo (asimétrico: shorts son más peligrosos):

```python
SHORT_MAX_TREND_SCORE = -0.70
MIN_SHORT_VOL_RATIO = 0.60
MAX_SHORT_VOL_RATIO = 1.30

def short_reasons(features):
    # Trend debe ser fuertemente negativo
    if trend > -0.70: reasons.append("trend_score_too_high")
    if volume < 0.60: reasons.append("volume_score_too_low")
    # Estructura técnica debe ser bearish
    if structure != -1.0: reasons.append("structure_not_bearish")
    # VWAP distance debe ser negativo
    if vwap_distance >= 0.0: reasons.append("vwap_not_bearish")
    # Sin exhaustion/reversion: short peligroso
    if rsi < 55 and mean_rev >= 0: reasons.append("not_exhausted")
    # Volatilidad no debe estar descontrolada
    if not 0.60 <= vol_ratio <= 1.30: reasons.append("vol_uncontrolled")
    return reasons
```

**Short score:**

$$
\mathrm{score}_{\text{short}} = \mathrm{clip}\left( 0.45 \cdot |\mathrm{trend}| + 0.25 \cdot \mathrm{volume} + 0.20 \cdot \mathbb{1}_{\text{struct}=-1} + 0.10 \cdot \mathbb{1}_{\text{vwap}<0}, 0, 1 \right)
$$

### 22.4 Integración con Funding

- **`status = PASS`** → permite `ALLOW` con size completo
- **`status = FAIL`** con razón no-crítica → `SIZE_DOWN` 50%
- **`status = FAIL`** con `overextended` o `vol_uncontrolled` → **HARD BLOCK** para shorts

**Razonamiento:** Cortos con extensión alcista o sin catalyst técnico son trampas estadísticas. La cuenta de fondeo no puede permitirlos.

---

# PARTE V — MOTORES DE FUNDING / RIESGO (Layer 5)

## 23. FTMO Survival Score — Funnel de Decisión Determinístico

### 23.1 Funcionalidad

Computa un **score de supervivencia** `[0, 100]` que agrega 5 dimensiones de "runway" del funding:

| Componente | Peso | Origen |
|-----------|------|--------|
| `daily_loss_runway` | 30% | `_daily_loss_metrics` |
| `max_loss_runway` | 25% | `_max_loss_metrics` |
| `consistency_runway` | 20% | `_consistency_metrics` |
| `historical_oos_quality` | 15% | `_historical_metrics` |
| `signal_conflict_pressure` | 10% | `1 - max(conflict_score)` |

**Fórmula:**

$$
\mathrm{Survival} = 0.30 \cdot D + 0.25 \cdot M + 0.20 \cdot C + 0.15 \cdot H + 0.10 \cdot (1 - K_{\max}) \in [0, 100]
$$

donde $D = 1 - \mathrm{usage}_{\text{daily}}/100$, $M = 1 - \mathrm{usage}_{\max}/100$, $C \in \{0, 0.3, 1.0\}$ (consistency), $H = \mathrm{quality\_score} \in [0,1]$, $K_{\max} = \max(\mathrm{conflict\_scores})$.

### 23.2 Estados (jerarquía)

```
INSUFFICIENT (4) > WOULD_BREACH (3) > AT_RISK (2) > MONITOR (1) > SAFE (0)
```

### 23.3 Lógica Detallada

```python
def compute_ftmo_survival_score(
    module_evidence: list[dict],
    account_state: dict | None = None,
    reason_codes: list[str] | None = None,
) -> dict:

    daily = _daily_loss_metrics(account)
    max_loss = _max_loss_metrics(account)
    consistency = _consistency_metrics(account)
    historical = _historical_metrics(evidence)

    # Hard fail por evidencia faltante
    if not evidence or historical["status"] == "INSUFFICIENT":
        return _payload(status="INSUFFICIENT", score=None, ...)

    status = "SAFE"

    # 1. Daily loss checks
    if daily["breached"]:
        summary_reasons.append("daily_loss_breach")
        status = "WOULD_BREACH"
    elif daily["usage_pct"] >= 80.0:
        summary_reasons.append("daily_loss_usage_high")
        status = "AT_RISK"

    # 2. Max loss checks
    if max_loss["breached"]:
        status = "WOULD_BREACH"
    elif max_loss["usage_pct"] >= 80.0:
        status = "AT_RISK"

    # 3. Consistency checks
    if consistency["blocked"]:
        status = "WOULD_BREACH"
    elif consistency["warning"]:
        status = "AT_RISK"

    # 4. Historical checks
    if historical["status"] == "WOULD_BREACH":
        status = "WOULD_BREACH"
    elif historical["status"] == "AT_RISK":
        status = "AT_RISK"

    # Score compuesto
    score = (
        0.30 * (1 - daily["usage_pct"]/100) +
        0.25 * (1 - max_loss["usage_pct"]/100) +
        0.20 * consistency["runway_score"] +
        0.15 * historical["quality_score"] +
        0.10 * (1 - max_conflict)
    ) * 100

    # Hard override
    if status == "WOULD_BREACH":  score = 0.0
    elif status == "MONITOR":     score = min(score, 65.0)
    elif status == "AT_RISK":     score = min(score, 49.0)
    else:
        status = "SAFE" if score >= 70 else "MONITOR" if score >= 50 else "AT_RISK"

    return _payload(status=status, score=score, ...)
```

### 23.4 Constantes FTMO

```python
FTMO_PROFILE_ID = "ftmo_2_step"
FTMO_INITIAL_CAPITAL = 100_000.0
FTMO_DAILY_LOSS_LIMIT_PCT = 5.0
FTMO_MAX_LOSS_LIMIT_PCT = 10.0
FTMO_CONSISTENCY_WARNING = 0.35
FTMO_CONSISTENCY_BLOCK = 0.50
FTMO_BASE_RISK_PER_TRADE_PCT = 0.50
```

### 23.5 Output Recomendado

```python
def _recommended_risk(status, reasons) -> float:
    if status in {"WOULD_BREACH", "INSUFFICIENT"}: return 0.0
    if "daily_loss_usage_high" in reasons or "max_loss_usage_high" in reasons: return 0.0
    if status == "AT_RISK":  return 0.10
    if status == "MONITOR":  return 0.25
    return FTMO_BASE_RISK_PER_TRADE_PCT  # 0.50
```

### 23.6 Integración con Funding

Es el **componente central** del módulo. Su score se combina con el del Portfolio Risk Service (4-Tier) y el del Scanner Funding Gate para producir la decisión final.

---

## 24. Portfolio Risk Service — 4-Tier Ladder + Kelly Fraccional

### 24.1 Funcionalidad

Servicio **binding** (autoritativo) que evalúa cada `TradeCandidate` contra las reglas del funding y produce una **decisión de 4 niveles** con tamaño ajustado.

### 24.2 La Escalera de 4 Tiers (de más severo a menos)

```
┌────────────────────────────────────────────────────────────┐
│ TIER 4 — BLOCK (0.0x)                                      │
│   Triggers (cualquiera):                                   │
│   • funding_suitability = block                            │
│   • rules.status ∈ {BREACHED, LOCKED}                      │
│   • budget.per_trade_amount ≤ 0                            │
│   • consistency.status = blocked                           │
│   • daily_loss_usage ≥ 80%                                 │
│   • candidate.stop is None                                 │
│   • stop_pct > remaining_daily_risk_pct                    │
│   • module_backtest_grade = overfit_risk                   │
│   • critical_module (options_gex, technical).suit=block    │
│   → decision = BLOCK, size_multiplier = 0.0                │
├────────────────────────────────────────────────────────────┤
│ TIER 3 — MICRO (0.25x)                                     │
│   Triggers (cualquiera, primera que aplique):               │
│   • consistency.status = warning                           │
│   • weakest_link_module.data_quality < 0.25                │
│   • daily_loss_usage ≥ 60%                                 │
│   • weak_edge_backtest + scanner_recommended < 0.5         │
│   → decision = SIZE_DOWN, size_multiplier = 0.25 × ...     │
├────────────────────────────────────────────────────────────┤
│ TIER 2 — REDUCED (0.50x)                                   │
│   Triggers:                                                │
│   • funding_suitability = size_down                        │
│   • non_critical_module.size_down (sentiment, micro, etc.) │
│   • scanner_recommended < 0.75                             │
│   • conflict_score ≥ 0.5                                   │
│   • tail_risk ≥ 0.7                                        │
│   → decision = SIZE_DOWN, size_multiplier = 0.50 × ...     │
├────────────────────────────────────────────────────────────┤
│ TIER 1 — NORMAL (1.0x)                                     │
│   Sin triggers de tiers superiores.                        │
│   → decision = ALLOW, size_multiplier = 1.0 × penalties    │
└────────────────────────────────────────────────────────────┘
```

### 24.3 Kelly Fraccional (Layer 5 Primitive)

```python
def fractional_kelly(
    win_prob: float,
    *,
    win_payoff: float = 1.0,
    loss_payoff: float = 1.0,
    shrink: float = 0.25,    # 1/4 Kelly
    cap: float = 0.25,       # máximo 25% del capital
) -> float:
    """
    Half/quarter Kelly style fraction with hard cap.
    p = win_prob, b = win_payoff / loss_payoff
    raw_kelly = (p * b - (1-p)) / b
    result = clip(raw_kelly * shrink, 0, cap)
    """
    p = max(0.0, min(1.0, win_prob))
    q = 1.0 - p
    b = max(win_payoff, 1e-9) / max(loss_payoff, 1e-9)
    raw = (p * b - q) / max(b, 1e-9)
    if raw <= 0:
        return 0.0
    return float(max(0.0, min(cap, raw * shrink)))
```

**Justificación:** Kelly completo maximiza log-wealth pero con varianza infinita. Quarter-Kelly es el sweet spot institucional: 75% del growth con 25% de la varianza.

### 24.4 Position Notional Calculation

```python
def position_notional_from_risk(
    equity: float,
    risk_budget_pct: float,  # ej. 0.5
    stop_distance_pct: float,  # ej. 1.5
) -> float:
    """
    Notional = equity * (risk% / stop%)
    """
    return equity * (risk_budget_pct / 100.0) / max(stop_distance_pct / 100.0, 1e-9)
```

### 24.5 Hist VaR (95%)

```python
def historical_var_pct(returns_pct, alpha=0.05) -> float | None:
    """
    VaR histórico en cola izquierda.
    Retorna |percentil alpha| (positivo, interpretable como pérdida).
    """
    xs = sorted(float(x) for x in returns_pct if math.isfinite(x))
    if len(xs) < max(10, int(1 / max(alpha, 0.01))):
        return None
    idx = max(0, min(len(xs) - 1, int(math.floor(alpha * len(xs))) - 1))
    return float(abs(xs[idx]))
```

### 24.6 Stress Loss

```python
def stress_loss_pct(mean, vol, shocks=(-2.0, -3.0)) -> dict:
    """
    Gaussian-style stress scenarios.
    stress_z = mean + z * vol  (en %)
    """
    return {f"z{z:.1f}_pct": round(mean + z * vol, 4) for z in shocks}
```

### 24.7 Challenge Simulation

Para cada preset canónico (`ftmo_2_step`, `topstep_combine`, `custom`):

```python
def _compute_challenge_simulation(request):
    results = []
    for preset_id in ["ftmo_2_step", "topstep_combine", "custom"]:
        preset = _resolve_preset(preset_id, request.account_state.initial_capital)
        rules = self._evaluate_rules(request, preset)
        consistency = self._consistency(request, preset)
        first_breach = _first_breach_rule(rules, consistency)
        results.append(ChallengeSimulationResult(
            preset_id=preset_id,
            first_breach_rule=first_breach,
            daily_loss_usage_pct=...,
            max_loss_usage_pct=...,
        ))
    return results
```

Esto da al usuario visibilidad de **"¿qué pasa con esta cuenta si la migro a otro prop firm?"**.

### 24.8 Tests Críticos

- `test_tier4_blocks_on_funding_suitability_block`
- `test_tier4_blocks_on_no_stop`
- `test_tier3_micro_on_weakest_link_dqs`
- `test_tier2_reduces_on_conflict`
- `test_tier1_allows_with_penalties`
- `test_kelly_negative_returns_zero`
- `test_kelly_caps_at_quarter`

---

## 25. BingX Risk Desk — 8 Guardrails + Idempotency + Audit

### 25.1 Funcionalidad

Risk Desk **pre-trade** que autoriza o bloquea cada `OrderIntent` con 8 guardrails independientes. Stateful (tracking de PnL, posiciones, kill switch), con **idempotency** vía SHA-256 y **audit append-only**.

### 25.2 Los 8 Guardrails (en orden de evaluación)

```python
def authorize_intent(intent, contract_metadata=None) -> RiskDeskDecision:
    idem_key = self.make_idempotency_key(intent)
    reason_codes = []

    # ── Gate 1: Kill switch (permanente) ───────────────────────────
    if self._state.kill_switch_engaged:
        return self._reject(intent, idem_key, ["risk_kill_switch_active"])

    # ── Gate 2: Daily loss cap ─────────────────────────────────────
    #       Si PnL_today <= -max_daily_loss_usdt
    if self._state.realized_pnl_today <= -abs(self._policy.max_daily_loss_usdt):
        reason_codes.append("risk_daily_loss_exceeded")

    # ── Gate 3: Total notional cap ─────────────────────────────────
    #       Suma de open_positions + intent.notional <= max
    projected_total = self._state.total_open_notional + intent.notional_usdt
    if projected_total > self._policy.max_position_notional_usdt:
        reason_codes.append("risk_position_cap_exceeded")

    # ── Gate 4: Max open positions ─────────────────────────────────
    if is_new_position and self._state.open_position_count >= self._policy.max_open_positions:
        reason_codes.append("risk_max_open_positions")

    # ── Gate 5: Per-symbol exposure cap ────────────────────────────
    if (existing_exposure + intent.notional_usdt) > self._policy.max_symbol_exposure_usdt:
        reason_codes.append("risk_symbol_exposure_exceeded")

    # ── Gate 6: Cooldown after loss ────────────────────────────────
    #       Si último loss hace menos de N minutos → block
    if self._state.last_loss_at is not None:
        elapsed_min = (now - self._state.last_loss_at).total_seconds() / 60.0
        if elapsed_min < self._policy.cooldown_after_loss_minutes:
            reason_codes.append("risk_cooldown_active")

    # ── Gate 7: Spread guard ───────────────────────────────────────
    if intent.spread_pct > self._policy.max_spread_pct:
        reason_codes.append("risk_spread_too_wide")

    # ── Gate 8a: L2 quality floor ──────────────────────────────────
    if intent.requires_l2 and intent.l2_quality_score < self._policy.min_l2_quality_score:
        reason_codes.append("risk_l2_quality_too_low")

    # ── Gate 8b: Provider health ────────────────────────────────────
    if self._policy.no_trade_when_provider_degraded and intent.provider_health != "ok":
        reason_codes.append("risk_provider_degraded")

    # ── Gate 9: Zone Validation (Mutual Exclusion) ─────────────────
    #   Si zone=ACUMULACION y side=SHORT → veto
    #   Si zone=DISTRIBUCION y side=LONG → veto
    if intent.price_zone == "ACUMULACION" and intent.position_side == "SHORT":
        reason_codes.append("risk_zone_veto_short")
    elif intent.price_zone == "DISTRIBUCION" and intent.position_side == "LONG":
        reason_codes.append("risk_zone_veto_long")

    # ── Gate 10: Margin Firewall (15% del available_margin) ───────
    if not intent.reduce_only and existing_exposure > 0:
        limit_margin = 0.15 * available_margin
        if projected_symbol >= limit_margin:
            reason_codes.append("risk_zone_long_full" o "risk_zone_short_full")

    if reason_codes:
        return self._reject(intent, idem_key, reason_codes)
    ...
```

### 25.3 Idempotency

```python
def make_idempotency_key(intent) -> str:
    raw = f"{intent.cycle_id}:{intent.venue_symbol}:{intent.side}:{intent.position_side}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
```

**Razonamiento:** El bot puede recibir la misma intent múltiples veces (retry de WS, reconexión). La key determinística garantiza que un fill ya procesado no se duplica.

### 25.4 Audit Trail

Cada `authorize`, `fill`, `kill_switch`, `reject` produce un `RiskDeskAuditEvent` con timestamp ISO-8601 UTC, evento, símbolo, payload completo. Se emite asincrónicamente al audit hook.

### 25.5 Política Configurable

```python
@dataclass(frozen=True)
class BingXRiskDeskPolicy:
    max_daily_loss_usdt: float = 3.0
    max_position_notional_usdt: float = 25.0
    max_open_positions: int = 3
    max_symbol_exposure_usdt: float = 12.0
    cooldown_after_loss_minutes: float = 15.0
    max_spread_pct: float = 0.005  # 0.5%
    min_l2_quality_score: float = 0.30
    no_trade_when_provider_degraded: bool = True
```

**Todos overridable por env vars** (PD-1 compliance: nada hardcodeado).

### 25.6 Integración con Funding

El Risk Desk es la **puerta final pre-exchange**. No es parte del cálculo del funding, pero es el **ejecutor** de las decisiones producidas por los motores de funding (FTMO Survival, Portfolio Risk, Scanner Funding Gate).

---

## 26. Scanner Funding Gate — Suitability + Reason Codes Estables

### 26.1 Funcionalidad

**Separa la tesis direccional del riesgo de funding**. Un candidato puede tener un score direccional alto, pero ser **funding-inadecuado** por overfit, weak edge, fuente light_proxy, etc.

### 26.2 Lógica de Suitability

```python
def evaluate_funding_suitability(
    backtest_evidence, source_tier, data_quality_score, conflict_score,
    daily_loss_usage_pct, stop_pct, remaining_risk_pct, consistency_ratio,
    lob_analysis_data_quality_score,
) -> dict:

    reasons = []
    size_multiplier = 1.0

    if not backtest_evidence:
        return {"suitability": "informational_only",
                "reason_codes": ["insufficient_backtest_evidence"],
                "size_multiplier": 1.0}

    grade = backtest_evidence["module_backtest_grade"]
    funding = backtest_evidence.get("funding_risk_metrics", {})
    survival = funding.get("funding_survival_grade")

    # ── Hard blockers ────────────────────────────────────────────
    if grade == "overfit_risk":
        return {"suitability": "block", "size_multiplier": 0.0,
                "reason_codes": ["overfit_module"]}
    if survival == "would_breach":
        return {"suitability": "block", "size_multiplier": 0.0,
                "reason_codes": ["funding_would_breach_in_history"]}
    if daily_loss_usage_pct >= 80.0:
        return {"suitability": "block", "size_multiplier": 0.0,
                "reason_codes": ["daily_loss_usage_high"]}
    if stop_pct > remaining_risk_pct:
        return {"suitability": "block", "size_multiplier": 0.0,
                "reason_codes": ["stop_exceeds_remaining_risk"]}

    # ── Reductions ───────────────────────────────────────────────
    if grade == "weak_edge":
        reasons.append("weak_edge_backtest")
        size_multiplier *= 0.5
    if survival == "at_risk":
        reasons.append("funding_at_risk_consistency")
        size_multiplier *= 0.5

    tier = (source_tier or "").lower()
    if tier == "light_proxy":
        reasons.append("light_proxy_only")
        size_multiplier *= 0.5
    elif tier == "snapshot_chain":
        reasons.append("snapshot_chain_only")
        size_multiplier *= 0.75

    if data_quality_score is not None and data_quality_score < 0.35:
        reasons.append("low_data_quality")
        size_multiplier *= 0.5

    if conflict_score is not None and conflict_score >= 0.5:
        reasons.append("conflicting_modules")
        size_multiplier *= 0.5

    if consistency_ratio is not None and consistency_ratio >= 0.35:
        reasons.append("consistency_cap_risk")
        size_multiplier *= 0.5

    # L2 quality (degrade-only)
    if (lob_analysis_data_quality_score is not None
        and lob_analysis_data_quality_score < 0.4):
        reasons.append("low_l2_quality")
        size_multiplier *= 0.5

    suitability = "allow" if not reasons else "size_down"
    return {"suitability": suitability,
            "reason_codes": reasons,
            "size_multiplier": round(max(0.0, min(1.0, size_multiplier)), 4)}
```

### 26.3 Reason Codes Estables (catalogo completo)

```
overfit_module
insufficient_backtest_evidence
weak_edge_backtest
funding_would_breach_in_history
funding_at_risk_consistency
weak_source_tier
light_proxy_only
snapshot_chain_only
low_data_quality
conflicting_modules
daily_loss_usage_high
stop_exceeds_remaining_risk
consistency_cap_risk
low_l2_quality
scanner_unavailable
scanner_score_too_low
scanner_trend_misaligned
scanner_intraday_not_aligned
scanner_daily_opposes
scanner_phase_b_missing
scanner_veto_present
scanner_confidence_too_low
```

### 26.4 Evaluaciones Compuestas

**Por símbolo (`evaluate_funding_suitability`):** un solo backtest, evaluación agregada.

**Por módulo (`evaluate_module_evidence`):** útil cuando múltiples módulos generan el mismo símbolo con diferentes backtests (e.g., técnico vs predictivo). El módulo con peor `data_quality_score` es el `weakest_link`.

**Confirmación de Scanner (`evaluate_scanner_confirmation`):** verifica que la fila del scanner cumple con los filtros FTMO del "Doble Llave": trend técnico en 5m/15m/1h alineado, score mínimo, sin vetoes.

### 26.5 Integración con Funding

Es la **primera línea de filtrado** por símbolo. Su output (`suitability ∈ {allow, size_down, block}`) alimenta directamente al Portfolio Risk Service (Tier 2 y Tier 4).

---

## 27. Intraday Outcomes (Funding Lab)

### 27.1 Funcionalidad

Calcula **outcomes intraday** (1h, 4h, EOD) para cada predicción del Meta-Learner. Métricas:

- `outcome_return_1h` / `4h` / `eod`: retorno direccional
- `sharpe_intraday_*`: Sharpe anualizado
- `profit_factor_eod`: gross_profit / |gross_loss|
- `max_drawdown_eod`: drawdown máximo intraday
- `bars_held_*_exit`: barras sostenidas hasta exit

### 27.2 Lógica

**EOD execution time** (ventana regulatoria): 15:45 NY time.

```python
EOD_EXECUTION_TIME = time(15, 45)  # 3:45 PM NY

def _eod_execution_datetime(pred_time, ny_tz) -> datetime:
    return datetime.combine(pred_time.date(), EOD_EXECUTION_TIME, tzinfo=ny_tz)
```

**Retorno direccional (ventana):**

$$
R_{\text{window}} = \mathrm{dir\_mult} \cdot \frac{P_{\text{exit}} - P_{\text{entry}}}{P_{\text{entry}}}
$$

donde $\mathrm{dir\_mult} = +1$ (long) o $-1$ (short), y $P_{\text{exit}}$ es:
- Para `1h`/`4h`: máximo favorable (`high` si long, `low` si short) — **best case**
- Para `eod`: `close` al momento de execution

**Sharpe intraday:**

$$
\mathrm{Sharpe}_{\text{intra}} = \frac{\bar{r}}{\sigma_r} \quad \text{sobre los retornos close-a-close de la ventana}
$$

**Profit Factor EOD:**

$$
\mathrm{PF}_{\text{eod}} = \frac{\sum_{r_i > 0} r_i}{|\sum_{r_i < 0} r_i|}
$$

**Max Drawdown EOD:**

$$
\mathrm{MaxDD} = \min_t \frac{P_t - \max_{s \le t} P_s}{\max_{s \le t} P_s}
$$

### 27.3 Persistencia

Se almacena en SQLite (`backend/data/predictions.db`, tabla `intraday_outcomes`) con `prediction_id` como PK, upsert atómico con `ON CONFLICT(prediction_id) DO UPDATE`.

### 27.4 Integración con Funding

Las métricas intraday alimentan el **calibration loop** del Meta-Learner (side meta confirmation). Si la predicción tiene `sharpe_eod < 0`, el modelo recalibra; si `profit_factor < 1.0`, se reduce el peso del módulo.

---

## 28. FTMO Simulation Service — Backtest Determinístico

### 28.1 Funcionalidad

Simula la **ejecución histórica** de intents aprobados por el Playbook contra `ftmo_provider_snapshots` (barras OHLC persistidas). Reproduce slippage, fills, PnL. **Nunca toca el broker real**.

### 28.2 Slippage Model

```python
def _slippage_pct(symbol: str) -> float:
    if symbol in {"XAUUSD", "XAGUSD", "US100.CASH"}:
        return 0.0005  # 0.05% — FX/major indices
    return 0.0002  # 0.02% — default
```

**Aplicación en fills:**

```python
def _entry_fill(side, entry, slippage):
    return round(entry * (1.0 + slippage if side == "LONG" else 1.0 - slippage), 6)

def _exit_fill(side, price, slippage, exit_reason):
    return round(price * (1.0 - slippage if side == "LONG" else 1.0 + slippage), 6)
```

Lógica: slippage siempre en contra del trader (entry +spread, exit -spread).

### 28.3 Stop/Target Detection

```python
def _stop_hit(side, stop, low, high) -> bool:
    """Para LONG: el low de la barra toca o perfora el stop."""
    return low <= stop if side == "LONG" else high >= stop

def _target_hit(side, target, low, high) -> bool:
    return high >= target if side == "LONG" else low <= target
```

**Prioridad:** Stop antes que target (peor caso, conservador).

### 28.4 PnL Calculation

```python
def _pnl(order) -> float:
    entry = order["entry_fill_price"]
    exit_p = order["exit_fill_price"]
    size = order["size_units"]
    if order["side"] == "SHORT":
        return (entry - exit_p) * size
    return (exit_p - entry) * size
```

### 28.5 Validation Thresholds

```python
SIM_MIN_CLOSED_TRADES = 20
SIM_MIN_CALENDAR_DAYS = 10
SIM_MIN_TRADING_DAYS = 4
SIM_MIN_PROFIT_FACTOR = 1.15
SIM_MAX_DRAWDOWN_PCT = 4.0
SIM_MAX_DAILY_USAGE_PCT = 80.0
SIM_MAX_LOSS_USAGE_PCT = 80.0
```

### 28.6 Validation Status Logic

```python
def _validation_status(summary, audit_chain):
    blockers = []
    if not audit_chain["ok"]:
        blockers.append("sim_audit_chain_broken")
    if summary["breach_counts"]["daily_loss"]:
        blockers.append("daily_loss_breach")
    if summary["breach_counts"]["max_loss"]:
        blockers.append("max_loss_breach")
    if summary["max_drawdown_pct"] > 4.0:
        blockers.append("sim_drawdown_exceeded")
    if summary["max_daily_loss_usage_pct"] >= 80.0:
        blockers.append("sim_daily_usage_high")
    if summary["max_loss_usage_pct"] >= 80.0:
        blockers.append("sim_max_loss_usage_high")

    if blockers:
        return SIM_FAILED, blockers
    if summary["open_trades"] > 0:
        return SIM_OPEN, []
    # Validation gaps (trades insuficientes)
    if summary["closed_trades"] < 20: gaps.append("sim_min_trades_missing")
    if summary["calendar_days"] < 10: gaps.append("sim_min_days_missing")
    if summary["trading_days"] < 4: gaps.append("sim_min_trading_days_missing")
    if (summary["profit_factor"] or 0) < 1.15: gaps.append("sim_profit_factor_low")
    return (SIM_OBSERVE if summary["closed_trades"] == 0 else SIM_CLOSED), gaps
```

### 28.7 Integración con Funding

Es el **backtest institucional obligatorio** antes de activar una estrategia. Su output `SIM_VALIDATED` es condición necesaria para mover de paper trading a live con capital real.

---

## 29. FTMO Playbook Service — Estado Manual + Audit Hash-Chain

### 29.1 Funcionalidad

**Estado manual** de la cuenta de fondeo: capital, PnL realized/unrealized, trade history, risk budget. **Audit chain** con hash encadenado (cada evento referencia el hash del anterior). Permite reconstruir exactamente la historia de decisiones.

### 29.2 Estado Default

```python
def default_playbook_state() -> dict:
    return {
        "profile_id": "ftmo_2_step_standard",
        "phase": "challenge",  # challenge | verification | funded
        "initial_capital": 100_000.0,
        "current_equity": 100_000.0,
        "start_of_day_balance": 100_000.0,
        "realized_daily_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "commissions": 0.0,
        "swaps": 0.0,
        "risk_budget_per_trade_pct": 0.50,
        "trade_history": [],
    }
```

### 29.3 Métricas Computadas

```python
def compute_ftmo_playbook_metrics(state):
    initial = state["initial_capital"]
    effective_equity = current_equity + unrealized - commissions - swaps

    daily_loss_amount = initial * 0.05     # 5%
    max_loss_amount  = initial * 0.10      # 10%
    daily_floor = start_of_day - daily_loss_amount
    max_floor   = initial - max_loss_amount

    daily_used = max(0.0, start_of_day - effective_equity)
    max_used   = max(0.0, initial - effective_equity)

    return {
        "effective_equity":        round(effective_equity, 2),
        "daily_loss_usage_pct":    round(daily_used/daily_loss_amount * 100, 2),
        "max_loss_usage_pct":      round(max_used/max_loss_amount * 100, 2),
        "remaining_daily_risk_pct": round(remaining_daily/initial*100, 4),
        "remaining_max_risk_pct":  round(remaining_max/initial*100, 4),
        "trading_days": len(trade_days),
        "best_day_contribution_pct": ...,
        "consistency_headroom_pct":  ...,
        "day_status":               "LOCKED" if breach_or_high else "OPEN",
        "blockers":                 [...],
    }
```

### 29.4 Trade Intent Evaluation

```python
async def evaluate_trade_intent(self, payload) -> dict:
    # 1. Check monitor
    if not monitor["ok"]:
        blockers.append("monitor_not_ok")
    if not monitor["source_ready"]:
        blockers.append("source_not_ready")
    if not monitor["production_ready"]:
        blockers.append("production_not_ready")

    # 2. Check day status
    if metrics["day_status"] == PLAYBOOK_LOCKED_DAY:
        blockers.extend(metrics["blockers"])

    # 3. Signal check
    signal_check = await self.funding_service.signal_check(symbol, side, account_state)
    if not signal_check["trade_ready"]:
        blockers.append("trade_not_ready")
    if signal_check["funding_survival"]["status"] != "SAFE":
        blockers.append("survival_not_safe")
    if signal_check["funding_survival"]["score"] < 70.0:
        blockers.append("survival_score_below_minimum")

    # 4. GEX validation
    gex = signal_check["readiness"]["gex_validation"]
    if gex and not gex_ready(gex):
        blockers.extend(gex.get("blockers", []))

    # 5. Stop distance & sizing
    stop_distance = entry - stop (LONG) or stop - entry (SHORT)
    if stop_distance <= 0: blockers.append("invalid_entry_stop")

    allowed_risk_pct = min(
        risk_budget_per_trade_pct,
        survival_recommended_risk_pct,
        remaining_daily_risk_pct,
        remaining_max_risk_pct,
    )
    position_size = allowed_risk_amount / stop_distance

    if blockers:
        decision = PLAYBOOK_BLOCK
    elif requested_risk > allowed_risk:
        decision = PLAYBOOK_REDUCE_SIZE
    else:
        decision = PLAYBOOK_READY
```

### 29.5 Journal Reconciliation

Cuando se registra un trade ejecutado, se concilia contra el intent:

```python
def _reconcile_journal(entry, intent):
    warnings = []
    if not intent: return {"reconciliation_status": "unlinked", ...}
    if intent["decision"] != PLAYBOOK_READY and entry["status"] == "executed":
        warnings.append("executed_blocked_intent")  # CRITICAL: se ejecutó un bloqueado
    if actual_risk > allowed_risk + 0.01:
        warnings.append("actual_risk_exceeded")
    if symbol mismatch: warnings.append("symbol_mismatch")
    return {"reconciliation_status": "warning" or "breach", "warnings": warnings}
```

### 29.6 Integración con Funding

Es el **nivel más alto de orquestación**: combina Monitor, Signal Check, Survival, GEX Validation, Playbook State en una decisión de trade completa con audit chain.

---

## 30. FTMO GEX Validation — Data Lineage Check

### 30.1 Funcionalidad

Verifica que el **snapshot de GEX** usado en un trade cumple con los requisitos de calidad/frescura del funding. Lee solo del SQLite persistido; **nunca** consulta APIs externas.

### 30.2 Lógica

**Símbolos con GEX directo requerido:** AAPL, GOOGL, TSLA
**Símbolos con proxy context:** XAUUSD → GLD, XAGUSD → SLV, US100.CASH → QQQ

```python
DIRECT_GEX_REQUIRED_SYMBOLS = {"AAPL", "GOOGL", "TSLA"}
PROXY_GEX_CONTEXT_BY_SYMBOL = {
    "XAUUSD": "GLD", "XAGUSD": "SLV", "US100.CASH": "QQQ",
}
```

**Blockers:**

```python
def load_ftmo_gex_validation(db_path, canonical_symbol, *, now=None, freshness_hours=24, min_quality=0.65):
    direct_required = symbol in DIRECT_GEX_REQUIRED_SYMBOLS
    proxy_required  = symbol in PROXY_GEX_CONTEXT_BY_SYMBOL

    snapshot = _latest_snapshot(db_path, gex_symbol or symbol)

    if not snapshot:
        blockers.append("gex_full_chain_missing" if direct_required else "gex_proxy_context_missing")
    if snapshot and snapshot["source_tier"] != "full_chain_gex":
        blockers.append("gex_source_not_validated")
    if _is_stale(snapshot["as_of"], now, freshness_hours):
        blockers.append("gex_snapshot_stale" if direct_required else "gex_proxy_context_stale")
    if not snapshot or snapshot["data_quality_score"] < min_quality:
        blockers.append("gex_quality_low" if direct_required else "gex_proxy_context_low_quality")

    return {
        "gex_validated":    direct_required and not blockers,
        "gex_context_ready": proxy_required and not blockers,
        "blockers":         blockers,
    }
```

### 30.3 Integración con Funding

Es un **gate adicional** en el Playbook. Si el símbolo requiere GEX directo y el snapshot está stale o tiene `quality < 0.65`, el trade se bloquea aunque todos los demás checks pasen.

---

# PARTE VI — SÍNTESIS Y MATRIZ DE DECISIÓN

## 31. Matriz de Composición: Cómo los Motores Alimentan el Funding

### 31.1 Pipeline Completo (Fase → Funding)

```
FASE A: Scanner (300 → candidatos)
  ↓
FASE B: Microestructura (VPIN, OFI, SMC, CVD)
  ├── Enriquecen MarketSnapshot
  ├── Alimentan funding_lab_side_meta_learner
  └── Conflicto Scanner↔Microestructura → conflict_score
  ↓
FASE C: Opciones (8 motores) + Predictivo (Multimodal + QuantumAlpha + Sentiment + CNN + Cross-asset)
  ├── Score compuesto (40% métricas + 60% motores)
  ├── gex_data → calculate_probabilistic_gex_gating
  ├── conviction ∈ [0, 1] + bias ∈ {LONG, SHORT, CASH}
  ├── backtest_evidence (módulo específico)
  └── Inyecta a: Scanner Funding Gate + Portfolio Risk
  ↓
FASE D: Real-time monitor
  ├── Tick-by-tick
  ├── Detect anomalías en vivo
  └── Update intraday outcomes
  ↓
CAPA 5 (FUNDING LAYER):
  ┌────────────────────────────────────────┐
  │ 1. Scanner Funding Gate                │
  │    → suitability ∈ {allow,size,block}  │
  │ 2. FTMO Survival Score                 │
  │    → status ∈ {SAFE,MONITOR,AT_RISK,   │
  │                WOULD_BREACH,INSUFF}    │
  │ 3. Portfolio Risk Service              │
  │    → tier ∈ {1,2,3,4}                 │
  │    → size_multiplier                   │
  │ 4. FTMO GEX Validation                 │
  │    → gex_validated                     │
  │ 5. FTMO Playbook                       │
  │    → decision ∈ {READY, BLOCK, REDUCE} │
  │ 6. FTMO Simulation (background)        │
  │    → status ∈ {VALIDATED, FAILED}      │
  └────────────────────────────────────────┘
  ↓
BingX Risk Desk (8 guardrails)
  ↓
EXCHANGE
```

### 31.2 Tabla Maestra de Pesos

| Motor / Señal | Peso en Survival | Peso en Portfolio Risk | Peso en Funding Gate |
|---------------|------------------|------------------------|----------------------|
| **Daily Loss Usage** | 30% | Tier 4 trigger | Hard block ≥80% |
| **Max Loss Usage** | 25% | Tier 4 trigger | Hard block ≥80% |
| **Consistency** | 20% | Tier 4 (blocked) | size_down ≥0.35 |
| **Backtest OOS Quality** | 15% | Tier 4 (overfit) | size_down (weak edge) |
| **Conflict Score** | 10% | Tier 2 (≥0.5) | size_down (≥0.5) |
| **Predictive Conviction** | (info) | Tier 1 influence | bias conflict |
| **GEX Regime** | (info) | penalty 0.5× | Hard block (data quality) |
| **L2 Quality** | (info) | penalty 0.5× <0.4 | size_down <0.4 |
| **Volatility Regime (VIX)** | (info) | ATR gate (Fase A) | Adapt thresholds |

### 31.3 Reglas de Conflict Resolution

Cuando dos motores discrepan:

1. **Hard rules > Soft rules** (block absoluto vs size_down).
2. **Live account state > Historical OOS** (el presente pesa más que el pasado).
3. **Funding survival > Scanner score** (sobrevivencia > oportunidad).
4. **Critical module > Non-critical module** (opciones_gex/technical > sentiment).
5. **Conservative > Aggressive** en empate (default: reducir size).

## 32. Pesos Recomendados y Conflict Resolution

### 32.1 Pesos por Régimen de Mercado

| Régimen | Survival | Conviction | Backtest OOS | Comentario |
|---------|----------|------------|--------------|------------|
| **Low VIX (< 15)** | 0.40 | 0.25 | 0.35 | Backtest pesa más; mercado estable |
| **Normal (15-30)** | 0.30 | 0.15 | 0.15 | Pesos balanceados |
| **High VIX (> 30)** | 0.50 | 0.10 | 0.10 | Supervivencia pesa más; caos |

### 32.2 Conflict Resolution Cascada

```python
def resolve_conflict(scanner_signal, predictive_signal, gex_data, survival):
    # 1. ¿Hay hard block?
    if survival["status"] in {"WOULD_BREACH", "INSUFFICIENT"}:
        return BLOCK

    # 2. ¿Conflicto direccional?
    scanner_dir = scanner_signal["direction"]
    pred_dir = predictive_signal["bias"]
    if opposite_dirs(scanner_dir, pred_dir):
        conflict_score = 0.7
        # Reduce size 50%
    else:
        conflict_score = 0.0

    # 3. ¿GEX adverso?
    if gex_data["regime"] in {"GAMMA_NEGATIVE", "AT_FLIP"}:
        # Reduce 50%, independent of direction
        size_mult *= 0.5

    # 4. Survival decide si permite sobrevivir
    if survival["score"] < 50:
        return BLOCK
    if survival["score"] < 70:
        return SIZE_DOWN
    return ALLOW
```

## 33. Hoja de Ruta de Integración

### 33.1 Estado Actual

| Componente | Estado | Ubicación |
|-----------|--------|-----------|
| `ftmo_survival_score` | ✅ Completo | `backend/services/ftmo_survival_score.py` |
| `scanner_funding_gate` | ✅ Completo | `backend/services/scanner_funding_gate.py` |
| `funding_lab_side_meta_learner` | ✅ Completo | `backend/services/funding_lab_side_meta_learner.py` |
| `portfolio_risk_service` | ✅ Completo | `backend/services/portfolio_risk_service.py` |
| `bingx_risk_desk` | ✅ Completo | `backend/services/bingx_risk_desk.py` |
| `funding_lab` (intraday) | ✅ Completo | `backend/services/funding_lab.py` |
| `ftmo_simulation_service` | ✅ Completo | `backend/services/ftmo_simulation_service.py` |
| `ftmo_playbook_service` | ✅ Completo | `backend/services/ftmo_playbook_service.py` |
| `ftmo_gex_validation` | ✅ Completo | `backend/services/ftmo_gex_validation.py` |
| `multimodal_predictive` | ✅ Completo | `backend/quant_engine/engines/predictive/multimodal_predictive.py` |
| `quantum_alpha` | ✅ Completo | `backend/quant_engine/engines/predictive/quantum_alpha.py` |
| `gamma_flip` | ✅ Completo | `backend/quant_engine/engines/options/gamma_flip.py` |
| VPIN from trades | ✅ Completo | `backend/layer_2_quant_engine/math_core/vpin_from_trades.py` |
| GEX/DEX/0DTE/Squeeze/Shadow Delta/DWF | ✅ Completos | `backend/quant_engine/engines/options/*.py` |
| Sentiment + CNN + Cross-asset | ✅ Completos | `backend/quant_engine/engines/predictive/*.py` |

### 33.2 Próximos Pasos Recomendados

1. **Tests de integración** `tests/integration/test_funding_pipeline.py`:
   - Pipeline completo: Scanner → Microestructura → Opciones → Funding → Risk Desk
   - Validar que `candidate.decision = BLOCK` se respeta
   - Validar que `survival.status = WOULD_BREACH` previene entry

2. **Backtest OOS automático** mensual:
   - `tasks/monthly_retrain.py` ya existe; ampliar para Funding Lab
   - Validar cada motor en ventana OOS de 90 días

3. **Dashboard de Funding Metrics** en frontend:
   - Visualización de Survival Score en tiempo real
   - Heatmap de consistency por día
   - Simulator de escenarios "what-if"

4. **Calibration Loop** (Meta-Learner):
   - Side meta confirmation debe aprender de outcomes
   - Si 70% de los `PASS` resultan en profit → subir thresholds
   - Si < 50% → bajar thresholds o re-entrenar

5. **Multi-Account Orchestrator**:
   - Coordinar N cuentas de fondeo simultáneas
   - Diversificación de correlación entre cuentas

6. **Funding Audit Trail Compliance**:
   - Trazabilidad completa para auditorías regulatorias
   - Export de reporte en formatos estándar (JSON, Markdown, PDF)

### 33.3 Decisiones de Arquitectura Pendientes

| ADR Pendiente | Opciones | Recomendación |
|---------------|----------|---------------|
| Almacenamiento de audit chain | SQLite vs PostgreSQL | **SQLite local + sync periódico a PG** (resilencia) |
| Recalibración de Meta-Learner | Online vs Batch | **Batch mensual** (estabilidad, menos overfitting) |
| Multi-cuenta | Secuencial vs Paralelo | **Paralelo con httpx.AsyncClient** (latencia) |
| Failover de Funding Service | No failover vs Hot-standby | **No failover** (Funding Lab es no-crítico para trading) |

---

## ANEXO A — Resumen Ejecutivo de Motores

| ID | Motor | Tipo | Función | Output clave |
|----|-------|------|---------|--------------|
| VPIN | Vol-Sync Prob Informed | Técnico | Toxicidad del flujo | `vpin ∈ [0, 1]` |
| OFI | Order Flow Imbalance | Técnico | Presión compradora/vendedora | `z_ofi ∈ [-3, +3]` |
| CVD | Cumulative Vol Delta | Técnico | Presión acumulada | `cvd ∈ ℝ` |
| SMC | Smart Money Concepts | Técnico | Estructura institucional | `events[]` (BOS, CHoCH, OB, FVG) |
| GEX | Gamma Exposure | Opciones | Régimen vol (pos/neg/flip) | `regime`, `flip_point` |
| GFlip-P | Gamma Flip Probability | Opciones | P(tocar ZGL pre-exp) | `p_flip ∈ [0, 1]` |
| DEX | Delta Exposure | Opciones | Delta neto MM | `delta_net`, `trap_score` |
| MaxPain | Magnetismo vencimiento | Opciones | Strike de menor dolor | `K_mp` |
| 0DTE | Zero-Day Engine | Opciones | Pinning/Cascade/Vacuum | `state` |
| ShadowDelta | Gap delta | Opciones | Divergencia delta | `shadow_score` |
| Squeeze | Squeeze Detection | Opciones | Compresión→expansión | `p_squeeze ∈ [0, 1]` |
| DWF | Delta-Weighted Flow | Opciones | Flujo institucional | `dwf_norm ∈ [-1, 1]` |
| Multimodal | Conv-LSTM Events | Predictivo | Bias + conviction | `bias`, `conviction` |
| Quantum | LSTM+Attn | Predictivo | Clasificación OHLCV | `signal` |
| Sentiment | NLP sentiment | Predictivo | Score agregado | `s ∈ [-1, 1]` |
| CNN-FG | CNN Fear/Greed | Predictivo | Régimen de mercado | `regime ∈ {fear, neutral, greed}` |
| XAsset | Cross-asset corr | Predictivo | Crisis detection | `crisis_index` |
| MetaLrn | Side confirmation | Predictivo | Heurística long/short | `PASS/FAIL` |
| FTMO-Surv | Survival Score | Funding | Runway agregado | `status`, `score`, `risk_pct` |
| PortRisk | 4-Tier Ladder | Funding | Sizing binding | `tier`, `size_mult` |
| BingX-RD | 8 Guardrails | Funding | Pre-trade authorize | `authorized`, `reasons` |
| ScanGate | Funding Gate | Funding | Suitability per-symbol | `suitability`, `mult` |
| Intraday | Outcomes intraday | Funding | Métricas 1h/4h/EOD | `sharpe`, `pf`, `maxdd` |
| Sim | FTMO Simulation | Funding | Backtest institucional | `status`, `blockers` |
| Playbook | Manual state | Funding | Orquestación intent | `decision`, `blockers` |
| GEX-Val | Data lineage | Funding | Freshness/quality | `gex_validated`, `blockers` |

---

## ANEXO B — Constantes FTMO 2-Step (referencia rápida)

```python
FTMO_PROFILE_ID              = "ftmo_2_step_standard"
FTMO_TIMEZONE                = "Europe/Prague"
FTMO_INITIAL_CAPITAL         = 100_000.0
DAILY_LOSS_LIMIT_PCT         = 5.0     # -$5,000/day
MAX_LOSS_LIMIT_PCT           = 10.0    # -$10,000 total
CHALLENGE_PROFIT_TARGET_PCT  = 10.0    # +$10,000 para pasar challenge
VERIFICATION_PROFIT_TARGET_PCT = 5.0   # +$5,000 para verification
MIN_TRADING_DAYS             = 4
CONSISTENCY_WARNING_RATIO    = 0.35    # best day < 35% of total profit
CONSISTENCY_BLOCK_RATIO      = 0.50    # best day < 50% of total profit
DEFAULT_RISK_PER_TRADE_PCT   = 0.50    # 0.5% per trade base
```

**Comparativa con otros presets:**

| Preset | Daily Loss | Max Loss | Profit Target | Drawdown Type |
|--------|-----------|----------|---------------|---------------|
| `ftmo_2_step` | 5% | 10% | 10% | static |
| `ftmo_1_step` | 3% | 10% | 10% | trailing_eod |
| `topstep_combine` | 2% ($2K) | $2K | 6% | trailing_intraday |
| `custom` | 5% | 10% | — | static |

---

## ANEXO C — Catálogo de Reason Codes (referencia para UI/Dashboards)

```
SURVIVAL STATUS REASONS:
  daily_loss_breach
  daily_loss_usage_high
  max_loss_breach
  max_loss_usage_high
  best_day_concentration
  consistency_warning
  historical_would_breach
  historical_at_risk
  insufficient_data
  missing_backtest_evidence

PORTFOLIO RISK TIER 4 REASONS:
  funding_suitability_block
  rules_breached_or_locked
  no_daily_risk_budget
  consistency_blocked
  daily_loss_usage_over_80
  no_stop_on_candidate
  stop_exceeds_remaining_daily_risk
  overfit_module
  critical_module_block

PORTFOLIO RISK TIER 3 REASONS:
  consistency_warning
  weakest_link_low_dqs
  daily_loss_usage_over_60
  weak_edge_low_scanner_size

PORTFOLIO RISK TIER 2 REASONS:
  scanner_funding_size_down
  non_critical_module_size_down
  scanner_recommended_low
  conflict_score_elevated
  tail_risk_elevated

SIGNAL PENALTIES:
  tail_risk_elevated
  jump_risk_elevated
  conflict_score_critical
  conflict_score_elevated
  gamma_regime_adverse
  iv_backwardation
  squeeze_probability_high
  light_proxy_source
  snapshot_chain_source
  low_options_gex_quality

SCANNER FUNDING GATE REASONS:
  overfit_module
  insufficient_backtest_evidence
  weak_edge_backtest
  funding_would_breach_in_history
  funding_at_risk_consistency
  weak_source_tier
  light_proxy_only
  snapshot_chain_only
  low_data_quality
  conflicting_modules
  daily_loss_usage_high
  stop_exceeds_remaining_risk
  consistency_cap_risk
  low_l2_quality

BINGX RISK DESK REASONS:
  risk_kill_switch_active
  risk_daily_loss_exceeded
  risk_position_cap_exceeded
  risk_max_open_positions
  risk_symbol_exposure_exceeded
  risk_cooldown_active
  risk_spread_too_wide
  risk_l2_quality_missing
  risk_l2_quality_too_low
  risk_provider_degraded
  risk_precision_invalid
  risk_below_min_notional
  risk_below_min_qty
  risk_zone_veto_short
  risk_zone_veto_long
  risk_zone_long_full
  risk_zone_short_full

FTMO SIMULATION REASONS:
  sim_audit_chain_broken
  daily_loss_breach
  max_loss_breach
  sim_drawdown_exceeded
  sim_daily_usage_high
  sim_max_loss_usage_high
  sim_min_trades_missing
  sim_min_days_missing
  sim_min_trading_days_missing
  sim_profit_factor_low
  sim_intent_missing
  sim_intent_not_playbook_ready
  sim_market_data_missing
  sim_size_missing

FTMO GEX VALIDATION REASONS:
  gex_full_chain_missing
  gex_source_not_validated
  gex_snapshot_stale
  gex_quality_low
  gex_proxy_context_missing
  gex_proxy_context_stale
  gex_proxy_context_low_quality

META-LEARNER REASONS:
  side_meta_invalid_direction
  side_meta_feature_missing
  side_meta_trend_score_too_low
  side_meta_trend_score_too_high
  side_meta_volume_score_too_low
  side_meta_overextended
  side_meta_structure_not_bearish
  side_meta_vwap_not_bearish
  side_meta_volatility_regime_uncontrolled
```

---

> **Final del Expediente.**
>
> Este documento describe el estado actual y las matemáticas detrás de **cada motor** que el Módulo de Funding compone. Para el blueprint de arquitectura, ver `docs/FUNDING_MODULE.md`. Para el plan de implementación por fases, ver `PROJECT_CONFIG.md` → "Hoja de Ruta de Integración".
>
> **Próximos pasos inmediatos:**
> 1. Implementar `tests/integration/test_funding_pipeline.py` para validar el flujo end-to-end
> 2. Configurar las variables de entorno `QA_FTMO_*` en `.env.example`
> 3. Crear dashboard en frontend que visualice `survival.status`, `consistency`, y `expected_risk_per_trade`
> 4. Programar backtest mensual en `tasks/monthly_retrain.py` para re-calibrar Meta-Learner
