"""Calibración C5 + ciclo dual-route + tabla de auditoría completa."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.alpaca_r1_options_scoring_config import default_calibration_path
from backend.config.settings import load_settings
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.services.alpaca_bot_service import AlpacaBotService
from backend.services.alpaca_market_hours import AlpacaMarketHoursGuard
from backend.services.alpaca_r1_options_calibration_service import run_r1_options_calibration
from backend.services.alpaca_universe_fetcher import (
    ALPACA_EXTENDED_CACHE,
    ensure_alpaca_universe_loaded,
)


class _ForceOpenClock:
    async def get_clock(self) -> dict[str, object]:
        return {"is_open": True, "next_open": None, "next_close": None}


def _as_dict(obj: object) -> dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return {}


def _fmt(value: object, decimals: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value) if value else "—"
    if isinstance(value, dict):
        return json.dumps(value, default=str)[:120]
    return str(value)


def _audit_row(
    analysis: dict[str, Any],
    decision: dict[str, Any] | None,
    risk: dict[str, Any] | None,
) -> dict[str, Any]:
    conf = analysis.get("options_confluence") or {}
    r2 = analysis.get("r2_technical_score") or {}
    intent = (risk or {}).get("intent") or {}
    return {
        "symbol": analysis.get("symbol"),
        "route": analysis.get("route"),
        "close": analysis.get("latest_close"),
        "atr": analysis.get("atr"),
        "macd_hist": analysis.get("macd_histogram"),
        "rs": analysis.get("relative_strength"),
        "vol_z": analysis.get("volume_z_score"),
        "range_pos": analysis.get("close_position_in_range"),
        "technical_ok": analysis.get("technical_ok"),
        "decision": (decision or {}).get("decision"),
        "direction": (decision or {}).get("direction"),
        "score": (decision or {}).get("score"),
        "probability": (decision or {}).get("probability"),
        "reason_codes": (decision or {}).get("reason_codes"),
        "opt_confluence": conf.get("score") if conf else None,
        "opt_direction": conf.get("dominant_direction") if conf else None,
        "opt_momentum": (conf.get("by_family") or {}).get("momentum") if conf else None,
        "opt_volume": (conf.get("by_family") or {}).get("volume") if conf else None,
        "opt_structure": (conf.get("by_family") or {}).get("structure") if conf else None,
        "opt_moderate": conf.get("moderate") if conf else None,
        "opt_engines": conf.get("by_engine") if conf else None,
        "r2_tech_score": r2.get("score_0_100"),
        "r2_tier": analysis.get("r2_confluence_tier") or r2.get("confluence_tier"),
        "r2_confluence_n": r2.get("confluence_count"),
        "r2_veto": r2.get("veto"),
        "authorized": (risk or {}).get("authorized"),
        "qty": intent.get("quantity"),
        "notional_usd": intent.get("notional_usd"),
        "stop_loss": intent.get("stop_loss"),
        "take_profit": intent.get("take_profit"),
    }


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_Sin filas._"
    cols = list(rows[0].keys())
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in rows:
        cells = [_fmt(row.get(c)) for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


async def _run_cycle(force: bool) -> dict[str, Any]:
    settings = load_settings()
    mode = settings.alpaca_trading_mode.strip().lower()
    base_url = (
        settings.alpaca_live_base_url if mode == "live" else settings.alpaca_trading_base_url
    )
    client = AlpacaClient(
        api_key=settings.alpaca_api_key.get_secret_value(),
        secret_key=settings.alpaca_api_secret.get_secret_value(),
        base_url=base_url,
        dry_run=mode == "dry_run",
    )
    guard = AlpacaMarketHoursGuard(_ForceOpenClock() if force else client)
    service = AlpacaBotService(
        client=client,
        universe=settings.default_universe,
        trading_mode=mode,
        market_hours_guard=guard,
    )
    api_key = settings.alpaca_api_key.get_secret_value()
    api_secret = settings.alpaca_api_secret.get_secret_value()
    if not ALPACA_EXTENDED_CACHE:
        await ensure_alpaca_universe_loaded(api_key, api_secret)

    calibration_path = default_calibration_path()
    calibration_payload: dict[str, Any] = {}
    if calibration_path.exists():
        calibration_payload = json.loads(calibration_path.read_text(encoding="utf-8"))

    result = await service.run_cycle()
    payload = result.to_dict()
    await service.aclose()

    analyses = {a["symbol"]: a for a in payload.get("analyses") or []}
    decisions = {d["symbol"]: d for d in payload.get("decisions") or []}
    risk_by_sym = {
        (rd.get("intent") or {}).get("symbol"): rd for rd in payload.get("risk_decisions") or []
    }

    audit_rows = []
    for sym in sorted(analyses.keys()):
        audit_rows.append(
            _audit_row(analyses[sym], decisions.get(sym), risk_by_sym.get(sym))
        )

    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "calibration": calibration_payload,
        "cycle": {
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "route1_symbols": payload.get("route1_symbols"),
            "route2_symbols": payload.get("route2_symbols"),
            "blocked_reasons": payload.get("blocked_reasons"),
            "executions": payload.get("executions"),
        },
        "audit_rows": audit_rows,
        "audit_table_markdown": _markdown_table(audit_rows),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-calibration", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not args.skip_calibration:
        run_r1_options_calibration(limit_per_symbol=500)

    report = await _run_cycle(force=args.force)
    out_path = args.out or (
        _ROOT / "reports" / f"alpaca_audit_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("# Auditoría Alpaca — Calibración + Dual Route\n")
    cal = report.get("calibration") or {}
    if cal:
        fw = cal.get("family_weights") or {}
        print("## Calibración C5")
        print(f"- calibrated_at: {cal.get('calibrated_at')}")
        print(f"- pesos: momentum={fw.get('momentum')} volume={fw.get('volume')} structure={fw.get('structure')}")
        metrics = cal.get("metrics") or {}
        print(f"- muestras: {metrics.get('n_samples')} | trades: {metrics.get('n_trades')} | sharpe: {metrics.get('sharpe')}")
        print(f"- notas: {cal.get('notes')}")
        print()

    cycle = report.get("cycle") or {}
    print("## Ciclo")
    print(f"- R1: {cycle.get('route1_symbols')}")
    print(f"- R2 ({len(cycle.get('route2_symbols') or [])}): {cycle.get('route2_symbols')}")
    print(f"- blocked: {cycle.get('blocked_reasons')}")
    print()

    print("## Tabla de auditoría (todos los símbolos analizados)")
    print(report.get("audit_table_markdown"))
    print(f"\n_JSON guardado en: {out_path}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
