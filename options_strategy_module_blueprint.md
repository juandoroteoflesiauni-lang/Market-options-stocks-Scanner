# Blueprint técnico completo del módulo de estrategias de opciones

Este documento define el diseño integral de un módulo de estrategias de opciones orientado a backend, pensado para integrarse con un bot de ejecución en Alpaca y construido a partir de motores técnicos, predictivos y de opciones presentes en el registro de motores cuantitativos[cite:4][cite:6]. El objetivo del módulo no es solamente generar señales de compra/venta de calls y puts, sino producir decisiones auditables, configurables y robustas sobre estructura, vencimiento, strike, sizing, gestión y bloqueo operativo usando una arquitectura por capas y playbooks especializados[cite:4][file:1].

## Objetivo del módulo

El módulo de estrategias de opciones debe funcionar como un orquestador cuantitativo que consuma señales estructurales, probabilísticas y mecánicas del mercado para transformarlas en decisiones operables sobre calls, puts y spreads de débito[cite:4][file:1]. En vez de un motor monolítico, el diseño recomendado es un meta-motor que separa adquisición de datos, cálculo de features, clasificación de régimen, selección de playbook, construcción de estructura, control de riesgo, salida de orden y auditoría, de modo que cada etapa pueda evolucionar sin romper el resto del sistema[cite:6][file:1].

## Principios de arquitectura

La arquitectura debe seguir un enfoque backend-first, con scripts ejecutables, configuración externa en YAML/JSON, persistencia de decisiones y loops desacoplados para señal y calibración[cite:6]. Este enfoque encaja con un sistema que necesita trazabilidad, replay y ajuste continuo de pesos, y evita acoplar la lógica de trading a una UI prematura o a un estado efímero en memoria[cite:6].

Los principios rectores del módulo son los siguientes:

- Orquestación por capas, no estrategia única rígida[cite:24][file:1].
- Salidas normalizadas por motor y por capa para evitar mezclar señales heterogéneas sin control[file:1].
- Configuración externa para thresholds, pesos, universos, límites y playbooks[cite:6].
- Hard blocks de riesgo y calidad de mercado por encima de cualquier sesgo direccional[file:1].
- Trazabilidad completa mediante `reason_codes`, snapshots y payloads persistidos[cite:6].
- Separación estricta entre decisión estratégica y ejecución broker-specific en Alpaca[cite:4][web:9].

## Alcance funcional

El módulo debe cubrir todo el ciclo de decisión de una estrategia de opciones direccional y semiestructural[cite:4][file:1]. Esto incluye lectura de contexto, evaluación de régimen, análisis de cadena, propuesta de estructura, control de sizing, generación de órdenes candidatas, gestión dinámica de posiciones y realimentación para recalibración offline[file:1].

El alcance recomendado para la primera versión productiva incluye:

- Long call y long put para setups direccionales simples[cite:4].
- Call debit spread y put debit spread para escenarios donde la prima esté cara o se quiera acotar desembolso[file:1].
- Playbooks de tendencia, rechazo dealer/gamma, breakout de compresión, evento y defensa por tail risk[file:1].
- Veto por iliquidez, flujo tóxico, cola extrema, spreads excesivos y conflicto severo entre capas[file:1].
- Journaling y replay de cada decisión para posteriores procesos de `feedback_engine` y `factor_calibration`[file:1].

### Universo operativo — solo Ruta 1 (R1)

**Regla obligatoria del MVP y de producción:** el módulo de estrategias de opciones opera **únicamente** sobre los tickers de la **Ruta 1 prioritaria** (`ALPACA_ROUTE1_WATCHLIST` en `backend/config/alpaca_priority_route.py`). No evalúa, no selecciona estructuras ni emite payloads de ejecución para símbolos de R2 ni del scan dinámico.

Los 11 subyacentes autorizados son:

`MSFT`, `TSLA`, `AAPL`, `GOOGL`, `META`, `NVDA`, `SPY`, `QQQ`, `IREN`, `CRWV`, `AMZN`

Implicaciones de diseño:

- **Gate duro en data intake:** antes de cualquier capa (técnica, predictiva, opciones), validar `is_route1_symbol(symbol)`. Si falla → `decision: NO_TRADE`, `reason_codes: [symbol_not_in_route1_universe]`, sin invocar motores ni selectors.
- **Fuente canónica única:** `options_universe.yaml` no define una lista paralela editable; referencia `source: alpaca_route1` y se resuelve en runtime contra `ALPACA_ROUTE1_WATCHLIST` (evita drift entre R1 del bot y el módulo de opciones).
- **Alineación con infra existente:** snapshots GEX, confluencia híbrida R1 (`alpaca_r1_options_*`) y captura institucional ya están acotados a estos 11 símbolos; el módulo reutiliza ese mismo universo.
- **R2 explícitamente excluida:** aunque un ticker de R2 tenga señal técnica fuerte en el embudo clásico, **no** recibe `long_call`, `long_put` ni debit spreads desde este módulo.


La arquitectura recomendada se organiza en ocho bloques principales, donde cada bloque cumple una responsabilidad única y emite un contrato claro de entrada/salida[cite:24][file:1].

### 1. Data intake layer

Esta capa consume datos de mercado spot, OHLCV, cadena de opciones, Greeks, open interest, volumen, eventos, sentimiento y contexto cross-asset[cite:3][file:1]. También concentra snapshots y adaptadores hacia proveedores de datos, permitiendo que el resto del sistema opere sobre modelos internos homogéneos en lugar de formatos crudos de API[file:1].

### 2. Feature engineering layer

Esta capa transforma datos crudos en métricas agregadas por ticker, expiración y régimen, incluyendo niveles de VWAP, perfiles de volumen, expected move, skew, tail moments, dealer support, toxicidad de flujo y scores de estructura[file:1]. Aquí deben vivir normalizadores, rolling windows, alineación temporal, validaciones de calidad y agregaciones multi-timeframe[file:1].

### 3. Technical layer

La capa técnica sintetiza estructura de precio, contexto de liquidez, microestructura y comportamiento intradía para producir variables direccionales y de calidad de setup[file:1]. Esta capa no decide estructuras de opciones, pero sí establece si existe un sesgo técnico suficientemente limpio como para justificar riesgo direccional[file:1].

Motores sugeridos para esta capa:

- `smc_engine`, `smc_fractal_engine`, `market_structure_engine`, `fractal_models`, `fvg_engine`, `confluence_models` (Technical)[file:1]
- `vwap_engine`, `volume_profile_engine`, `vpoc_engine`, `vpoc_migration`, `tpo_engine`, `tpo_skewness`, `volume_node_engine`[file:1]
- `vsa_engine`, `vsa_footprint_engine`, `order_flow_delta_engine`, `ofi_engine`, `lob_engine`, `lob_dynamics_engine`, `microstructure_confluence`[file:1]
- `squeeze_ignition`, `candle_geometry_engine`, `service` (Technical), `polygon_models`[file:1]

Salidas normalizadas propuestas:

- `technical_direction_bias` en rango [-1, 1][file:1]
- `trend_quality_score` en rango [0, 1][file:1]
- `breakout_state` categórico (`compressed`, `arming`, `confirmed`, `failed`)[file:1]
- `liquidity_location_score` en rango [0, 1][file:1]
- `reversal_risk_score` en rango [0, 1][file:1]
- `structure_alignment_score` en rango [0, 1][file:1]

### 4. Predictive layer

La capa predictiva estima régimen, distribución esperada, asimetrías, colas, sentimiento y movimiento estadístico proyectado[file:1]. Su función principal es responder no solo hacia dónde podría moverse el activo, sino con qué dispersión, con qué fragilidad y con qué probabilidad relativa frente al contexto macro y cross-asset[file:1].

Motores sugeridos para esta capa:

- `markov_regime_engine`, `macro_regime_prior_engine`, `regime_weights`, `cor3m_engine`[file:1]
- `expected_move_engine`, `probabilistic_engine`, `stochastic_predictive`, `stochastic_models`, `vol_term_engine`[file:1]
- `risk_neutral_density_engine`, `tail_risk_engine`, `skew_fattails_engine`, `volatility_surface_engine`, `volatility_skew_engine`[file:1]
- `fear_greed_engine`, `sentiment_engine`, `catalyst_nlp_engine`, `cross_asset_engine`, `correlation_analyzer`, `market_data_fetcher`[file:1][cite:3]
- `feedback_engine`, `factor_calibration`, `meta_learner`, `ensemble_meta_learner` para recalibración y meta-weighting offline[file:1]

Salidas normalizadas propuestas:

- `predictive_direction_bias` en rango [-1, 1][file:1]
- `regime_class` categórico (`trend`, `mean_reversion`, `volatile`, `event`, `dislocated`)[file:1]
- `expected_move_pct` y `expected_move_confidence`[file:1]
- `left_tail_risk_score` y `right_tail_risk_score`[file:1]
- `macro_alignment_score`[cite:3][file:1]
- `forecast_dispersion_score`[file:1]

### 5. Options layer

La capa de opciones traduce el contexto del subyacente y de la cadena en mecánicas específicas de mercado de opciones: dealer positioning, gamma regime, skew, superficie IV, flujo institucional, calidad de cadena, selección estructural y evaluación preliminar de payoff[file:1]. Esta capa es la que convierte una tesis de mercado en una expresión concreta mediante calls, puts o spreads[file:1].

Motores sugeridos para esta capa:

- `options`, `service` (Options), `chain_institutional_analytics`, `chain_analytics_history`, `options_flow`, `options_flow_signal`, `delta_rsi`, `delta_weighted_flow`, `delta_weighted_flow_engine`[file:1]
- `dealer_flow_dynamics_engine`, `gamma_flip`, `gamma_flip_probability`, `gamma_exposure_engine`, `dex`, `dex_engine`, `options_models`, `signal_combiner`[file:1]
- `iv_primitives`, `iv_surface_models`, `derivatives`, `volatility_surface_engine`, `volatility_skew_engine`, `hull_iv`, `zero_day`, `zero_day_engine`, `zomma_engine`[file:1]
- `fractal_oi`, `volume_profile_oi`, `volume_oi_engine`, `chain_institutional_analytics`, `shadow_delta`, `shadow_delta_engine`[file:1]
- `options_confluence`, `strategy_payoff`, `confluence_models` (Options), `stochastic_predictive` como soporte de rango[file:1]

Salidas normalizadas propuestas:

- `options_direction_bias` en rango [-1, 1][file:1]
- `dealer_regime` categórico (`supportive`, `suppressive`, `pinning`, `unstable`)[file:1]
- `gamma_pressure_score`[file:1]
- `iv_state` categórico (`cheap`, `fair`, `rich`, `extreme`)[file:1]
- `flow_conviction_score`[file:1]
- `chain_liquidity_score`[file:1]
- `structure_preference` categórico (`long_call`, `long_put`, `call_debit_spread`, `put_debit_spread`, `no_trade`)[file:1]

### 6. Fusion and veto layer

Esta capa es el corazón del módulo y debe unificar las tres familias en una decisión coherente, aplicando pesos, jerarquías, vetos y desempates[file:1]. No todos los motores deben pesar igual, y además deben existir reglas donde algunos outputs no suman score, sino que directamente bloquean operativa en ciertas condiciones[file:1].

Reglas recomendadas de fusión:

- Fusión por score ponderado entre capa técnica, predictiva y de opciones[cite:24][file:1].
- Veto jerárquico por riesgo de cola extremo, flujo tóxico, iliquidez o conflicto estructural severo[file:1].
- Agrupación previa de motores altamente correlacionados para evitar duplicación de señal[file:1].
- Confidence final penalizada por dispersión entre capas[file:1].
- La salida `NO_TRADE` debe ser un resultado natural y plenamente válido[file:1].

Salidas finales sugeridas:

- `global_bias`[-1, 1][file:1]
- `global_confidence`[0, 1][file:1]
- `veto_triggered` con código y causa[file:1]
- `playbook_family`[file:1]
- `recommended_structure`[file:1]
- `execution_ready` booleano[file:1]

### 7. Strategy construction layer

Una vez elegido el playbook, esta capa determina DTE, bucket de delta, strike policy, precio máximo, anchura de spread y parámetros de riesgo para la estructura seleccionada[file:1]. También debe decidir si corresponde una entrada outright o una estructura definida por riesgo mediante `strategy_payoff` y estado de IV[file:1].

### 8. Execution and audit layer

La ejecución no debe mezclarse con la lógica estratégica[cite:4][web:9]. El módulo estratégico entrega un payload limpio al `alpaca_executor`, y una capa separada registra la decisión, la orden enviada, el fill, el estado posterior y la comparación ex post con lo esperado por motores como `expected_move_engine` y `feedback_engine`[web:9][file:1].

## Estructura de directorios recomendada

La siguiente estructura está pensada para pasarla al IDE como blueprint inicial del módulo[cite:6][cite:24].

