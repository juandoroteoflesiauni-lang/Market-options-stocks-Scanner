# Engine Configuration Audit — Fase 1 (solo lectura)

> **Misión:** mapear TODOS los puntos de configuración que afectan decisión y sizing en los bots
> Alpaca (paper equity + options R1/R2) y BingX (VST demo perps, one-way LONG/SHORT).
> **Rama:** `sec/hardening-gitleaks-gemini` · **Fecha auditoría:** 2026-06-17
> **Estado:** Fase 1 completada con lectura directa de los archivos núcleo. Las áreas marcadas
> `PENDIENTE-LECTURA` requieren lectura adicional en próxima iteración (no se asumió contenido).

## 0. Metodología y alcance leído

Archivos **leídos directamente** (no asumidos):

- `CLAUDE.md`, `AGENTS.md`, `README.md` (contexto/directivas).
- `backend/models/strategy_weights.py` (pesos funnel 4 fases).
- `backend/config/phase_thresholds.py` (singleton de pesos activos; **no** es un archivo de umbrales numéricos como sugería el brief — es el holder thread-safe de `StrategyWeights`).
- `backend/config/bot_relaxed_thresholds.py` (fuente central verification + perfiles env).
- `backend/config/profit_calibration.py`, `execution_policy.py`, `alpaca_institutional_config.py`,
  `alpaca_r2_scoring_config.py`, `alpaca_r1_options_scoring_config.py`, `options_defined_risk.py`,
  `shared_options_tier_policy.py`, `alpaca_options_route_config.py`.
- `backend/services/alpaca_decision_engine.py`, `bingx_decision_engine.py`.
- `backend/services/bingx_risk_desk.py`, `predictive_risk_gate.py`, `bingx_predictive_bridge.py`.
- `backend/services/tca/{journal_tca,implementation_shortfall,tca_eod_report}.py`.
- `data/eod_snapshots/eod_audit_20260617.json` (baseline cuantitativo).
- Listados de `backend/config/`, `backend/quant_engine/engines/**`, `backend/services/tca/`.

`PENDIENTE-LECTURA`: `alpaca_pre_trade_risk_gate.py`, `alpaca_risk_desk.py`,
`technical_scanner_orchestrator.py`, `market_scanner_indicator_catalog.py`,
`market_scanner_institutional_scoring.py`, `options_strategy_loader.py` (cuerpo de playbooks),
y lectura línea-a-línea de los 165+ motores quant (clasificados aquí por directorio + referencias).

---

## A) Funnel 4 fases + pesos dinámicos (`strategy_weights.py`)

> **Nota arquitectónica:** El `StrategyWeights` rige el *scoring del funnel* (scanner→derivatives).
> Los bots de ejecución (Alpaca/BingX) corren sobre sus **propios** decision engines (secciones B/C),
> que NO consumen `StrategyWeights` directamente. Confirmar en Fase 3 cuánto del funnel impacta el
> hot-path de ejecución vs. el dashboard. Esto condiciona la prioridad de tuning de esta sección.

