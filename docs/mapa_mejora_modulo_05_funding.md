# Mapa de mejora del módulo 05 funding

## Panorama general

El módulo `05 funding` opera hoy como una **Funding Decision Layer** post-señal que recibe un `TradeCandidate` y un `AccountState`, y luego aplica una cascada de validación compuesta por `BuilderStateMachine`, `BuilderRuleEngine`, `BuilderPayoutEngine`, `BuilderSurvivalEngine` y `BuilderSizingOverlay` antes de aprobar o bloquear una operación [cite:1]. Su responsabilidad actual está bien definida para control contractual y supervivencia, pero no para generación de señal, priorización cross-sectional ni operación reactiva conectada a flujos vivos de mercado [cite:1].

En paralelo, el `omni_engine` ya está diseñado como un meta-motor de tres capas —Technical, Predictive y Options— con una capa de fusión y un router de ejecución hacia Alpaca, lo que lo convierte en la fuente natural de señal para una integración futura [cite:2]. Sin embargo, hoy existe una ruptura de contrato entre ese dominio de señal y el dominio del funding, porque `omni_engine` emite un payload orientado a opciones y el funding espera un candidato lineal con entrada, stop, dirección y símbolo [cite:1][cite:2].

La lista completa de motores cuantitativos confirma además que el stack disponible es amplio y heterogéneo, con motores maduros de estructura, régimen, flujo y opciones, pero también con piezas auxiliares, experimentales o de soporte que no deberían entrar todas en una primera versión operativa [cite:3]. Por eso, el objetivo del roadmap no es “conectar todo”, sino construir una integración robusta y acotada entre señal, gating de riesgo y ejecución [cite:1][cite:2][cite:3].

## Diagnóstico actual

### Fortaleza del módulo funding

La arquitectura actual de `05 funding` tiene una fortaleza clara: separa bien la lógica contractual por fases, drawdown, límite diario, payout y supervivencia, y además ya expone endpoints para estado, métricas, evaluación puntual, evaluación batch y backtest [cite:1]. También cuenta con reason codes determinísticos, métricas de cockpit y una lógica explícita para piso de drawdown, consistencia y sizing, lo cual reduce ambigüedad de implementación [cite:1].

### Debilidad estructural principal

La principal debilidad es que el módulo no está conectado a flujos de estado vivo ni al `EventBus`, y mantiene su estado sobre `SQLite`, por lo que no puede comportarse como un motor intradía reactivo tick-by-tick sin cambios arquitectónicos previos [cite:1]. A esto se suma que el `BuilderSizingOverlay` está modelado para instrumentos lineales de futuros CME, con `tick_value`, `tick_size` y `stop_ticks`, lo que impide evaluar de forma correcta estructuras de opciones donde el riesgo máximo depende de prima o spread y no de un stop lineal sobre el subyacente [cite:1].

### Tensión de dominios

Existe además una tensión entre dos dominios operativos distintos: el funding Builder está especializado para futuros CME y límites de minis/micros, mientras que `omni_engine` y su `alpaca_executor` están diseñados alrededor de opciones multi-pata y ejecución sobre Alpaca [cite:1][cite:2]. Esa incompatibilidad no es solo de broker; también es de payload, matemáticas de sizing, noción de riesgo máximo y estructura de la orden [cite:1][cite:2].

## Principio rector del rediseño

El rediseño debe asumir que **señal, riesgo y ejecución son tres capas distintas**. El stack cuantitativo genera una intención operativa; el funding actúa como compuerta de supervivencia y control contractual; y el ejecutor transforma la decisión aprobada en orden real [cite:1][cite:2].

Eso implica que el primer paso no es rehacer el cockpit ni conectar WebSockets, sino definir un contrato de datos común entre motores cuantitativos y funding. Sin un payload canónico, cualquier intento de integración quedará acoplado a adaptadores frágiles y a supuestos implícitos distintos entre futuros y opciones [cite:1][cite:2].

## Arquitectura objetivo

### Capas objetivo

| Capa | Rol | Estado actual | Estado objetivo |
|---|---|---|---|
| Señal cuantitativa | Generar sesgo, confianza y estructura sugerida | Parcialmente unificada en `omni_engine` [cite:2] | Entrada única al risk gate |
| Contrato canónico | Normalizar payload entre motores y funding | No encontrado como modelo único [cite:2] | Modelo común agnóstico al instrumento |
| Funding risk gate | Validar restricciones de cuenta y sizing | Listo para futuros lineales [cite:1] | Listo para futuros y opciones con lógica por tipo de activo |
| Reconciliación broker | Mantener equity y PnL vivos | No encontrado como reconciliación viva [cite:1] | Estado intradía confiable por eventos |
| Cockpit operativo | Visualizar riesgo y runway | Polling cada 5s [cite:1] | Streaming en tiempo real |

