"""Persistent temporal history for institutional options-chain analytics."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .chain_institutional_analytics import (
    ChainAlert,
    ChainInstitutionalAnalyticsResponse,
    InstitutionalOptionStrikeRow,
)

DEFAULT_HISTORY_DB = Path(__file__).resolve().parents[2] / "data" / "options_chain_analytics_history.sqlite3"
WALL_SHIFT_MIN_PCT = 0.25
ZERO_DTE_SURGE_MIN_POINTS = 10.0
ZERO_DTE_SURGE_ABSOLUTE_LEVEL = 35.0


@dataclass(frozen=True)
class ChainAnalyticsSnapshot:
    symbol: str
    expiry_scope: str
    as_of: str
    spot: float
    call_wall: float | None
    put_wall: float | None
    vol_trigger_proxy: float | None
    gamma_regime: str
    zero_dte_gamma_share: float
    dominant_expiry: str | None
    dominant_expiry_score: float | None
    standard_metrics: dict[str, float | None]


class ChainAnalyticsHistoryPoint(BaseModel):
    as_of: str
    spot: float
    call_wall: float | None = None
    put_wall: float | None = None
    vol_trigger_proxy: float | None = None
    gamma_regime: str
    zero_dte_gamma_share: float
    dominant_expiry: str | None = None
    dominant_expiry_score: float | None = None
    call_wall_change: float | None = None
    put_wall_change: float | None = None
    vol_trigger_change: float | None = None
    zero_dte_gamma_share_change: float | None = None
    dominant_expiry_changed: bool = False
    gamma_regime_changed: bool = False
    standard_metrics: dict[str, float | None] = Field(default_factory=dict)


class ChainAnalyticsHistoryResponse(BaseModel):
    ticker: str
    expiry_scope: str
    points: list[ChainAnalyticsHistoryPoint] = Field(default_factory=list)
    ok: bool = True
    error: str | None = None


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _best_call_wall(rows: list[InstitutionalOptionStrikeRow]) -> float | None:
    candidates = [
        (abs(call_gex), float(row.strike))
        for row in rows
        for call_gex in [_finite(row.call_gex)]
        if call_gex is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _best_put_wall(rows: list[InstitutionalOptionStrikeRow]) -> float | None:
    candidates = [
        (abs(put_gex), float(row.strike))
        for row in rows
        for put_gex in [_finite(row.put_gex)]
        if put_gex is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _snapshot_from_response(
    response: ChainInstitutionalAnalyticsResponse,
    expiry_scope: str | None,
) -> ChainAnalyticsSnapshot:
    dominant = response.dominant_expiries[0] if response.dominant_expiries else None
    zero_dte_share = max((float(exp.zero_dte_gamma_share) for exp in response.expiry_analytics), default=0.0)
    standard = response.advanced_flow_metrics.institutional_standard
    return ChainAnalyticsSnapshot(
        symbol=response.ticker.upper(),
        expiry_scope=expiry_scope or "__ALL__",
        as_of=response.as_of,
        spot=float(response.spot),
        call_wall=_best_call_wall(response.chain),
        put_wall=_best_put_wall(response.chain),
        vol_trigger_proxy=_finite(response.institutional_metrics.vol_trigger_proxy),
        gamma_regime=str(response.institutional_metrics.gamma_regime),
        zero_dte_gamma_share=zero_dte_share,
        dominant_expiry=dominant.expiration if dominant else None,
        dominant_expiry_score=float(dominant.dominance_score) if dominant else None,
        standard_metrics={
            "normalized_25d_skew_30": _finite(standard.normalized_25d_skew_30),
            "vix_style_vol_30d": _finite(standard.vix_style_vol_30d),
            "vega_notional_traded": _finite(standard.vega_notional_traded),
            "trade_weighted_quoted_spread_pct": _finite(standard.trade_weighted_quoted_spread_pct),
            "effective_spread_pct": _finite(standard.effective_spread_pct),
            "implied_borrow_30d": _finite(standard.implied_borrow_30d),
            "institutional_confidence_score": _finite(standard.institutional_confidence_score),
        },
    )


def _same_side_of_spot(snapshot: ChainAnalyticsSnapshot) -> int | None:
    if snapshot.vol_trigger_proxy is None or snapshot.spot <= 0:
        return None
    diff = snapshot.vol_trigger_proxy - snapshot.spot
    if abs(diff) <= max(snapshot.spot * 0.0005, 0.01):
        return 0
    return 1 if diff > 0 else -1


def _distance_pct(level: float | None, spot: float) -> float | None:
    if level is None or spot <= 0:
        return None
    return round(abs(level - spot) / spot * 100.0, 4)


def _diff(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 6)


def _parse_standard_metrics(raw: object) -> dict[str, float | None]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): _finite(value) for key, value in parsed.items()}


def _history_point(
    current: ChainAnalyticsSnapshot,
    previous: ChainAnalyticsSnapshot | None,
) -> ChainAnalyticsHistoryPoint:
    return ChainAnalyticsHistoryPoint(
        as_of=current.as_of,
        spot=current.spot,
        call_wall=current.call_wall,
        put_wall=current.put_wall,
        vol_trigger_proxy=current.vol_trigger_proxy,
        gamma_regime=current.gamma_regime,
        zero_dte_gamma_share=round(current.zero_dte_gamma_share, 6),
        dominant_expiry=current.dominant_expiry,
        dominant_expiry_score=current.dominant_expiry_score,
        call_wall_change=_diff(current.call_wall, previous.call_wall if previous else None),
        put_wall_change=_diff(current.put_wall, previous.put_wall if previous else None),
        vol_trigger_change=_diff(
            current.vol_trigger_proxy,
            previous.vol_trigger_proxy if previous else None,
        ),
        zero_dte_gamma_share_change=_diff(
            current.zero_dte_gamma_share,
            previous.zero_dte_gamma_share if previous else None,
        ),
        dominant_expiry_changed=bool(
            previous
            and current.dominant_expiry
            and previous.dominant_expiry
            and current.dominant_expiry != previous.dominant_expiry
        ),
        gamma_regime_changed=bool(previous and current.gamma_regime != previous.gamma_regime),
        standard_metrics=current.standard_metrics,
    )


def _wall_alert(
    *,
    kind_prefix: str,
    previous_level: float | None,
    current_level: float | None,
    spot: float,
) -> ChainAlert | None:
    if previous_level is None or current_level is None or spot <= 0 or previous_level == current_level:
        return None
    delta = current_level - previous_level
    move_pct = abs(delta) / spot * 100.0
    if move_pct < WALL_SHIFT_MIN_PCT:
        return None
    direction = "up" if delta > 0 else "down"
    label = "Call wall" if kind_prefix == "call_wall" else "Put wall"
    return ChainAlert(
        kind=f"{kind_prefix}_shift_{direction}",
        severity="warning",
        message=f"{label} shifted {direction} from {previous_level:g} to {current_level:g}.",
        level=current_level,
        distance_pct=round(move_pct, 4),
        source="session_proxy",
        metadata={
            "previous_level": previous_level,
            "current_level": current_level,
            "delta": round(delta, 6),
            "move_pct_of_spot": round(move_pct, 4),
        },
    )


def _temporal_alerts(previous: ChainAnalyticsSnapshot, current: ChainAnalyticsSnapshot) -> list[ChainAlert]:
    alerts: list[ChainAlert] = []

    for alert in (
        _wall_alert(
            kind_prefix="call_wall",
            previous_level=previous.call_wall,
            current_level=current.call_wall,
            spot=current.spot,
        ),
        _wall_alert(
            kind_prefix="put_wall",
            previous_level=previous.put_wall,
            current_level=current.put_wall,
            spot=current.spot,
        ),
    ):
        if alert is not None:
            alerts.append(alert)

    prev_side = _same_side_of_spot(previous)
    curr_side = _same_side_of_spot(current)
    if prev_side is not None and curr_side is not None and prev_side != curr_side:
        alerts.append(
            ChainAlert(
                kind="zero_gamma_crossed_spot",
                severity="critical",
                message="Zero-gamma / vol-trigger proxy crossed spot versus the previous persisted snapshot.",
                level=current.vol_trigger_proxy,
                distance_pct=_distance_pct(current.vol_trigger_proxy, current.spot),
                source="session_proxy",
                metadata={
                    "previous_vol_trigger_proxy": previous.vol_trigger_proxy,
                    "current_vol_trigger_proxy": current.vol_trigger_proxy,
                    "previous_spot": previous.spot,
                    "current_spot": current.spot,
                },
            )
        )

    if previous.gamma_regime != current.gamma_regime:
        alerts.append(
            ChainAlert(
                kind="dealer_regime_flip",
                severity="critical",
                message=f"Dealer gamma regime flipped from {previous.gamma_regime} to {current.gamma_regime}.",
                source="session_proxy",
                metadata={
                    "previous_gamma_regime": previous.gamma_regime,
                    "current_gamma_regime": current.gamma_regime,
                },
            )
        )

    zero_dte_delta = current.zero_dte_gamma_share - previous.zero_dte_gamma_share
    if zero_dte_delta >= ZERO_DTE_SURGE_MIN_POINTS and current.zero_dte_gamma_share >= ZERO_DTE_SURGE_ABSOLUTE_LEVEL:
        alerts.append(
            ChainAlert(
                kind="0dte_gamma_surge",
                severity="warning",
                message=(
                    "0DTE gamma concentration surged from "
                    f"{previous.zero_dte_gamma_share:.2f}% to {current.zero_dte_gamma_share:.2f}%."
                ),
                level=round(current.zero_dte_gamma_share, 4),
                source="session_proxy",
                metadata={
                    "previous_zero_dte_gamma_share": round(previous.zero_dte_gamma_share, 4),
                    "current_zero_dte_gamma_share": round(current.zero_dte_gamma_share, 4),
                    "delta_points": round(zero_dte_delta, 4),
                },
            )
        )

    if (
        previous.dominant_expiry
        and current.dominant_expiry
        and previous.dominant_expiry != current.dominant_expiry
    ):
        alerts.append(
            ChainAlert(
                kind="dominant_expiry_rotation",
                severity="warning",
                message=(
                    "Dominant expiry rotated from "
                    f"{previous.dominant_expiry} to {current.dominant_expiry}."
                ),
                source="session_proxy",
                metadata={
                    "previous_dominant_expiry": previous.dominant_expiry,
                    "current_dominant_expiry": current.dominant_expiry,
                    "previous_dominance_score": previous.dominant_expiry_score,
                    "current_dominance_score": current.dominant_expiry_score,
                },
            )
        )

    return alerts


class OptionsChainAnalyticsHistoryStore:
    """SQLite-backed snapshot history for chain analytics temporal alerts."""

    def __init__(self, db_path: str | Path = DEFAULT_HISTORY_DB) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS options_chain_analytics_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                expiry_scope TEXT NOT NULL,
                as_of TEXT NOT NULL,
                spot REAL NOT NULL,
                call_wall REAL,
                put_wall REAL,
                vol_trigger_proxy REAL,
                gamma_regime TEXT NOT NULL,
                zero_dte_gamma_share REAL NOT NULL,
                dominant_expiry TEXT,
                dominant_expiry_score REAL,
                standard_metrics_json TEXT NOT NULL DEFAULT '{}',
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(options_chain_analytics_snapshots)").fetchall()}
        if "standard_metrics_json" not in columns:
            conn.execute("ALTER TABLE options_chain_analytics_snapshots ADD COLUMN standard_metrics_json TEXT NOT NULL DEFAULT '{}'")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_options_chain_analytics_history_scope
            ON options_chain_analytics_snapshots(symbol, expiry_scope, as_of DESC, id DESC)
            """
        )
        return conn

    def latest_snapshot(self, symbol: str, expiry_scope: str | None) -> ChainAnalyticsSnapshot | None:
        scope = expiry_scope or "__ALL__"
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT symbol, expiry_scope, as_of, spot, call_wall, put_wall,
                       vol_trigger_proxy, gamma_regime, zero_dte_gamma_share,
                       dominant_expiry, dominant_expiry_score, standard_metrics_json
                FROM options_chain_analytics_snapshots
                WHERE symbol = ? AND expiry_scope = ?
                ORDER BY as_of DESC, id DESC
                LIMIT 1
                """,
                (symbol.upper(), scope),
            ).fetchone()
        if row is None:
            return None
        return ChainAnalyticsSnapshot(
            symbol=str(row[0]),
            expiry_scope=str(row[1]),
            as_of=str(row[2]),
            spot=float(row[3]),
            call_wall=_finite(row[4]),
            put_wall=_finite(row[5]),
            vol_trigger_proxy=_finite(row[6]),
            gamma_regime=str(row[7]),
            zero_dte_gamma_share=float(row[8]),
            dominant_expiry=str(row[9]) if row[9] is not None else None,
            dominant_expiry_score=_finite(row[10]),
            standard_metrics=_parse_standard_metrics(row[11]),
        )

    def recent_snapshots(
        self,
        symbol: str,
        expiry_scope: str | None,
        limit: int = 20,
    ) -> list[ChainAnalyticsSnapshot]:
        scope = expiry_scope or "__ALL__"
        clamped_limit = max(1, min(int(limit), 250))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, expiry_scope, as_of, spot, call_wall, put_wall,
                       vol_trigger_proxy, gamma_regime, zero_dte_gamma_share,
                       dominant_expiry, dominant_expiry_score, standard_metrics_json
                FROM options_chain_analytics_snapshots
                WHERE symbol = ? AND expiry_scope = ?
                ORDER BY as_of DESC, id DESC
                LIMIT ?
                """,
                (symbol.upper(), scope, clamped_limit + 1),
            ).fetchall()
        return [
            ChainAnalyticsSnapshot(
                symbol=str(row[0]),
                expiry_scope=str(row[1]),
                as_of=str(row[2]),
                spot=float(row[3]),
                call_wall=_finite(row[4]),
                put_wall=_finite(row[5]),
                vol_trigger_proxy=_finite(row[6]),
                gamma_regime=str(row[7]),
                zero_dte_gamma_share=float(row[8]),
                dominant_expiry=str(row[9]) if row[9] is not None else None,
                dominant_expiry_score=_finite(row[10]),
                standard_metrics=_parse_standard_metrics(row[11]),
            )
            for row in rows
        ]

    def history_response(
        self,
        symbol: str,
        expiry: str | None = None,
        limit: int = 20,
    ) -> ChainAnalyticsHistoryResponse:
        scope = expiry or "__ALL__"
        snapshots = self.recent_snapshots(symbol, scope, limit=limit)
        points: list[ChainAnalyticsHistoryPoint] = []
        for idx, snapshot in enumerate(snapshots[:limit]):
            previous = snapshots[idx + 1] if idx + 1 < len(snapshots) else None
            points.append(_history_point(snapshot, previous))
        return ChainAnalyticsHistoryResponse(
            ticker=symbol.upper(),
            expiry_scope=scope,
            points=points,
        )

    def save_snapshot(
        self,
        snapshot: ChainAnalyticsSnapshot,
        response: ChainInstitutionalAnalyticsResponse,
    ) -> None:
        if hasattr(response, "model_dump_json"):
            payload = response.model_dump_json()
        else:
            payload = json.dumps(response.dict())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO options_chain_analytics_snapshots (
                    symbol, expiry_scope, as_of, spot, call_wall, put_wall,
                    vol_trigger_proxy, gamma_regime, zero_dte_gamma_share,
                    dominant_expiry, dominant_expiry_score, standard_metrics_json, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.symbol,
                    snapshot.expiry_scope,
                    snapshot.as_of,
                    snapshot.spot,
                    snapshot.call_wall,
                    snapshot.put_wall,
                    snapshot.vol_trigger_proxy,
                    snapshot.gamma_regime,
                    snapshot.zero_dte_gamma_share,
                    snapshot.dominant_expiry,
                    snapshot.dominant_expiry_score,
                    json.dumps(snapshot.standard_metrics, sort_keys=True),
                    payload,
                ),
            )

    def enrich_and_save(
        self,
        response: ChainInstitutionalAnalyticsResponse,
        expiry: str | None = None,
    ) -> ChainInstitutionalAnalyticsResponse:
        current = _snapshot_from_response(response, expiry)
        previous = self.latest_snapshot(current.symbol, current.expiry_scope)
        if previous is not None:
            response.alerts.extend(_temporal_alerts(previous, current))
        self.save_snapshot(current, response)
        return response


def enrich_chain_analytics_with_history(
    response: ChainInstitutionalAnalyticsResponse,
    expiry: str | None = None,
    db_path: str | Path = DEFAULT_HISTORY_DB,
) -> ChainInstitutionalAnalyticsResponse:
    """Persist the response and append temporal alerts against the prior snapshot."""

    if not response.ok or not response.chain:
        return response
    return OptionsChainAnalyticsHistoryStore(db_path).enrich_and_save(response, expiry=expiry)