| Motor | Archivo config | Parámetro | Valor actual | Rol en decisión | Prioridad tuning |
|---|---|---|---|---|---|
| Funnel | strategy_weights.py | phase_a/b/c/d.phase_weight | 0.10 / 0.25 / 0.45 / 0.20 | Peso de cada fase en score compuesto | Media |
| Phase A gate | strategy_weights.py | validation_strictness | 0.85 | Estrictez validación datos crudos | Baja |
| Phase A | strategy_weights.py | min_price / min_volume / max_spread_pct | 0.50 / 10_000 / 0.20 | Filtro liquidez básico | Media |
| EMA Cluster | strategy_weights.py | ema_cluster_periods / min_aligned | (9,21,50,200) / 3 | Alineación tendencia | Media |
| ATR Gate | strategy_weights.py | min_atr_pct / max_atr_pct | 0.003 / 0.05 | Filtro volatilidad | **Alta** (gate de régimen) |
| RSI Extreme | strategy_weights.py | oversold / overbought | 15.0 / 85.0 | Filtro extremos (muy laxo: 15/85) | **Alta** |
| VWAP z-score | strategy_weights.py | vwap_max_zscore | 3.0 | Distancia a VWAP | Media |
| Entropy | strategy_weights.py | max_entropy | 3.5 | Ruido/orden de la serie | Baja |
| SuperTrend | strategy_weights.py | period / multiplier / max_changes | 10 / 3.0 / 2 | Régimen direccional | Media |
| Phase B | strategy_weights.py | ofi/smc/vpin_weight | 0.45 / 0.35 / 0.20 | Microestructura (∑=1) | **Alta** |
| Phase B | strategy_weights.py | ofi_sensitivity / smc_lookback / vpin_buckets | 1.0 / 20 / 50 | Sensibilidad microestructura | Media |
| Phase C engines | strategy_weights.py | gex/gamma_flip/dex/flow/zero_day/shadow_delta/delta_flow/phase_b_momentum | 0.20/0.12/0.15/0.12/0.10/0.10/0.08/0.13 | Pesos 8 motores opciones (∑=1) | **Alta** |
| Phase C contrato | strategy_weights.py | basic_metrics / engine_average | 0.40 / 0.60 | Mezcla métricas vs motores | Media |
| Phase C filtros | strategy_weights.py | min_dte / max_dte / optimal_dte | 14 / 60 / 35 | Selección contratos | Media |
| Phase C filtros | strategy_weights.py | delta_target_call/put / iv_min / iv_max | ±0.35 / 0.10 / 0.40 | Selección strike/IV | Media |
| Phase C filtros | strategy_weights.py | min_composite_score | 40.0 | Umbral de contrato | Media |
| Phase D | strategy_weights.py | momentum/vol/volspike/vwap/confluence | 0.35/0.25/0.20/0.10/0.10 | Señales tick (∑=1) | Media |
| Phase D | strategy_weights.py | min_confidence | 0.60 | Umbral emisión señal | **Alta** |
| Phase D riesgo | strategy_weights.py | stop_loss_pct / take_profit_pct | 0.02 / 0.04 | **R:R 1:2 fijo** | **Alta** (ver hallazgo H3) |
| Régimen | strategy_weights.py | regime_adaptation_enabled | True | Modula pesos por VIX+SPY MA | **Alta** |

**Notas:** todos los sub-pesos están validados a ∑=1.0 (Pydantic frozen). RSI 15/85 es extremadamente
permisivo (deja pasar casi todo). El R:R Phase D es fijo 2% / 4% y **no** es adaptativo a ATR/régimen.

---

## B) Decision engines + gates de los bots

### B.1 Alpaca (`alpaca_decision_engine.py`) — LONG-only

| Componente | Parámetro | Valor actual (default → env verification) | Rol | Prioridad |
|---|---|---|---|---|
| Score compuesto | `_WEIGHT_VOLUME/BREAKOUT/MACD/RS` | 0.30 / 0.30 / 0.20 / 0.20 (hardcoded) | Score técnico clásico 0..1 | **Alta** |
| Probabilidad | `_PROB_BASE + _PROB_SPAN*score` | 0.50 + 0.45·score (hardcoded) | Mapea score→prob [0.50, 0.95] | **Crítica** (ver abajo) |
| Gate prob | `prob_floor` (`ALPACA_PROB_FLOOR`) | 0.55 → **0.35** | Umbral ALLOW | **Crítica** |
| Gate sizing | `size_down_band` (`ALPACA_SIZE_DOWN_BAND`) | 0.05 → 0.15 | Banda SIZE_DOWN | Media |
| Gate volumen | `min_volume_z` (`ALPACA_MIN_VOLUME_Z`) | 1.0 → **0.30** | Filtro spike volumen | **Alta** |
| Gate rango | `min_close_position` (`ALPACA_MIN_CLOSE_POSITION`) | 0.60 → 0.35 | Cierre en rango | Alta |
| **Bullish gate** | `ALPACA_VERIFICATION_RELAXED_BULLISH` | **true** en verification | Si true → solo exige `close>0` | **Crítica** (H4) |
| R1 blend | `get_r1_blend_weights()` | classic 0.6 / options 0.4 | Mezcla técnico+opciones R1 | Alta |
| R2 blend | `R2_CLASSIC_WEIGHT/TECH_WEIGHT` | 0.6 / 0.4 | Mezcla clásico+L1 técnico R2 | Alta |
| **ML blend oculto** | `0.70·prob + 0.30·ml_prob` | hardcoded, sin env-flag | Inyección RandomForest | **Crítica** (H1) |

