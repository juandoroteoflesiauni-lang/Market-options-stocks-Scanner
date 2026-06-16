#!/usr/bin/env python
"""CLI del signal loop operativo Options Strategy (R1). # [PD-3][TH]

Ejemplos:
    # Una pasada dry-run, persistiendo señales:
    python backend/scripts/run_options_strategy_signal_loop.py --once --persist

    # Loop cada 5 min, 12 iteraciones, dry-run:
    python backend/scripts/run_options_strategy_signal_loop.py --interval 300 --iterations 12

    # Loop con ejecución real en Alpaca (paper/dry según cliente):
    python backend/scripts/run_options_strategy_signal_loop.py --interval 300 --execute
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config.logger_setup import get_logger
from backend.services.options_strategy.signal_loop import (
    OptionsStrategySignalLoop,
    SignalLoopReport,
)

logger = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Signal loop Options Strategy (R1)")
    parser.add_argument("--interval", type=float, default=300.0, help="Segundos entre pasadas")
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="N.º de pasadas (0 = infinito hasta Ctrl-C)",
    )
    parser.add_argument("--once", action="store_true", help="Una sola pasada y salir")
    parser.add_argument("--execute", action="store_true", help="Ejecuta órdenes en Alpaca")
    parser.add_argument("--persist", action="store_true", help="Persiste auditoría")
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="Lista R1 separada por comas (vacío = toda la R1)",
    )
    return parser


def _print_report(report: SignalLoopReport) -> None:
    logger.info(
        "loop_pass as_of=%s scanned=%d execute=%d no_trade=%d errors=%d",
        report.as_of.isoformat(),
        report.scanned,
        report.execute_count,
        report.no_trade_count,
        report.error_count,
    )
    for entry in report.entries:
        logger.info(
            "  %-6s | %-9s | %-18s | conf=%.2f | playbook=%s | veto=%s",
            entry.symbol,
            entry.decision,
            entry.structure,
            entry.confidence,
            entry.playbook_family or "-",
            entry.veto or "-",
        )
    for symbol, msg in report.errors:
        logger.warning("  %-6s | ERROR | %s", symbol, msg[:120])


def _run_pass(symbols: tuple[str, ...] | None, *, execute: bool, persist: bool) -> SignalLoopReport:
    if execute:
        return asyncio.run(
            OptionsStrategySignalLoop.scan_and_execute(symbols=symbols, persist=persist)
        )
    return OptionsStrategySignalLoop.scan_once(symbols=symbols, persist=persist)


def main() -> None:
    args = _build_parser().parse_args()
    symbols = (
        tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
        or None
    )

    if args.once or args.iterations == 1:
        _print_report(_run_pass(symbols, execute=args.execute, persist=args.persist))
        return

    iteration = 0
    try:
        while args.iterations == 0 or iteration < args.iterations:
            _print_report(_run_pass(symbols, execute=args.execute, persist=args.persist))
            iteration += 1
            if args.iterations != 0 and iteration >= args.iterations:
                break
            time.sleep(max(args.interval, 1.0))
    except KeyboardInterrupt:
        logger.info("signal_loop.interrupted iterations_done=%d", iteration)


if __name__ == "__main__":
    main()
