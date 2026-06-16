from __future__ import annotations
"""
Job: cada 30 min en horario de mercado US — calcula max pain (front expiry) y persiste en Redis.

Ejecutar: python -m backend.tasks.max_pain_history_job
"""


import asyncio
import logging
import os
from datetime import datetime, time

try:
    import pytz

    ET_ZONE = pytz.timezone("America/New_York")
except ImportError:
    ET_ZONE = None

from backend.services.max_pain_history_service import compute_and_store_front_max_pain

logger = logging.getLogger(__name__)

INTERVAL_MINUTES = 30
RISK_FREE = float(os.getenv("OPTIONS_RISK_FREE_RATE", "0.04"))
BASE_SYMBOLS = os.getenv(
    "MAX_PAIN_JOB_SYMBOLS",
    "SPY,QQQ,IWM,AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL",
)
SYMBOLS = [s.strip().upper() for s in BASE_SYMBOLS.split(",") if s.strip()]

MARKET_OPEN = time(9, 25)
MARKET_CLOSE = time(16, 5)


def is_market_hours() -> bool:
    if ET_ZONE:
        import pytz as _pytz

        now = datetime.now(_pytz.timezone("America/New_York"))
    else:
        from datetime import timedelta, timezone

        now = datetime.now(timezone(timedelta(hours=-4)))
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


async def run_once() -> None:
    ok = 0
    for sym in SYMBOLS:
        try:
            rec = await compute_and_store_front_max_pain(sym, RISK_FREE)
            if rec:
                ok += 1
                logger.info(
                    "max_pain_job %s mp=%s spot=%s d=%s%%",
                    sym,
                    rec.get("max_pain"),
                    rec.get("spot"),
                    rec.get("distance_pct"),
                )
        except Exception as exc:
            logger.warning("max_pain_job %s failed: %s", sym, exc)
    logger.info("max_pain_job batch: %s/%s OK", ok, len(SYMBOLS))


async def scheduler_loop() -> None:
    logger.info(
        "max_pain_history job — %s símbolos cada %s min (ET %s–%s)",
        len(SYMBOLS),
        INTERVAL_MINUTES,
        MARKET_OPEN,
        MARKET_CLOSE,
    )
    while True:
        if is_market_hours():
            await run_once()
        else:
            logger.info("max_pain_job: fuera de horario mercado US — skip")
        await asyncio.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    asyncio.run(scheduler_loop())