> **Implicación matemática (H4):** con `prob = 0.50 + 0.45·score`, incluso `score=0` da `prob=0.50 > 0.35`
> (floor verification). El **único** gate efectivo en verification es `_is_bullish`, que con
> `ALPACA_VERIFICATION_RELAXED_BULLISH=true` se reduce a `close>0`. Resultado: **Alpaca equity emite ALLOW
> LONG en casi todos los candidatos** con notional boosteado → consistente con la sangría R1 (-7,567 USD).

### B.2 BingX (`bingx_decision_engine.py`) — one-way LONG/SHORT

| Componente | Parámetro | Valor actual (default → env verification) | Rol | Prioridad |
|---|---|---|---|---|
| Pesos módulo | `_DEFAULT_WEIGHTS` | venue 0.15 / technical 0.25 / options 0.20 / predictive 0.25 / l2 0.10 / risk 0.05 | Aggregate score (renormaliza sobre módulos >0) | **Crítica** |
| Consenso técnico | `_TECHNICAL_WEIGHT_MATRIX` (16–19 motores) | hmm 0.12, ofi 0.12, vol_profile 0.08, vwap 0.08, lob 0.08, vsa 0.08, fvg 0.08, ofd 0.06, delta_vol 0.05, vpoc 0.04, tpo 0.03, single_prints 0.02*, vsa_footprint 0.02*, avwap_m13-18 0.04..0.02 | Voto ponderado direccional | **Alta** |
| Umbral dirección | consenso > +0.60 / < −0.60 | hardcoded | LONG/SHORT/FLAT | **Alta** (umbral muy exigente) |
| Gate score | `min_decision_score` (`BINGX_MIN_DECISION_SCORE`) | 0.55 → **0.30** | Umbral ALLOW | **Crítica** |
| Banda | `size_down_band` | 0.10 (hardcoded) | SIZE_DOWN | Media |
| Predictive floor | `min_predictive_confidence` (`BINGX_MIN_PREDICTIVE_CONFIDENCE`) | 0.50 → **0.35** | Floor confianza predictiva | **Alta** |
| L2 live gate | `require_l2_for_equity_live` (`BINGX_REQUIRE_L2_FOR_EQUITY_LIVE`) | true → **false** | BLOCK equity perps sin L2 | Media |
| **ML blend oculto** | `0.80·score + 0.20·ml_prob` | hardcoded, sin env-flag | Inyección RandomForest | **Crítica** (H1) |
| Confluence bonus | `+0.20` si confluence_signal == dirección; BLOCK si diverge | hardcoded | Multiplicador/veto | Alta |
| Charm penalty | dinámico por DTE (0.03 a 0.40) | hardcoded | Penaliza charm contrario | Media |

**Gates duros BingX (BLOCK, en orden):** tail_risk CRITICAL · NDDE contradice dirección · crypto
derivatives overheating (funding >±0.0008 o liquidaciones >100k) · DEX/ZGL invalidation · gamma
negative regime (solo LONG) · shadow_delta_imbalance >±0.8 · confluence divergence ·
insufficient_core_motors (<2) · L2 required (live). **Size reducers:** speed_instability (×0.70),
zomma (escalado), pinning DTE≤1 (escalado). Dirección: **predictive es autoritativo**; si predictive
FLAT cae a technical; nunca inventa dirección.

---

## C) Risk desks + gates

### C.1 BingX Risk Desk (`bingx_risk_desk.py`) — 10 gates

