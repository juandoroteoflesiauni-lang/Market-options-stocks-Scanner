from __future__ import annotations
# ruff: noqa: F403, F405

import logging
import time
import json
import duckdb
from datetime import datetime, UTC

logger = logging.getLogger(__name__)
from backend.services.research.research_types import *
from backend.services.research.research_types import (
    _DUCKDB_PATH,
    _safe_float,
    _unavailable_desk_status,
)


async def _fetch_predictive_desk(
    underlying_symbol: str,
    market_type: str,
) -> PredictiveDeskState:
    """Read the latest predictive/probabilistic snapshot from ``quantum_analyzer.duckdb``.

    Connects to the DuckDB database in **read-only** mode so it never blocks or
    corrupts the write path. All failures are caught and the desk degrades to
    ``unavailable``.
    """
    source_tag = "probabilistic_analyses_db"

    # Limit to equities since indices map to ETFs and crypto has its own setup
    if market_type not in ("stock_perp", "stock_index_perp"):
        return PredictiveDeskState(
            desk_status=_unavailable_desk_status(
                source=source_tag,
                reason=REASON_MARKET_TYPE_EXCLUDED,
            ),
            reason_codes=[REASON_MARKET_TYPE_EXCLUDED],
        )

    t0 = time.monotonic()
    try:
        db_path = _DUCKDB_PATH
        if not db_path.exists():
            return PredictiveDeskState(
                desk_status=_unavailable_desk_status(
                    source=source_tag,
                    reason="db_file_not_found",
                ),
                reason_codes=["db_file_not_found"],
            )

        # Connect to DuckDB in read-only mode.
        con = duckdb.connect(database=str(db_path), read_only=True)
        try:
            query = """
                SELECT pr_ordered, trend_strength, var_99, cvar_99, jump_prob,
                       vov, etv, kelly_full, is_ordered_gate, is_jump_gate,
                       gate_veto, raw_json, timestamp, dealer_bias
                FROM probabilistic_analyses
                WHERE symbol = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """
            res = con.execute(query, (underlying_symbol.upper(),)).fetchone()
        finally:
            con.close()

        if res is None:
            return PredictiveDeskState(
                desk_status=_unavailable_desk_status(
                    source=source_tag,
                    reason=f"no_snapshot_for_{underlying_symbol}",
                ),
                reason_codes=[f"no_snapshot_for_{underlying_symbol}"],
            )

        (
            pr_ordered,
            trend_strength,
            var_99,
            cvar_99,
            jump_prob,
            vov,
            etv,
            kelly_full,
            is_ordered_gate,
            is_jump_gate,
            gate_veto,
            raw_json_str,
            timestamp,
            dealer_bias,
        ) = res

        # ── Freshness check (72 hours TTL) ──────────────────────────────
        max_age_seconds = 72 * 3600
        # Determine current time based on timezone awareness of timestamp
        now = datetime.now(UTC) if (timestamp and timestamp.tzinfo) else datetime.now()
        age_seconds = (now - timestamp).total_seconds() if timestamp else float("inf")
        if age_seconds > max_age_seconds:
            return PredictiveDeskState(
                desk_status=_unavailable_desk_status(
                    source=source_tag,
                    reason="predictive_snapshot_stale",
                ),
                reason_codes=["predictive_snapshot_stale"],
            )

        # ── Map fields ──────────────────────────────────────────────────
        # Directional bias based on dealer_bias or pr_ordered
        db_bias = str(dealer_bias or "").upper()
        if db_bias in ("BULLISH", "LONG", "BUY"):
            directional_bias = "LONG"
        elif db_bias in ("BEARISH", "SHORT", "SELL"):
            directional_bias = "SHORT"
        else:
            if pr_ordered is not None:
                if pr_ordered > 0.55:
                    directional_bias = "LONG"
                elif pr_ordered < 0.45:
                    directional_bias = "SHORT"
                else:
                    directional_bias = "NEUTRAL"
            else:
                directional_bias = "NEUTRAL"

        # Probability
        if pr_ordered is not None:
            prob_long = float(pr_ordered)
            prob_short = 1.0 - float(pr_ordered)
        else:
            prob_long = None
            prob_short = None

        # Confidence - project trend strength or kelly full
        confidence = None
        if trend_strength is not None:
            confidence = float(trend_strength)
        elif kelly_full is not None:
            confidence = float(kelly_full)

        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        # Parse raw_json
        raw_dict = {}
        if raw_json_str:
            if isinstance(raw_json_str, dict):
                raw_dict = raw_json_str
            elif isinstance(raw_json_str, str):
                try:
                    raw_dict = json.loads(raw_json_str)
                except Exception:
                    raw_dict = {}

        # Extract particle_filter_state and regime_state if present
        particle_filter = raw_dict.get("particle_filter_state")
        if not isinstance(particle_filter, dict):
            particle_filter = raw_dict.get("particle_filter")
        if not isinstance(particle_filter, dict):
            particle_filter = {}

        regime = raw_dict.get("regime_state")
        if not isinstance(regime, dict):
            regime = raw_dict.get("regime")
        if not isinstance(regime, dict):
            regime = {}

        quality_score = 1.0  # Available and valid
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        logger.debug(
            "_fetch_predictive_desk | symbol=%s as_of=%s bias=%s pr_ordered=%s latency=%.0fms",
            underlying_symbol,
            timestamp,
            directional_bias,
            pr_ordered,
            latency_ms,
        )

        return PredictiveDeskState(
            desk_status=DeskReadStatus(
                status="available",
                source=source_tag,
                reason=None,
                quality_score=quality_score,
                latency_ms=latency_ms,
                captured_at=timestamp.isoformat() if timestamp else "",
            ),
            directional_bias=directional_bias,
            probability_long=prob_long,
            probability_short=prob_short,
            confidence=confidence,
            horizon="swing",
            source_tag="meta_signal",
            reason_codes=[],
            pr_ordered=_safe_float(pr_ordered),
            trend_strength=_safe_float(trend_strength),
            evt_var_99=_safe_float(var_99),
            evt_cvar_99=_safe_float(cvar_99),
            merton_jump_probability=_safe_float(jump_prob),
            particle_filter_state=particle_filter,
            regime_state=regime,
            raw=raw_dict,
        )

    except Exception as exc:
        reason = f"{REASON_DESK_FETCH_FAILED}:{type(exc).__name__}:{str(exc)[:120]}"
        logger.warning(
            "_fetch_predictive_desk.failed symbol=%s error=%s",
            underlying_symbol,
            reason,
        )
        return PredictiveDeskState(
            desk_status=_unavailable_desk_status(
                source=source_tag,
                reason=reason,
            ),
            reason_codes=[reason],
        )