```text
backend/
├── config/
│   ├── omni_engine.yaml
│   ├── options_universe.yaml
│   ├── playbooks.yaml
│   ├── risk_rules.yaml
│   └── alpaca_accounts.yaml
├── data/
│   ├── providers/
│   │   ├── market_data_provider.py
│   │   ├── options_chain_provider.py
│   │   ├── sentiment_provider.py
│   │   └── event_provider.py
│   └── snapshots/
├── engines/
│   ├── technical/
│   │   ├── technical_layer.py
│   │   ├── technical_normalizer.py
│   │   └── technical_contracts.py
│   ├── predictive/
│   │   ├── predictive_layer.py
│   │   ├── predictive_normalizer.py
│   │   └── predictive_contracts.py
│   ├── options/
│   │   ├── options_layer.py
│   │   ├── contract_selector.py
│   │   ├── structure_selector.py
│   │   ├── payoff_evaluator.py
│   │   └── options_contracts.py
│   ├── fusion/
│   │   ├── fusion_router.py
│   │   ├── veto_engine.py
│   │   ├── confidence_engine.py
│   │   └── reason_codes.py
│   ├── playbooks/
│   │   ├── trend_continuation.py
│   │   ├── gamma_wall_rejection.py
│   │   ├── compression_breakout.py
│   │   ├── event_volatility.py
│   │   └── tail_risk_defense.py
│   ├── risk/
│   │   ├── position_sizing.py
│   │   ├── portfolio_limits.py
│   │   ├── trade_guardrails.py
│   │   └── exit_manager.py
│   └── brokers/
│       └── alpaca_executor.py
├── models/
│   ├── market_snapshot.py
│   ├── normalized_features.py
│   ├── playbook_decision.py
│   ├── strategy_candidate.py
│   ├── execution_payload.py
│   └── audit_log.py
├── scripts/
│   ├── run_signal_loop.py
│   ├── run_entry_cycle.py
│   ├── run_position_management.py
│   └── run_calibration_loop.py
└── storage/
    ├── predictions.db
    └── audit/
```

## Contratos internos y modelos de datos

El sistema necesita contratos explícitos entre capas para que cada módulo sea testeable, versionable y fácil de refactorizar[cite:6]. La recomendación es usar modelos Pydantic o dataclasses tipadas para snapshots, features normalizadas, decisiones de playbook, candidatos de estrategia y payloads de ejecución[cite:24].

### Modelo de snapshot de mercado

Debe incluir al menos:

- ticker, timestamp, timeframe principal y secundarios[file:1]
- OHLCV reciente y métricas derivadas[file:1]
- estado de estructura técnica[file:1]
- estado de la cadena de opciones, Greeks agregados y niveles dealer[file:1]
- expected move, estados de IV y momentos de cola[file:1]
- eventos próximos, sentimiento y ratios cross-asset[cite:3][file:1]

### Modelo de features normalizadas

Debe consolidar salidas como:

- `technical_direction_bias`
- `predictive_direction_bias`
- `options_direction_bias`
- `trend_quality_score`
- `left_tail_risk_score`
- `flow_conviction_score`
- `chain_liquidity_score`
- `iv_state`
- `dealer_regime`
- `regime_class`
- `structure_alignment_score`

### Modelo de decisión de playbook

Debe contener:

- `decision`: `EXECUTE`, `NO_TRADE`, `REDUCE`, `EXIT`
- `playbook_family`
- `recommended_structure`
- `direction`
- `confidence`
- `reason_codes`
- `veto_triggered`
- `candidate_contract_policy`
- `risk_budget`

### Modelo de payload de ejecución

Debe incluir parámetros listos para el adaptador Alpaca[web:9]:

- símbolo subyacente
- estructura (`long_call`, `long_put`, `call_debit_spread`, `put_debit_spread`)
- DTE objetivo
- delta objetivo de compra y venta
- coste máximo permitido
- quantity/size
- time in force, tipo de orden y slippage máximo[web:9]
- `client_order_id`
- metadata de auditoría y `reason_codes`

## Indicadores y features recomendadas

El módulo no debe limitarse a usar “indicadores clásicos” como elementos aislados, sino convertir cada familia de motores en features compuestas y comparables[file:1]. A continuación se propone una taxonomía de indicadores internos para el módulo.

### Indicadores técnicos compuestos