| Gate | Parámetro | Default policy → **env sesión (`apply_paper_demo_account_env`)** | Rol | Prioridad |
|---|---|---|---|---|
| 1 Kill switch | estado | off | Bloqueo permanente | — |
| 2 Daily loss | `max_daily_loss_usdt` | 3.0 → **5000** | Circuito de pérdida diaria | **Crítica** |
| 3 Notional total | `max_position_notional_usdt` | 25.0 → **75000** | Cap exposición total | **Alta** |
| 4 Max posiciones | `max_open_positions` | 3 → **10** | Cap nº posiciones | Alta |
| 5 Exposición símbolo | `max_symbol_exposure_usdt` | 12.0 → **15000** | Cap por símbolo | Alta |
| 6 Cooldown post-loss | `cooldown_after_loss_minutes` | 15.0 → **2** | Pausa tras pérdida | **Alta** |
| 7 Spread guard | `max_spread_pct` | 0.005 | Bloquea spread ancho | Media |
| 8a L2 floor | `min_l2_quality_score` / `BINGX_RISK_REQUIRES_L2` | 0.30 → **0** / **false** | Calidad LOB | Media |
| 8b Provider degraded | `no_trade_when_provider_degraded` | true → **false** (RISK_NO_TRADE_PROVIDER_DEGRADED) | Bloqueo por datos degradados | Media |
| 9 Zone veto | `BINGX_ZONE_VETO_ENABLED` / `BINGX_NEUTRAL_ZONE_BLOCK` | true → **false / false** | Veto acumulación/distribución | Media |
| 10 Margin firewall | `0.15·available_margin` | hardcoded | Cap 15% margen por add | Alta |
| 10b Repeated exec | `EXECUTION_REPEATED_MAX_PER_SYMBOL` | 6 → (profit:3) | Límite FIA reentradas | Alta |

> **Hallazgo (H5):** en la sesión real los caps están **inflados ~2500×** respecto al default (notional 25→75000,
> daily_loss 3→5000) y los gates 7-9 desactivados. El único circuito que queda es daily_loss 5000 USDT
> (~5% del equity demo). Con notional verification 500 USDT y 200 trades, el desk **casi nunca bloquea**.
> Esto explica que BingX acumulara -2,375 USDT sin disparar el cap diario.

### C.2 Alpaca Pre-Trade Limits (`alpaca_institutional_config.py::AlpacaPreTradeLimits`)

| Parámetro | Default → env | Rol | Prioridad |
|---|---|---|---|
| `max_position_notional_usd` | 10_000 | Cap notional posición | Alta |
| `max_order_notional_usd` | 5_000 | Cap notional orden | Alta |
| `max_open_positions` | 5 → **10** | Cap posiciones | Alta |
| `order_rate_limit_per_minute` | 10 | Rate limit órdenes | Media |
| `bur_yellow / bur_red` | 0.5 / 0.8 | Buffer utilization zones | Media |
| `kill_switch` | off | Bloqueo | — |

`PENDIENTE-LECTURA`: lógica de `alpaca_pre_trade_risk_gate.py` y `alpaca_risk_desk.py` (cómo se
aplican estos límites en hot-path, EOD flatten, BUR).

### C.3 Predictive Risk Gate (`predictive_risk_gate.py`) — **DEAD_CODE_CANDIDATE en hot-path**

Evalúa 8 señales predictivas (nlp_catalyst, skew_fat_tails, zomma, dealer_flow, gamma_exposure,
options_toxicity, markov_regime, vsa) con sizing `Decimal` y vetos a 0.0. **Bien diseñado** (multiplicadores
0.5/0.6/0.75, bloqueos a VPIN p>0.95, buying climax, BEAR_VOLATILE, etc.).

> **Hallazgo (H2):** `grep` confirma que `PredictiveRiskGate` solo se instancia en `funding_orchestrator.py`
> y tests — **NO** en `alpaca_bot_service` ni `bingx_bot_service`. Además cada `_evaluate_*` hace early-return
> si la señal no está en `context_data` → si `predictive_signals` no se inyecta, el gate es un no-op.
> **Este gate institucional NO protege los trades del daemon dual.** Prioridad: decidir si cablearlo
> (alto valor) o documentarlo como inactivo.

