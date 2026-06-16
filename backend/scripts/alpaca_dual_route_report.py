"""Informe dual-route: R1 prioritaria (11) + R2 scan dinámico (top 20). # [TH]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.config.alpaca_priority_route import (
    ALPACA_ROUTE1_WATCHLIST,
    ROUTE1_NOTIONAL_MULTIPLIER,
    ROUTE2_NOTIONAL_MULTIPLIER,
)
from backend.config.settings import load_settings
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.services.alpaca_bot_service import AlpacaBotService
from backend.services.alpaca_market_hours import AlpacaMarketHoursGuard
from backend.services.alpaca_universe_fetcher import (
    ALPACA_EXTENDED_CACHE,
    ensure_alpaca_universe_loaded,
)


class _ForceOpenClock:
    async def get_clock(self) -> dict[str, object]:
        return {"is_open": True, "next_open": None, "next_close": None}


def _as_dict(obj: object) -> dict[str, object]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return {}


def _row_analysis(a: object) -> dict[str, object]:
    d = _as_dict(a)
    return {
        "symbol": d.get("symbol"),
        "route": d.get("route"),
        "latest_close": d.get("latest_close"),
        "atr": round(d.get("atr") or 0, 4) if d.get("atr") else None,
        "macd_histogram": round(d.get("macd_histogram") or 0, 6)
        if d.get("macd_histogram") is not None
        else None,
        "relative_strength": round(d.get("relative_strength") or 0, 6)
        if d.get("relative_strength") is not None
        else None,
        "volume_z_score": round(d.get("volume_z_score") or 0, 3)
        if d.get("volume_z_score") is not None
        else None,
        "technical_ok": d.get("technical_ok"),
    }


def _row_decision(d: object) -> dict[str, object]:
    x = _as_dict(d)
    return {
        "symbol": x.get("symbol"),
        "route": x.get("route"),
        "decision": x.get("decision"),
        "direction": x.get("direction"),
        "score": x.get("score"),
        "probability": x.get("probability"),
        "reason_codes": x.get("reason_codes"),
    }


def _route_summary(decisions: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for d in decisions:
        counts[str(d.get("decision", "?"))] += 1
    return dict(counts)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Informe dual-route Alpaca")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignorar mercado cerrado (diagnóstico offline)",
    )
    parser.add_argument("--json-only", action="store_true", help="Solo JSON, sin markdown")
    args = parser.parse_args()

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

    market_guard = (
        AlpacaMarketHoursGuard(_ForceOpenClock()) if args.force else AlpacaMarketHoursGuard(client)
    )

    service = AlpacaBotService(
        client=client,
        universe=settings.default_universe,
        trading_mode=mode,
        market_hours_guard=market_guard,
    )

    report: dict[str, object] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "trading_mode": service.trading_mode,
        "dry_run": service.dry_run,
        "route1_watchlist": list(ALPACA_ROUTE1_WATCHLIST),
        "route1_notional_mult": ROUTE1_NOTIONAL_MULTIPLIER,
        "route2_notional_mult": ROUTE2_NOTIONAL_MULTIPLIER,
        "force_market_open": args.force,
    }

    try:
        clock = await client.get_clock()
        report["market_clock"] = {
            "is_open": clock.get("is_open"),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
        }
    except Exception as exc:
        report["market_clock_error"] = str(exc)

    try:
        balance = await client.fetch_account_balance()
        report["account"] = {
            "equity": balance.get("equity"),
            "buying_power": balance.get("buying_power"),
            "cash": balance.get("cash"),
        }
    except Exception as exc:
        report["account_error"] = str(exc)

    api_key = settings.alpaca_api_key.get_secret_value()
    api_secret = settings.alpaca_api_secret.get_secret_value()
    if not ALPACA_EXTENDED_CACHE:
        await ensure_alpaca_universe_loaded(api_key, api_secret)

    full = service._full_universe()
    symbols = service._dual_route_symbols(full)
    report["universe"] = {
        "extended_count": len(full),
        "symbols_fetched": len(symbols),
    }

    result = await service.run_cycle()
    payload = result.to_dict()

    analyses = payload.get("analyses") or []
    decisions = payload.get("decisions") or []
    intents = payload.get("order_intents") or []
    risk_decisions = payload.get("risk_decisions") or []

    r1_decisions = [d for d in decisions if d.get("route") == "priority"]
    r2_decisions = [d for d in decisions if d.get("route") == "scan"]
    r1_intents = [i for i in intents if i.get("route") == "priority"]
    r2_intents = [i for i in intents if i.get("route") == "scan"]

    report["routes"] = {
        "route1": {
            "symbols_fixed": list(payload.get("route1_symbols") or ALPACA_ROUTE1_WATCHLIST),
            "analyses_count": len([a for a in analyses if a.get("route") == "priority"]),
            "decision_breakdown": _route_summary(r1_decisions),
            "buy_candidates": [
                _row_decision(d)
                for d in sorted(
                    [x for x in r1_decisions if x.get("decision") in {"ALLOW", "SIZE_DOWN"}],
                    key=lambda x: x.get("score", 0),
                    reverse=True,
                )
            ],
            "blocked": [
                _row_decision(d)
                for d in r1_decisions
                if d.get("decision") in {"BLOCK", "INSUFFICIENT_DATA"}
            ],
            "order_intents": [
                {
                    "symbol": i.get("symbol"),
                    "quantity": i.get("quantity"),
                    "notional_usd": i.get("notional_usd"),
                    "stop_loss": i.get("stop_loss"),
                    "take_profit": i.get("take_profit"),
                }
                for i in r1_intents
            ],
            "authorized": sum(
                1
                for rd in risk_decisions
                if rd.get("authorized") and rd.get("intent", {}).get("route") == "priority"
            ),
        },
        "route2": {
            "symbols_dynamic": list(payload.get("route2_symbols") or payload.get("prefiltered") or []),
            "analyses_count": len([a for a in analyses if a.get("route") == "scan"]),
            "decision_breakdown": _route_summary(r2_decisions),
            "buy_candidates": [
                _row_decision(d)
                for d in sorted(
                    [x for x in r2_decisions if x.get("decision") in {"ALLOW", "SIZE_DOWN"}],
                    key=lambda x: x.get("score", 0),
                    reverse=True,
                )
            ],
            "blocked": [
                _row_decision(d)
                for d in r2_decisions
                if d.get("decision") in {"BLOCK", "INSUFFICIENT_DATA"}
            ],
            "order_intents": [
                {
                    "symbol": i.get("symbol"),
                    "quantity": i.get("quantity"),
                    "notional_usd": i.get("notional_usd"),
                }
                for i in r2_intents
            ],
            "authorized": sum(
                1
                for rd in risk_decisions
                if rd.get("authorized") and rd.get("intent", {}).get("route") == "scan"
            ),
        },
    }

    report["cycle"] = {
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "blocked_reasons": payload.get("blocked_reasons"),
        "executions_count": len(payload.get("executions") or []),
    }

    report["all_analyses_by_route"] = {
        "route1": [_row_analysis(a) for a in analyses if a.get("route") == "priority"],
        "route2": [_row_analysis(a) for a in analyses if a.get("route") == "scan"],
    }

    await service.aclose()

    if args.json_only:
        print(json.dumps(report, indent=2, default=str))
        return 0

    # Markdown resumen en stdout
    md = _render_markdown(report)
    print(md)
    print("\n--- JSON completo ---\n")
    print(json.dumps(report, indent=2, default=str))
    return 0


def _render_markdown(report: dict[str, object]) -> str:
    clock = report.get("market_clock") or {}
    r1 = (report.get("routes") or {}).get("route1") or {}
    r2 = (report.get("routes") or {}).get("route2") or {}
    acct = report.get("account") or {}

    lines = [
        "# Informe Dual-Route Alpaca",
        "",
        f"**Timestamp UTC:** {report.get('timestamp_utc')}",
        f"**Modo:** {report.get('trading_mode')} | dry_run={report.get('dry_run')}",
        f"**Mercado abierto:** {clock.get('is_open')} "
        f"(force={report.get('force_market_open')})",
        f"**Buying power:** ${acct.get('buying_power', 'N/A')}",
        "",
        "## Ruta 1 — Prioritaria (11 fijos)",
        f"Sizing mult: **{report.get('route1_notional_mult')}x**",
        f"Análisis: {r1.get('analyses_count')} | Veredictos: {r1.get('decision_breakdown')}",
        f"Autorizadas: {r1.get('authorized')}",
        "",
    ]
    buys_r1 = r1.get("buy_candidates") or []
    if buys_r1:
        lines.append("| Símbolo | Decisión | Score | Prob | Razones |")
        lines.append("|---------|----------|-------|------|---------|")
        for b in buys_r1[:11]:
            reasons = ", ".join(b.get("reason_codes") or []) or "—"
            lines.append(
                f"| {b.get('symbol')} | {b.get('decision')} | {b.get('score')} "
                f"| {b.get('probability')} | {reasons} |"
            )
    else:
        lines.append("_Sin candidatos BUY en R1 este ciclo._")

    lines.extend(
        [
            "",
            "## Ruta 2 — Scan dinámico (top 20)",
            f"Sizing mult: **{report.get('route2_notional_mult')}x**",
            f"Símbolos embudo: {len(r2.get('symbols_dynamic') or [])}",
            f"Análisis: {r2.get('analyses_count')} | Veredictos: {r2.get('decision_breakdown')}",
            f"Autorizadas: {r2.get('authorized')}",
            "",
        ]
    )
    buys_r2 = r2.get("buy_candidates") or []
    if buys_r2:
        lines.append("| Símbolo | Decisión | Score | Prob |")
        lines.append("|---------|----------|-------|------|")
        for b in buys_r2[:20]:
            lines.append(
                f"| {b.get('symbol')} | {b.get('decision')} | {b.get('score')} "
                f"| {b.get('probability')} |"
            )
    else:
        lines.append("_Sin candidatos BUY en R2 este ciclo._")

    intents_r1 = r1.get("order_intents") or []
    intents_r2 = r2.get("order_intents") or []
    if intents_r1 or intents_r2:
        lines.extend(["", "## Órdenes generadas", ""])
        for label, items in [("R1", intents_r1), ("R2", intents_r2)]:
            for i in items:
                lines.append(
                    f"- **{label}** {i.get('symbol')}: "
                    f"{i.get('quantity')} acc @ ~${i.get('notional_usd')} notional"
                )

    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
