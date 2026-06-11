"""Read-only Crypto Options Intelligence for FTMO Funding Lab."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.layer_1_data.datos.binance_options_client import BinanceOptionsClient
from backend.layer_1_data.datos.deribit_options_client import DeribitOptionsClient
from backend.layer_1_data.datos.okx_options_client import OKXOptionsClient

DEFAULT_CRYPTO_OPTIONS_DB = Path("backend/data/predictions.db")
CRYPTO_OPTIONS_PROVIDER = "crypto_options_multi"
CRYPTO_OPTIONS_SYMBOLS = ("BTC/USDT", "XAUUSD", "XAGUSD", "US100.CASH")
DIRECT_CRYPTO_OPTIONS_SYMBOLS = frozenset({"BTC/USDT"})
CRYPTO_OPTIONS_PROVIDERS = ("deribit_options", "okx_options", "binance_options")


@dataclass(frozen=True)
class CryptoOptionContract:
    provider: str
    underlying: str
    instrument: str
    expiry: datetime | None
    strike: float | None
    call_put: str
    bid: float | None
    ask: float | None
    mark: float | None
    iv: float | None
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    open_interest: float | None
    volume: float | None
    as_of: datetime

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["expiry"] = self.expiry.isoformat() if self.expiry else None
        payload["as_of"] = self.as_of.isoformat()
        return payload


@dataclass(frozen=True)
class CryptoOptionsSnapshot:
    symbol: str
    underlying: str
    as_of: datetime
    ready: bool
    status: str
    context_only: bool
    gex_authorization: bool
    blockers: list[str]
    provider_health: dict[str, dict[str, Any]]
    metrics: dict[str, Any]
    contracts: list[CryptoOptionContract]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "as_of": self.as_of.isoformat(),
            "ready": self.ready,
            "status": self.status,
            "context_only": self.context_only,
            "gex_authorization": self.gex_authorization,
            "blockers": list(self.blockers),
            "provider_health": self.provider_health,
            "metrics": self.metrics,
            "contracts": [contract.to_dict() for contract in self.contracts],
        }


def fetch_crypto_options_snapshot(canonical_symbol: str) -> CryptoOptionsSnapshot:
    """Fetch and aggregate public crypto options data for one FTMO symbol."""
    symbol = _canonical_symbol(canonical_symbol)
    as_of = datetime.now(tz=UTC)
    if symbol != "BTC/USDT":
        btc_snapshot = fetch_crypto_options_snapshot("BTC/USDT")
        return CryptoOptionsSnapshot(
            symbol=symbol,
            underlying="BTC_CONTEXT",
            as_of=as_of,
            ready=False,
            status="crypto_options_context_only",
            context_only=True,
            gex_authorization=False,
            blockers=[],
            provider_health=btc_snapshot.provider_health,
            metrics=btc_snapshot.metrics,
            contracts=btc_snapshot.contracts,
        )

    contracts: list[CryptoOptionContract] = []
    provider_health: dict[str, dict[str, Any]] = {}
    contracts.extend(_fetch_deribit_contracts(as_of, provider_health))
    contracts.extend(_fetch_okx_contracts(as_of, provider_health))
    contracts.extend(_fetch_binance_contracts(as_of, provider_health))
    return build_crypto_options_snapshot(
        symbol,
        contracts,
        as_of=as_of,
        provider_health=provider_health,
    )


def build_crypto_options_snapshot(
    canonical_symbol: str,
    contracts: list[CryptoOptionContract],
    *,
    as_of: datetime | None = None,
    provider_health: dict[str, dict[str, Any]] | None = None,
) -> CryptoOptionsSnapshot:
    symbol = _canonical_symbol(canonical_symbol)
    current_time = _as_utc(as_of or datetime.now(tz=UTC))
    health = _provider_health_from_contracts(contracts)
    if provider_health:
        for provider, payload in provider_health.items():
            health[provider] = {**health.get(provider, {}), **dict(payload)}

    ok_providers = {provider for provider, payload in health.items() if payload.get("ok")}
    has_deribit = "deribit_options" in ok_providers
    quorum = len(ok_providers)
    ready = symbol == "BTC/USDT" and has_deribit and quorum >= 2
    context_only = symbol != "BTC/USDT"
    blockers: list[str] = []
    if not ready and not context_only:
        if quorum == 0:
            blockers.append("crypto_options_missing")
        elif not has_deribit:
            blockers.append("crypto_options_primary_missing")
        elif quorum == 1:
            blockers.append("crypto_options_degraded")
    if ready:
        status = "ready"
    elif context_only:
        status = "crypto_options_context_only"
    else:
        status = blockers[0] if blockers else "crypto_options_degraded"
    metrics = compute_crypto_options_metrics(contracts, as_of=current_time)
    metrics["provider_quorum"] = quorum / len(CRYPTO_OPTIONS_PROVIDERS)
    return CryptoOptionsSnapshot(
        symbol=symbol,
        underlying="BTC" if symbol == "BTC/USDT" else "BTC_CONTEXT",
        as_of=current_time,
        ready=ready,
        status=status,
        context_only=context_only,
        gex_authorization=False,
        blockers=blockers,
        provider_health=health,
        metrics=metrics,
        contracts=contracts,
    )


def compute_crypto_options_metrics(
    contracts: list[CryptoOptionContract],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    current_time = _as_utc(as_of or datetime.now(tz=UTC))
    calls = [item for item in contracts if item.call_put == "call"]
    puts = [item for item in contracts if item.call_put == "put"]
    call_volume = sum(_num(item.volume) for item in calls)
    put_volume = sum(_num(item.volume) for item in puts)
    call_oi = sum(_num(item.open_interest) for item in calls)
    put_oi = sum(_num(item.open_interest) for item in puts)
    total_oi = call_oi + put_oi
    by_expiry: dict[str, float] = {}
    for item in contracts:
        key = item.expiry.date().isoformat() if item.expiry else "unknown"
        by_expiry[key] = by_expiry.get(key, 0.0) + _num(item.open_interest)
    largest_expiry_oi = max(by_expiry.values(), default=0.0)
    near_7 = _mean_iv(
        item
        for item in contracts
        if item.expiry and abs((item.expiry - current_time).days - 7) <= 4
    )
    near_30 = _mean_iv(
        item
        for item in contracts
        if item.expiry and abs((item.expiry - current_time).days - 30) <= 10
    )
    call_25d = _nearest_delta_iv(calls, 0.25)
    put_25d = _nearest_delta_iv(puts, -0.25)
    gamma_pressure = sum(
        (_num(item.gamma) * _num(item.open_interest)) * (1.0 if item.call_put == "call" else -1.0)
        for item in contracts
    )
    largest = sorted(
        (
            {
                "strike": item.strike,
                "call_put": item.call_put,
                "open_interest": _num(item.open_interest),
                "provider": item.provider,
            }
            for item in contracts
            if item.strike is not None and item.open_interest is not None
        ),
        key=lambda row: float(row["open_interest"]),
        reverse=True,
    )[:5]
    return {
        "atm_iv": _mean_iv(contracts),
        "term_structure_7d_30d": _round_or_none(
            near_30 - near_7 if near_30 is not None and near_7 is not None else None
        ),
        "put_call_volume_ratio": _safe_ratio(put_volume, call_volume),
        "put_call_oi_ratio": _safe_ratio(put_oi, call_oi),
        "skew_25d": _round_or_none(
            put_25d - call_25d if put_25d is not None and call_25d is not None else None
        ),
        "gamma_pressure": _round_or_none(gamma_pressure),
        "largest_oi_strikes": largest,
        "expiry_concentration": _safe_ratio(largest_expiry_oi, total_oi),
    }


def persist_crypto_options_snapshot(
    db_path: Path | str,
    snapshot: CryptoOptionsSnapshot | dict[str, Any],
) -> bool:
    payload = snapshot.to_dict() if isinstance(snapshot, CryptoOptionsSnapshot) else dict(snapshot)
    symbol = _canonical_symbol(payload.get("symbol") or payload.get("canonical_symbol"))
    as_of = str(payload.get("as_of") or datetime.now(tz=UTC).isoformat())
    status = str(payload.get("status") or "unknown")
    snapshot_id = _snapshot_id(symbol, as_of, status)
    path = Path(db_path)
    _init_db(path)
    with sqlite3.connect(path) as con:
        cur = con.execute(
            """
            INSERT OR IGNORE INTO ftmo_crypto_options_snapshots (
                snapshot_id, canonical_symbol, underlying, as_of, status, ready,
                context_only, gex_authorization, provider, provider_health_json,
                metrics_json, contracts_json, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                symbol,
                str(payload.get("underlying") or ""),
                as_of,
                status,
                int(bool(payload.get("ready"))),
                int(bool(payload.get("context_only"))),
                int(bool(payload.get("gex_authorization"))),
                CRYPTO_OPTIONS_PROVIDER,
                json.dumps(payload.get("provider_health") or {}, sort_keys=True),
                json.dumps(payload.get("metrics") or {}, sort_keys=True),
                json.dumps(payload.get("contracts") or [], sort_keys=True, default=str),
                json.dumps(payload, sort_keys=True, default=str),
                datetime.now(tz=UTC).isoformat(),
            ),
        )
        con.commit()
        return cur.rowcount > 0


