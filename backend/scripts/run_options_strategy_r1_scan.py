"""Scan R1: corre el pipeline Options Strategy sobre los 11 tickers. # [PD-3][TH]

Uso:
    python backend/scripts/run_options_strategy_r1_scan.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.options_strategy_loader import get_options_strategy_config
from backend.services.options_strategy.input_builder import build_strategy_input
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline

logger = logging.getLogger("options_strategy.r1_scan")


def _fmt_row(symbol: str, decision: str, structure: str, direction: str,
             confidence: float, playbook: str, detail: str) -> str:
    return (
        f"{symbol:<6} | {decision:<9} | {structure:<18} | {direction:<8} "
        f"| {confidence:>5.2f} | {playbook:<18} | {detail}"
    )


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    config = get_options_strategy_config()
    as_of = datetime.now(timezone.utc)

    header = _fmt_row(
        "TICKER", "DECISION", "STRUCTURE", "DIR", 0.0, "PLAYBOOK", "DETALLE"
    ).replace(" 0.00", " CONF ")
    print("=" * len(header))
    print(header)
    print("=" * len(header))

    for symbol in ALPACA_ROUTE1_WATCHLIST:
        try:
            inp = build_strategy_input(symbol, as_of=as_of)
            log = OptionsStrategyPipeline.run_dry(inp, config=config, persist=False)
            d = log.playbook_decision
            detail_bits: list[str] = []
            if d.veto_triggered:
                detail_bits.append(f"veto={d.veto_triggered}")
            if d.reason_codes:
                detail_bits.append("reasons=" + ",".join(d.reason_codes[:3]))
            print(_fmt_row(
                symbol,
                str(d.decision),
                str(d.recommended_structure),
                str(d.direction),
                d.confidence,
                d.playbook_family or "-",
                "; ".join(detail_bits) or "-",
            ))
        except Exception as exc:  # noqa: BLE001 - resumen por ticker, no abortar scan
            print(_fmt_row(symbol, "ERROR", "-", "-", 0.0, "-", str(exc)[:80]))


if __name__ == "__main__":
    main()
