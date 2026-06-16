"""Ventanas EOD para flatten intraday Alpaca (libro limpio al cierre). # [PD-8][TH]"""

from __future__ import annotations

import os
from datetime import datetime, time

from backend.tasks.bingx_bot_scheduler import _et_now

_EOD_FLATTEN_ENABLED_ENV = "ALPACA_EOD_FLATTEN_ENABLED"
_EOD_ENTRY_CUTOFF_ENV = "ALPACA_EOD_ENTRY_CUTOFF_ET"
_EOD_FLATTEN_START_ENV = "ALPACA_EOD_FLATTEN_START_ET"


def _parse_hhmm(env_name: str, default: time) -> time:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        hour, minute = raw.split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return default


def alpaca_eod_flatten_enabled() -> bool:
    return os.getenv(_EOD_FLATTEN_ENABLED_ENV, "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def eod_entry_cutoff_et() -> time:
    """Después de esta hora ET no se abren posiciones nuevas."""
    return _parse_hhmm(_EOD_ENTRY_CUTOFF_ENV, time(15, 30))


def eod_flatten_start_et() -> time:
    """A partir de esta hora ET se cierra todo el libro."""
    return _parse_hhmm(_EOD_FLATTEN_START_ENV, time(15, 45))


def _now_et(now: datetime | None = None) -> datetime:
    return _et_now(now or datetime.now())


_EOD_ENTRY_CUTOFF_DISABLED_ENV = "ALPACA_EOD_ENTRY_CUTOFF_DISABLED"


def alpaca_eod_entry_cutoff_disabled() -> bool:
    """Permite entradas nuevas en sesión de verificación / pruebas."""
    return os.getenv(_EOD_ENTRY_CUTOFF_DISABLED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_eod_entry_cutoff(*, now: datetime | None = None) -> bool:
    """True si ya pasó el cutoff de nuevas entradas (15:30 ET por defecto)."""
    if alpaca_eod_entry_cutoff_disabled():
        return False
    moment = _now_et(now)
    if moment.weekday() >= 5:
        return True
    current = moment.time().replace(second=0, microsecond=0)
    return current >= eod_entry_cutoff_et()


def is_eod_flatten_window(*, now: datetime | None = None) -> bool:
    """True en ventana de flatten (15:45–16:00 ET por defecto)."""
    moment = _now_et(now)
    if moment.weekday() >= 5:
        return False
    current = moment.time().replace(second=0, microsecond=0)
    return eod_flatten_start_et() <= current < time(16, 0)


def trading_date_et_key(*, now: datetime | None = None) -> str:
    return _now_et(now).date().isoformat()


__all__ = [
    "alpaca_eod_entry_cutoff_disabled",
    "alpaca_eod_flatten_enabled",
    "eod_entry_cutoff_et",
    "eod_flatten_start_et",
    "is_eod_entry_cutoff",
    "is_eod_flatten_window",
    "trading_date_et_key",
]
