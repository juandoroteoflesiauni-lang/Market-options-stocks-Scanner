from __future__ import annotations
from typing import Any
"""prediction_logger.py
========================
Audit-grade logger for every meta-signal prediction emitted by the system.

Two purposes:
  1. Forensic audit trail of past decisions (who/when/why).
  2. Source-of-truth dataset for retraining the meta-learner once forward
     outcomes are observed.

Storage
-------
SQLite (no external deps). Two tables joined on prediction_id:
  predictions  — immutable record at emit time
  outcomes     — mutable, populated once forward returns are observed

Public API
----------
- PredictionLog                                   dataclass
- PredictionLogger                                class
- schedule_outcome_updates(logger, provider, delay_days)
"""


import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# PredictionLog dataclass
# ---------------------------------------------------------------------------


@dataclass
class PredictionLog:
    """Single audit record for one meta-signal prediction."""

    prediction_id: str
    symbol: str
    timestamp: str  # ISO-8601 UTC
    direction: str
    signal: float
    confidence: float
    p_up: float
    p_down: float
    p_neutral: float
    conviction_level: str
    should_trade: bool
    position_size_pct: float
    regime: str
    conflict_score: float
    motor_signals: dict[str, float] = field(default_factory=dict)
    shap_attribution: dict[str, Any] | None = None
    filter_reason: str | None = None
    meta_learner_used: bool = False

    # Populated later by update_outcome()
    outcome_return_1d: float | None = None
    outcome_return_5d: float | None = None
    outcome_direction_correct: bool | None = None
    outcome_logged_at: str | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path("data") / "predictions.sqlite"
_RETURN_DIRECTION_THRESHOLD = 0.005  # ±0.5 % matches create_targets()

_PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id     TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    direction         TEXT NOT NULL,
    signal            REAL NOT NULL,
    confidence        REAL NOT NULL,
    p_up              REAL NOT NULL,
    p_down            REAL NOT NULL,
    p_neutral         REAL NOT NULL,
    conviction_level  TEXT NOT NULL,
    should_trade      INTEGER NOT NULL,
    position_size_pct REAL NOT NULL,
    regime            TEXT NOT NULL,
    conflict_score    REAL NOT NULL,
    motor_signals     TEXT NOT NULL,        -- JSON
    shap_attribution  TEXT,                 -- JSON or NULL
    filter_reason     TEXT,
    meta_learner_used INTEGER NOT NULL,
    price_t0          REAL
);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_ts ON predictions(symbol, timestamp);
"""

_OUTCOMES_DDL = """
CREATE TABLE IF NOT EXISTS outcomes (
    prediction_id              TEXT PRIMARY KEY,
    outcome_return_1d          REAL,
    outcome_return_5d          REAL,
    outcome_direction_correct  INTEGER,
    outcome_logged_at          TEXT,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id)
);
"""


def _json_object_has_values(raw: object) -> bool:
    if raw is None:
        return False
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    return isinstance(value, dict) and bool(value)


# ---------------------------------------------------------------------------
# PredictionLogger
# ---------------------------------------------------------------------------


class PredictionLogger:
    """
    SQLite-backed predictions + outcomes log.

    Threadsafe via per-call connections + a module-level lock. Connections are
    cheap; we open/close per operation to avoid cross-thread cursor sharing.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else _DEFAULT_DB_PATH
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ── Schema ──────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_PREDICTIONS_DDL)
            conn.executescript(_OUTCOMES_DDL)
            cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()
            }
            if "price_t0" not in cols:
                conn.execute("ALTER TABLE predictions ADD COLUMN price_t0 REAL")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Public API ──────────────────────────────────────────────────────────

    def log_prediction(self, final_signal: dict[str, Any]) -> str:
        """
        Persist a meta-signal prediction. Returns the generated prediction_id.

        `final_signal` is the dict returned by compose_final_signal() — keys
        align with PredictionLog. Missing fields default sensibly so callers
        can pass partial dicts during early integration.
        """
        if not isinstance(final_signal, dict):
            raise TypeError("final_signal must be a dict")

        pid = str(uuid.uuid4())
        ts = str(final_signal.get("timestamp") or datetime.now().isoformat())

        price_t0_raw = final_signal.get("price_t0")
        price_t0_val = (
            float(price_t0_raw) if price_t0_raw is not None and float(price_t0_raw) > 0 else None
        )

        record = (
            pid,
            str(final_signal.get("symbol", "UNKNOWN")).upper(),
            ts,
            str(final_signal.get("direction", "NEUTRAL")),
            float(final_signal.get("signal", 0.0)),
            float(final_signal.get("confidence", 0.0)),
            float(final_signal.get("p_up", 0.0)),
            float(final_signal.get("p_down", 0.0)),
            float(final_signal.get("p_neutral", 0.0)),
            str(final_signal.get("conviction_level", "INSUFFICIENT")),
            int(bool(final_signal.get("should_trade", False))),
            float(final_signal.get("position_size_pct", 0.0)),
            str(final_signal.get("regime", "UNKNOWN")),
            float(final_signal.get("conflict_score", 0.0)),
            json.dumps(
                final_signal.get("motor_signals") or final_signal.get("component_signals") or {}
            ),
            (
                json.dumps(final_signal["shap_attribution"])
                if final_signal.get("shap_attribution") is not None
                else None
            ),
            final_signal.get("filter_reason"),
            int(bool(final_signal.get("meta_learner_used", False))),
            price_t0_val,
        )

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO predictions(
                    prediction_id, symbol, timestamp, direction, signal,
                    confidence, p_up, p_down, p_neutral, conviction_level,
                    should_trade, position_size_pct, regime, conflict_score,
                    motor_signals, shap_attribution, filter_reason,
                    meta_learner_used, price_t0
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                record,
            )
            conn.commit()

        return pid

    def update_outcome(
        self,
        prediction_id: str,
        price_t0: float,
        price_tN: float,
        N_days: int,
    ) -> None:
        """
        Compute realised return and direction-correctness, then persist into
        the outcomes table. N_days ∈ {1, 5} updates the matching column;
        other horizons are accepted but only stored when N_days==1 or 5.
        """
        if N_days <= 0:
            raise ValueError("N_days must be > 0")
        if price_t0 <= 0:
            raise ValueError("price_t0 must be > 0")

        ret = (price_tN - price_t0) / price_t0

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT direction FROM predictions WHERE prediction_id=?",
                (prediction_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown prediction_id: {prediction_id}")

            direction = row["direction"]
            # Direction correctness uses the same threshold as the targets.
            realised_dir = (
                "UP"
                if ret > _RETURN_DIRECTION_THRESHOLD
                else "DOWN" if ret < -_RETURN_DIRECTION_THRESHOLD else "NEUTRAL"
            )
            correct = realised_dir == direction

            existing = conn.execute(
                "SELECT * FROM outcomes WHERE prediction_id=?",
                (prediction_id,),
            ).fetchone()

            ret_1d = existing["outcome_return_1d"] if existing else None
            ret_5d = existing["outcome_return_5d"] if existing else None
            if N_days == 1:
                ret_1d = float(ret)
            elif N_days == 5:
                ret_5d = float(ret)

            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO outcomes(prediction_id, outcome_return_1d,
                                     outcome_return_5d, outcome_direction_correct,
                                     outcome_logged_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(prediction_id) DO UPDATE SET
                    outcome_return_1d         = excluded.outcome_return_1d,
                    outcome_return_5d         = excluded.outcome_return_5d,
                    outcome_direction_correct = excluded.outcome_direction_correct,
                    outcome_logged_at         = excluded.outcome_logged_at
                """,
                (prediction_id, ret_1d, ret_5d, int(correct), now),
            )
            conn.commit()

    def schedule_outcome_updates(
        self,
        symbol: str,
        current_price: float,
        n_days: int,
    ) -> dict[str, Any]:
        """
        Backfill outcomes for predictions of `symbol` whose timestamp is
        between `n_days - 0.5` and `n_days + 1.5` days old (centered window),
        and that don't yet have outcome_return_{n_days}d populated.

        Computes ret = (current_price / price_t0) - 1 and direction-correctness
        using the same threshold as create_targets(). Requires price_t0 to
        have been stored at log_prediction() time; rows missing it are
        skipped.

        Returns {processed, updated, errors, missing_price_t0}.
        """
        if n_days not in (1, 5):
            # Only 1d / 5d columns exist in schema; 10d-style horizons are
            # tracked elsewhere. Silently no-op so callers iterating
            # FORWARD_DAYS = [1,5,10] don't crash.
            return {"processed": 0, "updated": 0, "errors": 0, "missing_price_t0": 0}
        if current_price <= 0:
            raise ValueError("current_price must be > 0")

        # SQLite has no proper interval. Use ISO timestamp string compare with
        # explicit cutoff datetimes — predictions are stored as datetime.now()
        # ISO strings, so lexical compare matches chronological order.
        now = datetime.now()
        upper = (now - timedelta(days=int(n_days) - 1)).isoformat()  # at least n-1 days old
        lower = (now - timedelta(days=int(n_days) + 2)).isoformat()  # at most n+2 days old

        target_col = f"outcome_return_{n_days}d"

        processed = 0
        updated = 0
        errors = 0
        missing_price_t0 = 0

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.prediction_id, p.direction, p.price_t0
                FROM predictions p
                LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
                WHERE p.symbol = ?
                  AND p.timestamp <= ?
                  AND p.timestamp >= ?
                  AND (o.{target_col} IS NULL OR o.prediction_id IS NULL)
                """,
                (symbol.upper(), upper, lower),
            ).fetchall()

        for r in rows:
            processed += 1
            price_t0 = r["price_t0"]
            if price_t0 is None or float(price_t0) <= 0:
                missing_price_t0 += 1
                continue
            try:
                self.update_outcome(
                    r["prediction_id"],
                    float(price_t0),
                    float(current_price),
                    N_days=int(n_days),
                )
                updated += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "schedule_outcome_updates failed for %s/%s: %s",
                    symbol,
                    r["prediction_id"],
                    exc,
                )

        return {
            "processed": processed,
            "updated": updated,
            "errors": errors,
            "missing_price_t0": missing_price_t0,
        }

    def get_prediction(self, prediction_id: str) -> PredictionLog | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.*,
                       o.outcome_return_1d,
                       o.outcome_return_5d,
                       o.outcome_direction_correct,
                       o.outcome_logged_at
                FROM predictions p
                LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
                WHERE p.prediction_id = ?
                """,
                (prediction_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_log(row)

    def get_predictions_for_retraining(
        self,
        symbol: str,
        min_age_days: int = 5,
    ) -> pd.DataFrame:
        """
        Return predictions with COMPLETE outcomes (both 1d and 5d) and that
        are at least `min_age_days` days old. Output is ready to feed
        EnsembleMetaLearner.fit().
        """
        cutoff = (datetime.now() - timedelta(days=int(min_age_days))).isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       o.outcome_return_1d,
                       o.outcome_return_5d,
                       o.outcome_direction_correct,
                       o.outcome_logged_at
                FROM predictions p
                INNER JOIN outcomes o ON p.prediction_id = o.prediction_id
                WHERE p.symbol = ?
                  AND p.timestamp <= ?
                  AND o.outcome_return_1d IS NOT NULL
                  AND o.outcome_return_5d IS NOT NULL
                  AND o.outcome_direction_correct IS NOT NULL
                ORDER BY p.timestamp ASC
                """,
                (symbol.upper(), cutoff),
            ).fetchall()

        if not rows:
            return pd.DataFrame()

        records = [dict(r) for r in rows]
        # Decode JSON columns
        for rec in records:
            try:
                rec["motor_signals"] = (
                    json.loads(rec["motor_signals"]) if rec["motor_signals"] else {}
                )
            except (TypeError, ValueError):
                rec["motor_signals"] = {}
            try:
                rec["shap_attribution"] = (
                    json.loads(rec["shap_attribution"]) if rec["shap_attribution"] else None
                )
            except (TypeError, ValueError):
                rec["shap_attribution"] = None
            rec["should_trade"] = bool(rec["should_trade"])
            rec["meta_learner_used"] = bool(rec["meta_learner_used"])
            rec["outcome_direction_correct"] = bool(rec["outcome_direction_correct"])
        return pd.DataFrame(records)

    def count_predictions(self, symbol: str | None = None) -> dict[str, int]:
        """Conteo rapido sin requerir outcomes. Para monitoreo en vivo."""
        with self._lock, self._connect() as conn:
            if symbol:
                sym = symbol.upper()
                total = conn.execute(
                    "SELECT COUNT(*) FROM predictions WHERE symbol = ?", (sym,)
                ).fetchone()[0]
                with_outcome = conn.execute(
                    "SELECT COUNT(*) FROM predictions p "
                    "JOIN outcomes o ON p.prediction_id = o.prediction_id "
                    "WHERE p.symbol = ?",
                    (sym,),
                ).fetchone()[0]
            else:
                total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
                with_outcome = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        return {
            "total": int(total),
            "with_outcome": int(with_outcome),
            "without_outcome": int(total - with_outcome),
        }

    def get_training_readiness(
        self,
        symbol: str,
        *,
        min_samples: int = 300,
        primary_horizon_days: int = 5,
    ) -> dict[str, Any]:
        """Audit readiness for backfill/meta-learner training on logged predictions."""
        if primary_horizon_days != 5:
            raise ValueError("PredictionLogger currently supports primary_horizon_days=5")
        sym = symbol.upper().strip()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.price_t0, p.motor_signals, p.shap_attribution,
                       o.outcome_return_1d, o.outcome_return_5d,
                       o.outcome_direction_correct
                FROM predictions p
                LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
                WHERE p.symbol = ?
                """,
                (sym,),
            ).fetchall()

        total = len(rows)
        complete_1d = sum(1 for r in rows if r["outcome_return_1d"] is not None)
        complete_5d = sum(1 for r in rows if r["outcome_return_5d"] is not None)
        missing_price_t0 = sum(
            1 for r in rows if r["price_t0"] is None or float(r["price_t0"] or 0) <= 0
        )
        with_motor_signals = sum(1 for r in rows if _json_object_has_values(r["motor_signals"]))
        with_shap = sum(1 for r in rows if _json_object_has_values(r["shap_attribution"]))
        reasons: list[str] = []
        if complete_5d < min_samples:
            reasons.append("minimum_samples")
        if missing_price_t0:
            reasons.append("missing_price_t0")
        status = "approved" if not reasons else "blocked"
        return {
            "symbol": sym,
            "primary_horizon_days": primary_horizon_days,
            "total_predictions": total,
            "complete_1d_outcomes": complete_1d,
            "complete_5d_outcomes": complete_5d,
            "missing_price_t0": missing_price_t0,
            "with_motor_signals": with_motor_signals,
            "with_shap_attribution": with_shap,
            "model_gate": {
                "status": status,
                "minimum_samples": int(min_samples),
                "reasons": reasons,
            },
        }

    def get_performance_stats(
        self,
        symbol: str,
        last_N_days: int = 30,
    ) -> dict[str, Any]:
        """
        Aggregate stats over the last `last_N_days` for `symbol`:
          accuracy_by_direction, accuracy_by_conviction, hypothetical_sharpe,
          n_predictions, n_with_outcomes.
        """
        cutoff = (datetime.now() - timedelta(days=int(last_N_days))).isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.direction, p.conviction_level, p.should_trade,
                       p.position_size_pct,
                       o.outcome_return_5d, o.outcome_direction_correct
                FROM predictions p
                LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
                WHERE p.symbol = ? AND p.timestamp >= ?
                """,
                (symbol.upper(), cutoff),
            ).fetchall()

        n_total = len(rows)
        with_outcomes = [r for r in rows if r["outcome_direction_correct"] is not None]
        n_with_outcomes = len(with_outcomes)

        def _accuracy(records: list) -> float:
            if not records:
                return float("nan")
            return float(
                sum(int(bool(r["outcome_direction_correct"])) for r in records) / len(records)
            )

        # By direction
        dir_groups: dict[str, list] = {}
        for r in with_outcomes:
            dir_groups.setdefault(r["direction"], []).append(r)
        accuracy_by_direction = {d: _accuracy(rs) for d, rs in dir_groups.items()}

        # By conviction
        conv_groups: dict[str, list] = {}
        for r in with_outcomes:
            conv_groups.setdefault(r["conviction_level"], []).append(r)
        accuracy_by_conviction = {c: _accuracy(rs) for c, rs in conv_groups.items()}

        # Hypothetical Sharpe of trading the predicted direction at predicted size
        pnls: list[float] = []
        for r in with_outcomes:
            if not r["should_trade"]:
                continue
            ret = r["outcome_return_5d"] or 0.0
            sized = float(r["position_size_pct"]) * float(ret)
            if r["direction"] == "DOWN":
                sized = -sized
            pnls.append(sized)
        if len(pnls) > 1:
            arr = np.asarray(pnls)
            std = float(arr.std(ddof=1))
            sharpe = float(arr.mean() / std * np.sqrt(252)) if std > 0 else 0.0
        else:
            sharpe = float("nan")

        return {
            "symbol": symbol.upper(),
            "n_predictions": n_total,
            "n_with_outcomes": n_with_outcomes,
            "accuracy_overall": _accuracy(with_outcomes),
            "accuracy_by_direction": accuracy_by_direction,
            "accuracy_by_conviction": accuracy_by_conviction,
            "hypothetical_sharpe": sharpe,
        }

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_log(row: sqlite3.Row) -> PredictionLog:
        try:
            motors = json.loads(row["motor_signals"]) if row["motor_signals"] else {}
        except (TypeError, ValueError):
            motors = {}
        try:
            shap = json.loads(row["shap_attribution"]) if row["shap_attribution"] else None
        except (TypeError, ValueError):
            shap = None
        return PredictionLog(
            prediction_id=row["prediction_id"],
            symbol=row["symbol"],
            timestamp=row["timestamp"],
            direction=row["direction"],
            signal=float(row["signal"]),
            confidence=float(row["confidence"]),
            p_up=float(row["p_up"]),
            p_down=float(row["p_down"]),
            p_neutral=float(row["p_neutral"]),
            conviction_level=row["conviction_level"],
            should_trade=bool(row["should_trade"]),
            position_size_pct=float(row["position_size_pct"]),
            regime=row["regime"],
            conflict_score=float(row["conflict_score"]),
            motor_signals=motors,
            shap_attribution=shap,
            filter_reason=row["filter_reason"],
            meta_learner_used=bool(row["meta_learner_used"]),
            outcome_return_1d=(
                row["outcome_return_1d"] if "outcome_return_1d" in row.keys() else None
            ),
            outcome_return_5d=(
                row["outcome_return_5d"] if "outcome_return_5d" in row.keys() else None
            ),
            outcome_direction_correct=(
                bool(row["outcome_direction_correct"])
                if row["outcome_direction_correct"] is not None
                else None
            ),
            outcome_logged_at=(
                row["outcome_logged_at"] if "outcome_logged_at" in row.keys() else None
            ),
        )


