# Mejoras propuestas para `FUNDING_MODULE_ROADMAP_MAESTRO_v3.md`

## Objetivo
Este documento reúne los añadidos y ajustes de mayor impacto para fortalecer el roadmap principal antes de pasarlo al IDE. El documento base ya define una arquitectura de 7 capas, con 27 motores distribuidos entre microestructura, opciones, predictivos y servicios de funding, y detecta correctamente que la brecha crítica actual es la ausencia de una validación estadística continua del edge antes de operar [cite:1]. También incorpora un `CNN Fear/Greed`, un módulo `Cross-Asset` y reglas de régimen apoyadas en VIX, lo que crea una base adecuada para extender el sistema con una capa más formal de contexto global [cite:1].

## Mejora prioritaria
La mejora estructural más clara es agregar una capa transversal de **Contexto Global / Régimen Intermercado** entre la salida de señales y el `SizingEngine`. El documento actual ya usa VIX, Fear/Greed y correlaciones cross-asset en distintos puntos, pero todavía no los concentra en un motor único, explícito y reusable que sintetice liquidez global, rotación de liderazgo y amplitud de mercado en una sola salida operativa [cite:1].

La lógica de esta incorporación es consistente con la propia filosofía del roadmap: las señales no deberían evaluarse aisladas, sino condicionadas por un entorno de volatilidad, apetito por riesgo, crédito y correlación entre activos [cite:1]. Además, los indicadores de liquidez global son una herramienta reconocida para interpretar condiciones financieras y su impacto sobre los mercados, especialmente cuando se combinan métricas de crédito, dinero y flujos cross-border [cite:23][cite:26].

## Nuevo módulo sugerido
### `GlobalContextEngine`
Se propone incorporar un módulo nuevo denominado `GlobalContextEngine`, ubicado conceptualmente entre la Capa 3 y la Capa 4, o bien como un servicio transversal que alimente a `PerformanceAnalyticsEngine`, `SizingEngine`, `FTMO Survival Score` y `Portfolio Risk Service`. Su función no sería generar señales direccionales primarias, sino transformar el contexto macro-financiero en variables normalizadas de régimen que afecten sizing, filtros y bloqueos [cite:1].

### Inputs recomendados
| Variable | Qué captura | Justificación |
|---|---|---|
| VIX | Estrés implícito y volatilidad esperada | El documento ya usa VIX para modular pesos del Survival Score y para reducir tamaño bajo extremos de Fear/Greed [cite:1] |
| Fear & Greed Index | Apetito por riesgo / complacencia | CNN lo construye a partir de siete componentes de sentimiento y momentum del mercado [cite:4] |
| SPY/EEM | Liderazgo USA vs emergentes | Refleja preferencia por activos estadounidenses frente a emergentes y suele responder a dólar y flujos globales [cite:12][cite:18] |
| QQQ/IWM | Megacaps growth vs small caps | Ayuda a leer concentración, amplitud y salud del rally interno del mercado [cite:19][cite:25] |
| XLY/XLP | Consumo cíclico vs defensivo | Proxy clásico de risk-on / risk-off en renta variable [cite:4] |
| HYG/TLT o HYG/LQD | Crédito vs duración / calidad | Aproxima condiciones financieras y estrés de crédito [cite:4][cite:23] |
| Breadth ratio | Participación del mercado | Útil para diferenciar rally sano de rally estrecho [cite:22][cite:4] |
| Crisis / correlation index | Ruptura de diversificación | El documento ya lo contempla como reducción fuerte de tamaño cuando la correlación tiende a 1 [cite:1] |

### Outputs recomendados
El nuevo módulo debería emitir, como mínimo, las siguientes variables:

- `global_regime_score` en escala 0-1.
- `risk_on_off_state` con estados `RISK_ON`, `NEUTRAL`, `RISK_OFF`, `PANIC`.
- `liquidity_rotation_score` para captar rotación entre growth, small caps, emergentes y defensivos.
- `macro_conflict_score` para medir contradicción entre señal local y contexto global.
- `global_context_multiplier` para modular tamaño.
- `global_hard_block` para eventos extremos.

Esto haría que el contexto global deje de ser un conjunto de señales dispersas y pase a ser una pieza formal del pipeline de decisión [cite:1].

## Cambios concretos al roadmap
### 1. Extender la arquitectura a 8 capas lógicas
El documento principal hoy presenta 7 capas y ubica opciones, predictivos y salidas de régimen dentro de la Capa 3 [cite:1]. La mejora recomendada es insertar una nueva capa lógica:

