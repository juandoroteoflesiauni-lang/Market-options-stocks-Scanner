"""CLI C5 — calibración de pesos R1 opciones.

Ejecutar:
    python -m backend.scripts.alpaca_r1_options_calibrate
    python -m backend.scripts.alpaca_r1_options_calibrate --symbols AAPL,NVDA --limit 300
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.alpaca_r1_options_scoring_config import default_calibration_path
from backend.services.alpaca_r1_options_calibration_service import run_r1_options_calibration

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_symbols(raw: str | None) -> tuple[str, ...] | None:
    if not raw:
        return None
    return tuple(s.strip().upper() for s in raw.split(",") if s.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrar pesos R1 opciones (C5)")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Ruta options_gex_snapshots.sqlite3 (default: backend/data/options_gex_snapshots.sqlite3)",
    )
    parser.add_argument("--output", type=Path, default=None, help="JSON de salida")
    parser.add_argument("--symbols", type=str, default=None, help="CSV símbolos R1")
    parser.add_argument("--limit", type=int, default=500, help="Snapshots/símbolo")
    parser.add_argument(
        "--entry-threshold",
        type=float,
        default=0.55,
        help="Umbral de entrada LONG en backtest",
    )
    args = parser.parse_args(argv)

    symbols = _parse_symbols(args.symbols)
    output = args.output or default_calibration_path()

    logger.info(
        "alpaca_r1_calibrate start symbols=%s output=%s",
        symbols or ALPACA_ROUTE1_WATCHLIST,
        output,
    )
    result = run_r1_options_calibration(
        db_path=args.db,
        symbols=symbols,
        output_path=output,
        limit_per_symbol=args.limit,
        entry_threshold=args.entry_threshold,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    logger.info(
        "alpaca_r1_calibrate done sharpe=%s weights=%s",
        result.metrics.sharpe,
        result.family_weights.model_dump(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