---

## D) Ejecución — Fase B (`execution_policy.py`)

| Parámetro | Default → env verification / profit | Rol | Prioridad |
|---|---|---|---|
| `bingx_twap_enabled` / `min_notional_usdt` | true / 400.0 | TWAP slivering BingX | Media |
| `alpaca_elite_enabled` / `algorithm` | false→**true** / VWAP | Smart router VWAP/TWAP/DMA | Media |
| `alpaca_elite_min_notional_usd` | 1_500 | Umbral para algoritmo | Media |
| `price_collar_enabled` / `max_deviation_pct` | true / 0.0075 → (profit **0.005**) | Collar anti-slippage | **Alta** |
| `repeated_execution_enabled` / `max_per_symbol` | true / 6 → (profit **3**) | Límite reentradas FIA | Alta |

> **Bloqueador de medición (H6):** los thresholds TWAP/VWAP/collar **no pueden calibrarse** hasta tener
> Implementation Shortfall medido. El EOD reporta `trades_with_tca=0`, `portfolio_avg_is_bps=null` →
> la ejecución se está optimizando a ciegas.

---

## E) Opciones (R1 sofisticada / R2 básica)

| Motor | Archivo | Parámetro | Valor actual | Rol | Prioridad |
|---|---|---|---|---|---|
| R1 familias | alpaca_r1_options_scoring_config.py | family_weights | momentum/volume/structure = **0.333 c/u** (calibrado JSON, equal) | Pesos familias R1 | **Alta** |
| R1 blend | idem | classic / options | 0.6 / 0.4 | Mezcla clásico+opciones | Alta |
| R1 motores | idem | R1_OPTIONS_ENGINE_KEYS | delta_rsi, shadow_macd, vidya_iv_gamma, cvd_ndde_gamma, volume_profile_oi, bb_gex, sma_gamma, hybrid_ribbon | 8 motores opciones | Alta |
| R2 confluencia | alpaca_r2_scoring_config.py | `R2_CONFLUENCE_MIN_ENGINES` | 4 → **1** (verification) | Mín motores que confirman | **Crítica** |
| R2 score | idem | `R2_MIN_SCORE` | 65 → **32** (verification) | Umbral score L1 | **Crítica** |
| R2 veto | idem | `R2_GATE_VETO_THRESHOLD` | 0.3 → **0.05** | Umbral veto | **Alta** |
| R2 régimen | idem | `R2_HMM_BULLISH_ONLY` / `R2_VSA_VOLUME_GATE` | true → **false** | Gates régimen/volumen | Alta |
| R2 tiers | idem | S1/S2/S3 min_engines | 2 / 4 / 6 ; `ACCEPT_S1`→true (verif) | Tiering confluencia | Alta |
| Riesgo definido | options_defined_risk.py | `OPTIONS_DEFINED_RISK_ONLY` | true | Solo estructuras acotadas (sin naked) | Mantener (PD) |
| Riesgo definido | idem | `PREFER_SPREAD_OVER_SINGLE_LEG` | true | Verticales > long suelto | Media |
| Universo R1 | bot_relaxed_thresholds.py | DTE_MIN/MAX, OI, vol | 3 / 45 / 100 / 25 | Universo lenient R1 | Media |
| Vetos opciones | bot_relaxed_thresholds.py | MIN_GLOBAL_CONFIDENCE | 0.52 (base) → 0.18 (relaxed) → profit **0.42** | Confianza mínima estrategia | **Alta** |
| Vetos opciones | idem | TAIL_RISK / GAMMA_PRESSURE / FLOW_TOXIC | 0.92 / 0.88 / disp 0.82 | Vetos institucionales | Alta |
| Tier compartido | shared_options_tier_policy.py | `SHARED_OPTIONS_TIER_ENABLED` | true | R1 watchlist + abiertas = quant completo | Media |

