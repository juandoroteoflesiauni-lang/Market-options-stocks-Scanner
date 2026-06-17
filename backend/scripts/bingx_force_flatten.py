"""Emergency flatten: cancela órdenes y cierra todas las posiciones BingX perp. # [PD-3][TH]

Usage:
    python backend/scripts/bingx_force_flatten.py --confirm
    python backend/scripts/bingx_force_flatten.py --confirm --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BingX emergency flatten all perp positions.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag to execute live close (safety).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Intercept closes (log only).",
    )
    parser.add_argument(
        "--no-cancel",
        action="store_true",
        help="Skip cancel-all open orders before close.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    from backend.config.logger_setup import get_logger
    from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient

    logger = get_logger(__name__)
    if not args.confirm:
        logger.error("bingx_flatten.aborted reason=missing_confirm_flag")
        return 1

    trading_env = os.getenv("BINGX_BOT_TRADING_ENV", "prod-vst")
    base_url = BINGX_REST_VST_BASE if trading_env == "prod-vst" else None
    dry_run = args.dry_run or os.getenv("BINGX_DRY_RUN", "false").lower() in {
        "1",
        "true",
        "yes",
    }

    client = BingXClient(
        api_key=os.getenv("BINGX_API_KEY", ""),
        secret_key=os.getenv("BINGX_SECRET", ""),
        base_url=base_url or os.getenv("BINGX_REST_BASE", ""),
        dry_run=dry_run,
        allow_env_dry_run_override=False,
    )

    try:
        positions = await client.fetch_perp_positions()
        logger.info("bingx_flatten.positions_before count=%d", len(positions))
        for pos in positions:
            sym = pos.get("symbol", "?")
            amt = pos.get("positionAmt", "?")
            pnl = pos.get("unrealizedProfit", "?")
            logger.info("bingx_flatten.position symbol=%s amt=%s uPnL=%s", sym, amt, pnl)

        if not args.no_cancel:
            cancel = await client.cancel_all_orders_perp()
            logger.info("bingx_flatten.cancel_all result=%s", cancel)

        result = await client.close_all_positions(confirm=True)
        logger.info("bingx_flatten.close_all result=%s", result)

        await asyncio.sleep(2)
        after = await client.fetch_perp_positions()
        logger.info("bingx_flatten.positions_after count=%d", len(after))
        print(json.dumps({"closed": result, "positions_remaining": len(after)}, indent=2))
        return 0 if len(after) == 0 else 2
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