def crypto_options_status_for_symbol(
    db_path: Path | str,
    canonical_symbol: str,
    *,
    now: datetime | None = None,
    freshness_hours: int = 24,
) -> dict[str, Any]:
    symbol = _canonical_symbol(canonical_symbol)
    snapshot = _latest_snapshot(Path(db_path), symbol)
    if snapshot is None:
        context_only = symbol != "BTC/USDT"
        status = "crypto_options_context_missing" if context_only else "crypto_options_missing"
        blockers = [] if context_only else [status]
        return _status_payload(symbol, status, False, context_only, blockers, {}, {})
    age_stale = _is_stale(
        snapshot.get("as_of"),
        now=_as_utc(now or datetime.now(tz=UTC)),
        freshness_hours=freshness_hours,
    )
    blockers = list(snapshot.get("blockers") or [])
    if age_stale:
        blockers.append("crypto_options_stale")
    ready = bool(snapshot.get("ready")) and not age_stale
    status = "crypto_options_stale" if age_stale else str(snapshot.get("status") or "unknown")
    return _status_payload(
        symbol,
        status,
        ready,
        bool(snapshot.get("context_only")),
        blockers,
        _dict(snapshot.get("provider_health")),
        _dict(snapshot.get("metrics")),
    )


def normalize_okx_option_contract(
    instrument: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> CryptoOptionContract | None:
    summary = summary or {}
    inst_id = str(instrument.get("instId") or summary.get("instId") or "")
    parts = inst_id.split("-")
    if len(parts) < 5:
        return None
    return CryptoOptionContract(
        provider="okx_options",
        underlying=parts[0].upper(),
        instrument=inst_id,
        expiry=_millis_to_datetime(instrument.get("expTime")),
        strike=_float(instrument.get("stk") or parts[-2]),
        call_put=_call_put(instrument.get("optType") or parts[-1]),
        bid=_float(summary.get("bidPx") or summary.get("bid")),
        ask=_float(summary.get("askPx") or summary.get("ask")),
        mark=_float(summary.get("markPx") or summary.get("markVol")),
        iv=_normalize_iv(
            _float(summary.get("volLv") or instrument.get("volLv") or summary.get("markVol"))
        ),
        delta=_float(summary.get("delta") or instrument.get("delta")),
        gamma=_float(summary.get("gamma") or instrument.get("gamma")),
        vega=_float(summary.get("vega") or instrument.get("vega")),
        theta=_float(summary.get("theta") or instrument.get("theta")),
        open_interest=_float(summary.get("oi") or instrument.get("openInterest")),
        volume=_float(summary.get("vol24h") or summary.get("volume")),
        as_of=_as_utc(as_of or datetime.now(tz=UTC)),
    )


def normalize_binance_option_contract(
    option_symbol: dict[str, Any],
    *,
    mark: dict[str, Any] | None = None,
    ticker: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> CryptoOptionContract | None:
    mark = mark or {}
    ticker = ticker or {}
    symbol = str(option_symbol.get("symbol") or mark.get("symbol") or ticker.get("symbol") or "")
    parts = symbol.split("-")
    if len(parts) < 4:
        return None
    underlying = str(option_symbol.get("underlying") or parts[0]).replace("USDT", "")
    return CryptoOptionContract(
        provider="binance_options",
        underlying=underlying.upper(),
        instrument=symbol,
        expiry=_millis_to_datetime(option_symbol.get("expiryDate")),
        strike=_float(option_symbol.get("strikePrice") or parts[-2]),
        call_put=_call_put(option_symbol.get("side") or parts[-1]),
        bid=_float(ticker.get("bid") or ticker.get("bidPrice")),
        ask=_float(ticker.get("ask") or ticker.get("askPrice")),
        mark=_float(mark.get("markPrice")),
        iv=_normalize_iv(_float(mark.get("markIV") or mark.get("bidIV") or mark.get("askIV"))),
        delta=_float(mark.get("delta")),
        gamma=_float(mark.get("gamma")),
        vega=_float(mark.get("vega")),
        theta=_float(mark.get("theta")),
        open_interest=_float(ticker.get("openInterest")),
        volume=_float(ticker.get("volume")),
        as_of=_as_utc(as_of or datetime.now(tz=UTC)),
    )


def normalize_deribit_option_contract(
    instrument: dict[str, Any],
    *,
    summary: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> CryptoOptionContract | None:
    summary = summary or {}
    name = str(instrument.get("instrument_name") or summary.get("instrument_name") or "")
    if not name:
        return None
    return CryptoOptionContract(
        provider="deribit_options",
        underlying=str(instrument.get("base_currency") or name.split("-")[0]).upper(),
        instrument=name,
        expiry=_millis_to_datetime(instrument.get("expiration_timestamp")),
        strike=_float(instrument.get("strike")),
        call_put=_call_put(instrument.get("option_type") or name.split("-")[-1]),
        bid=_float(summary.get("bid_price")),
        ask=_float(summary.get("ask_price")),
        mark=_float(summary.get("mark_price")),
        iv=_normalize_iv(_float(summary.get("mark_iv"))),
        delta=_float(_dict(summary.get("greeks")).get("delta")),
        gamma=_float(_dict(summary.get("greeks")).get("gamma")),
        vega=_float(_dict(summary.get("greeks")).get("vega")),
        theta=_float(_dict(summary.get("greeks")).get("theta")),
        open_interest=_float(summary.get("open_interest")),
        volume=_float(summary.get("volume")),
        as_of=_as_utc(as_of or datetime.now(tz=UTC)),
    )


def _fetch_deribit_contracts(
    as_of: datetime,
    provider_health: dict[str, dict[str, Any]],
) -> list[CryptoOptionContract]:
    try:
        client = DeribitOptionsClient()
        instruments = client.get_instruments(currency="BTC")
        summaries = {
            str(item.get("instrument_name")): item
            for item in client.get_book_summary_by_currency(currency="BTC")
        }
        rows = [
            contract
            for item in instruments
            if (
                contract := normalize_deribit_option_contract(
                    item,
                    summary=summaries.get(str(item.get("instrument_name"))),
                    as_of=as_of,
                )
            )
        ]
        provider_health["deribit_options"] = {"ok": bool(rows), "rows": len(rows), "status": "ok"}
        return rows
    except Exception as exc:
        provider_health["deribit_options"] = {
            "ok": False,
            "rows": 0,
            "status": "error",
            "error_type": type(exc).__name__,
        }
        return []


def _fetch_okx_contracts(
    as_of: datetime,
    provider_health: dict[str, dict[str, Any]],
) -> list[CryptoOptionContract]:
    try:
        client = OKXOptionsClient()
        instruments = client.get_instruments(inst_family="BTC-USD")
        summaries = {
            str(item.get("instId")): item
            for item in client.get_option_summary(inst_family="BTC-USD")
        }
        rows = [
            contract
            for item in instruments
            if (
                contract := normalize_okx_option_contract(
                    item,
                    summary=summaries.get(str(item.get("instId"))),
                    as_of=as_of,
                )
            )
        ]
        provider_health["okx_options"] = {"ok": bool(rows), "rows": len(rows), "status": "ok"}
        return rows
    except Exception as exc:
        provider_health["okx_options"] = {
            "ok": False,
            "rows": 0,
            "status": "error",
            "error_type": type(exc).__name__,
        }
        return []


def _fetch_binance_contracts(
    as_of: datetime,
    provider_health: dict[str, dict[str, Any]],
) -> list[CryptoOptionContract]:
    try:
        client = BinanceOptionsClient()
        exchange_info = client.exchange_info()
        marks = {str(item.get("symbol")): item for item in client.mark(underlying="BTCUSDT")}
        tickers = {str(item.get("symbol")): item for item in client.ticker(underlying="BTCUSDT")}
        instruments = [
            item
            for item in exchange_info.get("optionSymbols", [])
            if str(item.get("underlying") or "").upper() == "BTCUSDT"
        ]
        rows = [
            contract
            for item in instruments
            if (
                contract := normalize_binance_option_contract(
                    item,
                    mark=marks.get(str(item.get("symbol"))),
                    ticker=tickers.get(str(item.get("symbol"))),
                    as_of=as_of,
                )
            )
        ]
        provider_health["binance_options"] = {"ok": bool(rows), "rows": len(rows), "status": "ok"}
        return rows
    except Exception as exc:
        provider_health["binance_options"] = {
            "ok": False,
            "rows": 0,
            "status": "error",
            "error_type": type(exc).__name__,
        }
        return []


def _provider_health_from_contracts(
    contracts: list[CryptoOptionContract],
) -> dict[str, dict[str, Any]]:
    health: dict[str, dict[str, Any]] = {
        provider: {"ok": False, "rows": 0, "status": "missing"}
        for provider in CRYPTO_OPTIONS_PROVIDERS
    }
    for provider in CRYPTO_OPTIONS_PROVIDERS:
        rows = [item for item in contracts if item.provider == provider]
        if rows:
            health[provider] = {"ok": True, "rows": len(rows), "status": "ok"}
    return health


def _init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS ftmo_crypto_options_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                canonical_symbol TEXT NOT NULL,
                underlying TEXT,
                as_of TEXT NOT NULL,
                status TEXT NOT NULL,
                ready INTEGER NOT NULL DEFAULT 0,
                context_only INTEGER NOT NULL DEFAULT 0,
                gex_authorization INTEGER NOT NULL DEFAULT 0,
                provider TEXT NOT NULL,
                provider_health_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                contracts_json TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ftmo_crypto_options_symbol_asof
                ON ftmo_crypto_options_snapshots(canonical_symbol, as_of);
            """
        )


def _latest_snapshot(path: Path, symbol: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        tables = {
            str(row[0]) for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "ftmo_crypto_options_snapshots" not in tables:
            return None
        row = con.execute(
            """
            SELECT snapshot_json
            FROM ftmo_crypto_options_snapshots
            WHERE canonical_symbol = ?
            ORDER BY as_of DESC, created_at DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    parsed = json.loads(str(row["snapshot_json"]))
    return parsed if isinstance(parsed, dict) else None


def _status_payload(
    symbol: str,
    status: str,
    ready: bool,
    context_only: bool,
    blockers: list[str],
    provider_health: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "crypto_options_ready": ready,
        "crypto_options_status": status,
        "crypto_options_context_only": context_only,
        "crypto_options_gex_authorization": False,
        "crypto_options_provider_health": provider_health,
        "crypto_options_metrics": metrics,
        "crypto_options_blockers": list(dict.fromkeys(blockers)),
    }


def _snapshot_id(symbol: str, as_of: str, status: str) -> str:
    return hashlib.sha256(f"crypto_options:{symbol}:{as_of}:{status}".encode()).hexdigest()


def _canonical_symbol(value: object) -> str:
    text = str(value or "").upper().strip()
    if text in {"BTCUSDT", "BTC-USD", "BTCUSD"}:
        return "BTC/USDT"
    if text in {"US100", "US100CASH"}:
        return "US100.CASH"
    return text


def _millis_to_datetime(value: object) -> datetime | None:
    parsed = _float(value)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed / 1000.0, tz=UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_iv(value: float | None) -> float | None:
    if value is None:
        return None
    if abs(value) > 3.0:
        return _round_or_none(value / 100.0)
    return _round_or_none(value)


def _num(value: object) -> float:
    return _float(value) or 0.0


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _call_put(value: object) -> str:
    text = str(value or "").upper()
    if text in {"C", "CALL"}:
        return "call"
    if text in {"P", "PUT"}:
        return "put"
    return "unknown"


def _mean_iv(items: Any) -> float | None:
    values = [_num(item.iv) for item in items if getattr(item, "iv", None) is not None]
    if not values:
        return None
    return _round_or_none(sum(values) / len(values))


def _nearest_delta_iv(items: list[CryptoOptionContract], target_delta: float) -> float | None:
    candidates = [item for item in items if item.delta is not None and item.iv is not None]
    if not candidates:
        return None
    selected = min(candidates, key=lambda item: abs(float(item.delta or 0.0) - target_delta))
    return _round_or_none(selected.iv)


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return _round_or_none(numerator / denominator)


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 8)


def _is_stale(value: object, *, now: datetime, freshness_hours: int) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    parsed = _as_utc(parsed)
    return (now - parsed).total_seconds() > max(1, int(freshness_hours)) * 3600