- **Trend stack**: alineación de `smc_engine`, `hybrid_ribbon`, `market_structure_engine` y `vwap_engine` para sesgo direccional de continuidad[file:1].
- **Liquidity acceptance/rejection**: combinación de `volume_profile_engine`, `vpoc_engine`, `tpo_engine`, `single_prints` y `volume_node_engine` para distinguir aceptación de valor vs rechazo del área[file:1].
- **Microstructure impulse**: combinación de `ofi_engine`, `order_flow_delta_engine`, `vsa_engine`, `vsa_footprint_engine` y `lob_dynamics_engine` para detectar iniciativa real vs fake move[file:1].
- **Compression state**: combinación de `squeeze_ignition`, `candle_geometry_engine` y `vsa_forecast` para detectar pre-expansión[file:1].

### Indicadores predictivos compuestos

- **Regime map**: combinación de `markov_regime_engine`, `macro_regime_prior_engine`, `regime_weights` y `cor3m_engine` para clasificar el estado dominante[file:1].
- **Move map**: combinación de `expected_move_engine`, `stochastic_predictive` y `probabilistic_engine` para rango esperado y convexidad del recorrido[file:1].
- **Tail map**: combinación de `risk_neutral_density_engine`, `tail_risk_engine` y `skew_fattails_engine` para asimetrías de cola[file:1].
- **Context map**: combinación de `fear_greed_engine`, `sentiment_engine`, `cross_asset_engine`, `correlation_analyzer` y ratios de liquidez global deseados por el sistema[cite:3][file:1].

### Indicadores de opciones compuestos

- **Dealer regime map**: combinación de `dealer_flow_dynamics_engine`, `gamma_flip`, `gamma_flip_probability`, `dex_engine` y `gamma_exposure_engine` para soporte/supresión/pinning[file:1].
- **Chain quality map**: combinación de `chain_institutional_analytics`, `options_flow_signal`, `options_order_flow_toxicity_engine`, `fractal_oi` y `volume_oi_engine` para liquidez y limpieza de cadena[file:1].
- **Volatility map**: combinación de `iv_primitives`, `iv_surface_models`, `volatility_surface_engine`, `volatility_skew_engine` y `derivatives` para determinar si la prima está barata, justa o cara[file:1].
- **Structure map**: combinación de `options_confluence`, `options_models`, `confluence_models` (Options) y `strategy_payoff` para decidir la estructura más eficiente[file:1].

## Playbooks estratégicos del módulo

El módulo debe operar con familias de estrategias encapsuladas en playbooks, no con señales sueltas[file:1]. Cada playbook define contexto válido, precondiciones mínimas, estructura permitida, política de contrato, reglas de salida e invalidaciones[file:1].

### Playbook 1: Trend continuation

Este playbook se activa cuando la estructura técnica está alineada, la capa predictiva favorece continuidad y la capa de opciones no detecta freno dealer o prima excesiva[file:1]. La estructura preferida será long call/put o debit spread, dependiendo del estado de IV y del coste relativo de la prima[file:1].

Precondiciones sugeridas:

- `trend_quality_score` alto[file:1]
- `regime_class = trend`[file:1]
- `dealer_regime != suppressive`[file:1]
- `iv_state` no extremo[file:1]

### Playbook 2: Gamma wall rejection

Se activa cuando el spot se aproxima a niveles sensibles de gamma/delta, existe rechazo técnico y el flujo no confirma continuación limpia[file:1]. Su uso principal es capturar rebotes/rechazos tácticos con opciones de corta duración y objetivos precisos[file:1].

Precondiciones sugeridas:

- proximidad a `gamma_flip` o murallas OI[file:1]
- rechazo en `smc_fractal_engine`, `vsa_engine` o `market_structure_engine`[file:1]
- ausencia de flow conviction a favor de ruptura[file:1]

### Playbook 3: Compression breakout

Se activa cuando el mercado está comprimido, la probabilidad de expansión aumenta y aparece confirmación de iniciativa real en microestructura y rango estadístico[file:1]. Puede desembocar en calls, puts o incluso evaluaciones neutras si la expansión es más clara que la dirección[file:1].

### Playbook 4: Event volatility

Se activa en presencia de earnings, noticias o catalizadores fuertes donde expected move, skew y confluencia de opciones justifican una estructura de riesgo definido[file:1]. Debe compararse siempre el movimiento implícito frente al escenario proyectado y penalizar operaciones donde la prima ya descuenta demasiado[file:1].

### Playbook 5: Tail risk defense

