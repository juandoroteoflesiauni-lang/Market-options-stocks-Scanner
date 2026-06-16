"""Smoke test: ciclo completo Alpaca paper + informe JSON (sin secrets)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Repo root on sys.path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.config.settings import load_settings
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.services.alpaca_bot_service import AlpacaBotService
from backend.services.alpaca_universe_fetcher import (
    ALPACA_EXTENDED_CACHE,
    ensure_alpaca_universe_loaded,
)


def _summarize_analysis(a: object) -> dict[str, object]:
    d = a.model_dump() if hasattr(a, "model_dump") else {}
    return {
        "symbol": d.get("symbol"),
        "latest_close": d.get("latest_close"),
        "atr": d.get("atr"),
        "macd_histogram": d.get("macd_histogram"),
        "relative_strength": d.get("relative_strength"),
        "volume_z_score": d.get("volume_z_score"),
        "technical_ok": d.get("technical_ok"),
    }


def _summarize_decision(d: object) -> dict[str, object]:
    x = d.model_dump() if hasattr(d, "model_dump") else {}
    return {
        "symbol": x.get("symbol"),
        "decision": x.get("decision"),
        "direction": x.get("direction"),
        "score": x.get("score"),
        "reason_codes": x.get("reason_codes"),
    }


async def main() -> int:
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

    service = AlpacaBotService(
        client=client,
        universe=settings.default_universe,
        trading_mode=mode,
    )

    report: dict[str, object] = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "trading_mode": service.trading_mode,
        "is_live": service.is_live,
        "dry_run": service.dry_run,
        "base_url": base_url,
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
            "status": balance.get("status"),
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
    pool = service._working_pool(full)
    report["universe"] = {
        "extended_count": len(full),
        "working_pool_count": len(pool),
        "working_pool_sample": list(pool[:15]),
    }

    result = await service.run_cycle()
    payload = result.to_dict()

    report["cycle"] = {
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "trading_environment": payload.get("trading_environment"),
        "blocked_reasons": payload.get("blocked_reasons"),
        "prefiltered_count": len(payload.get("prefiltered") or []),
        "prefiltered_top10": (payload.get("prefiltered") or [])[:10],
        "analyses_count": len(payload.get("analyses") or []),
        "decisions_count": len(payload.get("decisions") or []),
        "order_intents_count": len(payload.get("order_intents") or []),
        "authorized_count": sum(
            1 for rd in (payload.get("risk_decisions") or []) if rd.get("authorized")
        ),
        "executions_count": len(payload.get("executions") or []),
    }

    analyses = payload.get("analyses") or []
    decisions = payload.get("decisions") or []
    report["top_analyses"] = [
        _summarize_analysis(a) for a in analyses[:8]
    ]
    report["top_decisions"] = [
        _summarize_decision(d)
        for d in sorted(decisions, key=lambda x: x.get("score", 0), reverse=True)[:8]
    ]

    intents = payload.get("order_intents") or []
    report["order_intents"] = [
        {
            "symbol": i.get("symbol"),
            "quantity": i.get("quantity"),
            "reference_price": i.get("reference_price"),
            "stop_loss": i.get("stop_loss"),
            "take_profit": i.get("take_profit"),
            "notional_usd": i.get("notional_usd"),
        }
        for i in intents
    ]

    risk_decisions = payload.get("risk_decisions") or []
    report["risk_decisions"] = [
        {
            "symbol": rd.get("intent", {}).get("symbol"),
            "authorized": rd.get("authorized"),
            "reason_codes": rd.get("reason_codes"),
            "adjusted_quantity": rd.get("adjusted_quantity"),
        }
        for rd in risk_decisions
    ]

    executions = payload.get("executions") or []
    report["executions"] = [
        {
            "id": e.get("id"),
            "symbol": e.get("symbol"),
            "status": e.get("status"),
            "qty": e.get("qty"),
            "filled_qty": e.get("filled_qty"),
            "client_order_id": e.get("client_order_id"),
        }
        for e in executions
        if isinstance(e, dict)
    ]

    try:
        positions = await client.fetch_positions()
        report["positions_after"] = [
            {
                "symbol": p.get("symbol"),
                "qty": p.get("qty"),
                "market_value": p.get("market_value"),
                "unrealized_pl": p.get("unrealized_pl"),
            }
            for p in positions[:10]
        ]
    except Exception as exc:
        report["positions_error"] = str(exc)

    await service.aclose()
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
