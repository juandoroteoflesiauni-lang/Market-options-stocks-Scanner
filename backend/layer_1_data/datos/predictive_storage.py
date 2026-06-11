"""
backend/layer_1_data/datos/predictive_storage.py
════════════════════════════════════════════════════════════════════════════════
Persistence layer for Probabilistic and Predictive analysis results.
════════════════════════════════════════════════════════════════════════════════
"""

import json
from datetime import datetime
from functools import lru_cache

from .db_manager import DuckDBManager

try:
    from config.logger_setup import get_logger
except ModuleNotFoundError:
    from backend.config.logger_setup import get_logger

logger = get_logger(__name__)


class PredictiveStorage:
    """Handles persistence for probabilistic and predictive analyses."""

    def __init__(self):
        self.db = DuckDBManager()
        self._init_tables()

    def _init_tables(self):
        """Initializes the probabilistic_analyses table."""
        query = """
        CREATE TABLE IF NOT EXISTS probabilistic_analyses (
            id VARCHAR PRIMARY KEY,
            symbol VARCHAR,
            timestamp TIMESTAMP,
            pr_ordered FLOAT,
            trend_strength FLOAT,
            var_99 FLOAT,
            cvar_99 FLOAT,
            jump_prob FLOAT,
            vov FLOAT,
            etv FLOAT,
            kelly_full FLOAT,
            is_ordered_gate BOOLEAN,
            is_jump_gate BOOLEAN,
            gate_veto BOOLEAN,
            gex_gating_safe BOOLEAN,
            dealer_bias VARCHAR,
            is_local_ar BOOLEAN,
            vix FLOAT,
            us10y FLOAT,
            pc_flow_ratio FLOAT,
            squeeze_state VARCHAR,
            squeeze_cooling_count INTEGER,
            squeeze_ignition_price FLOAT,
            raw_json JSON
        );
        CREATE TABLE IF NOT EXISTS option_oi_snapshots (
            symbol VARCHAR,
            strike FLOAT,
            option_type VARCHAR,
            expiration VARCHAR,
            open_interest INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_prob_symbol_ts ON probabilistic_analyses (symbol, timestamp);
        CREATE INDEX IF NOT EXISTS idx_oi_sym_ts ON option_oi_snapshots (symbol, timestamp);
        """
        try:
            with self.db as db:
                conn = db._connection
                conn.execute(query)
                self._migrate_probabilistic_columns(conn)
            logger.info("PredictiveStorage: Tables initialized.")
        except Exception as e:
            logger.error(f"PredictiveStorage: Failed to initialize tables: {e}")

    @staticmethod
    def _migrate_probabilistic_columns(conn) -> None:
        """Add columns missing from older DB files (CREATE TABLE IF NOT EXISTS does not alter)."""
        try:
            rows = conn.execute("PRAGMA table_info('probabilistic_analyses')").fetchall()
            existing = {r[1] for r in rows}  # name is column 1
        except Exception as e:
            logger.warning(f"PredictiveStorage: Could not read probabilistic_analyses schema: {e}")
            return

        alters: list[tuple[str, str]] = [
            ("pc_flow_ratio", "FLOAT"),
            ("squeeze_state", "VARCHAR"),
            ("squeeze_cooling_count", "INTEGER"),
            ("squeeze_ignition_price", "FLOAT"),
        ]
        for col, typ in alters:
            if col not in existing:
                try:
                    conn.execute(f"ALTER TABLE probabilistic_analyses ADD COLUMN {col} {typ}")
                    logger.info(f"PredictiveStorage: Added column probabilistic_analyses.{col}")
                except Exception as e:
                    logger.error(f"PredictiveStorage: Failed to add column {col}: {e}")

    def save_analysis(self, result: object, extra_metadata: dict | None = None) -> None:
        """Saves a probabilistic analysis result to DuckDB.

        Accepts any object with ``model_dump()`` (e.g. Pydantic ``ProbabilisticResult``)
        or a plain mapping — avoids Layer 1 importing Layer 3 domain types.
        """
        import uuid

        if hasattr(result, "model_dump"):
            rd = result.model_dump()
        elif isinstance(result, dict):
            rd = dict(result)
        else:
            logger.error("PredictiveStorage: save_analysis requires model_dump-capable result")
            return

        analysis_id = str(uuid.uuid4())
        ts = datetime.now()

        ticker = str(rd.get("ticker", "") or "")
        state = rd.get("state") or {}
        tail = rd.get("tail") or {}
        jump = rd.get("jump") or {}
        delta_flow = rd.get("delta_flow") or {}

        # Serialize full result for the raw_json column
        if extra_metadata:
            rd = {**rd, **extra_metadata}
        raw_json = json.dumps(rd, default=str)

        query = """
        INSERT INTO probabilistic_analyses (
            id, symbol, timestamp, pr_ordered, trend_strength,
            var_99, cvar_99, jump_prob, vov, etv,
            kelly_full, is_ordered_gate, is_jump_gate, gate_veto,
            gex_gating_safe, dealer_bias, is_local_ar, vix, us10y, pc_flow_ratio,
            squeeze_state, squeeze_cooling_count, squeeze_ignition_price, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            analysis_id,
            ticker,
            ts,
            state.get("pr_ordered"),
            state.get("trend_strength"),
            tail.get("var_99"),
            tail.get("cvar_99"),
            jump.get("probability"),
            rd.get("vov"),
            rd.get("etv"),
            rd.get("kelly_prob"),
            rd.get("is_ordered_gate"),
            rd.get("is_jump_gate"),
            rd.get("gate_veto"),
            rd.get("gex_gating_safe"),
            rd.get("dealer_bias"),
            rd.get("is_local_ar"),
            rd.get("vix"),
            rd.get("us10y"),
            delta_flow.get("pc_flow_ratio") if isinstance(delta_flow, dict) else None,
            extra_metadata.get("squeeze_state") if extra_metadata else None,
            extra_metadata.get("squeeze_cooling_count") if extra_metadata else None,
            extra_metadata.get("squeeze_ignition_price") if extra_metadata else None,
            raw_json,
        )

        try:
            with self.db as db:
                db._connection.execute(query, params)
            logger.info("PredictiveStorage: Saved analysis for %s", ticker)
        except Exception as e:
            logger.error("PredictiveStorage: Failed to save analysis for %s: %s", ticker, e)

    @lru_cache(maxsize=32)
    def get_history(self, symbol: str, limit: int = 50) -> list[dict]:
        """Retrieves historical analyses for a symbol with caching."""
        query = """
        SELECT * FROM probabilistic_analyses
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """
        try:
            with self.db as db:
                cursor = db._connection.execute(query, (symbol.upper(), limit))
                columns = [desc[0] for desc in cursor.description]
                results = []
                for row in cursor.fetchall():
                    results.append(dict(zip(columns, row, strict=False)))
                return results
        except Exception as e:
            logger.error(f"PredictiveStorage: Failed to fetch history for {symbol}: {e}")
            return []

    @lru_cache(maxsize=32)
    def get_recent_pc_ratios(self, symbol: str, limit: int = 20) -> list[float]:
        """Retrieves recent PC Flow Ratios for a symbol with caching."""
        query = """
        SELECT pc_flow_ratio FROM probabilistic_analyses
        WHERE symbol = ? AND pc_flow_ratio IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT ?
        """
        try:
            with self.db as db:
                rows = db._connection.execute(query, (symbol.upper(), limit)).fetchall()
                # Reverse to get chronological order (oldest first) for the engine deque
                return [float(r[0]) for r in reversed(rows)]
        except Exception as e:
            logger.error(f"PredictiveStorage: Failed to fetch PC ratios: {e}")
            return []

    @lru_cache(maxsize=32)
    def get_last_squeeze_state(self, symbol: str) -> dict | None:
        """Retrieves the last known squeeze state for a symbol with caching."""
        query = """
        SELECT squeeze_state, squeeze_cooling_count, squeeze_ignition_price
        FROM probabilistic_analyses
        WHERE symbol = ? AND squeeze_state IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 1
        """
        try:
            with self.db as db:
                row = db._connection.execute(query, (symbol.upper(),)).fetchone()
                if row:
                    return {"state": row[0], "cooling_count": row[1], "ignition_price": row[2]}
        except Exception as e:
            logger.error(f"PredictiveStorage: Failed to fetch squeeze state: {e}")
        return None

    def save_option_oi_snapshot(self, symbol: str, chain: list[dict]):
        """Saves current OI for all strikes in the chain."""
        query = """
        INSERT INTO option_oi_snapshots (symbol, strike, option_type, expiration, open_interest)
        VALUES (?, ?, ?, ?, ?)
        """
        try:
            with self.db as db:
                rows = [
                    (
                        symbol.upper(),
                        c["strike"],
                        c["option_type"],
                        c["expiration"],
                        c["open_interest"],
                    )
                    for c in chain
                ]
                db._connection.executemany(query, rows)
        except Exception as e:
            logger.error(f"PredictiveStorage: Failed to save OI snapshot: {e}")

    @lru_cache(maxsize=32)
    def get_last_oi_snapshot(self, symbol: str) -> dict[tuple, int]:
        """Returns a mapping of (strike, type, expiry) -> open_interest from the last snapshot with caching."""
        query = """
        WITH latest_ts AS (
            SELECT MAX(timestamp) as ts FROM option_oi_snapshots WHERE symbol = ?
        )
        SELECT strike, option_type, expiration, open_interest
        FROM option_oi_snapshots
        WHERE symbol = ? AND timestamp = (SELECT ts FROM latest_ts)
        """
        try:
            with self.db as db:
                rows = db._connection.execute(query, (symbol.upper(), symbol.upper())).fetchall()
                return {(r[0], r[1], r[2]): r[3] for r in rows}
        except Exception as e:
            logger.error(f"PredictiveStorage: Failed to fetch last OI snapshot: {e}")
        return {}
