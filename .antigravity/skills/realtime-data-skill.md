# SKILL: Real-Time Market Data
## Compatible con: Antigravity, Claude Code, Cursor, VS Code

---

## DESCRIPCIÓN
Skill especializado para implementar feeds de datos de mercado en tiempo real
en la terminal de trading. Cubre WebSockets, stores de precios y normalización de datos.

---

## ACTIVACIÓN
Se activa cuando el contexto incluye:
- Archivos con "websocket", "stream", "feed", "ticker" en el nombre
- Imports de `WebSocket`, `useMarketStore`, `BinanceSocketManager`
- Palabras clave: "precio en tiempo real", "order book", "market data"

---

## COMPORTAMIENTO ESPECIALIZADO

### Para WebSockets Backend:
1. Siempre implementar `MarketFeedManager` como singleton
2. Un solo stream por símbolo (no duplicar conexiones al exchange)
3. Broadcast a N clientes desde 1 stream
4. Reconexión automática con backoff exponencial
5. Cleanup automático cuando no hay más suscriptores

### Para WebSockets Frontend:
1. Usar el hook `useMarketFeed` centralizado
2. Nunca crear WebSockets directamente en componentes
3. Siempre cleanup en useEffect return
4. Throttle de updates de UI a máximo 10/segundo
5. Reconexión automática en caso de corte

### Para Stores de Mercado:
1. Usar `subscribeWithSelector` en Zustand para selectores granulares
2. Cada componente suscribe solo al dato que necesita
3. `React.memo` en componentes de precio para evitar re-renders
4. Usar `useShallow` para seleccionar múltiples valores sin re-renders innecesarios

---

## DATOS DE NORMALIZACIÓN OBLIGATORIOS

```python
# Estructura interna estándar — SIEMPRE normalizar datos del exchange
@dataclass
class NormalizedTick:
    symbol: str           # Ej: "BTCUSDT"
    price: Decimal        # Precio actual — SIEMPRE Decimal
    bid: Decimal          # Mejor precio de compra
    ask: Decimal          # Mejor precio de venta
    spread: Decimal       # ask - bid
    volume_24h: Decimal   # Volumen en últimas 24h
    change_24h_pct: Decimal  # % de cambio en 24h
    timestamp: datetime   # UTC siempre
    exchange: str         # "binance", "mt5", etc.
```

---

## CHECKLIST ANTES DE COMPLETAR

```
□ ¿El WebSocket tiene cleanup en el return del useEffect?
□ ¿Los precios usan Decimal (no float)?
□ ¿El store usa selectores granulares (no todo el store)?
□ ¿Los componentes de precio usan React.memo?
□ ¿Hay reconexión automática configurada?
□ ¿Los logs de errores de WS están implementados?
□ ¿Los datos del exchange pasan por el normalizador?
```
