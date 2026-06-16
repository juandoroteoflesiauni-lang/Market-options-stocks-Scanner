#!/usr/bin/env python
"""CLI offline para calibrar pesos del módulo Options Strategy. # [TH]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config.logger_setup import get_logger
from backend.services.options_strategy.calibration_loop import OptionsStrategyCalibrationLoop
from backend.services.options_strategy.calibration_store import OptionsStrategyCalibrationStore

logger = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibración offline de pesos Options Strategy (Fase 7)",
    )
    parser.add_argument("--limit", type=int, default=500, help="Máximo de audits a leer")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Ruta SQLite de auditoría (default: backend/data/options_strategy_audit.sqlite3)",
    )
    parser.add_argument(
        "--write-config",
        type=Path,
        default=None,
        help="Escribe omni_engine_calibrated.yaml en la ruta indicada",
    )
    parser.add_argument("--no-persist", action="store_true", help="No guardar reporte en SQLite")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = _build_parser().parse_args()
    from backend.services.options_strategy.audit_store import OptionsStrategyAuditStore

    audit_store = OptionsStrategyAuditStore(db_path=args.db_path)
    report = OptionsStrategyCalibrationLoop.run(audit_store=audit_store, limit=args.limit)

    logger.info(
        "calibration_complete observations=%s execute_rate=%.3f limitations=%s",
        report.observation_count,
        report.execute_rate,
        report.limitations,
    )
    logger.info("current_weights=%s", report.current_weights)
    logger.info("suggested_weights=%s", report.suggested_weights)
    logger.info(
        "confidence current=%.3f suggested=%.3f",
        report.current_min_global_confidence,
        report.suggested_min_global_confidence,
    )
    for item in report.recommendations:
        logger.info("recommendation: %s", item)

    if not args.no_persist:
        OptionsStrategyCalibrationStore(db_path=args.db_path).persist(report)

    if args.write_config is not None:
        OptionsStrategyCalibrationLoop.write_calibrated_config(report, args.write_config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
