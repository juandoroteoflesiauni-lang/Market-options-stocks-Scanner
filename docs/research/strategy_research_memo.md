# Strategy Research Memo — Fase 2 (literatura + lógica financiera)

> **Fecha:** 2026-06-17 · **Rama:** `sec/hardening-gitleaks-gemini`
> **Insumos:** `engine_configuration_audit.md` (Fase 1), `performance_baseline_20260617.md` (Fase 3),
> EOD 20260617, literatura citada inline. **Objetivo:** priorizar palancas de alpha risk-adjusted
> sin sobreajuste, y dar una matriz régimen×config para los perfiles verification/profit.

---

## 0. Tesis central

El sistema **no sufre de falta de motores** (hay ~165), sino de tres patologías clásicas de sistemas
multi-motor sobre-ingenierizados: (1) **medición rota** → no se puede separar señal de ruido
(López de Prado, *Advances in Financial ML*, cap. 11–12: "sin etiquetas y sin PnL atribuible no hay
ML financiero"); (2) **confluencia mal ponderada** → motores correlacionados se cuentan como
independientes, inflando falsa confianza; (3) **gates desactivados en verification** → el sistema
opera sin las defensas que justifican su complejidad. Las palancas de mayor alpha son, en este orden,
**arreglar medición → endurecer entradas → ponderar confluencia por régimen** — no añadir motores.

---

## 1. Confluencia multi-motor sin overfitting

**Problema actual:** BingX pondera 6 módulos (venue/tech/opt/pred/l2/risk) con pesos fijos y, dentro
de `technical`, 16–19 motores con una matriz de pesos estática que **suma votos como si fueran
independientes**. Pero `ofi`, `order_flow_delta`, `delta_volume`, `vpoc` y `volume_profile` miden
esencialmente el **mismo factor** (order-flow/volumen) → multicolinealidad que infla la convicción.

**Literatura aplicable:**
- **Bayesian Model Averaging** (Hoeting et al., 1999): ponderar modelos por su probabilidad posterior,
  no por pesos a mano. En trading: ponderar cada motor por su *edge histórico condicional al régimen*.
- **Black-Litterman** (1992): combinar un prior (consenso de mercado/régimen) con "views" (motores),
  ponderando cada view por su confianza → reduce el peso de motores ruidosos automáticamente.
- **De-correlación de señales** (López de Prado, *clustering de features*): agrupar motores por
  factor latente (order-flow, structure, volatility, options-flow) y ponderar **por clúster**, no por
  motor individual. Esto evita que 5 motores de volumen dominen.

**Recomendación:** migrar de pesos estáticos a **pesos por clúster de factor** + **regime-conditional**
(la infraestructura ya existe: `regime_adaptation_enabled=True`, `RegimeWeightingEngine`). El
meta-learner debe **calibrar** (Platt/isotónico — ya hay `alpaca_r1_engine_calibrators.joblib`) y
**vetar/size-down**, nunca generar dirección desde un solo motor (el engine ya respeta esto con
`INSUFFICIENT_DATA` <2 motores — correcto).

---

## 2. Indicadores clásicos refaccionados — umbrales institucionales

| Indicador | Valor actual (`PhaseAWeights`) | Estándar institucional | Comentario |
|---|---|---|---|
| RSI extremos | oversold 15 / overbought 85 | 25–30 / 70–75 (Wilder); 20/80 agresivo | **15/85 deja pasar casi todo** → gate inerte. Subir a 25/75 para que filtre. |
| ATR gate | min 0.3% / max 5% | 0.5%–4% típico intradía | Banda inferior baja deja entrar activos sin movimiento; subir min a 0.5%. |
| EMA cluster | (9,21,50,200), min_aligned 3 | igual | Razonable; mantener. |
| VWAP z-score | max 3.0 | 2.0–2.5 (mean-reversion) | 3σ es muy permisivo para entradas; 2.0 para fade. |
| MACD | histograma > 0 (binario) | pendiente + cruce + divergencia | El uso binario pierde información; ponderar por magnitud/pendiente. |
| SuperTrend | period 10, mult 3.0 | 10/3 estándar (ATR) | OK. |

**Lógica:** en verification los umbrales clásicos se complementan con `prob_floor=0.35` y bullish
relajado, anulando su efecto (Fase 3 H4). El edge de los indicadores clásicos **solo aparece si los
gates están activos**; relajarlos para "recolectar datos" genera datos de trades sin edge → ruido que
contamina la calibración (López de Prado: *garbage-in en labels = meta-learner inútil*, ver H11).

---

## 3. Microestructura (VPIN/OFI) — veto vs score

**VPIN** (Easley, López de Prado, O'Hara, 2012, *Flow Toxicity and Liquidity in a High-Frequency
World*): mide probabilidad de información adversa (order-flow toxicity). Hallazgos del paper: VPIN
spikes **preceden** eventos de iliquidez (ej. Flash Crash 2010).

**Aplicación recomendada:**
- VPIN/OFI como **score** en condiciones normales (peso en Phase B 0.45/0.20).
- VPIN como **veto duro** solo en cola: el `predictive_risk_gate` ya implementa `vpin_percentile>0.95
  → BLOCK` y `>0.70 → size-down` — **correcto en diseño, pero está desconectado del hot-path** (H2).
- **Recomendación:** cablear el umbral de veto VPIN p>0.95 al hot-path BingX/Alpaca (no solo
  `funding_orchestrator`). Es una defensa barata de alto valor en colas.

**Caveat empírico (Fase 3 §5):** los stock-perp sintéticos `NCSK*USD` devuelven `l2_unavailable:
empty_book` → VPIN/OFI/L2 no tienen datos para gran parte del universo BingX. **Sin libro L2, la
microestructura no aporta** — priorizar universo con L2 real o degradar explícitamente.

---

## 4. Opciones como señal direccional (GEX, gamma flip, flow toxicity, tail risk)

**Marco** (Squeezemetrics *GEX White Paper*; Nomura/McElligott sobre dealer gamma): en régimen de
**gamma negativo**, los dealers amplifican el movimiento (pro-cíclico) → mayor volatilidad y riesgo de
trend; en **gamma positivo**, lo amortiguan (mean-reversion). El gamma flip es el nivel de cambio.

**Lo que el sistema ya hace bien (BingX `decide`):**
- Veto LONG en `is_gamma_negative_regime` (correcto: no comprar en gamma negativo).
- Guardrail gamma flip (spot vs flip contradice dirección → `options_contradicts`).
- Veto `shadow_delta_imbalance>±0.8`, `tail_risk CRITICAL`, DEX/ZGL invalidation, NDDE.

**Riesgos / calibración:**
- Los umbrales (`OPTIONS_RELAXED_TAIL_RISK_THRESHOLD=0.92`, `GAMMA_PRESSURE=0.88`) son altos → vetan
  solo en extremos. En profit deberían bajar (más selectivo). Sugerido profit: tail 0.85, gamma 0.80.
- **Flow toxicity** como veto direccional: bien para opciones; en perps usar con cuidado (el flujo de
  opciones del subyacente no siempre transfiere al perp 1:1).
- El stack de opciones para **dirección** (no solo riesgo) está infrautilizado: `OPTIONS_R1=0 trades`
  (H7). Diagnosticar antes de re-ponderar.

---

## 5. Predictive stack — prioridad, confianza mínima, veto vs size-down

**Estado (Fase 1 H1/H2/H10 + Fase 3 §5/§7):** la "predicción institucional" está **triplemente
comprometida**: (a) el bridge solo cablea la heurística equity (cascada meta_signal/pred_opt2/thesis
NO conectada); (b) el `PredictiveRiskGate` no está en el hot-path; (c) hay un blend ML oculto
(0.20–0.30 de peso) sin gobierno. Y el meta-learner es **sintético con accuracy 0.0–0.45** (peor que
azar en varios símbolos).

**Principios (López de Prado; Bailey & López de Prado, *Deflated Sharpe Ratio*, 2014):**
- Un modelo con accuracy <0.5 en out-of-sample **no debe pesar en la dirección** — solo, a lo sumo,
  size-down. El bloqueo de promoción sintética (`META_LEARNER_PROMOTE_SYNTHETIC=false`) es **correcto**.
- **Confianza mínima:** el floor BingX 0.35 (verification) es demasiado bajo para un modelo no
  validado. Profit 0.50 es más sano. Recomendado: **no usar predictive para dirección hasta tener
  outcomes reales etiquetados** (requiere reparar medición, Fase 3 §8).
- **Veto vs size-down:** predictive debe **size-down** cuando confianza < floor, y **vetar** solo
  cuando contradice fuertemente (lo que el `predictive_risk_gate` ya hace con multiplicadores Decimal).

**Recomendación:** (1) exponer el blend ML a env (`ML_BLEND_WEIGHT`, default 0.0 hasta validar);
(2) cablear el `PredictiveRiskGate` como **size-down only** en hot-path; (3) congelar el peso
`predictive` de BingX a su rol actual (heurística equity) y **no subirlo** hasta medir edge.

---

## 6. Sizing óptimo — Kelly fraccional, buying-power %, notional

**Kelly** (1956) maximiza crecimiento log-geométrico pero es **muy agresivo**; la práctica
institucional usa **½-Kelly o ¼-Kelly** (Thorp; MacLean/Ziemba) por incertidumbre de parámetros.

| Parámetro | Actual | Evaluación |
|---|---|---|
| `PROFIT_KELLY_FRACTION=0.25` | ¼-Kelly | **Correcto** institucionalmente. |
| `OPTIONS_KELLY_FRACTION=0.5` vs `MAX=0.25` | inconsistente (H8) | Corregir: fraction ≤ max. |
| Kelly OFF en verification | — | Correcto (no estimar Kelly con muestra ruidosa). |
| High-prob buying power 10–15% (thr 0.85) | — | Razonable, pero el thr 0.85 casi nunca se alcanza con prob=0.50+0.45·score (máx 0.95). |

**Caveat clave:** Kelly **requiere un estimador fiable de win-rate y payoff** — que hoy **no existe**
(Fase 3 §7). Aplicar Kelly sobre un PF rolling=null o sobre contabilidad contradictoria es
**peligroso** (sobre-apuesta sobre ruido). **Kelly debe permanecer OFF hasta reparar medición.**

---

## 7. Ejecución — TWAP/VWAP vs Implementation Shortfall medido

**Perold (1988), *The Implementation Shortfall*:** el coste real = (precio de decisión − precio de
ejecución) + coste de oportunidad. **Almgren-Chriss (2000):** trade-off óptimo entre impacto de
mercado (ejecutar rápido) y riesgo de timing (ejecutar lento).

**Estado:** la política Fase B (TWAP BingX, VWAP Alpaca Elite, price collar 0.75%, repeated limit) es
**arquitectónicamente correcta**, pero **no se puede calibrar** porque `trades_with_tca=0` / `IS=null`
(Fase 3 §3). El collar 0.75% (verification) → 0.5% (profit) es razonable a ciegas, pero óptimo
desconocido.

**Recomendación:** reparar el journaling TCA (prerequisito) y luego calibrar
`min_notional`/`collar`/`max_per_symbol` contra IS medido por ruta. Objetivo IS<15bps no es evaluable
hoy.

---

## 8. Régimen de mercado — HMM + macro fallback

`regime_adaptation_enabled=True` y existe `RegimeWeightingEngine` (VIX + SPY MA50/MA200) y
`markov_regime_engine`. **Pero** el `R2_HMM_BULLISH_ONLY` se desactiva en verification, y el regime
scalar de sizing (`SIZING_REGIME_SCALAR_*`) sí está activo. La adaptación por régimen es la palanca
con **mejor ratio impacto/riesgo de sobreajuste** porque condiciona pesos a un estado observable, en
vez de optimizar pesos absolutos (que sobreajusta).

---

## 9. TOP 10 PALANCAS DE ALPHA (impacto × facilidad)

| # | Palanca | Impacto | Facilidad | Prioridad | Hallazgo |
|---|---|---|---|---|---|
| 1 | **Reparar journaling/medición TCA** (decision_score, correlation_id, fills→journal) | Muy alto | Media | **P0** | H6, F3§3 |
| 2 | **Endurecer entrada Alpaca** (prob_floor 0.35→0.50, restaurar `_is_bullish`) | Alto | Alta | **P0** | H4 |
| 3 | **Aislar blend ML tras env-flag** (default 0.0 hasta validar) | Alto | Alta | **P0** | H1 |
| 4 | **Recalibrar caps risk desk BingX** a tamaño real del libro | Alto | Alta | **P1** | H5 |
| 5 | **Subir umbrales clásicos** (RSI 25/75, ATR min 0.5%, VWAP z 2.0) | Medio-alto | Alta | **P1** | §2 |
| 6 | **Cablear PredictiveRiskGate como size-down** en hot-path | Alto | Media | **P1** | H2, §3 |
| 7 | **Ponderación por clúster de factor + regime-conditional** | Alto | Baja | **P2** | §1 |
| 8 | **Diagnosticar/activar OPTIONS_R1** (0 trades) | Medio | Media | **P2** | H7 |
| 9 | **Activar Kelly ¼ solo tras medición fiable** (corregir H8) | Medio | Media | **P2** | §6, H8 |
| 10 | **Calibrar ejecución (collar/TWAP) contra IS medido** | Medio | Baja (dep. P0) | **P3** | §7 |

---

## 10. TOP 5 RIESGOS DE SOBREAJUSTE

1. **Calibrar sobre trades sin edge** (verification con gates off genera labels ruidosos) → el
   meta-learner aprende ruido. *Mitigación:* calibrar solo con trades que pasaron gates reales.
2. **Multiplicidad de pruebas** (Bailey & López de Prado): con 165 motores y muchos umbrales, el
   *Deflated Sharpe* castiga severamente. *Mitigación:* fijar hipótesis antes de optimizar; usar
   walk-forward, no in-sample.
3. **Pesos absolutos a mano** (matriz técnica de 19 motores) → sobreajuste a la muestra. *Mitigación:*
   pesos por clúster + regime-conditional.
4. **Optimizar contra una métrica contradictoria** (4 sistemas de PnL que no reconcilian) → se
   optimiza el artefacto de medición, no el alpha. *Mitigación:* fuente única de verdad (P0).
5. **Blend ML oculto** (H1) entrenado sobre datos sintéticos → falsa confianza. *Mitigación:*
   env-flag + validación out-of-sample con Deflated Sharpe antes de pesar.

---

## 11. MATRIZ RÉGIMEN × CONFIGURACIÓN RECOMENDADA

> Régimen detectado por VIX + SPY MA50/MA200 + `markov_regime`. Ajustes **relativos** al perfil profit
> (evitan sobreajuste: condicionan a estado observable, no optimizan valores absolutos).

| Régimen | Pesos módulo (BingX) | Entrada (gates) | Sizing | Opciones/Vetos |
|---|---|---|---|---|
| **Bull tendencial** (VIX<15, SPY>MA50>MA200) | ↑ technical, ↑ momentum; pred neutral | prob_floor base; confluencia 2 | Kelly ¼ pleno; regime_scalar 1.0 | gamma flip relajado; tail 0.90 |
| **Bear / risk-off** (VIX>25, SPY<MA200) | ↑ options (gamma/tail), ↓ technical long | confluencia 3; LONG restringido (HMM bullish-only ON) | regime_scalar 0.4–0.65; notional −50% | veto gamma-negativo estricto; tail 0.80 |
| **Chop / rango** (sin tendencia MA) | ↑ microestructura (OFI/VPIN), mean-reversion | VWAP z 2.0 fade; confluencia 2 | size-down ×0.5; cooldown ↑ | flow toxicity veto p>0.90 |
| **Alta vol** (VIX>30) | ↑ risk/predictive veto; ↓ todo | confluencia 3; prob_floor +0.10 | regime_scalar 0.4; Kelly OFF | VPIN p>0.95 BLOCK; speed/zomma size-down |

---

## 12. Síntesis para Fase 4

El **Blueprint de Fase 4** debe ordenarse por la cascada de dependencias: **medición (P0) → entradas
(P0) → gobierno ML (P0) → caps de riesgo (P1) → ponderación por régimen (P2) → sizing/ejecución
(P2–P3)**. No tiene sentido proponer pesos óptimos (palanca 7) ni Kelly (palanca 9) antes de que la
medición (palanca 1) permita validarlos. El perfil **verification** debe seguir recolectando, pero
**con gates activos** (si no, recolecta ruido). El perfil **profit** debe endurecer entradas y activar
defensas, manteniendo EOD flatten y el bloqueo de meta-learner sintético (restricciones inviolables).
