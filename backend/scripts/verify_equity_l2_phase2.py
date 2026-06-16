"""Verificación en vivo Fase 2 — feed L2 equity (BingX REST + stream workers)."""
from __future__ import annotations

import asyncio
import json
import sys

from backend.services.equity_l2_feed_service import EquityL2FeedService, equity_l2_feed_enabled


async def main() -> int:
    if not equity_l2_feed_enabled():
        print("FAIL: EQUITY_L2_FEED_ENABLED is off")
        return 1

    from backend.layer_1_data.datos.bingx_client import BingXClient

    service = EquityL2FeedService()
    async with BingXClient(dry_run=True) as client:
        service._client = client
        refresh = await service.refresh_all()
        status = service.snapshot_status()

    print("refresh:", json.dumps(refresh))
    print("status:", json.dumps(status, indent=2, default=str))

    ok = refresh.get("ok", 0)
    total = refresh.get("refreshed", 0)
    if ok < 1:
        print(f"FAIL: bootstrap ok={ok}/{total}")
        return 1

    sample = service.get_microstructure("AAPL")
    if not sample or not sample.get("order_book"):
        print("FAIL: AAPL missing order_book in cache")
        return 1

    if not sample.get("ofi", {}).get("ok"):
        print("FAIL: AAPL missing OFI enrichment")
        return 1

    print("PASS: Fase 2 bootstrap OK — sample AAPL keys:", sorted(sample.keys()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
