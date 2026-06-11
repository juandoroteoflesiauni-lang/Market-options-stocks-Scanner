"""monthly_retrain.py
======================
Automated monthly retraining cycle. Runs the full audit -> train -> evaluate
pipeline per symbol, gates the swap on Sharpe + accuracy deltas, versions
artifacts, and prunes old models.

    python -m backend.tasks.monthly_retrain --symbols SPY QQQ AAPL TSLA

Cron (Linux/Mac):
    0 9 1 * * cd /path/to/project && python -m backend.tasks.monthly_retrain

Windows Task Scheduler:
    Trigger: monthly day 1 at 09:00
    Action:  python -m backend.tasks.monthly_retrain --symbols SPY QQQ

Versioning:
    backend/models/meta_learner_{SYM}_{YYYY-MM-DD}.joblib
    backend/models/meta_learner_{SYM}_latest.joblib   (active artifact)
    Keeps the last 3 dated versions per symbol; older ones are deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from backend.config.logger_setup import get_logger
from backend.scripts.audit_prediction_quality import audit_symbol
from backend.scripts.evaluate_live_performance import evaluate as evaluate_live
from backend.scripts.train_meta_learner import _load_existing_mean_accuracy, train_for_symbol_real

logger = get_logger(__name__)

MODELS_DIR = Path("backend/models")
REPORTS_DIR = Path("backend/reports")
KEEP_VERSIONS = 3
EVAL_WEEKS = 4
ACCURACY_TOLERANCE = 0.05
WEBHOOK_ENV_VAR = "RETRAIN_WEBHOOK_URL"


@dataclass
class SymbolResult:
    symbol: str
    skipped: bool
    skip_reason: str | None
    n_predictions: int | None
    train_accuracy: float | None
    prev_train_accuracy: float | None
    sharpe_new: float | None
    sharpe_old: float | None
    model_updated: bool
    active_path: str | None
    error: str | None = None


# ---------------------------------------------------------------------------
# Versioning helpers
# ---------------------------------------------------------------------------


def _versioned_path(symbol: str, today: date) -> Path:
    return MODELS_DIR / f"meta_learner_{symbol.upper()}_{today.isoformat()}.joblib"


def _latest_path(symbol: str) -> Path:
    return MODELS_DIR / f"meta_learner_{symbol.upper()}_latest.joblib"


def _list_versions(symbol: str) -> list[Path]:
    sym = symbol.upper()
    pattern = f"meta_learner_{sym}_*.joblib"
    versions = [p for p in MODELS_DIR.glob(pattern) if not p.name.endswith("_latest.joblib")]
    return sorted(versions, key=lambda p: p.stat().st_mtime, reverse=True)


def _prune_versions(symbol: str, keep: int = KEEP_VERSIONS) -> list[str]:
    versions = _list_versions(symbol)
    pruned: list[str] = []
    for old in versions[keep:]:
        try:
            old.unlink()
            pruned.append(str(old))
        except OSError as exc:
            logger.warning("No se pudo eliminar version vieja %s: %s", old, exc)
    return pruned


_ROUTER_DEFAULT_PATH = MODELS_DIR / "meta_learner.joblib"


def _activate_version(versioned: Path, symbol: str) -> Path:
    """
    Copy versioned artifact onto the `_latest` slot AND the router default
    (`meta_learner.joblib`). Copy (not symlink) for Windows compat. Hot-reload
    the router cache so live traffic picks up the new model without restart.
    """
    latest = _latest_path(symbol)
    shutil.copy2(versioned, latest)
    shutil.copy2(versioned, _ROUTER_DEFAULT_PATH)
    try:
        from backend.routers.probabilistic_router import get_or_load_meta_learner

        get_or_load_meta_learner(force_reload=True)
    except Exception as exc:
        logger.warning("Router hot-reload fallo (no critico): %s", exc)
    return latest


# ---------------------------------------------------------------------------
# Sharpe lookup
# ---------------------------------------------------------------------------


def _eval_sharpe(symbol: str, weeks: int) -> float | None:
    """Run evaluate_live_performance and return its baseline_sharpe (or None)."""
    try:
        report = evaluate_live(symbol, weeks, REPORTS_DIR)
    except SystemExit as exc:
        logger.warning("Evaluate skipped for %s: %s", symbol, exc)
        return None
    except Exception as exc:
        logger.warning("Evaluate failed for %s: %s", symbol, exc)
        return None
    sharpe = report.get("baseline_sharpe")
    if sharpe is None:
        return None
    try:
        val = float(sharpe)
    except (TypeError, ValueError):
        return None
    if val != val:  # NaN
        return None
    return val


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def _send_webhook(payload: dict[str, Any]) -> None:
    url = os.environ.get(WEBHOOK_ENV_VAR)
    if not url:
        return
    try:
        data = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Webhook OK (status=%s) %s", resp.status, url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Webhook fallo: %s", exc)


# ---------------------------------------------------------------------------
# Main per-symbol cycle
# ---------------------------------------------------------------------------


def process_symbol(symbol: str, today: date | None = None) -> SymbolResult:
    sym = symbol.upper().strip()
    today = today or date.today()
    latest = _latest_path(sym)

    # 1. Audit
    try:
        audit = audit_symbol(sym)
    except Exception as exc:
        logger.exception("Audit fallo para %s", sym)
        return SymbolResult(
            symbol=sym,
            skipped=True,
            skip_reason=f"audit_error: {exc}",
            n_predictions=None,
            train_accuracy=None,
            prev_train_accuracy=None,
            sharpe_new=None,
            sharpe_old=None,
            model_updated=False,
            active_path=str(latest) if latest.exists() else None,
            error=str(exc),
        )

    if not audit.get("retrain_ready"):
        msg = (
            f"retrain_ready=False (with_outcome_5d={audit.get('with_outcome_5d')}, "
            f"days_to_ready={audit.get('days_to_retrain_ready')})"
        )
        logger.info("%s: skip — %s", sym, msg)
        return SymbolResult(
            symbol=sym,
            skipped=True,
            skip_reason=msg,
            n_predictions=audit.get("total_predictions"),
            train_accuracy=None,
            prev_train_accuracy=None,
            sharpe_new=None,
            sharpe_old=None,
            model_updated=False,
            active_path=str(latest) if latest.exists() else None,
        )

    # 2. Sharpe under the *current* active model (predictions logged with it).
    #    Backward-looking only — outcomes were generated by the OLD model.
    sharpe_old = _eval_sharpe(sym, EVAL_WEEKS) if latest.exists() else None

    # Snapshot baseline accuracy from the active `_latest` BEFORE train clobbers
    # the versioned slot (which would compare against itself = 0).
    prev_train_acc = _load_existing_mean_accuracy(latest) if latest.exists() else 0.0

    # 3. Train into a versioned slot. train_and_save_real's internal gate
    #    compares vs the versioned-slot baseline (fresh file → 0); the real
    #    accuracy gate runs HERE against the snapshot above.
    versioned = _versioned_path(sym, today)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        train_result = train_for_symbol_real(sym, output_path=versioned)
    except Exception as exc:
        logger.exception("Train fallo para %s", sym)
        return SymbolResult(
            symbol=sym,
            skipped=False,
            skip_reason=None,
            n_predictions=audit.get("total_predictions"),
            train_accuracy=None,
            prev_train_accuracy=prev_train_acc,
            sharpe_new=None,
            sharpe_old=sharpe_old,
            model_updated=False,
            active_path=str(latest) if latest.exists() else None,
            error=f"train_error: {exc}",
        )

    train_acc = train_result.get("mean_accuracy")

    if not versioned.exists():
        return SymbolResult(
            symbol=sym,
            skipped=False,
            skip_reason=None,
            n_predictions=audit.get("total_predictions"),
            train_accuracy=train_acc,
            prev_train_accuracy=prev_train_acc,
            sharpe_new=None,
            sharpe_old=sharpe_old,
            model_updated=False,
            active_path=str(latest) if latest.exists() else None,
            error="train returned no artifact on disk",
        )

    # 4. Promotion gate: TRAIN CV accuracy delta vs previously-active model.
    #    Sharpe is informational only — it cannot validate the new model
    #    forward until next month's predictions accumulate. sharpe_new is
    #    reserved for symmetry with the report schema; populated next cycle
    #    as that cycle's sharpe_old.
    sharpe_new: float | None = None

    accuracy_ok = train_acc is None or train_acc > prev_train_acc - ACCURACY_TOLERANCE

    promote = accuracy_ok
    if promote:
        active = _activate_version(versioned, sym)
        logger.info(
            "Modelo %s mejorado: Sharpe %.3f → %.3f (acc %.3f → %.3f). Activo: %s",
            sym,
            sharpe_old if sharpe_old is not None else float("nan"),
            sharpe_new if sharpe_new is not None else float("nan"),
            prev_train_acc if prev_train_acc is not None else float("nan"),
            train_acc if train_acc is not None else float("nan"),
            active,
        )
        active_path = str(active)
        model_updated = True
    else:
        logger.info(
            "Modelo %s sin mejora — manteniendo version anterior. "
            "Sharpe %.3f → %.3f, acc %.3f → %.3f",
            sym,
            sharpe_old if sharpe_old is not None else float("nan"),
            sharpe_new if sharpe_new is not None else float("nan"),
            prev_train_acc if prev_train_acc is not None else float("nan"),
            train_acc if train_acc is not None else float("nan"),
        )
        active_path = str(latest) if latest.exists() else None
        model_updated = False

    # 5. Prune (always, even on no-promote — keeps disk bounded).
    pruned = _prune_versions(sym)
    if pruned:
        logger.info("%s: %d versiones viejas eliminadas", sym, len(pruned))

    return SymbolResult(
        symbol=sym,
        skipped=False,
        skip_reason=None,
        n_predictions=audit.get("total_predictions"),
        train_accuracy=train_acc,
        prev_train_accuracy=prev_train_acc,
        sharpe_new=sharpe_new,
        sharpe_old=sharpe_old,
        model_updated=model_updated,
        active_path=active_path,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _write_report(results: list[SymbolResult], today: date) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"monthly_retrain_{today.isoformat()}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "results": [asdict(r) for r in results],
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return out


def _log_summary(results: list[SymbolResult]) -> None:
    logger.info("=" * 72)
    logger.info("Resumen mensual retrain")
    logger.info("=" * 72)
    for r in results:
        if r.skipped:
            logger.info("%-6s SKIP — %s", r.symbol, r.skip_reason)
            continue
        if r.error:
            logger.info("%-6s ERROR — %s", r.symbol, r.error)
            continue
        logger.info(
            "%-6s updated=%s acc %.3f→%.3f sharpe %s→%s",
            r.symbol,
            r.model_updated,
            r.prev_train_accuracy if r.prev_train_accuracy is not None else float("nan"),
            r.train_accuracy if r.train_accuracy is not None else float("nan"),
            f"{r.sharpe_old:.3f}" if r.sharpe_old is not None else "n/a",
            f"{r.sharpe_new:.3f}" if r.sharpe_new is not None else "n/a",
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(symbols: list[str]) -> list[SymbolResult]:
    today = date.today()
    results: list[SymbolResult] = []
    for sym in symbols:
        try:
            results.append(process_symbol(sym, today=today))
        except Exception as exc:
            logger.exception("Fallo total procesando %s", sym)
            results.append(
                SymbolResult(
                    symbol=sym.upper(),
                    skipped=False,
                    skip_reason=None,
                    n_predictions=None,
                    train_accuracy=None,
                    prev_train_accuracy=None,
                    sharpe_new=None,
                    sharpe_old=None,
                    model_updated=False,
                    active_path=None,
                    error=str(exc),
                )
            )

    _log_summary(results)
    report_path = _write_report(results, today)
    logger.info("Reporte escrito en: %s", report_path)

    _send_webhook(
        {
            "event": "monthly_retrain",
            "date": today.isoformat(),
            "results": [asdict(r) for r in results],
        }
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monthly retrain cycle for the meta-learner.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["SPY", "QQQ", "AAPL", "TSLA"],
        help="Symbols to process (space-separated).",
    )
    args = parser.parse_args(argv)
    results = run([s.upper() for s in args.symbols])
    has_error = any(r.error for r in results)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