> **Hallazgo (H7):** `OPTIONS_R1` reportó **0 trades** en el EOD pese a `ALPACA_OPTIONS_ENABLED=true`,
> `PRIORITY_EQUITY=true` y universo lenient. Verificar en Fase 3 si la ruta de opciones se ejecuta o
> está bloqueada aguas arriba (hidratación de chain, `min_legs=4`, confianza). Marca de posible ruta inerte.
> Las `R1 family_weights` están en 0.333 equal porque el calibrador EOD corrió con `n_trades=0`
> (sin señal real para calibrar) → la calibración es un placeholder, no aporta edge.

---

## F) Sizing y calibración profit (`profit_calibration.py` + `bot_relaxed_thresholds.py`)

| Parámetro | verification | profit | Rol | Prioridad |
|---|---|---|---|---|
| `ALPACA_PROB_FLOOR` | 0.35 | 0.55 | Floor probabilidad | Crítica |
| `ALPACA_MIN_VOLUME_Z` | 0.30 | 0.65 | Gate volumen | Alta |
| `ALPACA_R2_MIN_SCORE` | 32 | 48 | Score R2 | Crítica |
| `ALPACA_R2_CONFLUENCE_MIN_ENGINES` | 1 | 2 | Confluencia | Crítica |
| `BINGX_MIN_DECISION_SCORE` | 0.30 | 0.55 | Score BingX | Crítica |
| `BINGX_MIN_PREDICTIVE_CONFIDENCE` | 0.35 | 0.50 | Floor predictivo | Alta |
| Notional Alpaca | 2_000 | 1_500 | Tamaño por trade | Alta |
| Notional BingX | 500 | 350 | Tamaño por trade | Alta |
| Cooldown ejecución | 3.0 min | 8.0 min | Pausa entre trades | Alta |
| `rolling_pf_min` | 0.85 | 1.15 | Gate PF rolling | **Crítica** |
| `rolling_pf_window` / `min_sample` | 30 / 10 | 30 / 10 | Ventana PF | Alta |
| Kelly | OFF | ON, fraction 0.25, scalar [0.35, 1.0], min_sample 12 | Sizing fraccional | **Alta** |
| Boost sizing | `ROUTE_SIZING_BOOST_FACTOR` | 1.03 | +3% sobre multiplicadores | Media |
| High-prob BP% | `ALPACA_HIGH_PROB_BUYING_POWER_PCT` | 0.10–0.15 (thr 0.85) | % buying power alta prob | Alta |
| Kelly opciones | `OPTIONS_KELLY_FRACTION` / `MAX_FRACTION` | **0.5 / 0.25** ⚠ | Kelly opciones | **Alta** (inconsistencia) |
| Heat caps | portfolio/sector/risk_budget | 12% / 5% / 1.875% | Límites de calor | Alta |

> **Hallazgo (H8 — inconsistencia):** `OPTIONS_KELLY_FRACTION=0.5` es **mayor** que
> `OPTIONS_KELLY_MAX_FRACTION=0.25`. Semánticamente contradictorio; verificar cuál vincula en el sizer.
>
> **Hallazgo (H9 — PF gate inerte):** el EOD muestra `rolling_pf=null` para todas las rutas
> (sample_size 0 salvo BINGX/PORTFOLIO=30) y `pf_gate_allowed=true` por defecto. El gate de seguridad
> **no frena nada** porque el journal TCA no acumula muestras (ver H6). En profit mode el floor 1.15 no
> llegaría a vincular hasta reparar el journaling.

---

## G) Motores quant — clasificación por dominio y uso real

Inventario (excluyendo `__pycache__`): **technical** ~36, **predictive** ~45, **options** ~38,
**fundamental** ~18, **portfolio** ~10, **macro/confluence/argentina** ~6. Clasificación por uso en
el **hot-path del daemon dual** (cruzando con las matrices de pesos y tuplas de engine-keys leídas):

### Referenciados (hot-path confirmado)
- **BingX technical consensus** (`_TECHNICAL_WEIGHT_MATRIX`): `hmm_engine`, `ofi_engine`,
  `volume_profile`, `vwap_engine`, `lob_dynamics_engine`, `vsa`, `fvg_engine`, `order_flow_delta_engine`,
  `volume` (delta), `vpoc_migration`, `tpo_skewness`, `single_prints`*, `vsa_footprint_engine`*, `avwap_hybrid` (m13-18).