Se activa cuando la cola izquierda, la superficie IV o la distribución implícita señalan fragilidad sistémica[file:1]. Puede servir tanto para seleccionar puts defensivas como para bloquear calls y reducir agresividad del sistema[file:1].

## Router de selección de estructuras

El módulo necesita una capa dedicada de selección de estructura que no dependa únicamente de la dirección, sino de la relación entre convicción, IV, cola, expected move y calidad de cadena[file:1]. La lógica recomendada es la siguiente:

| Contexto | IV state | Convicción | Estructura preferida |
|---|---|---|---|
| Sesgo alcista limpio [file:1] | cheap/fair [file:1] | Alta [file:1] | Long call [cite:4][file:1] |
| Sesgo alcista limpio [file:1] | rich [file:1] | Media/alta [file:1] | Call debit spread [file:1] |
| Sesgo bajista limpio [file:1] | cheap/fair [file:1] | Alta [file:1] | Long put [cite:4][file:1] |
| Sesgo bajista limpio [file:1] | rich [file:1] | Media/alta [file:1] | Put debit spread [file:1] |
| Reversión táctica [file:1] | fair [file:1] | Alta pero corta duración [file:1] | Long option DTE corto [file:1] |
| Tail risk elevado [file:1] | rich/extreme [file:1] | Defensiva [file:1] | Put spread o no-trade [file:1] |

## Selección de contrato

El `contract_selector` debe ser un submódulo crítico y no una regla menor, porque de él depende que una buena señal se traduzca en una ejecución operable y líquida[file:1]. Debe aplicar filtros de open interest, spread bid-ask, delta bucket, DTE, coste máximo, disponibilidad y consistencia con la estructura elegida[file:1].

Parámetros sugeridos:

- bucket DTE principal: 7 a 21 días para señales tácticas[cite:24]
- delta objetivo outright: 0.25 a 0.45[file:1]
- delta de la pata corta en spreads: 0.15 a 0.25[file:1]
- mínimo open interest y volumen por strike[file:1]
- spread bid-ask máximo porcentual[file:1]
- exclusión de cadenas con liquidez institucional insuficiente[file:1]

## Gestión del riesgo

La gestión de riesgo debe estar desacoplada del sesgo de mercado y actuar como sistema operativo del módulo[cite:6]. Debe incluir sizing, límites por trade, límites agregados, reglas de no-trade, control por exposición de cartera y salidas por invalidación, tiempo o deterioro del edge[file:1].

Componentes mínimos:

- `position_sizing.py`: riesgo por operación, premium-at-risk, notional equivalente y límites relativos[cite:6]
- `portfolio_limits.py`: máximo de riesgo diario, máximo por ticker, máximo por familia de playbook y correlación agregada[cite:6][cite:3]
- `trade_guardrails.py`: vetos por cola, toxicidad, iliquidez, conflicto multicapas, evento no permitido[file:1]
- `exit_manager.py`: stop por prima, stop por tesis, take profit parcial, time stop y cierre por deterioro de estructura[file:1]

Reglas recomendadas:

- pérdida máxima por operación expresada como porcentaje del equity o presupuesto diario[cite:6]
- límite de pérdida diaria por módulo[cite:6]
- bloqueo automático ante encadenamiento de trades fallidos en un mismo playbook[file:1]
- reducción de tamaño cuando `forecast_dispersion_score` sea alto[file:1]
- prioridad a spreads frente a opciones outright cuando la prima esté rica[file:1]

## Configuración externa

La configuración debe vivir fuera del código para facilitar calibración, deployment y control de versiones[cite:6]. Se recomienda al menos un archivo principal del motor, uno de playbooks, uno de reglas de riesgo y uno de universo/contratos[cite:6].

### Ejemplo de `omni_engine.yaml`

```yaml
omni_engine:
  enabled_layers: [technical, predictive, options]
  fusion_mode: weighted_hierarchical
  min_global_confidence: 0.68
  weights:
    technical: 0.30
    predictive: 0.30
    options: 0.40
  disagreement_penalty: 0.15
  veto_rules:
    - tail_risk_critical
    - options_flow_toxic
    - chain_liquidity_poor
    - event_blackout
```

### Ejemplo de `playbooks.yaml`

```yaml
playbooks:
  trend_continuation:
    enabled: true
    min_trend_quality: 0.65
    min_predictive_bias: 0.55
    min_options_bias: 0.60
    allowed_structures: [long_call, long_put, call_debit_spread, put_debit_spread]
  gamma_wall_rejection:
    enabled: true
    require_gamma_level: true
    max_dte: 14
    allowed_structures: [long_call, long_put]
  compression_breakout:
    enabled: true
    require_breakout_state: confirmed
    allowed_structures: [long_call, long_put, call_debit_spread, put_debit_spread]
```

