"""Optional HTTPS webhook delivery after Market Scanner runs (desk alerts)."""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import MarketScannerRequest, MarketScannerResponse

logger = get_logger(__name__)

_PRIVATE_HOST_PATTERNS = (
    re.compile(r"^localhost$", re.I),
    re.compile(r"^127\.", re.I),
    re.compile(r"^10\.", re.I),
    re.compile(r"^192\.168\.", re.I),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[0-1])\.", re.I),
    re.compile(r"^0\.", re.I),
    re.compile(r"^\[::1\]$", re.I),
)


def webhook_url_is_allowed(url: str) -> bool:
    """Basic SSRF guard: HTTPS only, hostname (no literal public-IP hosts), no loopback labels."""
    raw = (url or "").strip()
    if not raw.startswith("https://"):
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    host = (parsed.hostname or "").strip()
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    for pat in _PRIVATE_HOST_PATTERNS:
        if pat.search(host):
            return False
    return True


def build_webhook_payload(
    request: MarketScannerRequest,
    result: MarketScannerResponse,
    *,
    max_symbols: int = 24,
) -> dict[str, Any]:
    rows_out: list[dict[str, Any]] = []
    for row in result.rows[:max_symbols]:
        rows_out.append(
            {
                "symbol": row.symbol,
                "scanner_score": row.scanner_score,
                "setup_grade": row.setup_grade,
                "direction": row.direction,
                "warnings": row.warnings[:6],
                "vetoes": row.vetoes[:6],
            }
        )
    return {
        "event": "market_scanner.completed",
        "universe": result.universe,
        "scoring_version": result.scoring_version,
        "row_count": len(result.rows),
        "skipped": len(result.skipped_symbols),
        "universe_regime_summary": result.universe_regime_summary,
        "macro_context": result.macro_context,
        "top_rows": rows_out,
        "request": {
            "max_rows": request.max_rows,
            "direction": request.direction,
            "timeframes": list(request.timeframes),
        },
    }


async def post_scanner_webhook(
    url: str, payload: dict[str, Any], *, timeout_seconds: float = 6.0
) -> None:
    if not webhook_url_is_allowed(url):
        logger.warning("scanner_webhook.rejected_invalid_or_private_url")
        return
    body = json.dumps(payload, default=str).encode("utf-8")
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(
                url,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.warning(
                    "scanner_webhook.http_error status=%s len=%s",
                    resp.status_code,
                    len(resp.text or ""),
                )
    except Exception as exc:
        logger.warning("scanner_webhook.post_failed error=%s", str(exc)[:200])