- **R2 L1** (`R2_L1_ENGINE_KEYS`): candle_geometry, market_structure, order_flow_delta, vsa,
  volume_profile, volume_nodes, vwap_advanced, delta_volume, vpoc_migration, ofi, fvg, tpo_skewness,
  single_prints, hmm_regime, vsa_footprint.
- **R1 options** (`R1_OPTIONS_ENGINE_KEYS`): delta_rsi, shadow_macd, vidya_suite, cvd_suite,
  volume_profile_oi, bb_dynamic (gex), sma_gamma, hybrid_ribbon.
- **Phase C / options dirección**: gex_profile/gamma_exposure, gamma_flip, dex, options_flow,
  zero_day, shadow_delta, delta_weighted_flow.

`*` `single_prints` y `vsa_footprint` están en la matriz BingX pero `_engine_bias_vote` **siempre
retorna NEUTRAL** para ellos → aportan peso muerto (0.02 c/u) que nunca vota. **DEAD_VOTE.**

### DEAD_CODE_CANDIDATE (alto valor, sin cableado al hot-path del daemon)
- **`predictive/` (mayoría):** meta_learner, ensemble_meta_learner (50KB), multimodal_predictive,
  tail_risk_engine, markov_regime_engine, fear_greed*, gamma_flip_engine, skew_fattails_engine,
  shadow_delta_engine, dealer_flow_dynamics_engine, options_order_flow_toxicity_engine, zomma_engine,
  speed_instability_engine, expected_move_engine, vol_term/skew engines, cross_asset, catalyst_nlp…
  Estos alimentan el **`PredictiveRiskGate`** (H2, no cableado) y/o la **cascada del bridge** (H10, no cableada).
- **`fundamental/` completo** (valuation, pillar_scorer, statements, smart_money…): sin referencia en
  el hot-path de los bots intradía. Probable dominio de scanner/dashboard, no de ejecución.
- **`portfolio/` (mic arbitrator, portfolio_optimization, risk):** verificar uso (posible dashboard).

> Confirmar DEAD_CODE en Fase 3 con trazas de ejecución. La hipótesis: **el inventario de motores es
> ~165 pero el hot-path de ejecución consume ~30–35**; el grueso del stack predictivo institucional
> está construido pero desconectado de las decisiones live.

---

## H) Hallazgos críticos consolidados

