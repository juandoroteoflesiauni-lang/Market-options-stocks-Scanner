"""Recopila operaciones BingX del bot (API + audit DuckDB) para un rango de fechas. # [PD-3][TH]"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from backend.layer_1_data.datos.bingx_fill_price import (
    parse_bingx_executed_at_utc,
    resolve_fill_price_from_row,
)

_DEFAULT_SYMBOLS = (
    "AAPL-USDT",
    "TSLA-USDT",
    "META-USDT",
    "INTC-USDT",
    "GOOGL-USDT",
    "MCD-USDT",
    "SPX-USDT",
    "HOOD-USDT",
    "COIN-USDT",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect BingX bot operations for date range.")
    parser.add_argument(
        "--from-date", default="2026-06-16", help="Start date YYYY-MM-DD (inclusive)"
    )
    parser.add_argument("--to-date", default="2026-06-17", help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--output",
        default="reports/bingx_bot_operations_{date_from}_{date_to}.json",
        help="Output JSON path (supports {from} {to} placeholders)",
    )
    parser.add_argument("--audit-db", default="data/bingx_bot_audit.duckdb")
    return parser.parse_args()


def _ms_range(d0: date, d1: date) -> tuple[int, int]:
    start = datetime.combine(d0, time.min, tzinfo=UTC)
    end = datetime.combine(d1 + timedelta(days=1), time.min, tzinfo=UTC) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _unwrap_bingx_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    code = payload.get("code")
    if code is not None and str(code) not in {"0", "00000"}:
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"items": data}
    return payload


def _unwrap_orders(payload: Any) -> list[dict[str, Any]]:
    data = _unwrap_bingx_payload(payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("orders", "fill_history_orders", "items"):
        block = data.get(key)
        if isinstance(block, list):
            return [row for row in block if isinstance(row, dict)]
    return []


def _normalize_order(row: dict[str, Any], *, symbol: str) -> dict[str, Any]:
    ts = row.get("time") or row.get("updateTime") or row.get("createTime") or row.get("timestamp")
    filled_qty = (
        row.get("executedQty") or row.get("filledQty") or row.get("quantity") or row.get("qty")
    )
    avg_price = resolve_fill_price_from_row(row)
    pnl = (
        row.get("profit")
        or row.get("realisedProfit")
        or row.get("realizedProfit")
        or row.get("pnl")
    )
    fee = row.get("commission") or row.get("fee")
    status = str(row.get("status") or row.get("orderStatus") or "").upper()
    side = row.get("side") or row.get("positionSide")
    reduce_only = row.get("reduceOnly")
    order_type = row.get("type") or row.get("orderType")
    order_id = row.get("orderId") or row.get("orderID") or row.get("id")
    client_id = row.get("clientOrderId") or row.get("newClientOrderId")

    executed_at = None
    parsed_dt = parse_bingx_executed_at_utc(row)
    if parsed_dt is not None:
        executed_at = parsed_dt.isoformat()
    elif isinstance(ts, str) and ts.strip():
        executed_at = ts

    return {
        "source": "bingx_api",
        "symbol": symbol,
        "executed_at_utc": executed_at,
        "side": side,
        "position_side": row.get("positionSide"),
        "order_type": order_type,
        "status": status or None,
        "filled_qty": float(filled_qty) if filled_qty not in (None, "") else None,
        "avg_price": float(avg_price) if avg_price not in (None, "") else None,
        "closed_pnl_vst": float(pnl) if pnl not in (None, "") else None,
        "fee_vst": float(fee) if fee not in (None, "") else None,
        "reduce_only": reduce_only,
        "leverage": row.get("leverage"),
        "venue_order_id": str(order_id) if order_id else None,
        "client_order_id": str(client_id) if client_id else None,
        "raw_status": status,
    }


def _parse_executed_at(row: dict[str, Any]) -> datetime | None:
    return parse_bingx_executed_at_utc(row)


def _in_range(dt: datetime | None, d0: date, d1: date) -> bool:
    if dt is None:
        return True
    local_date = dt.astimezone(UTC).date()
    return d0 <= local_date <= d1


def _normalize_fill(row: dict[str, Any], *, display_symbol: str) -> dict[str, Any]:
    executed_at = _parse_executed_at(row)
    qty = row.get("qty") or row.get("executedQty") or row.get("quantity")
    price = resolve_fill_price_from_row(row)
    pnl = row.get("realisedPNL") or row.get("profit") or row.get("realizedProfit")
    fee = row.get("commission")
    return {
        "source": "bingx_fill_history",
        "symbol": display_symbol,
        "venue_symbol": row.get("symbol"),
        "executed_at_utc": executed_at.astimezone(UTC).isoformat() if executed_at else None,
        "side": row.get("side"),
        "position_side": row.get("positionSide"),
        "order_type": "MARKET",
        "status": "FILLED",
        "filled_qty": float(qty) if qty not in (None, "") else None,
        "avg_price": float(price) if price not in (None, "") else None,
        "closed_pnl_vst": float(pnl) if pnl not in (None, "") else None,
        "fee_vst": float(fee) if fee not in (None, "") else None,
        "venue_order_id": str(row.get("orderId") or row.get("orderID") or "") or None,
        "trade_id": row.get("tradeId"),
        "client_order_id": row.get("clientOrderID") or row.get("clientOrderId"),
    }


async def _fetch_exchange_orders(
    symbols: tuple[str, ...],
    d0: date,
    d1: date,
) -> list[dict[str, Any]]:
    from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient

    trading_env = os.getenv("BINGX_BOT_TRADING_ENV", "prod-vst")
    base_url = BINGX_REST_VST_BASE if trading_env == "prod-vst" else None
    client = BingXClient(
        api_key=os.getenv("BINGX_API_KEY", ""),
        secret_key=os.getenv("BINGX_SECRET", ""),
        base_url=base_url or os.getenv("BINGX_REST_BASE", ""),
        dry_run=False,
        allow_env_dry_run_override=False,
    )

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for symbol in symbols:
        api_symbol = await client._resolve_perp_symbol(symbol)
        payloads: list[Any] = []
        for path, extra in (
            ("/openApi/swap/v2/trade/fillHistory", {}),
            ("/openApi/swap/v2/trade/allOrders", {}),
            ("/openApi/swap/v2/trade/allFillOrders", {}),
        ):
            try:
                params: dict[str, Any] = {"symbol": api_symbol, "limit": 500, **extra}
                payloads.append(await client._signed_request("GET", path, params))
            except Exception as exc:
                out.append(
                    {
                        "source": "bingx_api_error",
                        "symbol": symbol,
                        "endpoint": path,
                        "error": str(exc)[:200],
                    }
                )

        for payload in payloads:
            for row in _unwrap_orders(payload):
                executed_at = _parse_executed_at(row)
                if not _in_range(executed_at, d0, d1):
                    continue
                normalized = _normalize_fill(row, display_symbol=symbol)
                key = "|".join(
                    str(normalized.get(k) or "")
                    for k in ("venue_order_id", "trade_id", "filled_qty", "executed_at_utc")
                )
                if key in seen:
                    continue
                seen.add(key)
                if normalized["filled_qty"] and normalized["filled_qty"] > 0:
                    out.append(normalized)

    out.sort(key=lambda r: str(r.get("executed_at_utc") or ""))
    return out


def _load_audit_operations(
    audit_db: str,
    d0: date,
    d1: date,
) -> list[dict[str, Any]]:
    import duckdb

    path = Path(audit_db)
    if not path.exists():
        return []

    target_dates = {
        (d0 + timedelta(days=offset)).isoformat() for offset in range((d1 - d0).days + 1)
    }
    rows: list[tuple[Any, ...]] = []
    try:
        con = duckdb.connect(str(path), read_only=True)
        rows = con.execute(
            "SELECT started_at, finished_at, dry_run, payload FROM bingx_audit_cycles ORDER BY started_at"
        ).fetchall()
        con.close()
    except duckdb.IOException:
        con = duckdb.connect(":memory:")
        con.execute(f"ATTACH '{path.as_posix()}' AS audit_db (READ_ONLY)")
        rows = con.execute(
            "SELECT started_at, finished_at, dry_run, payload FROM audit_db.bingx_audit_cycles ORDER BY started_at"
        ).fetchall()
        con.close()

    ops: list[dict[str, Any]] = []
    for started_at, finished_at, dry_run, payload_raw in rows:
        started = str(started_at)[:10]
        if started not in target_dates:
            continue
        payload = json.loads(payload_raw)
        for block in ("executions", "exchange_responses"):
            for _index, ex in enumerate(payload.get(block) or []):
                if not isinstance(ex, dict):
                    continue
                client_id = ex.get("client_order_id") or ex.get("clientOrderId")
                symbol = ex.get("symbol") or ex.get("venue_symbol")
                if not symbol and isinstance(client_id, str) and "bingxbot_" in client_id:
                    tail = client_id.split("_")[-1]
                    if tail.endswith("USDT"):
                        symbol = f"{tail[:-4]}-USDT" if "-" not in tail else tail
                ops.append(
                    {
                        "source": f"audit_{block}",
                        "cycle_id": payload.get("cycle_id"),
                        "cycle_started_utc": started_at,
                        "cycle_finished_utc": finished_at,
                        "dry_run": bool(dry_run),
                        "symbol": symbol,
                        "side": ex.get("side"),
                        "position_side": ex.get("position_side"),
                        "ok": ex.get("ok"),
                        "quantity": ex.get("quantity") or ex.get("qty"),
                        "notional_usdt": ex.get("notional_usdt"),
                        "venue_order_id": ex.get("venue_order_id") or ex.get("order_id"),
                        "client_order_id": client_id,
                        "error": ex.get("error"),
                        "reduce_only": ex.get("reduce_only"),
                    }
                )
    return ops


def _dedupe_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(k) or "")
        for k in ("venue_order_id", "client_order_id", "symbol", "executed_at_utc", "filled_qty")
    )


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [
        float(r["closed_pnl_vst"]) for r in rows if isinstance(r.get("closed_pnl_vst"), int | float)
    ]
    by_symbol: dict[str, int] = {}
    for row in rows:
        sym = str(row.get("symbol") or "UNKNOWN")
        by_symbol[sym] = by_symbol.get(sym, 0) + 1
    return {
        "total_rows": len(rows),
        "symbols": by_symbol,
        "closed_pnl_vst_sum": round(sum(pnl_values), 4) if pnl_values else None,
        "rows_with_pnl": len(pnl_values),
    }


async def _main() -> int:
    from backend.config.logger_setup import get_logger

    logger = get_logger(__name__)
    args = _parse_args()
    d0 = date.fromisoformat(args.from_date)
    d1 = date.fromisoformat(args.to_date)

    logger.info("collect_bingx_ops range=%s..%s", d0, d1)
    audit_rows = _load_audit_operations(args.audit_db, d0, d1)
    audit_symbols = {
        str(row.get("symbol"))
        for row in audit_rows
        if isinstance(row.get("symbol"), str) and row.get("symbol")
    }
    symbols = tuple(dict.fromkeys((*_DEFAULT_SYMBOLS, *sorted(audit_symbols))))
    api_rows = await _fetch_exchange_orders(symbols, d0, d1)

    merged: dict[str, dict[str, Any]] = {}
    for row in api_rows + audit_rows:
        if row.get("source") == "bingx_api_error":
            merged[f"error:{row.get('symbol')}"] = row
            continue
        merged[_dedupe_key(row)] = row

    exchange_filled = [r for r in api_rows if r.get("source") == "bingx_fill_history"]
    exchange_filled.sort(key=lambda r: str(r.get("executed_at_utc") or ""))

    report = {
        "generated_at_utc": datetime.now(tz=UTC).isoformat(),
        "account_env": os.getenv("BINGX_BOT_TRADING_ENV", "prod-vst"),
        "date_range": {"from": d0.isoformat(), "to": d1.isoformat()},
        "summary": {
            "exchange_orders": _summarize(exchange_filled),
            "audit_executions": _summarize(audit_rows),
        },
        "exchange_orders_filled": exchange_filled,
        "audit_bot_executions": audit_rows,
        "symbols_queried": list(symbols),
        "notes": [
            "exchange_orders_filled: órdenes FILLED desde BingX API (fuente de verdad del exchange).",
            "audit_bot_executions: ejecuciones registradas por el bot en DuckDB.",
        ],
    }

    out_path = args.output.format(date_from=d0.isoformat(), date_to=d1.isoformat())
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info(
        "collect_bingx_ops.done path=%s exchange=%s audit=%s",
        out_path,
        len(exchange_filled),
        len(audit_rows),
    )
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
