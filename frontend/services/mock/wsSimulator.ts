import type { Ticker } from "@/types";

type TickCallback = (ticker: Ticker) => void;

interface Subscription {
  symbol: string;
  callback: TickCallback;
}

const subscriptions: Subscription[] = [];
const timers = new Map<string, ReturnType<typeof setInterval>>();
const tickers = new Map<string, Ticker>();

function randomTick(t: Ticker): Ticker {
  const bps = (Math.random() - 0.49) * 0.002; // small random walk
  const price = Math.max(1, t.price * (1 + bps));
  const change = price - t.price;
  const changePct = (change / t.price) * 100;

  return {
    ...t,
    price,
    priceChange: change,
    priceChangePct: changePct,
  };
}

/** Register (or update) a ticker in the simulator's state. */
export function registerTicker(ticker: Ticker): void {
  tickers.set(ticker.symbol, ticker);
}

/** Subscribe to live ticks for a symbol. Returns an unsubscribe function. */
export function subscribe(symbol: string, callback: TickCallback): () => void {
  const sub: Subscription = { symbol, callback };
  subscriptions.push(sub);

  // Start a per-symbol interval if not already running
  if (!timers.has(symbol)) {
    const interval = 50 + Math.random() * 250; // 50–300ms
    const id = setInterval(() => {
      const current = tickers.get(symbol);
      if (!current) return;
      const updated = randomTick(current);
      tickers.set(symbol, updated);
      for (const s of subscriptions) {
        if (s.symbol === symbol) s.callback(updated);
      }
    }, interval);
    timers.set(symbol, id);
  }

  return () => {
    const idx = subscriptions.indexOf(sub);
    if (idx !== -1) subscriptions.splice(idx, 1);
    // Stop interval if no more subscribers for this symbol
    if (!subscriptions.some((s) => s.symbol === symbol)) {
      clearInterval(timers.get(symbol));
      timers.delete(symbol);
    }
  };
}

/** Get current snapshot for a symbol */
export function getSnapshot(symbol: string): Ticker | undefined {
  return tickers.get(symbol);
}

/** Get all registered tickers */
export function getAllTickers(): Ticker[] {
  return Array.from(tickers.values());
}