| # | Hallazgo | Evidencia | Impacto | Acción propuesta (Fase 4) |
|---|---|---|---|---|
| H1 | **Blend ML oculto y no gobernado** en ambos decision engines (`TradePredictor`, ml_engine). BingX `0.80·s+0.20·ml`, Alpaca `0.70·p+0.30·ml`. Magic numbers, sin env-flag, try/except que traga errores. | alpaca L186-200, bingx L808-825 | Inyecta 20-30% de peso de un modelo no auditado en cada decisión; viola "sin magic numbers / config sobre hardcode". | Exponer pesos a env, condicionar por session_mode, log explícito; o desactivar hasta validar el modelo. Verificar si existe artefacto entrenado (si no, `load()` falla silenciosamente). |
| H2 | **`PredictiveRiskGate` no cableado** al hot-path (solo `funding_orchestrator`). | grep usages | El veto predictivo institucional (8 motores) no protege trades live. | Decidir: cablear en bot services o documentar inactivo. |
| H3 | **R:R fijo y BingX expectativa negativa pese a W68/L12** (-2,375 USDT). | EOD route_pnl | Perdedores >> ganadores → gestión TP/SL asimétrica rota. | Stops/TP adaptativos a ATR; revisar lógica de cierre BingX. |
| H4 | **Alpaca verification ≈ sin gate** (`prob=0.50+0.45·score` > floor 0.35; bullish relajado a `close>0`). | alpaca_decision_engine + bot_relaxed | R1 sangra -7,567 USD con notional boosteado. | Subir `prob_floor`/recortar notional o restaurar `_is_bullish`. |
| H5 | **Risk desk BingX inflado ~2500×** + gates 7-9 off. | bingx_risk_desk vs env | Único circuito = daily_loss 5000. | Recalibrar caps a tamaño real del libro. |
| H6 | **TCA desconectado** (`trades_with_tca=0`). | EOD tca_report + journal_today=0 | Imposible medir IS/slippage → ejecución y PF a ciegas. | Reparar journaling en hot-path antes de optimizar ejecución/PF. |
| H7 | **OPTIONS_R1 0 trades** pese a habilitado. | EOD route_pnl | Ruta de opciones posiblemente inerte; calibración placeholder (n_trades=0). | Diagnosticar hidratación chain / vetos. |
| H8 | **Kelly opciones inconsistente** (FRACTION 0.5 > MAX 0.25). | bot_relaxed_thresholds | Sizing ambiguo. | Corregir semántica. |
| H9 | **PF rolling gate inerte** (rolling_pf=null, sample 0). | EOD profit_calibration | Gate de seguridad no vincula. | Depende de H6. |
| H10 | **Bridge predictivo degradado**: cascada meta_signal→pred_opt2→thesis documentada pero solo `equity_summary_fn` cableado en `bingx_candidate_analysis.py`. Además audit lee `signal.direction/probability` (atributos inexistentes → None). | bingx_predictive_bridge + grep | La "predicción institucional" BingX es solo heurística equity; auditoría predictiva vacía. | Cablear fetchers reales o documentar; corregir atributos de audit. |
| H11 | **Meta-learner sintético = ruido** (accuracy 0.0–0.45, `synthetic_yfinance`). Promoción bloqueada (correcto). | EOD eod_calibrations | Si se cablea predictive, inyecta ruido. | Mantener bloqueo; no subir peso predictivo sin outcomes reales. |
| H12 | **DEAD_VOTE** `single_prints`/`vsa_footprint` en matriz BingX (siempre NEUTRAL). | bingx_decision_engine | Peso muerto 0.04 total. | Reasignar peso o implementar voto. |

---

## I) Mapa verification vs profit (env efectivo)

`apply_session_mode_env(mode)` → `apply_verification_session_env` o `apply_profit_session_env`, ambos
sobre `apply_paper_demo_account_env` (Alpaca paper 100k, BingX VST ~100k, caps risk desk inflados).
Diferencias clave ya tabuladas en sección F. Constantes EOD comunes: `ALPACA_EOD_FLATTEN_ENABLED=true`,
cutoff 15:30 ET, flatten 15:45 ET, `META_LEARNER_PROMOTE_SYNTHETIC=false`, `AI_AGENTIC_COMMITTEE_MODE=off`.

---

## J) Bloqueadores y próximos pasos

**Bloqueadores activos:**
- **B1 (medición):** TCA journaling desconectado (H6) — prerequisito para Fases 3/4 de ejecución y PF.
- **B2 (datos):** sin Redis/Postgres confirmados → impacto en `max_pain_history`, pub/sub (documentar en Fase 3).
- **B3 (gobernanza):** blend ML oculto (H1) altera toda comparación baseline; debe aislarse antes del backtest.

**Estado de tareas:**
- ✅ Fase 1 — inventario y mapa de motores (este documento).
- ⏳ Próximo — Fase 2 (memo estratégico) y Fase 3 (baseline cuantitativo con DuckDB + pytest TCA).

**Comando exacto para validar lo leído (Fase 3, próxima sesión):**
```powershell
cd c:\dev\deep-funnel-station
python -m pytest tests/unit/test_tca_phase_a.py tests/unit/test_execution_policy_phase_b.py tests/unit/test_profit_calibration_phase_c.py -v
python backend/scripts/audit_prediction_quality.py
python backend/scripts/run_eod_session.py
```

`PENDIENTE-LECTURA` para cerrar el mapa al 100%: `alpaca_pre_trade_risk_gate.py`, `alpaca_risk_desk.py`,
`technical_scanner_orchestrator.py`, `market_scanner_indicator_catalog.py`,
`market_scanner_institutional_scoring.py`, cuerpo de `options_strategy_loader.py`.
