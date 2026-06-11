"""Optional EnsembleMetaLearner adjustment for Market Scanner scores."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_MODEL: object | None = None
_MODEL_PATH: str | None = None


def _default_meta_path() -> Path:
    env = os.getenv("MARKET_SCANNER_META_LEARNER_PATH", "").strip()
    if env:
        return Path(env)
    return Path("backend/models/meta_learner.joblib")


def _load_model() -> object | None:
    global _MODEL, _MODEL_PATH
    path = _default_meta_path()
    if not path.is_file():
        return None
    sp = str(path.resolve())
    if _MODEL is not None and sp == _MODEL_PATH:
        return _MODEL
    try:
        import joblib

        loaded = joblib.load(path)
    except Exception as exc:
        logger.warning("scanner_meta_learner.load_failed path=%s err=%s", path, exc)
        return None

    if not getattr(loaded, "is_fitted", False):
        logger.warning("scanner_meta_learner.not_fitted path=%s", path)
        return None

    _MODEL = loaded
    _MODEL_PATH = sp
    logger.info("scanner_meta_learner.loaded path=%s", path)
    return _MODEL


def _price_block_from_bars(bars: list[dict[str, Any]]) -> dict[str, float]:
    """Derive price__* features from OHLCV (last window)."""
    if len(bars) < 8:
        return {}
    closes: list[float] = []
    for b in bars:
        if not isinstance(b, dict):
            continue
        c = b.get("close", b.get("c"))
        if c is None:
            continue
        try:
            closes.append(float(c))
        except (TypeError, ValueError):
            continue
    if len(closes) < 8:
        return {}
    s = pd.Series(closes, dtype=np.float64)
    r1 = s.pct_change(1).iloc[-1]
    r5 = s.pct_change(5).iloc[-1] if len(s) > 5 else np.nan
    vol5 = s.pct_change(1).iloc[-5:].std() if len(s) >= 5 else np.nan
    vol20 = s.pct_change(1).iloc[-20:].std() if len(s) >= 20 else np.nan
    ma20 = s.rolling(20).mean().iloc[-1] if len(s) >= 20 else np.nan
    out: dict[str, float] = {
        "return_1d": float(r1) if pd.notna(r1) else np.nan,
        "return_5d": float(r5) if pd.notna(r5) else np.nan,
        "realized_vol_5d": float(vol5) if pd.notna(vol5) else np.nan,
        "realized_vol_20d": float(vol20) if pd.notna(vol20) else np.nan,
    }
    if pd.notna(ma20) and float(ma20) > 0:
        out["price_vs_ma20"] = float(s.iloc[-1] / float(ma20) - 1.0)
    return {k: v for k, v in out.items() if not (isinstance(v, float) and np.isnan(v))}


def try_meta_learner_score_delta(
    scanner_score: float,
    module_signals: dict[str, Any],
    bars_primary: list[dict[str, Any]],
) -> tuple[float, dict[str, Any] | None]:
    """Return score delta in [-15, 15] from calibrated meta-learner if available."""
    model = _load_model()
    if model is None:
        return 0.0, None

    from backend.layer_3_specialists.ia_probabilistico.engines.ensemble_meta_learner import (
        build_feature_matrix,
    )

    tech = module_signals.get("technical")
    prob = module_signals.get("probabilistic")
    opt = module_signals.get("options_gex")
    orch_signal = float(np.clip(((scanner_score - 50.0) / 50.0), -1.0, 1.0))
    orch_conf = 0.0
    n = 0
    for m in (tech, prob, opt):
        if m is not None and hasattr(m, "confidence"):
            orch_conf += float(getattr(m, "confidence", 0) or 0)
            n += 1
    if n:
        orch_conf /= n

    price_block = _price_block_from_bars(bars_primary)
    entry: dict[str, Any] = {
        "timestamp": pd.Timestamp.now("UTC"),
        "dte_category": "monthly",
        "orchestrator": {"signal": orch_signal, "confidence": orch_conf, "conflict_score": 0.0},
    }
    entry.update(price_block)

    try:
        x = build_feature_matrix([entry])
        if x.empty:
            return 0.0, None
        proba = model.predict_proba(x, explain=False)
        p_up = float(proba.get("UP", 0.0))
        p_dn = float(proba.get("DOWN", 0.0))
        edge = p_up - p_dn
        delta = float(np.clip(edge * 18.0, -15.0, 15.0))
        return delta, {
            "meta_up": round(p_up, 4),
            "meta_down": round(p_dn, 4),
            "meta_neutral": round(float(proba.get("NEUTRAL", 0.0)), 4),
            "delta": round(delta, 4),
        }
    except Exception as exc:
        logger.debug("scanner_meta_learner.predict_failed err=%s", str(exc)[:160])
        return 0.0, None