### Payload canónico propuesto

El sistema necesita un `CanonicalSignalPayload` agnóstico al instrumento que sirva como frontera entre los motores cuantitativos y el funding. La necesidad de ese contrato surge porque hoy `omni_engine` produce una decisión rica en `playbook`, `structure`, `confidence` y parámetros de ejecución, mientras que `05 funding` recibe un `TradeCandidate` lineal insuficiente para estructuras multi-pata [cite:1][cite:2].

Campos mínimos sugeridos:

- `symbol`
- `asset_type`
- `direction`
- `confidence`
- `entry_price`
- `stop_loss_price` opcional
- `max_loss_usd` opcional
- `structure`
- `legs`
- `source_engine`
- `timestamp`
- `reason_codes`

Con este contrato, el funding deja de depender de la semántica de cada motor y pasa a depender de una intención de riesgo bien tipada [cite:1][cite:2].

## Subset mínimo para V1

No conviene lanzar una v1 con todos los motores del registro cuantitativo. El inventario muestra una oferta muy amplia de motores Technical, Predictive y Options, pero muchos son complementarios, experimentales o demasiado costosos para el path crítico en tiempo real [cite:3].

La v1 debería apoyarse en un subset mínimo con buena madurez y bajo costo operacional:

| Capa | Motores recomendados V1 | Justificación |
|---|---|---|
| Technical | `MarketStructureEngine`, `SMCEngine`, `VWAPEngine` [cite:3] | Proveen sesgo estructural y contexto sin requerir cadena completa de opciones |
| Predictive | `MarkovRegimeEngine`, `ExpectedMoveEngine` [cite:3] | Aportan régimen y rango esperado con modelos estables |
| Options | `GammaFlipEngine`, `IV Primitives`, `OptionsFlowSignalEngine` [cite:3] | Dan contexto institucional y de volatilidad útil para estructuras básicas |
| Contexto | `GlobalContextEngine` simplificado [cite:3] | Permite modular agresividad global sin sobrecargar la red |
| Funding risk | `BuilderRuleEngine`, `BuilderSurvivalEngine`, `BuilderSizingOverlay` refactorizado [cite:1] | Conservan la lógica de supervivencia y enforcement contractual |

Motores como `TailRiskEngine`, `DealerFlowDynamicsEngine` o análisis completos de superficie de volatilidad deberían quedar fuera de la primera iteración si requieren cadenas intradía pesadas o aumentan demasiado la complejidad operativa [cite:2][cite:3].

## Opción de integración recomendada

Aunque el módulo Builder del funding está fuertemente especializado en futuros CME, la vía más realista para una primera integración productiva es el eje **omni/options + Alpaca**, porque ya existe un `alpaca_executor` y un flujo de salida orientado a opciones multi-pata dentro de `omni_engine` [cite:2]. En cambio, el funding Builder de MFFU está optimizado para contratos de futuros y no aparece acompañado en la documentación por un ejecutor real equivalente para ese dominio [cite:1][cite:2].

Esto no significa descartar el Builder, sino aceptar que en la v1 el funding debe absorber reglas de supervivencia reutilizables mientras se adapta progresivamente a payloads y sizing de opciones. La migración ideal no es mover `omni_engine` hacia futuros, sino desacoplar `05 funding` de sus supuestos lineales para que pueda evaluar un riesgo máximo expresado como `max_loss_usd` además de `stop_ticks` [cite:1][cite:2].

## Roadmap por fases

### Fase 1: Congelar contrato y subset

Objetivo: reducir ambigüedad antes de tocar motores sensibles [cite:1][cite:2][cite:3].

Entregables:

- Definición formal de `CanonicalSignalPayload`.
- Selección de motores mínimos para v1.
- Tabla de mapeo entre outputs actuales y campos del payload canónico.
- Catálogo inicial de `reason_codes` compartidos entre señal y funding.

Riesgos:

- Si el contrato cambia tarde, obliga a refactorizar señal, funding y ejecución a la vez.
- Si se intenta incluir demasiados motores, la v1 se vuelve frágil y lenta [cite:3].

### Fase 2: Refactor de sizing por tipo de activo

Objetivo: desacoplar la matemática de riesgo del Builder respecto de ticks CME [cite:1].

Entregables:

