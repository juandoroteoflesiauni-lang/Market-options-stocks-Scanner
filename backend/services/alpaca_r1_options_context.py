"""Carga de contexto de opciones R1 — un read por símbolo/ciclo. # [PD-3][TH]"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.config.alpaca_institutional_config import ml_direction_classifier_enabled
from backend.config.logger_setup import get_logger
from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB
from backend.domain.alpaca_options_models import Route1OptionsSnapshotContext
from backend.domain.probabilistic_models import PredictiveOptionsBundleReport
from backend.services.research.research_types import _bucket_tail_risk, _safe_float

logger = get_logger(__name__)

_CYCLE_CACHE: dict[str, Route1OptionsSnapshotContext] = {}
_REPORT_CACHE: dict[str, PredictiveOptionsBundleReport | None] = {}


@dataclass(frozen=True)
class Route1OptionsBundle:
    """Bundle compartido entre gate crítico, replay y scorer."""

    report: PredictiveOptionsBundleReport | None
    context: Route1OptionsSnapshotContext | None


def clear_route1_options_cache() -> None:
    """Limpia caché in-memory al inicio de cada ciclo."""
    _CYCLE_CACHE.clear()
    _REPORT_CACHE.clear()


def _as_of_bucket_5min(as_of: str) -> str:
    try:
        dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return as_of[:16]
    minute = (dt.minute // 5) * 5
    bucket = dt.replace(minute=minute, second=0, microsecond=0)
    return bucket.astimezone(UTC).isoformat()


def _cache_key(symbol: str, as_of: str) -> str:
    return f"{symbol.upper()}:{_as_of_bucket_5min(as_of)}"


def _read_snapshot_row(
    symbol: str,
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    if not OPTIONS_GEX_SNAPSHOTS_DB.exists():
        return None
    uri = f"file:{OPTIONS_GEX_SNAPSHOTS_DB.as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=3.0, check_same_thread=False)
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT features_json, snapshot_json, as_of "
                "FROM options_gex_snapshots WHERE symbol = ? "
                "ORDER BY as_of DESC LIMIT 1",
                (symbol.upper(),),
            )
            row = cur.fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        logger.warning(
            "route1_options.db_read_failed symbol=%s error=%s",
            symbol,
            str(exc)[:120],
        )
        return None
    if row is None:
        return None
    features_raw, snapshot_raw, as_of = row
    features: dict[str, Any] = json.loads(features_raw) if features_raw else {}
    snapshot: dict[str, Any] = json.loads(snapshot_raw) if snapshot_raw else {}
    return features, snapshot, str(as_of)


def classify_trade_direction(
    features: dict[str, Any],
    *,
    fallback_tick_rule: bool = True,
) -> str:
    """Lightweight ML direction stub; XGBoost behind feature flag, else tick-rule.

    Returns: ``BUY``, ``SELL``, or ``NEUTRAL``.
    """
    if ml_direction_classifier_enabled():
        try:
            import numpy as np

            call_flow = _safe_float(features.get("call_flow")) or 0.0
            put_flow = _safe_float(features.get("put_flow")) or 0.0
            composite = _safe_float(features.get("composite_directional_signal")) or 0.0
            vec = np.array([[call_flow, put_flow, composite]], dtype=np.float64)
            # Stub weights (no trained model dependency); replace with joblib when calibrated
            weights = np.array([0.4, -0.35, 0.25])
            score = float(vec @ weights.T)
            if score > 0.05:
                return "BUY"
            if score < -0.05:
                return "SELL"
            return "NEUTRAL"
        except Exception as exc:
            logger.debug("route1_options.ml_direction_fallback error=%s", exc)

    if not fallback_tick_rule:
        return "NEUTRAL"
    call_flow = _safe_float(features.get("call_flow")) or 0.0
    put_flow = _safe_float(features.get("put_flow")) or 0.0
    if call_flow > put_flow:
        return "BUY"
    if put_flow > call_flow:
        return "SELL"
    return "NEUTRAL"


def _build_predictive_report(
    features: dict[str, Any],
    snapshot: dict[str, Any],
) -> PredictiveOptionsBundleReport | None:
    gex_levels: dict[str, Any] = snapshot.get("gex_levels") or {}
    engine_signal: dict[str, Any] = snapshot.get("engine_signal") or {}
    spot = _safe_float(snapshot.get("spot"))
    gamma_flip = _safe_float(gex_levels.get("zero_gamma_level"))
    shadow_delta_raw = _safe_float(features.get("shadow_delta_signal"))
    zero_day_pinning = _safe_float(gex_levels.get("max_pain"))
    gf_dir = _safe_float(features.get("gamma_flip_directional_signal")) or 0.0
    composite = _safe_float(features.get("composite_directional_signal")) or 0.0
    speed_instability = gf_dir != 0.0 and composite != 0.0 and gf_dir * composite < 0
    tail_risk_raw = _safe_float(features.get("tail_risk_directional_signal"))
    tail_risk_severity = _bucket_tail_risk(tail_risk_raw)
    net_gex = _safe_float(engine_signal.get("total_gex"))
    zomma_risk = (
        min(1.0, abs(net_gex) / 1_000_000_000.0)
        if net_gex is not None and spot is not None and spot > 0
        else 0.0
    )
    is_gamma_negative = spot is not None and gamma_flip is not None and spot < gamma_flip
    return PredictiveOptionsBundleReport(
        gamma_flip_level=float(gamma_flip) if gamma_flip is not None else 0.0,
        is_gamma_negative_regime=bool(is_gamma_negative),
        shadow_delta_imbalance=(float(shadow_delta_raw) if shadow_delta_raw is not None else 0.0),
        zero_day_pinning_strike=(float(zero_day_pinning) if zero_day_pinning is not None else 0.0),
        speed_instability_warning=bool(speed_instability),
        tail_risk_severity=str(tail_risk_severity or "LOW"),
        zomma_risk_score=float(zomma_risk),
        pinning_probability=0.0,
    )


def _load_walls(symbol: str) -> tuple[float | None, float | None]:
    try:
        from backend.quant_engine.engines.options.chain_analytics_history import (
            OptionsChainAnalyticsHistoryStore,
        )

        snap = OptionsChainAnalyticsHistoryStore().latest_snapshot(symbol, None)
        if snap is None:
            return None, None
        return snap.call_wall, snap.put_wall
    except Exception as exc:
        logger.debug("route1_options.walls_failed symbol=%s error=%s", symbol, exc)
        return None, None


def _load_max_pain(symbol: str) -> float | None:
    try:
        from backend.services.max_pain_history_service import read_max_pain_history

        rows = read_max_pain_history(symbol, limit=1)
        if not rows:
            return None
        return _safe_float(rows[-1].get("max_pain"))
    except Exception as exc:
        logger.debug("route1_options.max_pain_failed symbol=%s error=%s", symbol, exc)
        return None


def load_route1_options_context(symbol: str) -> Route1OptionsSnapshotContext | None:
    """Lee snapshot ~5min + walls + max-pain con caché por bucket."""
    row = _read_snapshot_row(symbol)
    if row is None:
        return None
    features, snapshot, as_of = row
    key = _cache_key(symbol, as_of)
    cached = _CYCLE_CACHE.get(key)
    if cached is not None:
        return cached

    gex_levels: dict[str, Any] = snapshot.get("gex_levels") or {}
    call_wall, put_wall = _load_walls(symbol)
    if call_wall is None:
        call_wall = _safe_float(gex_levels.get("call_wall"))
    if put_wall is None:
        put_wall = _safe_float(gex_levels.get("put_wall"))
    max_pain = _load_max_pain(symbol) or _safe_float(gex_levels.get("max_pain"))

    ctx = Route1OptionsSnapshotContext(
        symbol=symbol.upper(),
        as_of=as_of,
        available=True,
        features=features,
        snapshot=snapshot,
        call_wall=call_wall,
        put_wall=put_wall,
        max_pain=max_pain,
    )
    _CYCLE_CACHE[key] = ctx
    return ctx


async def fetch_route1_options_bundle(symbol: str) -> Route1OptionsBundle:
    """Un solo read DB por símbolo; report + contexto para gate y replay."""
    ctx = load_route1_options_context(symbol)
    if ctx is None:
        return Route1OptionsBundle(report=None, context=None)

    key = _cache_key(symbol, ctx.as_of)
    if key in _REPORT_CACHE:
        return Route1OptionsBundle(report=_REPORT_CACHE[key], context=ctx)

    report = _build_predictive_report(ctx.features, ctx.snapshot)
    _REPORT_CACHE[key] = report
    return Route1OptionsBundle(report=report, context=ctx)


__all__ = [
    "Route1OptionsBundle",
    "classify_trade_direction",
    "clear_route1_options_cache",
    "fetch_route1_options_bundle",
    "load_route1_options_context",
]
