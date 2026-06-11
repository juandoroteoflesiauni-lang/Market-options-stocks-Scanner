"""audit_prediction_quality.py
==================================
Weekly audit of accumulated predictions before they're consumed by the
meta-learner retrainer. Detects coverage gaps, direction bias, outcome
backfill failures, feature-distribution issues and concept drift.

    python -m backend.scripts.audit_prediction_quality --symbol SPY

Output JSON is written to backend/reports/audit_{symbol}_{YYYY-MM-DD}.json
and also echoed to stdout.

Checks
------
1. Coverage     : gaps > 4h during market hours, days covered, gaps > 1 BD
2. Direction    : UP/DOWN/NEUTRAL distribution vs realised direction of asset
3. Outcomes     : completion rate per horizon; older preds without outcome_1d
4. Features     : confidence distribution, conflict_score average
5. Drift        : accuracy first 30 days vs last 30 days

Exit codes: 0 OK, 1 if errors[] non-empty.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from backend.config.logger_setup import get_logger
from backend.services.prediction_logger import PredictionLogger

logger = get_logger(__name__)

REPORTS_DIR = Path("backend/reports")
RETRAIN_TARGET = 300
DIRECTION_BIAS_THRESHOLD = 0.60
GAP_HOURS_THRESHOLD = 4.0
GAP_BUSINESS_DAYS_LIMIT = 1
OUTCOME_COMPLETION_FLOOR = 0.80
OLD_PRED_AGE_DAYS = 2
CONFIDENCE_FLOOR = 0.55
CONFLICT_SCORE_CEIL = 0.50
DRIFT_DROP_THRESHOLD = 0.10
RETURN_DIRECTION_THRESHOLD = 0.005  # matches PredictionLogger
DRIFT_WINDOW_DAYS = 30
MIN_OUTCOMES_FOR_DRIFT = 30


# ---------------------------------------------------------------------------


def _load_predictions(symbol: str, pl: PredictionLogger) -> pd.DataFrame:
    """Load every prediction (with or without outcome) for `symbol`."""
    with pl._lock, pl._connect() as conn:
        rows = conn.execute(
            """
            SELECT p.prediction_id, p.timestamp, p.direction, p.confidence,
                   p.conviction_level, p.should_trade, p.conflict_score,
                   p.price_t0,
                   o.outcome_return_1h, o.outcome_return_4h, o.outcome_return_eod
            FROM predictions p
            LEFT JOIN outcomes_v3 o ON p.prediction_id = o.prediction_id
            WHERE p.symbol = ?
            ORDER BY p.timestamp ASC
            """,
            (symbol.upper(),),
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# 1 — Coverage
# ---------------------------------------------------------------------------


def _check_coverage(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"days_covered": 0, "coverage_gaps": [], "warnings": []}

    ts_sorted = df["timestamp"].dropna().sort_values().reset_index(drop=True)
    diffs = ts_sorted.diff().dropna()

    gaps_hours = []
    for prev, curr, delta in zip(ts_sorted[:-1], ts_sorted[1:], diffs, strict=False):
        # Only flag intra-market-day gaps: same business day, gap > N hours.
        if prev.date() == curr.date() and delta.total_seconds() / 3600.0 > GAP_HOURS_THRESHOLD:
            gaps_hours.append(
                {
                    "from": prev.isoformat(),
                    "to": curr.isoformat(),
                    "hours": round(delta.total_seconds() / 3600.0, 2),
                }
            )

    # Business-day gap detection: count business days between consecutive preds
    bd_gaps = []
    warnings: list[str] = []
    for prev, curr in zip(ts_sorted[:-1], ts_sorted[1:], strict=False):
        bd = np.busday_count(prev.date(), curr.date())
        if bd > GAP_BUSINESS_DAYS_LIMIT:
            bd_gaps.append(
                {
                    "from": prev.isoformat(),
                    "to": curr.isoformat(),
                    "business_days": int(bd),
                }
            )

    if bd_gaps:
        warnings.append(
            f"{len(bd_gaps)} gap(s) > {GAP_BUSINESS_DAYS_LIMIT} business day(s) detected"
        )

    days_covered = int(ts_sorted.dt.date.nunique())
    return {
        "days_covered": days_covered,
        "coverage_gaps": gaps_hours + bd_gaps,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 2 — Direction bias
# ---------------------------------------------------------------------------


def _realised_direction_distribution(
    symbol: str, start: datetime, end: datetime
) -> dict[str, float] | None:
    """Distribution of daily realised directions of the underlying."""
    try:
        hist = yf.Ticker(symbol).history(
            start=start.date().isoformat(),
            end=(end.date() + timedelta(days=1)).isoformat(),
        )
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", symbol, exc)
        return None
    if hist.empty:
        return None
    rets = hist["Close"].pct_change().dropna()
    if rets.empty:
        return None
    up = float((rets > RETURN_DIRECTION_THRESHOLD).mean())
    down = float((rets < -RETURN_DIRECTION_THRESHOLD).mean())
    neu = float(1.0 - up - down)
    return {"UP": up, "DOWN": down, "NEUTRAL": max(0.0, neu)}


def _check_direction_bias(df: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if df.empty:
        return {
            "direction_bias": "NEUTRAL",
            "predicted_distribution": {},
            "realised_distribution": None,
            "warnings": [],
        }

    counts = df["direction"].value_counts(normalize=True)
    predicted = {k: float(counts.get(k, 0.0)) for k in ("UP", "DOWN", "NEUTRAL")}

    bias = "NEUTRAL"
    warnings: list[str] = []
    if predicted["UP"] > DIRECTION_BIAS_THRESHOLD:
        bias = "UP"
        warnings.append(
            f"Predicted UP rate {predicted['UP']:.1%} > {DIRECTION_BIAS_THRESHOLD:.0%} — possible systematic bullish bias"
        )
    if predicted["DOWN"] > DIRECTION_BIAS_THRESHOLD:
        bias = "DOWN"
        warnings.append(
            f"Predicted DOWN rate {predicted['DOWN']:.1%} > {DIRECTION_BIAS_THRESHOLD:.0%} — possible systematic bearish bias"
        )

    realised = _realised_direction_distribution(
        symbol, df["timestamp"].min(), df["timestamp"].max()
    )
    if realised is not None:
        # Flag if predicted/realised differ by > 20pp on any side
        for d in ("UP", "DOWN"):
            if abs(predicted[d] - realised[d]) > 0.20:
                warnings.append(
                    f"Predicted {d} {predicted[d]:.1%} diverges from realised {realised[d]:.1%} by > 20pp"
                )

    return {
        "direction_bias": bias,
        "predicted_distribution": predicted,
        "realised_distribution": realised,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 3 — Outcomes quality
# ---------------------------------------------------------------------------


def _check_outcomes(df: pd.DataFrame) -> dict[str, Any]:
    total = len(df)
    if total == 0:
        return {
            "outcome_1h_completion": 0.0,
            "outcome_4h_completion": 0.0,
            "outcome_eod_completion": 0.0,
            "with_outcome_eod": 0,
            "accuracy_eod": None,
            "accuracy_by_conviction": {},
            "warnings": [],
            "errors": [],
        }

    has_1h = (
        df["outcome_return_1h"].notna() if "outcome_return_1h" in df else pd.Series([False] * total)
    )
    has_4h = (
        df["outcome_return_4h"].notna() if "outcome_return_4h" in df else pd.Series([False] * total)
    )
    has_eod = (
        df["outcome_return_eod"].notna()
        if "outcome_return_eod" in df
        else pd.Series([False] * total)
    )

    completion_1h = float(has_1h.mean())
    completion_4h = float(has_4h.mean())
    completion_eod = float(has_eod.mean())

    warnings: list[str] = []
    errors: list[str] = []

    # Older preds without outcome_eod → updater failure
    cutoff_old = pd.Timestamp(datetime.now() - timedelta(days=OLD_PRED_AGE_DAYS))
    old = df[df["timestamp"] <= cutoff_old]
    if len(old) > 0:
        old_completion_eod = (
            float(old["outcome_return_eod"].notna().mean()) if "outcome_return_eod" in old else 0.0
        )
        if old_completion_eod < OUTCOME_COMPLETION_FLOOR:
            errors.append(
                f"Only {old_completion_eod:.1%} of preds older than {OLD_PRED_AGE_DAYS}d have outcome_eod "
                f"(< {OUTCOME_COMPLETION_FLOOR:.0%}) — outcome_updater likely failing"
            )

    def _compute_correctness(row, return_col):
        ret = row.get(return_col)
        if pd.isna(ret):
            return np.nan
        pred = row.get("direction")
        if ret > RETURN_DIRECTION_THRESHOLD:
            return pred == "UP"
        if ret < -RETURN_DIRECTION_THRESHOLD:
            return pred == "DOWN"
        return pred == "NEUTRAL"

    accuracies = {}
    for h in ["1h", "4h", "eod"]:
        col = f"outcome_return_{h}"
        if col in df:
            correct = df.apply(lambda r: _compute_correctness(r, col), axis=1)
            accuracies[f"accuracy_{h}"] = (
                float(correct.dropna().mean()) if len(correct.dropna()) > 0 else None
            )
        else:
            accuracies[f"accuracy_{h}"] = None

    accuracy_by_conviction: dict[str, float] = {}
    # Use eod as baseline for conviction breakdown
    if "outcome_return_eod" in df:
        df_correct = df.copy()
        df_correct["correct_eod"] = df.apply(
            lambda r: _compute_correctness(r, "outcome_return_eod"), axis=1
        )
        df_correct = df_correct[df_correct["correct_eod"].notna()]
        for level, group in df_correct.groupby("conviction_level"):
            if len(group) >= 5:
                accuracy_by_conviction[str(level)] = float(group["correct_eod"].astype(bool).mean())

    very_high = accuracy_by_conviction.get("VERY_HIGH")
    low = accuracy_by_conviction.get("LOW")
    if very_high is not None and low is not None and very_high < low:
        warnings.append(
            f"VERY_HIGH accuracy ({very_high:.1%}) < LOW accuracy ({low:.1%}) — calibration broken"
        )

    return {
        "outcome_1h_completion": completion_1h,
        "outcome_4h_completion": completion_4h,
        "outcome_eod_completion": completion_eod,
        "with_outcome_eod": int(has_eod.sum()),
        "accuracy_1h": accuracies["accuracy_1h"],
        "accuracy_4h": accuracies["accuracy_4h"],
        "accuracy_eod": accuracies["accuracy_eod"],
        "accuracy_by_conviction": accuracy_by_conviction,
        "warnings": warnings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 4 — Features distribution
# ---------------------------------------------------------------------------


def _check_features(df: pd.DataFrame) -> dict[str, Any]:
    warnings: list[str] = []
    if df.empty:
        return {
            "max_confidence_should_trade": None,
            "avg_conflict_score": None,
            "warnings": warnings,
        }

    trade_df = df[df["should_trade"] == 1]
    max_conf = float(trade_df["confidence"].max()) if len(trade_df) > 0 else None
    if max_conf is not None and max_conf < CONFIDENCE_FLOOR:
        warnings.append(
            f"max blended_confidence on should_trade=True is {max_conf:.2f} < {CONFIDENCE_FLOOR} — model too conservative"
        )

    avg_conflict = float(df["conflict_score"].mean())
    if avg_conflict > CONFLICT_SCORE_CEIL:
        warnings.append(
            f"avg conflict_score {avg_conflict:.2f} > {CONFLICT_SCORE_CEIL} — motors persistently disagree"
        )

    return {
        "max_confidence_should_trade": max_conf,
        "avg_conflict_score": avg_conflict,
        "n_should_trade": int((df["should_trade"] == 1).sum()),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 5 — Drift
# ---------------------------------------------------------------------------


def _check_drift(df: pd.DataFrame) -> dict[str, Any]:
    if "outcome_return_eod" not in df:
        return {
            "drift_detected": False,
            "accuracy_first": None,
            "accuracy_last": None,
            "warnings": [],
        }

    def _compute_correctness(row):
        ret = row.get("outcome_return_eod")
        if pd.isna(ret):
            return np.nan
        pred = row.get("direction")
        if abs(ret) < 0.0015:
            return pred == "NEUTRAL"
        if ret > 0:
            return pred == "UP"
        return pred == "DOWN"

    df = df.copy()
    df["correct_eod"] = df.apply(_compute_correctness, axis=1)
    correct = df[df["correct_eod"].notna()]

    if len(correct) < 2 * MIN_OUTCOMES_FOR_DRIFT:
        return {
            "drift_detected": False,
            "accuracy_first": None,
            "accuracy_last": None,
            "warnings": [],
        }

    correct = correct.sort_values("timestamp")
    cutoff_first = correct["timestamp"].min() + timedelta(days=DRIFT_WINDOW_DAYS)
    cutoff_last = correct["timestamp"].max() - timedelta(days=DRIFT_WINDOW_DAYS)

    first_window = correct[correct["timestamp"] <= cutoff_first]
    last_window = correct[correct["timestamp"] >= cutoff_last]

    if len(first_window) < MIN_OUTCOMES_FOR_DRIFT or len(last_window) < MIN_OUTCOMES_FOR_DRIFT:
        return {
            "drift_detected": False,
            "accuracy_first": None,
            "accuracy_last": None,
            "warnings": [],
        }

    acc_first = float(first_window["correct_eod"].astype(bool).mean())
    acc_last = float(last_window["correct_eod"].astype(bool).mean())
    drop = acc_first - acc_last

    warnings: list[str] = []
    drift = drop > DRIFT_DROP_THRESHOLD
    if drift:
        warnings.append(
            f"accuracy dropped {drop * 100:.1f}pp ({acc_first:.1%} → {acc_last:.1%}) — concept drift, retrain recommended"
        )

    return {
        "drift_detected": drift,
        "accuracy_first": acc_first,
        "accuracy_last": acc_last,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def audit_symbol(symbol: str) -> dict[str, Any]:
    sym = symbol.upper().strip()
    if sym in {"SPY", "QQQ"}:
        logger.info(
            "Asset treated as Contextual Macro Anchor; verifying structural alignment instead of core funding execution."
        )

    pl = PredictionLogger(db_path="backend/data/predictions.db")
    df = _load_predictions(sym, pl)
    today = date.today()

    if df.empty:
        return {
            "symbol": sym,
            "audit_date": today.isoformat(),
            "total_predictions": 0,
            "with_outcome_eod": 0,
            "accuracy_eod": None,
            "direction_bias": "NEUTRAL",
            "coverage_gaps": [],
            "warnings": ["no predictions logged"],
            "errors": [],
            "retrain_recommended": False,
            "retrain_ready": False,
            "days_to_retrain_ready": None,
        }

    coverage = _check_coverage(df)
    bias = _check_direction_bias(df, sym)
    outcomes = _check_outcomes(df)
    features = _check_features(df)
    drift = _check_drift(df)

    warnings: list[str] = []
    warnings.extend(coverage["warnings"])
    warnings.extend(bias["warnings"])
    warnings.extend(outcomes["warnings"])
    warnings.extend(features["warnings"])
    warnings.extend(drift["warnings"])

    errors = list(outcomes["errors"])

    # Retrain readiness
    with_outcome_eod = outcomes["with_outcome_eod"]
    retrain_ready = with_outcome_eod >= RETRAIN_TARGET
    retrain_recommended = drift["drift_detected"] and retrain_ready

    # Days estimate
    ts_min, ts_max = df["timestamp"].min(), df["timestamp"].max()
    elapsed_days = max((ts_max - ts_min).total_seconds() / 86400.0, 1e-6)
    preds_per_day = len(df) / elapsed_days
    remaining = max(0, RETRAIN_TARGET - with_outcome_eod)
    days_to_ready: int | None
    if remaining == 0:
        days_to_ready = 0
    elif preds_per_day > 0:
        days_to_ready = int(round(remaining / preds_per_day))
    else:
        days_to_ready = None

    return {
        "symbol": sym,
        "audit_date": today.isoformat(),
        "total_predictions": int(len(df)),
        "with_outcome_eod": with_outcome_eod,
        "accuracy_eod": outcomes["accuracy_eod"],
        "accuracy_1h": outcomes["accuracy_1h"],
        "accuracy_4h": outcomes["accuracy_4h"],
        "direction_bias": bias["direction_bias"],
        "coverage_gaps": coverage["coverage_gaps"],
        "days_covered": coverage["days_covered"],
        "predicted_distribution": bias["predicted_distribution"],
        "realised_distribution": bias["realised_distribution"],
        "outcome_1h_completion": outcomes["outcome_1h_completion"],
        "outcome_4h_completion": outcomes["outcome_4h_completion"],
        "outcome_eod_completion": outcomes["outcome_eod_completion"],
        "accuracy_by_conviction": outcomes["accuracy_by_conviction"],
        "max_confidence_should_trade": features["max_confidence_should_trade"],
        "avg_conflict_score": features["avg_conflict_score"],
        "n_should_trade": features["n_should_trade"],
        "drift_detected": drift["drift_detected"],
        "accuracy_first_window": drift["accuracy_first"],
        "accuracy_last_window": drift["accuracy_last"],
        "warnings": warnings,
        "errors": errors,
        "retrain_recommended": retrain_recommended,
        "retrain_ready": retrain_ready,
        "days_to_retrain_ready": days_to_ready,
    }


def _save_report(report: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"audit_{report['symbol']}_{report['audit_date']}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="Ticker to audit (e.g. SPY)")
    args = parser.parse_args(argv)

    report = audit_symbol(args.symbol)
    out_path = _save_report(report)

    print(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved to: {out_path}", file=sys.stderr)

    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