# ---------------------------------------------------------------------------
# Scheduled outcome backfiller
# ---------------------------------------------------------------------------


def schedule_outcome_updates(
    logger_obj: PredictionLogger,
    data_provider: Callable[[str, str], float],
    delay_days: list[int] | tuple[int, ...] = (1, 5, 10),
) -> dict[str, Any]:
    """
    Sweep over predictions older than each delay_day in `delay_days` whose
    outcome row is incomplete, and call `data_provider(symbol, timestamp_iso)`
    to fetch the realised price at t+N. Update the outcome row.

    `data_provider` is a synchronous callable: (symbol, iso_ts) -> price.
    Designed to be invoked by an external scheduler (cron / APScheduler /
    Celery beat); this function performs one sweep and returns counters.

    Returns
    ───────
    {processed: int, updated: int, errors: int}
    """
    now = datetime.now()
    processed = 0
    updated = 0
    errors = 0

    for n_days in delay_days:
        if n_days not in (1, 5):
            # Only 1d and 5d columns exist; skip other horizons silently.
            continue
        cutoff = (now - timedelta(days=int(n_days))).isoformat()
        col = f"outcome_return_{n_days}d"

        with logger_obj._lock, logger_obj._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT p.prediction_id, p.symbol, p.timestamp
                FROM predictions p
                LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
                WHERE p.timestamp <= ?
                  AND (o.{col} IS NULL OR o.prediction_id IS NULL)
                """,
                (cutoff,),
            ).fetchall()

        for r in rows:
            processed += 1
            try:
                t0_iso = r["timestamp"]
                tN_iso = (datetime.fromisoformat(t0_iso) + timedelta(days=int(n_days))).isoformat()
                price_t0 = float(data_provider(r["symbol"], t0_iso))
                price_tN = float(data_provider(r["symbol"], tN_iso))
                logger_obj.update_outcome(r["prediction_id"], price_t0, price_tN, N_days=n_days)
                updated += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "schedule_outcome_updates failed for %s/%s: %s",
                    r["symbol"],
                    r["prediction_id"],
                    exc,
                )

    return {"processed": processed, "updated": updated, "errors": errors}
