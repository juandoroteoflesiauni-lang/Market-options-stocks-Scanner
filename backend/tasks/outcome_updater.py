from __future__ import annotations
"""outcome_updater.py
======================
Periodic task that backfills realised outcomes for past predictions.

One-shot:
    python -m backend.tasks.outcome_updater SPY QQQ AAPL

Continuous loop (every hour, all default symbols):
    python -m backend.tasks.outcome_updater --loop

Logic
-----
For each symbol and each forward horizon N in FORWARD_DAYS:
  1. Fetch the current spot price via yfinance.
  2. Call PredictionLogger.schedule_outcome_updates(symbol, current_price, n_days)
     which finds predictions with timestamp ~ N days old whose price_t0 was
     captured at log time, computes realised return = (current/price_t0)-1,
     and persists outcome_return_{N}d + outcome_direction_correct.

Horizons of 10 days are accepted but no-op silently because the schema only
has 1d / 5d outcome columns.
"""


import asyncio
import sys

import yfinance as yf

from backend.config.logger_setup import get_logger
from backend.services.prediction_logger import PredictionLogger

logger = get_logger(__name__)

FORWARD_DAYS: tuple[int, ...] = (1, 5, 10)

DEFAULT_SYMBOLS: list[str] = [
    "SPY",
    "QQQ",
    "IWM",
    "GLD",
    "TLT",
    "XLF",
    "XLE",
    "EEM",
    "HYG",
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "NFLX",
    "COIN",
    "PLTR",
    "MSTR",
    "IBIT",
]

DEFAULT_INTERVAL_MINUTES = 60


def _current_price(symbol: str) -> float | None:
    """Last close from yfinance 5d history; returns None on empty/error."""
    try:
        hist = yf.Ticker(symbol).history(period="5d")
    except Exception as exc:
        logger.error("yfinance fetch failed for %s: %s", symbol, exc)
        return None
    if hist.empty:
        logger.warning("No price data for %s", symbol)
        return None
    return float(hist["Close"].iloc[-1])


def update_outcomes_for_symbol(
    symbol: str,
    pl: PredictionLogger | None = None,
) -> dict[str, int | str]:
    """Backfill all FORWARD_DAYS horizons for one symbol."""
    if pl is None:
        pl = PredictionLogger()

    sym = symbol.upper().strip()
    price_now = _current_price(sym)
    if price_now is None or price_now <= 0:
        return {"symbol": sym, "updated": 0, "errors": 1, "missing_price_t0": 0}

    total_updated = 0
    total_errors = 0
    total_missing = 0

    for n in FORWARD_DAYS:
        try:
            res = pl.schedule_outcome_updates(
                symbol=sym,
                current_price=price_now,
                n_days=n,
            )
        except Exception as exc:
            logger.error("Error updating %dd outcomes for %s: %s", n, sym, exc)
            total_errors += 1
            continue

        total_updated += int(res.get("updated", 0))
        total_errors += int(res.get("errors", 0))
        total_missing += int(res.get("missing_price_t0", 0))

    logger.info(
        "Outcomes updated for %s: %d records, %d errors, %d missing price_t0",
        sym,
        total_updated,
        total_errors,
        total_missing,
    )
    return {
        "symbol": sym,
        "updated": total_updated,
        "errors": total_errors,
        "missing_price_t0": total_missing,
    }


def update_all_outcomes(symbols: list[str]) -> list[dict[str, int | str]]:
    pl = PredictionLogger()
    return [update_outcomes_for_symbol(s, pl) for s in symbols]


async def outcome_update_loop(
    symbols: list[str] | None = None,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
) -> None:
    """Continuous loop: run update_all_outcomes every `interval_minutes`."""
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    logger.info(
        "Outcome updater iniciado — %d simbolos, cada %d min",
        len(symbols),
        interval_minutes,
    )

    while True:
        logger.info("--- Actualizando outcomes ---")
        try:
            results = update_all_outcomes(symbols)
        except Exception as exc:
            logger.exception("update_all_outcomes fallo: %s", exc)
            results = []

        total_updated = sum(int(r.get("updated", 0)) for r in results)
        total_errors = sum(int(r.get("errors", 0)) for r in results)
        logger.info("Total actualizado: %d outcomes, %d errores", total_updated, total_errors)

        await asyncio.sleep(interval_minutes * 60)


def main() -> None:
    args = sys.argv[1:]
    if "--loop" in args:
        args.remove("--loop")
        syms = args or DEFAULT_SYMBOLS
        try:
            asyncio.run(outcome_update_loop(syms))
        except KeyboardInterrupt:
            logger.info("Outcome updater detenido por el usuario.")
        return

    syms = args or DEFAULT_SYMBOLS
    results = update_all_outcomes(syms)
    for r in results:
        logger.info("%s", r)


if __name__ == "__main__":
    main()