```text
CAPA 3.5 — GLOBAL CONTEXT ENGINE
VIX · Fear/Greed · SPY/EEM · QQQ/IWM · XLY/XLP · HYG/TLT · Breadth · DXY proxies
Output: global_regime_score, risk_on_off_state, macro_conflict_score, global_context_multiplier
```

Este cambio ayuda a separar tres niveles que hoy aparecen parcialmente mezclados: señal micro, señal predictiva y contexto macro/intermercado [cite:1].

### 2. Incorporar ratios relativos como features de régimen
Los ratios sugeridos no deberían tratarse como indicadores discretos simples, sino como series relativas normalizadas con z-score, slope, distancia a medias y régimen de ruptura. La relación SPY/EEM puede capturar preferencia por EEUU frente a emergentes, mientras QQQ/IWM aporta una lectura de concentración de liderazgo y amplitud interna del equity estadounidense [cite:12][cite:18][cite:19].

Una implementación razonable sería calcular para cada ratio:
- `ratio_level_z20`
- `ratio_level_z60`
- `ratio_slope_5d`
- `ratio_slope_20d`
- `distance_to_ma50`
- `distance_to_ma200`
- `breakout_state`

Eso evita usar valores absolutos poco robustos y transforma cada ratio en una señal de cambio de régimen más defendible [cite:12][cite:19].

### 3. Integrar contexto global al `SizingEngine`
El modelo actual usa `Kelly_base × F_vol × F_dd × F_signal × F_regime × F_conviction` [cite:1]. La mejora recomendada es ampliar la fórmula de esta manera:

```python
allowed_risk_pct = min(
    kelly_base
    * F_vol
    * F_dd
    * F_signal
    * F_regime
    * F_conviction
    * F_global,
    survival_recommended_risk_pct,
    remaining_daily_risk_pct,
    remaining_max_risk_pct,
    FTMO_BASE_RISK_PER_TRADE_PCT
)
```

Donde `F_global` sería una función del `global_regime_score`, la amplitud y la rotación intermercado. Esto es coherente con la idea ya presente en el documento de que el tamaño debe reaccionar al régimen, no solo a la señal local [cite:1].

### 4. Sumar conflicto macro al `Conflict Resolution`
El roadmap ya define una cascada de resolución de conflictos basada en hard blocks, dirección opuesta entre scanner y predictivo, GEX adverso y métricas de riesgo cuantitativo [cite:1]. Conviene añadir una regla adicional:

```python
if global_context.macro_conflict_score >= 0.7:
    size_multiplier *= 0.5
if global_context.global_hard_block:
    return BLOCK
```

Esto permite capturar un caso realista: una señal técnicamente buena, pero dentro de un entorno global muy hostil, con volatilidad implícita alta, mala amplitud y deterioro de crédito [cite:4][cite:23].

### 5. Hacer que el `FTMO Survival Score` vea liquidez global
El Survival Score actual pondera buffers de pérdida, consistencia, calidad OOS y presión de conflicto, y además adapta pesos según el régimen de VIX [cite:1]. La mejora natural es incluir una sexta dimensión que mida tensión global de liquidez o risk-off estructural.

Ejemplo conceptual:

```text
Survival = 0.25×D + 0.20×M + 0.15×C + 0.15×H + 0.10×(1 − K_max) + 0.15×G
```

donde `G` representa la salud del contexto global. Esto le da al score una sensibilidad más realista frente a jornadas donde los límites de la prop firm todavía no están comprometidos, pero el entorno macro está empeorando con rapidez [cite:1][cite:23].

## Ratios e indicadores prioritarios
### Prioridad alta
Estos son los añadidos que más valor aportarían sin disparar demasiado la complejidad:

| Indicador | Uso principal | Acción sugerida |
|---|---|---|
| VIX | Volatilidad esperada | Multiplicador defensivo y cambio de pesos de supervivencia [cite:1][cite:4] |
| Fear & Greed | Sentimiento agregado | Reducir tamaño en extremos y confirmar régimen [cite:1][cite:4] |
| SPY/EEM | Liquidez global / rotación regional | Ajustar sesgo risk-on global [cite:12][cite:18] |
| QQQ/IWM | Concentración vs amplitud | Penalizar rallies estrechos [cite:19][cite:22][cite:25] |
| Breadth | Calidad interna del mercado | Confirmación o veto de momentum [cite:22][cite:4] |
| HYG/TLT o HYG/LQD | Crédito y estrés financiero | Hardener del sizing cuando el crédito deteriora [cite:4][cite:23] |

