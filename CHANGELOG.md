# Changelog

Todos los cambios notables de este proyecto se documentarán en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/lang/es/).

## [Unreleased]
### Added
- Documentación inicial de comunidad (Licencia, Código de Conducta, Guías de contribución).
- Plantilla base de Changelog.
- Archivo `bloomberg-variables.css` con tokens primitivos/semánticos de Bloomberg.
- Componente `PriceCell.tsx` para parpadeo de datos numéricos y alineación tabular-nums.
- Componente `DataPanel.tsx` para panelización en rejillas con indicadores de fase.
- Hook `usePriceFlash.ts` para detectar dirección de variaciones en tiempo real y emitir clases de parpadeo.
- Pestaña `AlpacaBot.tsx` que reemplaza el placeholder de Alpaca por el monitor de flujo de opciones inusuales y KPIs.

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
