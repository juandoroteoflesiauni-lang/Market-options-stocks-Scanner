"""Optional Redis read-through cache for Market Scanner live prices."""

from __future__ import annotations

import json
import os
from typing import Any

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


def redis_url_configured() -> str | None:
    raw = (os.getenv("MARKET_SCANNER_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()
    return raw or None


def live_price_cache_key(provider_key: str, symbol: str) -> str:
    return f"qa:scanner:live:{provider_key}:{symbol.upper()}"


def redis_get_live_price(provider_key: str, symbol: str) -> dict[str, Any] | None:
    url = redis_url_configured()
    if not url:
        return None
    try:
        import redis

        client = redis.from_url(url, decode_responses=True)
        raw = client.get(live_price_cache_key(provider_key, symbol))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug(
            "scanner_cache_redis.get_failed symbol=%s err=%s",
            symbol,
            str(exc)[:120],
        )
        return None


def redis_set_live_price(
    provider_key: str,
    symbol: str,
    payload: dict[str, Any],
    ttl_seconds: int,
) -> None:
    url = redis_url_configured()
    if not url or ttl_seconds <= 0:
        return
    try:
        import redis

        client = redis.from_url(url, decode_responses=True)
        client.setex(
            live_price_cache_key(provider_key, symbol),
            ttl_seconds,
            json.dumps(payload),
        )
    except Exception as exc:
        logger.debug(
            "scanner_cache_redis.set_failed symbol=%s err=%s",
            symbol,
            str(exc)[:120],
        )