### Ejemplo de `risk_rules.yaml`

```yaml
risk:
  max_risk_per_trade_pct: 0.75
  max_daily_loss_pct: 2.0
  max_open_positions: 4
  max_same_direction_exposure_pct: 2.5
  min_chain_liquidity_score: 0.60
  max_bid_ask_spread_pct: 8.0
  cooldown_after_loss_minutes: 45
```

### Ejemplo de `options_universe.yaml`

```yaml
# Universo = solo Ruta 1. Lista canónica en backend/config/alpaca_priority_route.py
universe:
  source: alpaca_route1          # resuelve ALPACA_ROUTE1_WATCHLIST en runtime
  enforce_route1_only: true    # gate duro; símbolos fuera → NO_TRADE
  # underlyings: NO listar manualmente — evita drift con el bot dual-route
  dte_min: 7
  dte_max: 21
  min_open_interest: 500
  min_daily_volume: 100
  allowed_order_types: [limit]
  trade_sessions: [regular]
```

## Reason codes y auditoría

Todo trade o no-trade debe quedar explicado por códigos estandarizados, ya que el módulo no será realmente utilizable si no puede auditarse ex post[cite:6]. Los `reason_codes` deben acompañar cada decisión, cada payload y cada registro de auditoría[cite:6].

Ejemplos de `reason_codes`:

- `smc_bullish_alignment`[file:1]
- `above_vwap_with_acceptance`[file:1]
- `markov_regime_trend`[file:1]
- `positive_dealer_support`[file:1]
- `iv_rich_prefer_spread`[file:1]
- `tail_risk_critical_veto`[file:1]
- `chain_liquidity_poor_veto`[file:1]
- `options_flow_toxic_veto`[file:1]
- `symbol_not_in_route1_universe` — símbolo fuera de `ALPACA_ROUTE1_WATCHLIST`; veto inmediato[file:1]
- `expected_move_supportive`[file:1]
- `macro_alignment_risk_on`[cite:3][file:1]

Cada registro de auditoría debería guardar:

- timestamp
- snapshot de inputs
- features normalizadas
- pesos activos del motor
- playbook elegido
- estructura candidata
- payload enviado a ejecución
- fill o rechazo del broker[web:9]
- outcome posterior y feedback de desempeño[file:1]

## Integración con Alpaca

La integración con Alpaca debe quedar encapsulada en un adaptador específico, ya que la API de opciones utiliza la Orders API de Alpaca y conviene mantener ese detalle aislado de la lógica estratégica[web:9]. El módulo estratégico no debe conocer detalles del broker más allá del contrato de payload de ejecución[cite:4][web:9].

Responsabilidades del `alpaca_executor.py`:

- traducir `execution_payload` a órdenes válidas de Alpaca[web:9]
- resolver símbolos OCC/contratos concretos[web:9]
- usar `client_order_id` para trazabilidad[web:9]
- gestionar envío, cancelación, reintentos y reconciliación de fills[web:9]
- persistir respuesta del broker y estado final[web:9]

## Loops operativos

El módulo necesita al menos tres loops desacoplados para funcionar de forma robusta[cite:6].

### Signal loop

Se ejecuta con frecuencia intradía y recalcula snapshots, features y decisiones sobre universo permitido[cite:6]. Su salida puede ser `NO_TRADE`, `EXECUTE`, `REDUCE` o `EXIT`[cite:24].

### Position management loop

Monitorea posiciones vivas, reevaluando invalidación técnica, deterioro predictivo, cambios en IV, nuevos niveles dealer y condiciones de salida[file:1]. Esta capa evita depender únicamente de stops fijos y permite cierres por pérdida del edge[file:1].

### Calibration loop

Se ejecuta offline, idealmente por CRON en horarios no operativos, y utiliza `feedback_engine`, `factor_calibration`, `meta_learner` y `ensemble_training` para ajustar pesos, thresholds y eficacia por playbook[file:1][cite:6]. Esta separación protege la estabilidad del loop operativo en tiempo real[cite:6].

## Roadmap de implementación

El desarrollo del módulo conviene dividirlo en fases, no intentar desplegar todo al mismo tiempo[cite:6]. Un roadmap razonable sería:

1. **Fase 1**: contratos internos, snapshots, feature store y adaptadores de datos[file:1].
2. **Fase 2**: technical layer + predictive layer básicos, con salida normalizada[file:1].
3. **Fase 3**: options layer, contract selector y structure selector[file:1].
4. **Fase 4**: fusion router, veto engine y playbooks iniciales[file:1].
5. **Fase 5**: risk engine, exit manager y auditoría persistente[cite:6].
6. **Fase 6**: integración con Alpaca paper trading[web:9].
7. **Fase 7**: calibration loop y ajuste offline de pesos[file:1][cite:6].

## MVP recomendado

El MVP del módulo debe enfocarse en demostrar coherencia del pipeline, no en cubrir todos los motores del registro desde el primer día[cite:6]. La recomendación es arrancar con un subconjunto fuerte pero manejable de motores y cuatro estructuras: `long_call`, `long_put`, `call_debit_spread` y `put_debit_spread`[cite:4][file:1].

**Alcance de universo MVP:** solo los 11 tickers de Ruta 1 (`ALPACA_ROUTE1_WATCHLIST`). R2 y scan dinámico quedan fuera de alcance del módulo de opciones en todas las fases.

Subset recomendado para MVP:

- Técnica: `smc_engine`, `market_structure_engine`, `vwap_engine`, `volume_profile_engine`, `ofi_engine`[file:1]
- Predictiva: `markov_regime_engine`, `expected_move_engine`, `tail_risk_engine`, `fear_greed_engine`[file:1]
- Opciones: `dealer_flow_dynamics_engine`, `gamma_flip`, `dex_engine`, `iv_primitives`, `options_flow_signal`, `strategy_payoff`[file:1]

Con ese conjunto ya se puede construir un router funcional que clasifique régimen, elija estructura, filtre contratos y emita payloads operables hacia Alpaca[web:9][file:1]. Después pueden añadirse capas más sofisticadas como `risk_neutral_density_engine`, `volatility_surface_engine`, `catalyst_nlp_engine`, `fractal_oi` y meta-learners[file:1].

## Riesgos de diseño a evitar

El principal riesgo es crear un sistema que “mezcle todo” sin normalización, jerarquía ni trazabilidad[file:1]. Eso produce redundancia de señales, confianza artificial, decisiones opacas y una complejidad imposible de depurar o calibrar[file:1].

Errores que deben evitarse:

- sumar motores correlacionados como si fueran evidencia independiente[file:1]
- permitir entradas aunque la calidad de cadena sea pobre[file:1]
- usar score direccional sin vetos por cola o toxicidad[file:1]
- acoplar selección de estrategia con ejecución broker-specific[cite:4][web:9]
- depender de parámetros hardcodeados y no auditables[cite:6]
- no distinguir entre falta de señal y señal negativa; `NO_TRADE` debe existir como decisión explícita[file:1]
- ampliar el universo de opciones más allá de R1 sin revisión de blueprint (R2 no tiene pipeline GEX/confluencia completo)[file:1]

## Especificación mínima del payload final

El payload final del módulo debe ser compacto, estable y apto para ejecución automática[cite:24][web:9]. Una versión base podría ser la siguiente:

```json
{
  "symbol": "SPY",
  "timestamp": "2026-06-13T21:30:00-03:00",
  "decision": "EXECUTE",
  "playbook_family": "trend_continuation",
  "recommended_structure": "call_debit_spread",
  "direction": "bullish",
  "global_confidence": 0.74,
  "dte_target": 14,
  "delta_buy_target": 0.38,
  "delta_sell_target": 0.20,
  "max_premium_usd": 250.0,
  "risk_budget_pct": 0.60,
  "veto_triggered": null,
  "reason_codes": [
    "smc_bullish_alignment",
    "above_vwap_with_acceptance",
    "markov_regime_trend",
    "positive_dealer_support",
    "iv_rich_prefer_spread"
  ]
}
```

## Cierre técnico

El módulo de estrategias de opciones debe entenderse como un sistema de decisión multicapas, con especialización por playbooks, configuración externa, auditoría integral y una separación estricta entre análisis y ejecución[cite:24][cite:6]. El registro de motores ya contiene suficientes piezas para construir una base institucional sólida; la clave no está en activarlas todas al mismo tiempo, sino en ordenarlas por función, normalizarlas, fusionarlas con disciplina y desplegarlas por fases[file:1].
