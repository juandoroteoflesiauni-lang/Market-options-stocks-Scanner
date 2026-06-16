# Changelog

Todos los cambios notables de este proyecto se documentarán en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]
### Added
- **Módulo 05 Funding - Integración Multiactivo y Telemetría en Vivo (Fases 1-5):**
  - **Fase 1 (Contrato Canónico)**: Contratos `CanonicalSignalPayload` y `CanonicalLegSpec` con precisión `Decimal` en `backend/models/canonical_signal.py`.
  - **Fase 2 (Sizing)**: Separación matemática de sizing mediante `LinearInstrumentSizer` y `StructuredOptionsSizer` (con penalización por spread y margen de buying power de Alpaca).
  - **Fase 3 (Integración)**: Enrutador en `FundingOrchestrator` y pase al pipeline de riesgo.
  - **Fase 4 (Reconciliación)**: Caché en memoria en `BuilderStateStore` y reconciliador `BrokerStateReconciliator` opcional para Alpaca.
  - **Fase 5 (WebSocket & Telemetría)**: Stream WebSocket `/ws/funding` en el backend (desacoplado de Alpaca para soportar cuentas manuales independientes) y reactividad en tiempo real con reconexión en el frontend `fundingStore.ts`.
  - **Tests**: 36 pruebas unitarias pasadas y 3 de integración en `test_funding_ws.py` y `test_risk_metrics_api.py`.
- **MFFU Builder Plan — mejoras de supervivencia y payout:**
  - Persistencia de PnL diario (`builder_daily_pnl`) que alimenta consistency y survival con histórico real.
  - Intraday trailing DD tracker: proyecta el floor EOD de mañana y alerta cuando un nuevo máximo encarece el piso (`builder_floor_drift_warning`).
  - Consistency 50% live tracker: calcula el techo de ganancia diario para no quemar un payout con un hero-day.
  - Payout planner: buffer/días cualificados restantes + ETA estimada al primer retiro.
  - What-if por trade en `POST /builder/evaluate`: escenario post-stop (equity, distancias a DD/DLL, breach).
  - Backtest determinístico de supervivencia (`POST /builder/backtest`) sobre secuencias de PnL diario.
  - Batch evaluate (`POST /builder/evaluate-batch`) para gating de los leaders del scanner.
  - `BuilderCockpit` ampliado y acción `evaluateBuilderCandidate()` en el store.
  - 16 tests nuevos (77 tests Builder en total) incluyendo E2E EVAL→SIM→payout.
- Documentación inicial de comunidad (Licencia, Código de Conducta, Guías de contribución).
- Plantilla base de Changelog.
- Archivo `bloomberg-variables.css` con tokens primitivos/semánticos de Bloomberg.
- Componente `PriceCell.tsx` para parpadeo de datos numéricos y alineación tabular-nums.
- Componente `DataPanel.tsx` para panelización en rejillas con indicadores de fase.
- Hook `usePriceFlash.ts` para detectar dirección de variaciones en tiempo real y emitir clases de parpadeo.
- Pestaña `AlpacaBot.tsx` que reemplaza el placeholder de Alpaca por el monitor de flujo de opciones inusuales y KPIs.
- `docs/FUNDING_MODULE_EXPEDIENTE.md` — Expediente técnico de 91 KB que cataloga los 26 motores del Módulo de Funding en 4 familias (Técnicos, Opciones, Predictivos, Funding/Risk) con su funcionalidad, lógica, matemática y código de implementación extraído del codebase.

### Changed
- Rediseño completo del frontend a Bloomberg-grade (Wall Street Standard v1.0).
- Configuración de `@theme` de Tailwind CSS v4 en `globals.css` mapeada a los tokens de Bloomberg.
- Modificación en `layout.tsx` para cargar las fuentes `Inter`, `JetBrains Mono` e `IBM Plex Sans` e inyectar variables de tipografía.
- Modificación de la barra superior `TopNavigationBar.tsx` y barra de estado `SystemStatusBar.tsx` con ticker tape e indicadores dinámicos.
- Rediseño de la pestaña `MarketScanner.tsx` con tabla densa, sparklines, sliders de pesos y analíticas de fase.
- Rediseño de la pestaña `BingXBot.tsx` con sub-header de portafolio, tarjetas con griegas de opciones, simulación de gráfico de velas SVG y registro de trades.
- Re-animación de transiciones entre pestañas con Framer Motion limitadas a 120ms para rendimiento de terminal.

### Fixed
- Corrección de la advertencia de ESLint en `SystemStatusBar.tsx` por llamada síncrona a `setState` en `useEffect` inicializando el estado de manera perezosa (lazy).
- Corrección de la advertencia de exportación anónima en `postcss.config.mjs`.
