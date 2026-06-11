"""Recent options trades provider for order-flow toxicity analysis."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pandas as pd

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

TRADE_COLUMNS = [
    "timestamp",
    "option_type",
    "strike",
    "expiry",
    "volume",
    "premium",
    "bid",
    "ask",
    "implied_vol",
    "delta",
]

_FLOAT_COLUMNS = ("strike", "premium", "bid", "ask", "implied_vol", "delta")
_POLYGON_BASE_URLS = ("https://api.polygon.io", "https://api.massive.com")


def _empty_trades_df() -> pd.DataFrame:
    df = pd.DataFrame({column: pd.Series(dtype="object") for column in TRADE_COLUMNS})
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["option_type"] = df["option_type"].astype("string")
    df["expiry"] = df["expiry"].astype("string")
    df["volume"] = df["volume"].astype("int64")
    for column in _FLOAT_COLUMNS:
        df[column] = df[column].astype("float64")
    return df


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(out):
        return None
    return out


def _safe_int(value: object) -> int:
    number = _safe_float(value)
    if number is None or number <= 0:
        return 0
    return max(0, int(round(number)))


def _mid_or_last(
    bid: object, ask: object, last: object
) -> tuple[float | None, float | None, float | None]:
    bid_f = _safe_float(bid)
    ask_f = _safe_float(ask)
    last_f = _safe_float(last)
    if bid_f is not None and ask_f is not None and bid_f >= 0 and ask_f >= bid_f:
        return (bid_f + ask_f) / 2.0, bid_f, ask_f
    if last_f is not None and last_f > 0:
        return last_f, last_f, last_f
    return None, None, None


def _load_option_chain_rows(symbol: str) -> tuple[list[object], str, str, dict[str, object]]:
    from backend.layer_1_data.datos.massive_options_fetcher import fetch_option_chain_raw
    from backend.routers.options_router import _parse_finnhub_chain

    raw, chain_src, fetch_meta = fetch_option_chain_raw(symbol, None)
    as_of = datetime.now(tz=UTC).isoformat()
    meta = dict(fetch_meta) if isinstance(fetch_meta, dict) else {}
    if raw is None:
        return [], as_of, chain_src or "", meta

    quote = raw.get("quote") if isinstance(raw, dict) else None
    spot_raw = _safe_float(quote.get("c") if isinstance(quote, dict) else None)
    spot = spot_raw or 100.0
    rows, *_ = _parse_finnhub_chain(raw, spot, None, 0.04)
    return rows, as_of, chain_src or "", meta


def _polygon_api_key() -> str | None:
    for env_name in (
        "MASSIVE_KEY_OPTIONS_PRIMARY",
        "MASSIVE_KEY_OPTIONS_SECONDARY",
    ):
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw
    return None


def _occ_option_ticker(symbol: str, expiry: str, option_type: str, strike: float) -> str:
    compact_expiry = expiry.replace("-", "")[2:]
    right = option_type.upper()
    strike_code = f"{int(round(strike * 1000)):08d}"
    return f"O:{symbol.upper()}{compact_expiry}{right}{strike_code}"


def _candidate_contracts(
    symbol: str, rows: Iterable[object], max_contracts: int = 6
) -> list[SimpleNamespace]:
    candidates: list[SimpleNamespace] = []
    for row in rows:
        strike = _safe_float(getattr(row, "strike", None))
        expiry = str(getattr(row, "expiration", "") or "")
        if strike is None or not expiry:
            continue
        for option_type, prefix in (("C", "call"), ("P", "put")):
            volume = _safe_int(getattr(row, f"{prefix}_volume", None))
            if volume <= 0:
                continue
            candidates.append(
                SimpleNamespace(
                    row=row,
                    option_type=option_type,
                    volume=volume,
                    ticker=_occ_option_ticker(symbol, expiry, option_type, strike),
                )
            )
    candidates.sort(key=lambda item: item.volume, reverse=True)
    return candidates[:max_contracts]


def _fetch_polygon_trades_for_contract(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    candidate: SimpleNamespace,
    since: datetime,
) -> list[dict[str, object]]:
    try:
        response = client.get(
            f"{base_url}/v3/trades/{candidate.ticker}",
            params={
                "timestamp.gte": int(since.timestamp() * 1_000_000_000),
                "limit": 50,
                "order": "desc",
                "apiKey": api_key,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("options trades real fetch failed for %s: %s", candidate.ticker, exc)
        return []

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return []
    return [entry for entry in results if isinstance(entry, dict)]


def _real_trade_rows(
    candidate: SimpleNamespace, entries: list[dict[str, object]]
) -> list[dict[str, object]]:
    row = candidate.row
    prefix = "call" if candidate.option_type == "C" else "put"
    premium_mid, bid, ask = _mid_or_last(
        getattr(row, f"{prefix}_bid", None),
        getattr(row, f"{prefix}_ask", None),
        getattr(row, f"{prefix}_last", None),
    )
    if premium_mid is None or bid is None or ask is None:
        return []

    out: list[dict[str, object]] = []
    for entry in entries:
        premium = _safe_float(entry.get("price") or entry.get("p"))
        volume = _safe_int(entry.get("size") or entry.get("s"))
        timestamp_ns = _safe_float(
            entry.get("sip_timestamp") or entry.get("participant_timestamp") or entry.get("t")
        )
        if premium is None or volume <= 0:
            continue
        ts = datetime.now(tz=UTC)
        if timestamp_ns is not None and timestamp_ns > 0:
            ts = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC)
        out.append(
            {
                "timestamp": ts,
                "option_type": candidate.option_type,
                "strike": _safe_float(getattr(row, "strike", None)) or 0.0,
                "expiry": str(getattr(row, "expiration", "") or ""),
                "volume": volume,
                "premium": premium,
                "bid": bid,
                "ask": ask,
                "implied_vol": _safe_float(getattr(row, f"{prefix}_iv", None)) or 0.0,
                "delta": _safe_float(getattr(row, f"{prefix}_delta", None)) or 0.0,
                "_synthetic": False,
                "open_interest": _safe_float(getattr(row, f"{prefix}_oi", None)) or 0.0,
            }
        )
    return out


def _fetch_real_trades(symbol: str, rows: list[object], lookback_hours: int) -> pd.DataFrame:
    api_key = _polygon_api_key()
    if api_key is None:
        logger.info("options trades: no Polygon/Massive historical trades key configured")
        return _empty_trades_df()

    candidates = _candidate_contracts(symbol, rows)
    if not candidates:
        return _empty_trades_df()

    since = datetime.now(tz=UTC) - timedelta(hours=max(1, int(lookback_hours)))
    with httpx.Client(timeout=5.0) as client:
        for base_url in _POLYGON_BASE_URLS:
            all_rows: list[dict[str, object]] = []
            for candidate in candidates:
                entries = _fetch_polygon_trades_for_contract(
                    client, base_url, api_key, candidate, since
                )
                all_rows.extend(_real_trade_rows(candidate, entries))
            if all_rows:
                logger.info("options trades: using real trades via %s for %s", base_url, symbol)
                return _normalize_trades_df(pd.DataFrame(all_rows))

    return _empty_trades_df()


def _split_volume(volume: int) -> list[int]:
    n_trades = min(20, max(1, volume // 50))
    base = max(1, volume // n_trades)
    chunks = [base] * n_trades
    remainder = volume - sum(chunks)
    for idx in range(max(0, remainder)):
        chunks[idx % n_trades] += 1
    return [chunk for chunk in chunks if chunk > 0]


def _synthetic_rows_for_side(
    row: object,
    option_type: str,
    lookback_hours: int,
    now: datetime,
) -> list[dict[str, object]]:
    prefix = "call" if option_type == "C" else "put"
    volume = _safe_int(getattr(row, f"{prefix}_volume", None))
    if volume <= 0:
        return []

    premium, bid, ask = _mid_or_last(
        getattr(row, f"{prefix}_bid", None),
        getattr(row, f"{prefix}_ask", None),
        getattr(row, f"{prefix}_last", None),
    )
    if premium is None or bid is None or ask is None:
        return []

    chunks = _split_volume(volume)
    window = timedelta(hours=max(1, int(lookback_hours)))
    step = window / max(len(chunks), 1)
    start = now - window
    return [
        {
            "timestamp": start + step * idx,
            "option_type": option_type,
            "strike": _safe_float(getattr(row, "strike", None)) or 0.0,
            "expiry": str(getattr(row, "expiration", "") or ""),
            "volume": chunk,
            "premium": premium,
            "bid": bid,
            "ask": ask,
            "implied_vol": _safe_float(getattr(row, f"{prefix}_iv", None)) or 0.0,
            "delta": _safe_float(getattr(row, f"{prefix}_delta", None)) or 0.0,
            "_synthetic": True,
            "open_interest": _safe_float(getattr(row, f"{prefix}_oi", None)) or 0.0,
        }
        for idx, chunk in enumerate(chunks)
    ]


def _build_synthetic_trades(rows: list[object], lookback_hours: int) -> pd.DataFrame:
    now = datetime.now(tz=UTC)
    output: list[dict[str, object]] = []
    for row in rows:
        output.extend(_synthetic_rows_for_side(row, "C", lookback_hours, now))
        output.extend(_synthetic_rows_for_side(row, "P", lookback_hours, now))

    if not output:
        return _empty_trades_df()

    logger.info(
        "options trades: using synthetic trades from option-chain volume (%d rows)", len(output)
    )
    return _normalize_trades_df(pd.DataFrame(output))


def _normalize_trades_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_trades_df()

    out = df.reindex(columns=TRADE_COLUMNS).copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce", utc=True).dt.tz_convert(
        None
    )
    out["option_type"] = out["option_type"].astype(str).str.upper()
    out["expiry"] = out["expiry"].astype(str)
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).round().astype("int64")
    for column in _FLOAT_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0).astype("float64")
    out = out.dropna(subset=["timestamp"])
    out = out[out["volume"] > 0].sort_values("timestamp").reset_index(drop=True)
    return out if not out.empty else _empty_trades_df()


def fetch_recent_option_trades(symbol: str, lookback_hours: int) -> pd.DataFrame:
    """Return recent options trades or synthetic flow rows with a stable schema."""
    sym = symbol.upper().strip()
    try:
        rows, _, source, _ = _load_option_chain_rows(sym)
    except Exception as exc:
        logger.warning("options trades: option-chain snapshot failed for %s: %s", sym, exc)
        return _empty_trades_df()

    if not rows:
        logger.warning("options trades: no option-chain rows available for %s", sym)
        return _empty_trades_df()

    try:
        real = _fetch_real_trades(sym, list(rows), lookback_hours)
        if not real.empty:
            return real
    except Exception as exc:
        logger.warning("options trades: real trades path failed for %s: %s", sym, exc)

    synthetic = _build_synthetic_trades(list(rows), lookback_hours)
    if synthetic.empty:
        logger.warning(
            "options trades: no real or synthetic trades available for %s via %s", sym, source
        )
    return synthetic


if __name__ == "__main__":
    sample = fetch_recent_option_trades("SPY", lookback_hours=4)
    logger.info(
        "SPY options trades provider sample rows=%d columns=%s", len(sample), list(sample.columns)
    )