### Prioridad media
| Indicador | Uso principal | Acción sugerida |
|---|---|---|
| XLY/XLP | Risk-on vs defensivo | Confirmación secundaria de apetito por riesgo [cite:4] |
| QQQ/SPY | Concentración megacap | Medir dependencia de pocas leaders [cite:19] |
| DXY proxies | Fortaleza del dólar | Confirmar presión sobre emergentes y activos de riesgo [cite:18][cite:23] |
| MOVE o tasas implícitas | Estrés en bonos | Complemento del VIX cuando el riesgo viene de rates [cite:23] |

## Mejoras metodológicas
### Evitar proliferación de thresholds arbitrarios
El documento principal ya tiene numerosos umbrales para VPIN, OFI, GEX, Fear/Greed, BUR y otros módulos [cite:1]. Agregar contexto global puede volver el sistema más inteligente, pero también más frágil si se suman muchos thresholds fijos sin evidencia suficiente [cite:1]. Por eso conviene que los nuevos indicadores entren primero como variables continuas, calibradas con z-scores, percentiles rolling y validación out-of-sample [cite:1].

### Reducir complejidad con una jerarquía clara
Una mejora importante para el IDE es declarar explícitamente qué variables son de:
- observación,
- reducción de tamaño,
- veto blando,
- veto duro.

Esto ayuda a que el equipo no mezcle métricas informativas con criterios ejecutivos. El riesgo actual del roadmap es que demasiados módulos terminen compitiendo por bloquear la misma operación por razones distintas [cite:1].

### Añadir tests específicos de régimen global
El roadmap ya prevé tests de integración, Monte Carlo y backtest OOS mensual [cite:1]. Habría que sumar:
- test de sensibilidad del sizing ante shocks de VIX,
- test de rotación de régimen con SPY/EEM y QQQ/IWM,
- test de conflicto entre señal local y contexto global,
- test de estabilidad de `global_regime_score` para evitar whipsaws.

Esto es importante porque el valor del contexto global no está en predecir giros exactos, sino en evitar operar con agresividad cuando la estructura de mercado se deteriora [cite:23][cite:26].

## Cambios de redacción sugeridos al documento principal
### Añadir sección nueva en Parte I o Parte IV
Se sugiere incorporar una subsección nueva con este título:

```text
1.4 Global Context Engine — Régimen Macro, Liquidez y Rotación Intermercado
```

o alternativamente:

```text
4.6 Global Regime Overlay — Contexto Macro para Sizing y Supervivencia
```

La primera opción lo presenta como motor cuantitativo; la segunda como overlay de control. La segunda probablemente sea mejor si se quiere evitar que el equipo lo interprete como un generador de señal autónomo [cite:1].

### Añadir reason codes nuevos
Conviene proponer reason codes específicos para que el IDE los incorpore desde el inicio:

- `riskglobalcontextpanic`
- `riskbreadthdeterioration`
- `riskcreditstress`
- `riskrotationagainstsignal`
- `riskemunderperformance`
- `risksmallcapsbreadthfailure`

Esto mantiene coherencia con la filosofía actual de trazabilidad, auditoría y decisión determinística [cite:1].

## Implementación mínima viable
La versión MVP de esta mejora debería ser deliberadamente pequeña. En vez de meter diez ratios desde el primer día, conviene empezar con:

1. VIX.
2. Fear & Greed.
3. SPY/EEM.
4. QQQ/IWM.
5. Breadth.
6. HYG/TLT.

Con eso ya se obtiene una lectura bastante rica de volatilidad, sentimiento, amplitud, crédito y rotación global [cite:4][cite:12][cite:19][cite:22][cite:23]. Después, si la evidencia OOS lo justifica, se pueden añadir overlays sectoriales o de dólar [cite:18][cite:23].

## Recomendación final para el IDE
La recomendación concreta es **no reescribir el roadmap**, sino extenderlo con un bloque de contexto global formal, pocos ratios al inicio y una integración directa con sizing, survival y conflict resolution. El documento base ya contiene gran parte de los conceptos necesarios; lo que falta es consolidarlos en una pieza arquitectónica única, explícita y testeable [cite:1].

En términos de impacto, esta mejora haría tres cosas a la vez: mejorar la lectura de régimen, reducir operaciones agresivas en contextos hostiles y darle al sistema una capa más robusta de coherencia macro antes de arriesgar capital [cite:1][cite:23][cite:26].