- Separación entre `LinearInstrumentSizer` y `StructuredOptionsSizer`.
- Soporte de `max_loss_usd` como camino alternativo a `stop_ticks`.
- Incorporación de `asset_type`, `structure` y `legs` al flujo de evaluación.
- Tests de pérdida máxima para calls, puts y debit spreads.

Riesgos:

- Error de sizing puede provocar sobreexposición o falso bloqueo de señales válidas.
- Mezclar ambas lógicas en una sola clase aumentaría el acoplamiento [cite:1].

### Fase 3: Integración del funding con el contrato canónico

Objetivo: convertir a `FundingOrchestrator` en un consumidor estable de intenciones de riesgo [cite:1].

Entregables:

- Adaptador de `CanonicalSignalPayload` hacia evaluación funding.
- Uso explícito de factores de señal y régimen dentro del cálculo de riesgo permitido.
- Compatibilidad con `reason_codes` de motores cuantitativos y del funding.
- Respuesta unificada de aprobación, bloqueo o resize.

Riesgos:

- Falsos rechazos si la traducción desde señal a riesgo no conserva semántica.
- Inconsistencias si el payload llega sin `max_loss_usd` ni stop interpretable [cite:1][cite:2].

### Fase 4: Reconciliación broker y estado vivo

Objetivo: sustituir el estado mayormente manual por un account state confiable [cite:1].

Entregables:

- Listener de eventos de broker y/o reconciliador periódico.
- Caché en memoria o Redis para `BuilderAccountState` con persistencia asíncrona.
- Actualización automática de `equity`, `unrealized_pnl`, `realized_daily_pnl` y fills.
- Reglas de fallback si falla el stream o se congela el broker.

Riesgos:

- Un estado congelado puede autorizar una operación que ya no cabe dentro del runway real.
- El uso directo de SQLite en cada evaluación se vuelve cuello de botella bajo carga [cite:1].

### Fase 5: Cockpit en tiempo real

Objetivo: reemplazar el polling de 5 segundos por visibilidad operativa continua [cite:1].

Entregables:

- Endpoint WebSocket para balance, PnL, posiciones y alertas.
- Vista de runway intradía frente a trailing DD y DLL.
- Indicadores de `max_loss_usd` por estructura abierta.
- Alertas visuales por `floor drift`, proximidad al DLL y consistencia de payout.

Riesgos:

- Exceso de eventos puede saturar el frontend si no se agregan o agrupan mensajes.
- Un cockpit en streaming sin reconciliación broker fiable generaría falsa sensación de control [cite:1].

## Quick wins

Los quick wins más útiles no son visuales; son de contrato y seguridad operativa [cite:1][cite:2].

- Crear el modelo `CanonicalSignalPayload`.
- Añadir `asset_type`, `structure`, `legs` y `max_loss_usd` al dominio de entrada.
- Incorporar un adaptador inicial desde `OptionsExecutionPayload` al contrato canónico [cite:2].
- Aislar la lógica de sizing lineal en una interfaz separada del sizing por prima.
- Añadir caché de lectura para el estado de cuenta antes de saltar a un rediseño completo del store [cite:1].

## Cambios estructurales

Los cambios estructurales sí valen la pena, pero después de estabilizar la frontera entre señal y riesgo [cite:1][cite:2]. Los principales son:

- Sustituir el estado funding basado en lectura síncrona de SQLite por una arquitectura event-driven con memoria rápida y persistencia asíncrona [cite:1].
- Convertir el funding en un evaluador multimodal por tipo de activo, en vez de una lógica implícitamente acoplada a futuros CME [cite:1].
- Mover el cockpit a streaming solo después de que el estado vivo provenga de reconciliación real y no de mocks o inserts manuales [cite:1].

## Riesgos de sobreingeniería

Una v1 no debería incluir calibración bayesiana online, optimización dinámica de pesos en caliente ni evaluación completa de superficie de volatilidad sobre toda la cadena en cada tick, porque esos componentes multiplican latencia, costo de datos y fragilidad sin resolver primero el contrato y el estado vivo [cite:2][cite:3]. Tampoco conviene diseñar desde el inicio una abstracción multi-prop-firm si el objetivo actual sigue siendo la supervivencia bajo reglas del Builder Plan de MFFU [cite:1].

## Recomendación final

La hoja de ruta recomendada es: **contrato canónico -> subset mínimo de motores -> refactor de sizing -> integración funding -> reconciliación broker -> cockpit en tiempo real** [cite:1][cite:2][cite:3]. Ese orden preserva la lógica valiosa ya existente en `05 funding`, reduce el riesgo de integrar dominios incompatibles demasiado pronto y crea una base limpia para que el sistema pase de un gate estático post-señal a un control operativo realmente útil [cite:1][cite:2].
