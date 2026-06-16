# Especificación Técnica - Fase 1: Contrato Canónico y Selección de Motores

Este documento establece la definición formal de la frontera de integración entre los motores cuantitativos de señales y el módulo de control contractual y riesgo de supervivencia (`05 funding`).

---

## 1. Definición del Contrato Canónico

El modelo `CanonicalSignalPayload` (definido en [canonical_signal.py](file:///c:/dev/deep-funnel-station/backend/models/canonical_signal.py)) unifica los contratos de futuros lineales y de opciones multi-pata en una sola interfaz que el motor de riesgo de supervivencia del funding puede procesar de manera uniforme.

### Campos y Especificación de Tipos

| Campo | Tipo | Requerido | Descripción / Validación |
|---|---|---|---|
| `symbol` | `str` | Sí | Símbolo en mayúsculas (ej. `SPY`, `MNQ`). |
| `asset_type` | `Literal` | Sí | Valores válidos: `"equity"`, `"option"`, `"future"`, `"crypto"`, `"cash"`, `"other"`. |
| `direction` | `Literal` | Sí | Mapea la dirección: `"bullish"`, `"bearish"`, `"neutral"`. |
| `confidence` | `float` | Sí | Nivel de confianza ponderado normalizado en rango `[0.0, 1.0]`. |
| `entry_price` | `Decimal` | Sí | Precio proyectado de entrada o spot de referencia. Debe ser mayor a 0. |
| `stop_loss_price` | `Decimal` | Opcional | Precio del subyacente para disparo del stop. Opcional en opciones. |
| `max_loss_usd` | `Decimal` | Opcional | Coste neto de la prima (opciones) o pérdida estimada en el stop (futuros). |
| `structure` | `str` | Sí | Nombre identificativo de la estructura (ej. `"long_call"`, `"call_debit_spread"`, `"linear"`). |
| `legs` | `tuple[CanonicalLegSpec, ...]` | Sí | Lista de patas del trade. En spreads de opciones contiene 2 patas; en futuros o outrights está vacío. |
| `source_engine` | `str` | Sí | Identificador del motor de señales (ej. `"omni_engine"`). |
| `timestamp` | `datetime` | Sí | Marca de tiempo con zona horaria UTC explícita. |
| `reason_codes` | `tuple[str, ...]` | Sí | Lista ordenada de códigos de justificación que respaldan la señal. |

---

## 2. Mapa de Mapeo de Campos de Señal a Payload Canónico

Cuando el loop de señal genera una propuesta desde el meta-motor `omni_engine`, la salida debe transformarse en el payload canónico de acuerdo con las siguientes reglas de conversión:

| Campo Canónico | Entrada desde `OptionsExecutionPayload` / `PlaybookDecision` | Regla de Conversión |
|---|---|---|
| `symbol` | `payload.symbol` | Uppercase + strip. |
| `asset_type` | Deducido según motor origen | Fijo en `"option"` para `omni_engine`. |
| `direction` | `payload.direction` | Directo: `"bullish"`, `"bearish"`, o `"neutral"`. |
| `confidence` | `payload.global_confidence` o `decision.confidence` | Directo. Rango validado `[0.0, 1.0]`. |
| `entry_price` | Precio de spot de mercado de la señal | Convertido a `Decimal` de alta precisión. |
| `stop_loss_price` | `payload.stop_loss_price` o deducido de la pata de salida | Convertido a `Decimal` si se dispone del valor del subyacente. |
| `max_loss_usd` | `payload.max_premium_usd` o pérdida en stop loss | Convertido a `Decimal`. Representa la prima neta de las patas en spreads. |
| `structure` | `payload.recommended_structure` (StrEnum) | Mapeado directo a string (ej. `"call_debit_spread"`). |
| `legs` | `payload.legs` | Mapea cada `OptionsLegSpec` a `CanonicalLegSpec`. Traduce `side` (`"buy"`/`"sell"`) y extrae `contract_symbol` y `ratio`. |
| `source_engine` | `"omni_engine"` | Identificador por defecto del meta-motor. |
| `timestamp` | `payload.timestamp` | Debe mantener la zona horaria UTC (`tzinfo` no nulo). |
| `reason_codes` | `payload.reason_codes` | Tupla de strings consolidados. |

---

## 3. Catálogo Inicial de Códigos de Justificación (`reason_codes`)

El sistema implementa códigos estandarizados para que las decisiones del sizer, vetos y cockpit sean auditables y correlacionables ex post.

### 3.1 Códigos de Señal (Motores Cuantitativos)
* **Technical (`technical_layer`)**:
  - `smc_bullish_alignment`: Estructura alcista alineada en Smart Money Concepts.
  - `smc_bearish_alignment`: Estructura bajista alineada en Smart Money Concepts.
  - `above_vwap_with_acceptance`: Precio por encima del VWAP diario con volumen de aceptación.
  - `below_vwap_with_rejection`: Rechazo por debajo de la zona del VWAP.
  - `market_structure_trend_bullish`: Tendencia alcista confirmada en estructura del mercado.
  - `market_structure_trend_bearish`: Tendencia bajista confirmada en estructura del mercado.
  - `compress_breakout_ignition`: Activación de salida del estado de compresión de volatilidad.
* **Predictive (`predictive_layer`)**:
  - `markov_regime_trend_quiet`: Régimen clasificado en tendencia alcista tranquila.
  - `markov_regime_mean_reversion`: Mercado en régimen de reversión a la media.
  - `expected_move_supportive`: Objetivo proyectado dentro del rango esperado calculado por IV.
  - `fear_greed_fear_buy`: Pánico generalizado (oportunidad técnica de compra).
  - `fear_greed_greed_sell`: Codicia extrema (precaución o corto técnico).
* **Options (`options_layer`)**:
  - `dealer_supportive_gex`: Creadores de mercado en régimen GEX que amortigua caídas.
  - `dealer_suppressive_gex`: Posicionamiento dealer que suprime rallys.
  - `gamma_flip_positive`: Transición a régimen de Gamma Positiva (baja volatilidad).
  - `gamma_flip_negative`: Transición a régimen de Gamma Negativa (alta volatilidad).
  - `options_flow_conviction_high`: Flujo institucional neto con alta convicción en la dirección.
  - `iv_cheap_buy_outright`: Volatilidad implícita barata; preferencia de compra de opción limpia (*outright*).
  - `iv_rich_buy_spread`: Volatilidad implícita cara; preferencia por spreads de débito para mitigar coste.

### 3.2 Códigos de Riesgo y Veto (Funding Layer)
* **Veto de Señal / Mercado**:
  - `symbol_not_in_route1_universe`: Símbolo fuera del universo prioritario de Ruta 1.
  - `chain_liquidity_poor_veto`: Iliquidez de strikes o interés abierto insuficiente.
  - `options_flow_toxic_veto`: Flujo institucional en contra de la dirección del trade candidate.
  - `tail_risk_critical_veto`: Riesgo de cola izquierdo extremo (cisne negro inminente).
* **Bloqueos Contractuales de Supervivencia**:
  - `daily_loss_limit_reached`: Pérdida diaria límite de la cuenta alcanzada (DLL).
  - `drawdown_limit_violated`: Equity de la cuenta por debajo del piso mínimo permitido.
  - `max_open_positions_exceeded`: Límite máximo de posiciones abiertas alcanzado en la sesión.
  - `cooldown_active_after_loss`: Bloqueo preventivo temporal tras una operación perdedora reciente.

---

## 4. Justificación del Subset de Motores Mínimos para V1

Para evitar latencia innecesaria en el hilo de ejecución intradía y asegurar que las integraciones iniciales sean 100% estables, se define la siguiente selección de motores para la primera versión productiva:

1. **Capa Técnica (Technical Layer)**:
   - `MarketStructureEngine`: Aporta la tendencia estructural macro y micro del precio (BOS/MSS).
   - `SMCEngine`: Identifica zonas de oferta/demanda y bloques institucionales como soporte/resistencia física.
   - `VWAPEngine`: Actúa como el ancla de precio promedio de la sesión regular para valorar desviaciones extremas.
2. **Capa Predictiva (Predictive Layer)**:
   - `MarkovRegimeEngine`: Determina si el mercado actual favorece estrategias de rango o de tendencia.
   - `ExpectedMoveEngine`: Establece la desviación típica estadística implícita para acotar la selección de vencimientos y deltas.
3. **Capa de Opciones (Options Layer)**:
   - `GammaFlipEngine`: Determina si el subyacente opera bajo dinámicas de cobertura supresiva o expansiva.
   - `IV Primitives` / `Historical Volatility`: Permite la clasificación simple de estados de IV (`IvState`) para dirimir entre estructuras individuales y compuestas (outright vs spread).
   - `OptionsFlowSignalEngine`: Captura la dirección e ímpetu del capital inteligente (bloques y sweepers).
