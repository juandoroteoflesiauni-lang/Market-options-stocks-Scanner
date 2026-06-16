from __future__ import annotations
from typing import Any
"""
Persistencia temporal de max pain (front expiry) para serie dinámica.

Almacén: Redis LIST ``qa:options:max_pain_hist:{SYMBOL}`` (LPUSH + LTRIM).
Sin Redis: lecturas vacías y el job solo registra warning (modo dev).
"""


import functools
import json
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_LIST_KEY = "qa:options:max_pain_hist:{symbol}"
_MAX_LEN = 2500
_TTL_SEC = 86400 * 45

_redis_client: Any = None
_redis_failed = False


def _get_redis() -> object | None:  # pragma: no cover - integration
    global _redis_client, _redis_failed
    if _redis_failed:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis

        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(url, decode_responses=True)
        r.ping()
        _redis_client = r
        logger.info("max_pain_history: Redis OK (%s)", url.split("@")[-1])
        return _redis_client
    except Exception as exc:
        _redis_failed = True
        logger.warning("max_pain_history: Redis unavailable (%s) — history disabled", exc)
        return None


def append_max_pain_record(symbol: str, record: dict[str, Any]) -> bool:
    """Guarda un punto al frente de la lista (más reciente primero en Redis)."""
    r = _get_redis()
    if not r:
        return False
    sym = symbol.upper().strip()
    key = _LIST_KEY.format(symbol=sym)
    try:
        payload = json.dumps(record, separators=(",", ":"), sort_keys=False)
        pipe = r.pipeline()
        pipe.lpush(key, payload)
        pipe.ltrim(key, 0, _MAX_LEN - 1)
        pipe.expire(key, _TTL_SEC)
        pipe.execute()
        return True
    except Exception as exc:
        logger.warning("max_pain_history: LPUSH failed %s: %s", sym, exc)
        return False


def read_max_pain_history(symbol: str, *, limit: int = 500) -> list[dict[str, Any]]:
    """Devuelve puntos en orden cronológico ascendente (más antiguo primero)."""
    r = _get_redis()
    if not r:
        return []
    sym = symbol.upper().strip()
    key = _LIST_KEY.format(symbol=sym)
    lim = max(1, min(int(limit), _MAX_LEN))
    try:
        raw = r.lrange(key, 0, lim - 1)
        out: list[dict[str, Any]] = []
        for s in raw:
            try:
                out.append(json.loads(s))
            except json.JSONDecodeError:
                continue
        # LPUSH: índice 0 = más reciente → invertir para graficar tiempo ↑
        out.reverse()
        return out
    except Exception as exc:
        logger.warning("max_pain_history: LRANGE failed %s: %s", sym, exc)
        return []


async def compute_and_store_front_max_pain(
    symbol: str, risk_free: float = 0.04
) -> dict[str, Any] | None:
    """
    Obtiene cadena (nearest expiry), calcula max_pain y persiste un punto.
    Retorna el registro guardado o None si falla el fetch / cadena vacía.
    """
    import asyncio

    from backend.layer_1_data.datos.massive_options_fetcher import fetch_option_chain_raw
    from backend.api.routes.options_router import _build_gex_levels, _parse_finnhub_chain, _safe_float

    sym = symbol.upper().strip()
    loop = asyncio.get_event_loop()
    raw, chain_src, _meta = await loop.run_in_executor(
        None,
        functools.partial(fetch_option_chain_raw, sym, None),
    )
    if raw is None or not isinstance(raw.get("data"), list) or len(raw["data"]) == 0:
        logger.info("max_pain_history: no chain for %s (src=%s)", sym, chain_src)
        return None

    spot_raw = _safe_float(
        raw.get("quote", {}).get("c") if isinstance(raw.get("quote"), dict) else None
    )
    if spot_raw is None:
        dl = raw.get("data") or []
        first = dl[0] if isinstance(dl, list) and dl and isinstance(dl[0], dict) else {}
        spot_raw = _safe_float(first.get("underlying", {}).get("close"))
    spot = float(spot_raw or 100.0)

    _rows, strikes, call_oi, put_oi, call_iv, put_iv, tte, expiry_used = _parse_finnhub_chain(
        raw, spot, None, risk_free
    )
    if strikes is None or len(strikes) == 0:
        return None

    gex_levels = _build_gex_levels(strikes, call_oi, put_oi, call_iv, put_iv, spot, tte, risk_free)
    mp = gex_levels.max_pain
    if mp is None:
        return None

    distance_pct = round(((spot - float(mp)) / spot * 100.0) if spot > 0 else 0.0, 4)
    try:
        exp_dt = datetime.strptime(str(expiry_used)[:10], "%Y-%m-%d").date()
        today = datetime.now(tz=UTC).date()
        dte_days = float(max((exp_dt - today).days, 1))
    except ValueError:
        dte_days = float(max(tte * 365.0, 1.0))

    ts = int(datetime.now(tz=UTC).timestamp())
    record = {
        "timestamp": ts,
        "max_pain": round(float(mp), 4),
        "spot": round(spot, 4),
        "distance_pct": distance_pct,
        "expiry": str(expiry_used),
        "dte_days": round(dte_days, 2),
    }
    append_max_pain_record(sym, record)
    return record
