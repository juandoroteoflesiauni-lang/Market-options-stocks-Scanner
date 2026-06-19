"""Ejecuta pipeline EOD manual: auditoría + meta-learner + calibración. # [PD-3]"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


async def _main() -> int:
    from backend.config.logger_setup import get_logger
    from backend.config.settings import load_settings
    from backend.layer_1_data.datos.alpaca_client import AlpacaClient
    from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient
    from backend.services.eod_session_pipeline import run_eod_session_pipeline

    logger = get_logger(__name__)
    settings = load_settings()
    alpaca_mode = __import__("os").getenv("ALPACA_TRADING_MODE", "paper").strip().lower()
    alpaca_base = (
        settings.alpaca_live_base_url if alpaca_mode == "live" else settings.alpaca_trading_base_url
    )
    alpaca_client = AlpacaClient(
        api_key=settings.alpaca_api_key.get_secret_value() if settings.alpaca_api_key else None,
        secret_key=(
            settings.alpaca_api_secret.get_secret_value() if settings.alpaca_api_secret else None
        ),
        base_url=alpaca_base,
        dry_run=False,
    )
    bx_key = settings.bingx_api_key.get_secret_value() if settings.bingx_api_key else None
    bx_secret = settings.bingx_secret.get_secret_value() if settings.bingx_secret else None
    bingx_client = BingXClient(
        api_key=bx_key,
        secret_key=bx_secret,
        base_url=BINGX_REST_VST_BASE,
        dry_run=False,
        allow_env_dry_run_override=False,
    )
    try:
        out = await run_eod_session_pipeline(
            alpaca_client=alpaca_client,
            bingx_client=bingx_client,
            alpaca_audit_db="data/alpaca_bot_audit.duckdb",
            bingx_audit_db="data/bingx_bot_audit.duckdb",
        )
        logger.info("eod_session.complete path=%s", out)
        print(out)
        return 0
    finally:
        await alpaca_client.aclose()
        await bingx_client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EOD audit + calibration + meta-learner.")
    _ = parser.parse_args()
    return asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
