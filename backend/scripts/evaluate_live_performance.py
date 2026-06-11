"""evaluate_live_performance.py
=================================
Live-edge evaluation: once >=4 weeks of predictions with backfilled outcomes
exist, score the meta-signal pipeline on real money behavior and recommend
threshold adjustments for the regime gate.

    python -m backend.scripts.evaluate_live_performance --symbol SPY --weeks 4

Outputs:
  backend/reports/calibration_{symbol}_{YYYY-MM-DD}.json
  backend/reports/performance_{symbol}_{YYYY-MM-DD}.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.services.prediction_logger import PredictionLogger

logger = get_logger(__name__)

REPORTS_DIR = Path("backend/reports")
SHARPE_FLOOR = 0.5
MIN_TRADES_PER_MONTH = 20
CONVICTION_ORDER = ("VERY_HIGH", "HIGH", "MEDIUM", "LOW")
THRESHOLD_GRID = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40)
CALIBRATION_BINS = (
    (0.00, 0.10),
    (0.10, 0.20),
    (0.20, 0.30),
    (0.30, 0.40),
    (0.40, 0.50),
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.01),
)
HORIZON_DAYS = 5
TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_predictions(symbol: str, weeks: int, pl: PredictionLogger) -> pd.DataFrame:
    cutoff = (datetime.now() - timedelta(weeks=int(weeks))).isoformat()
    with pl._lock, pl._connect() as conn:
        rows = conn.execute(
            """
            SELECT p.prediction_id, p.timestamp, p.direction, p.confidence,
                   p.p_up, p.p_down, p.p_neutral,
                   p.conviction_level, p.should_trade, p.position_size_pct,
                   p.conflict_score,
                   o.outcome_return_1d, o.outcome_return_5d,
                   o.outcome_direction_correct
            FROM predictions p
            LEFT JOIN outcomes o ON p.prediction_id = o.prediction_id
            WHERE p.symbol = ? AND p.timestamp >= ?
            ORDER BY p.timestamp ASC
            """,
            (symbol.upper(), cutoff),
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["should_trade"] = df["should_trade"].astype(bool)
    return df


# ---------------------------------------------------------------------------
# Sharpe primitives
# ---------------------------------------------------------------------------


def _direction_sign(direction: str) -> int:
    if direction == "UP":
        return 1
    if direction == "DOWN":
        return -1
    return 0


def _strategy_returns(df: pd.DataFrame) -> np.ndarray:
    """Per-prediction PnL: only should_trade=True, signed by direction, sized."""
    if df.empty:
        return np.array([], dtype=float)
    mask = df["should_trade"] & df["outcome_return_5d"].notna()
    sub = df.loc[mask]
    if sub.empty:
        return np.array([], dtype=float)
    signs = sub["direction"].map(_direction_sign).astype(float).to_numpy()
    sizes = sub["position_size_pct"].fillna(1.0).astype(float).to_numpy()
    rets = sub["outcome_return_5d"].astype(float).to_numpy()
    return signs * sizes * rets


def _sharpe(returns: np.ndarray) -> float:
    if returns.size < 2:
        return float("nan")
    std = float(returns.std(ddof=1))
    if std <= 0:
        return 0.0
    annualization = float(np.sqrt(TRADING_DAYS_PER_YEAR / HORIZON_DAYS))
    return float(returns.mean() / std * annualization)


def _accuracy(records: pd.DataFrame, col: str) -> float:
    have = records[records[col].notna()]
    if have.empty:
        return float("nan")
    return float((have[col].astype(bool)).mean())


def _direction_accuracy_5d(records: pd.DataFrame) -> float:
    """Direction-correct rate using outcome_return_5d when boolean is missing."""
    explicit = records["outcome_direction_correct"].notna().sum()
    if explicit:
        return _accuracy(records, "outcome_direction_correct")
    sub = records[records["outcome_return_5d"].notna()]
    if sub.empty:
        return float("nan")
    sign_pred = sub["direction"].map(_direction_sign).astype(float)
    sign_real = np.sign(sub["outcome_return_5d"].astype(float))
    return float((sign_pred == sign_real).mean())


def _direction_accuracy_1d(records: pd.DataFrame) -> float:
    sub = records[records["outcome_return_1d"].notna()]
    if sub.empty:
        return float("nan")
    sign_pred = sub["direction"].map(_direction_sign).astype(float)
    sign_real = np.sign(sub["outcome_return_1d"].astype(float))
    return float((sign_pred == sign_real).mean())


# ---------------------------------------------------------------------------
# 1 — Per-conviction breakdown
# ---------------------------------------------------------------------------


@dataclass
class ConvictionStats:
    conviction: str
    n_predictions: int
    n_with_outcome: int
    accuracy_1d: float
    accuracy_5d: float
    sharpe: float


def _per_conviction_stats(df: pd.DataFrame) -> list[ConvictionStats]:
    out: list[ConvictionStats] = []
    for level in CONVICTION_ORDER:
        sub = df[df["conviction_level"] == level]
        n_total = int(len(sub))
        with_out = sub[sub["outcome_return_5d"].notna()]
        rets = _strategy_returns(sub)
        out.append(
            ConvictionStats(
                conviction=level,
                n_predictions=n_total,
                n_with_outcome=int(len(with_out)),
                accuracy_1d=_direction_accuracy_1d(sub),
                accuracy_5d=_direction_accuracy_5d(sub),
                sharpe=_sharpe(rets),
            )
        )
    return out


def _conviction_monotonicity_ok(stats: list[ConvictionStats]) -> bool:
    """VERY_HIGH > HIGH > MEDIUM > LOW (NaNs treated as no signal → skip)."""
    accs = [s.accuracy_5d for s in stats]
    valid = [a for a in accs if not np.isnan(a)]
    if len(valid) < 2:
        return True
    pairs = list(zip(accs[:-1], accs[1:], strict=False))
    for higher, lower in pairs:
        if np.isnan(higher) or np.isnan(lower):
            continue
        if higher < lower:
            return False
    return True


# ---------------------------------------------------------------------------
# 2 — Calibration curve (P(UP) buckets)
# ---------------------------------------------------------------------------


def _calibration_curve(df: pd.DataFrame) -> list[dict[str, Any]]:
    """For each P(UP) bin: empirical fraction of UP outcomes."""
    sub = df[df["outcome_return_5d"].notna() & df["p_up"].notna()].copy()
    if sub.empty:
        return []
    sub["realised_up"] = (sub["outcome_return_5d"].astype(float) > 0.005).astype(int)

    out: list[dict[str, Any]] = []
    for lo, hi in CALIBRATION_BINS:
        bucket = sub[(sub["p_up"] >= lo) & (sub["p_up"] < hi)]
        n = int(len(bucket))
        out.append(
            {
                "bin_low": float(lo),
                "bin_high": float(min(hi, 1.0)),
                "n": n,
                "mean_predicted_p_up": float(bucket["p_up"].mean()) if n else None,
                "empirical_up_rate": float(bucket["realised_up"].mean()) if n else None,
            }
        )
    return out


def _calibration_error(curve: list[dict[str, Any]]) -> float:
    """Weighted mean |empirical - predicted|. NaN if no data."""
    weighted_sum = 0.0
    total_n = 0
    for b in curve:
        if b["n"] == 0 or b["mean_predicted_p_up"] is None:
            continue
        weighted_sum += b["n"] * abs(b["empirical_up_rate"] - b["mean_predicted_p_up"])
        total_n += b["n"]
    if total_n == 0:
        return float("nan")
    return weighted_sum / total_n


# ---------------------------------------------------------------------------
# 3 — Threshold sweep
# ---------------------------------------------------------------------------


def _months_span(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    span = (df["timestamp"].max() - df["timestamp"].min()).days
    return max(span / 30.0, 1.0 / 30.0)


def _threshold_sweep(df: pd.DataFrame) -> list[dict[str, Any]]:
    months = _months_span(df)
    out: list[dict[str, Any]] = []
    for thr in THRESHOLD_GRID:
        sub = df[df["confidence"] >= thr]
        rets = _strategy_returns(sub)
        n_trades = int(rets.size)
        trades_per_month = n_trades / months if months > 0 else 0.0
        out.append(
            {
                "threshold": float(thr),
                "n_predictions": int(len(sub)),
                "n_trades": n_trades,
                "trades_per_month": float(trades_per_month),
                "sharpe": _sharpe(rets),
                "mean_return": float(rets.mean()) if rets.size else float("nan"),
            }
        )
    return out


def _recommend_threshold(sweep: list[dict[str, Any]], baseline_sharpe: float) -> dict[str, Any]:
    eligible = [
        row
        for row in sweep
        if row["trades_per_month"] >= MIN_TRADES_PER_MONTH and not np.isnan(row["sharpe"])
    ]
    if not eligible:
        return {
            "recommendation": "Sistema sin edge — revisar motores",
            "reason": (
                f"Ningun threshold mantiene >= {MIN_TRADES_PER_MONTH} trades/mes "
                f"con Sharpe valido."
            ),
            "baseline_sharpe": baseline_sharpe,
            "selected_threshold": None,
        }

    best = max(eligible, key=lambda r: r["sharpe"])
    if best["sharpe"] < SHARPE_FLOOR:
        return {
            "recommendation": "Sistema sin edge — revisar motores",
            "reason": (
                f"Mejor Sharpe alcanzable {best['sharpe']:.2f} < piso {SHARPE_FLOOR:.2f} "
                f"@ threshold {best['threshold']:.2f}."
            ),
            "baseline_sharpe": baseline_sharpe,
            "selected_threshold": best["threshold"],
            "selected_sharpe": best["sharpe"],
        }

    if best["sharpe"] - baseline_sharpe > 0.10:
        return {
            "recommendation": f"Subir umbral a {best['threshold']:.2f}",
            "reason": (
                f"Sharpe sube de {baseline_sharpe:.2f} a {best['sharpe']:.2f} "
                f"manteniendo {best['trades_per_month']:.1f} trades/mes."
            ),
            "baseline_sharpe": baseline_sharpe,
            "selected_threshold": best["threshold"],
            "selected_sharpe": best["sharpe"],
        }

    return {
        "recommendation": "Mantener umbrales actuales",
        "reason": (
            f"Sharpe actual {baseline_sharpe:.2f} >= piso {SHARPE_FLOOR:.2f} y "
            f"sweep no aporta mejora >0.10."
        ),
        "baseline_sharpe": baseline_sharpe,
        "selected_threshold": best["threshold"],
        "selected_sharpe": best["sharpe"],
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_summary(
    symbol: str,
    weeks: int,
    n_total: int,
    n_with_outcome: int,
    baseline_sharpe: float,
    monotonic: bool,
    conviction_stats: list[ConvictionStats],
    sweep: list[dict[str, Any]],
    recommendation: dict[str, Any],
    cal_error: float,
) -> None:
    logger.info("=" * 72)
    logger.info("Live performance — %s — ultimas %s semanas", symbol, weeks)
    logger.info("=" * 72)
    logger.info(
        "n_predictions=%s  n_with_outcome=%s  baseline_sharpe=%.3f  cal_err=%.3f",
        n_total,
        n_with_outcome,
        baseline_sharpe,
        cal_error,
    )
    logger.info("Monotonicidad accuracy_5d por conviction: %s", "OK" if monotonic else "ROTA")
    logger.info("-" * 72)
    logger.info(
        "%-12s %5s %5s %9s %9s %9s",
        "conviction",
        "n",
        "n_o",
        "acc_1d",
        "acc_5d",
        "sharpe",
    )
    for s in conviction_stats:
        logger.info(
            "%-12s %5d %5d %9.3f %9.3f %9.3f",
            s.conviction,
            s.n_predictions,
            s.n_with_outcome,
            s.accuracy_1d,
            s.accuracy_5d,
            s.sharpe,
        )
    logger.info("-" * 72)
    logger.info("%-10s %5s %7s %9s %9s", "thr", "n_tr", "tr/mes", "sharpe", "mean_r")
    for row in sweep:
        logger.info(
            "%-10.2f %5d %7.2f %9.3f %9.4f",
            row["threshold"],
            row["n_trades"],
            row["trades_per_month"],
            row["sharpe"],
            row["mean_return"],
        )
    logger.info("-" * 72)
    logger.info("RECOMENDACION: %s", recommendation["recommendation"])
    logger.info("Motivo: %s", recommendation["reason"])


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def evaluate(symbol: str, weeks: int, reports_dir: Path = REPORTS_DIR) -> dict[str, Any]:
    pl = PredictionLogger()
    df = _load_predictions(symbol, weeks, pl)
    if df.empty:
        raise SystemExit(f"Sin predicciones para {symbol} en las ultimas {weeks} semanas.")

    n_with_outcome = int(df["outcome_return_5d"].notna().sum())
    baseline_returns = _strategy_returns(df)
    baseline_sharpe = _sharpe(baseline_returns)

    conviction_stats = _per_conviction_stats(df)
    monotonic = _conviction_monotonicity_ok(conviction_stats)

    calibration_curve = _calibration_curve(df)
    cal_error = _calibration_error(calibration_curve)

    sweep = _threshold_sweep(df)
    recommendation = _recommend_threshold(sweep, baseline_sharpe)

    today = date.today().isoformat()
    cal_path = reports_dir / f"calibration_{symbol.upper()}_{today}.json"
    perf_path = reports_dir / f"performance_{symbol.upper()}_{today}.json"

    _write_json(
        cal_path,
        {
            "symbol": symbol.upper(),
            "weeks": weeks,
            "generated_at": datetime.now().isoformat(),
            "calibration_curve": calibration_curve,
            "weighted_calibration_error": cal_error,
        },
    )
    perf_payload = {
        "symbol": symbol.upper(),
        "weeks": weeks,
        "generated_at": datetime.now().isoformat(),
        "n_predictions": int(len(df)),
        "n_with_outcome": n_with_outcome,
        "baseline_sharpe": baseline_sharpe,
        "monotonicity_ok": monotonic,
        "conviction_stats": [asdict(s) for s in conviction_stats],
        "threshold_sweep": sweep,
        "weighted_calibration_error": cal_error,
        "recommendation": recommendation,
    }
    _write_json(perf_path, perf_payload)

    _print_summary(
        symbol,
        weeks,
        int(len(df)),
        n_with_outcome,
        baseline_sharpe,
        monotonic,
        conviction_stats,
        sweep,
        recommendation,
        cal_error,
    )

    if not monotonic:
        logger.warning(
            "Meta-learner no captura incertidumbre: accuracy_5d no monotona en conviction."
        )

    return perf_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate live meta-signal edge.")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--weeks", type=int, default=4)
    parser.add_argument("--reports-dir", default=str(REPORTS_DIR))
    args = parser.parse_args()
    evaluate(args.symbol, args.weeks, Path(args.reports_dir))


if __name__ == "__main__":
    main()
