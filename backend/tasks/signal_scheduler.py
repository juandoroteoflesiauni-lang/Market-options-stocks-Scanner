from __future__ import annotations
from typing import Any
"""signal_scheduler.py
=======================
Llama al endpoint meta-signal cada 15 minutos para cada simbolo durante
horario de mercado (9:30 AM - 4:00 PM ET, Lun-Vie). Acumula predicciones
en el PredictionLogger sin intervencion manual.

    python -m backend.tasks.signal_scheduler

Dejar corriendo en background durante 3+ dias para alcanzar
~2800 predicciones (22 simbolos x 26 rondas/dia x 3 dias).
"""


import asyncio
import logging
import os
from datetime import datetime, time
from pathlib import Path

import httpx
import pytz

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

SCHEDULER_LOG_FILE = "signal_scheduler.log"

SYMBOLS: list[str] = [
    # ETFs — mayor liquidez, options chain mas limpia
    "SPY",
    "QQQ",
    "IWM",
    "GLD",
    "TLT",
    "XLF",
    "XLE",
    "EEM",
    "HYG",
    # Mega cap — maximo volumen de opciones
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMZN",
    "META",
    "GOOGL",
    # High vol — mas movimiento = mas signal en los motores
    "AMD",
    "NFLX",
    "COIN",
    "PLTR",
    "MSTR",
    # BTC proxy
    "IBIT",
]

INTERVAL_MINUTES = 15
BASE_URL = os.environ.get("META_SIGNAL_BASE_URL", "http://localhost:8000")
MAX_CONCURRENT = 4
REQUEST_TIMEOUT_S = 60.0
ET_ZONE = pytz.timezone("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


class _SchedulerFileHandler(logging.FileHandler):
    _signal_scheduler_handler = True


def _has_scheduler_file_handler(target_log: Path) -> bool:
    for handler in logger.handlers:
        if not getattr(handler, "_signal_scheduler_handler", False):
            continue
        if not isinstance(handler, logging.FileHandler):
            continue
        if Path(handler.baseFilename).resolve() == target_log.resolve():
            return True
    return False


def configure_logging(log_dir: str | os.PathLike[str] = "logs") -> logging.Logger:
    """Attach a dedicated scheduler log file while preserving central logging."""
    resolved_log_dir = Path(log_dir)
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    scheduler_log = (resolved_log_dir / SCHEDULER_LOG_FILE).resolve()

    if not _has_scheduler_file_handler(scheduler_log):
        file_handler = _SchedulerFileHandler(scheduler_log, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(file_handler)

    return logger


def is_market_hours() -> bool:
    now_et = datetime.now(ET_ZONE)
    if now_et.weekday() >= 5:
        return False
    return MARKET_OPEN <= now_et.time() <= MARKET_CLOSE


async def fetch_signal(client: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
    try:
        resp = await client.get(
            f"{BASE_URL}/api/v1/probabilistic/meta-signal/{symbol}",
            timeout=REQUEST_TIMEOUT_S,
        )
    except (httpx.RequestError, httpx.TimeoutException) as exc:
        logger.error("[%s] Request error: %s", symbol, exc)
        return {"symbol": symbol, "ok": False, "error": str(exc)}

    if resp.status_code != 200:
        logger.warning("[%s] HTTP %s", symbol, resp.status_code)
        return {"symbol": symbol, "ok": False, "status": resp.status_code}

    try:
        data = resp.json()
    except ValueError as exc:
        logger.error("[%s] JSON decode error: %s", symbol, exc)
        return {"symbol": symbol, "ok": False, "error": "json_decode"}

    logger.info(
        "[%s] dir=%s conf=%.2f trade=%s",
        symbol,
        data.get("direction"),
        float(data.get("blended_confidence") or 0.0),
        data.get("should_trade"),
    )
    return {"symbol": symbol, "ok": True}


async def run_batch(symbols: list[str]) -> list[Any]:
    """Correr lote con concurrencia limitada por semaforo."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def fetch_with_limit(client: httpx.AsyncClient, sym: str) -> dict[str, Any]:
        async with semaphore:
            return await fetch_signal(client, sym)

    async with httpx.AsyncClient() as client:
        tasks = [fetch_with_limit(client, sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    ok = sum(1 for r in results if isinstance(r, dict) and r.get("ok"))
    failed = len(symbols) - ok
    logger.info("Batch completo: %d/%d OK, %d fallidos", ok, len(symbols), failed)
    return results


async def scheduler_loop() -> None:
    logger.info("Scheduler iniciado — %d simbolos, cada %d min", len(SYMBOLS), INTERVAL_MINUTES)
    logger.info("Simbolos: %s", ", ".join(SYMBOLS))

    while True:
        if is_market_hours():
            now = datetime.now(ET_ZONE).strftime("%H:%M:%S ET")
            logger.info("--- Ronda de senales [%s] ---", now)
            await run_batch(SYMBOLS)
        else:
            now_et = datetime.now(ET_ZONE)
            logger.info(
                "Fuera de horario de mercado (%s). Esperando...",
                now_et.strftime("%H:%M ET %a"),
            )

        await asyncio.sleep(INTERVAL_MINUTES * 60)


def main() -> None:
    configure_logging()
    try:
        asyncio.run(scheduler_loop())
    except KeyboardInterrupt:
        logger.info("Scheduler detenido por el usuario.")


if __name__ == "__main__":
    main()
