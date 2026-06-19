from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

import pandas as pd

# ruff: noqa: F403, F405


logger = logging.getLogger(__name__)
from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB
from backend.services.options_combiner_service import run_options_combiner
from backend.services.research.research_types import *
from backend.services.research.research_types import (
    _bucket_tail_risk,
    _safe_float,
    _unavailable_desk_status,
)


def _build_gamma_flip_chain_dataframe_local(chain: list[dict[str, Any]], spot: float) -> Any:
    """Long-format chain (one row per call/put leg) for GammaFlipEngine."""

    rows: list[dict[str, Any]] = []
    for r in chain:
        if not isinstance(r, dict):
            continue
        coi = int(r.get("call_oi") or 0)
        poi = int(r.get("put_oi") or 0)
        call_gamma = r.get("call_gamma")
        put_gamma = r.get("put_gamma")
        strike = _safe_float(r.get("strike"))
        if strike is None:
            continue
        if coi > 0 and call_gamma is not None:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "call",
                    "gamma": float(call_gamma),
                    "open_interest": coi,
                    "current_spot": float(spot),
                }
            )
        if poi > 0 and put_gamma is not None:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "put",
                    "gamma": float(put_gamma),
                    "open_interest": poi,
                    "current_spot": float(spot),
                }
            )
    return pd.DataFrame(rows)


def _build_shadow_delta_portfolio_df_local(
    chain: list[dict[str, Any]], spot: float, dte_years: float, r_rate: float
) -> Any:
    """Long-format rows (CALL/PUT per strike) for ShadowDeltaEngine from OptionStrikeRow chain."""

    rows: list[dict[str, Any]] = []
    for row in chain:
        if not isinstance(row, dict):
            continue
        strike = _safe_float(row.get("strike"))
        if strike is None:
            continue
        coi = float(row.get("call_oi") or 0)
        poi = float(row.get("put_oi") or 0)
        civ = row.get("call_iv")
        piv = row.get("put_iv")
        if coi > 0 and civ is not None and float(civ) > 1e-6:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "CALL",
                    "iv": float(civ),
                    "open_interest": int(coi),
                    "quantity": float(coi),
                    "expiry": float(dte_years),
                    "r": float(r_rate),
                }
            )
        if poi > 0 and piv is not None and float(piv) > 1e-6:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "PUT",
                    "iv": float(piv),
                    "open_interest": int(poi),
                    "quantity": float(poi),
                    "expiry": float(dte_years),
                    "r": float(r_rate),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)


async def _fetch_options_gex_desk(
    underlying_symbol: str,
    market_type: str,
    *,
    options_snapshot: dict[str, Any] | None = None,
    klines: tuple[dict[str, Any], ...] | None = None,
) -> OptionsGexDeskState:
    """Read the latest Options/GEX snapshot from ``options_gex_snapshots.sqlite3``.

    Connects to the SQLite database in **read-only** mode (``mode=ro`` URI) so
    it never blocks or corrupts the write path.  All failures — missing file,
    locked DB, missing table, JSON parse errors — are caught and the desk
    degrades to ``unavailable``.

    Field mapping from ``options_gex_snapshots``
    -------------------------------------------
    Column          : ``features_json`` (JSON blob) and ``snapshot_json``
    gamma_flip      : ``snapshot_json.gex_levels.zero_gamma_level``
                       (the GEX zero-gamma level IS the gamma flip in this pipeline)
    is_gamma_neg    : derived — ``spot < gamma_flip_level``
    shadow_delta    : ``features_json.shadow_delta_signal`` (normalised [-1, 1])
    tail_risk       : bucketed from ``features_json.tail_risk_directional_signal``
    speed_warning   : ``True`` when ``features_json.composite_directional_signal``
                      has flipped sign vs ``features_json.gamma_flip_directional_signal``
                      (proxy until Speed engine writes its own column)
    zomma_risk      : normalised from engine_signal.total_gex / spot — proxy
                      until the Zomma engine exposes its own field directly.
    """
    source_tag = "options_gex_snapshots_db"

    # Crypto perps do not have equity options chains — skip immediately.
    if market_type not in ("stock_perp", "stock_index_perp"):
        return OptionsGexDeskState(
            desk_status=_unavailable_desk_status(
                source=source_tag,
                reason=REASON_MARKET_TYPE_EXCLUDED,
            )
        )

    t0 = time.monotonic()
    try:
        db_path = OPTIONS_GEX_SNAPSHOTS_DB
        if not db_path.exists():
            return OptionsGexDeskState(
                desk_status=_unavailable_desk_status(
                    source=source_tag,
                    reason="db_file_not_found",
                )
            )

        # Open in read-only mode via URI to avoid any write-lock contention.
        uri = f"file:{db_path.as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=3.0, check_same_thread=False)
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT features_json, snapshot_json, as_of, data_quality_score "
                "FROM options_gex_snapshots "
                "WHERE symbol = ? "
                "ORDER BY as_of DESC LIMIT 1",
                (underlying_symbol,),
            )
            row = cur.fetchone()
        finally:
            con.close()

        if row is None:
            return OptionsGexDeskState(
                desk_status=_unavailable_desk_status(
                    source=source_tag,
                    reason=f"no_snapshot_for_{underlying_symbol}",
                )
            )

        features_raw, snapshot_raw, as_of, db_quality = row
        features: dict[str, Any] = json.loads(features_raw) if features_raw else {}
        snapshot: dict[str, Any] = json.loads(snapshot_raw) if snapshot_raw else {}

        # ── Extract fields ──────────────────────────────────────────────
        gex_levels: dict[str, Any] = snapshot.get("gex_levels") or {}
        engine_signal: dict[str, Any] = snapshot.get("engine_signal") or {}
        iv_surface: dict[str, Any] = snapshot.get("iv_surface") or {}
        snapshot.get("confluence") or {}

        spot = _safe_float(snapshot.get("spot"))

        # Default fallbacks from DB
        gamma_flip = _safe_float(gex_levels.get("zero_gamma_level"))
        shadow_delta_raw = _safe_float(features.get("shadow_delta_signal"))
        zero_day_pinning = _safe_float(gex_levels.get("max_pain"))
        pinning_prob = None

        speed_instability = False
        gf_dir = _safe_float(features.get("gamma_flip_directional_signal")) or 0.0
        composite = _safe_float(features.get("composite_directional_signal")) or 0.0
        if gf_dir != 0.0 and composite != 0.0:
            speed_instability = gf_dir * composite < 0

        tail_risk_raw = _safe_float(features.get("tail_risk_directional_signal"))
        tail_risk_severity = _bucket_tail_risk(tail_risk_raw)

        net_gex = _safe_float(engine_signal.get("total_gex"))
        if net_gex is not None and spot is not None and spot > 0:
            zomma_risk = min(1.0, abs(net_gex) / 1_000_000_000.0)
        else:
            zomma_risk = None

        # ── Run Quantitative Engines Dynamically ──────────────────────────
        predictive_report = None
        combiner_payload = None
        combiner_result = run_options_combiner(
            underlying_symbol,
            snapshot=snapshot,
            klines=klines,
            spot=spot,
        )
        combiner_payload = combiner_result.get("combiner")
        if combiner_result.get("ok"):
            from backend.domain.probabilistic_models import PredictiveOptionsBundleReport

            gamma_neg = spot is not None and gamma_flip is not None and spot < gamma_flip
            combiner_data = combiner_result.get("combiner") or {}
            entry_allowed = bool(combiner_data.get("entry_allowed", True))
            ndde_val = combiner_result.get("ndde") or 0.0
            predictive_report = PredictiveOptionsBundleReport(
                gamma_flip_level=float(gamma_flip) if gamma_flip is not None else 0.0,
                is_gamma_negative_regime=bool(gamma_neg),
                shadow_delta_imbalance=float(ndde_val),
                zero_day_pinning_strike=(
                    float(zero_day_pinning) if zero_day_pinning is not None else 0.0
                ),
                speed_instability_warning=not entry_allowed,
                tail_risk_severity=str(tail_risk_severity or "LOW"),
                zomma_risk_score=float(zomma_risk) if zomma_risk is not None else 0.0,
                pinning_probability=(float(pinning_prob) if pinning_prob is not None else 0.0),
            )

        is_gamma_negative = spot is not None and gamma_flip is not None and spot < gamma_flip

        # Supplementary fields.
        atm_iv = _safe_float(iv_surface.get("atm_iv"))
        call_wall = _safe_float(gex_levels.get("call_wall"))
        put_wall = _safe_float(gex_levels.get("put_wall"))
        net_gex_total = _safe_float(gex_levels.get("net_gex_total"))
        dealer_bias = str(gex_levels.get("dealer_bias") or "NEUTRAL")

        quality_score = _safe_float(db_quality) if db_quality is not None else None
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        logger.debug(
            "_fetch_options_gex_desk | symbol=%s as_of=%s gamma_flip=%.4f "
            "shadow_delta=%s tail=%s speed=%s zomma=%s latency=%.0fms",
            underlying_symbol,
            as_of,
            gamma_flip or 0.0,
            shadow_delta_raw,
            tail_risk_severity,
            speed_instability,
            zomma_risk,
            latency_ms,
        )

        # Build the real PredictiveOptionsBundleReport
        if predictive_report is None:
            # Build the real PredictiveOptionsBundleReport
            from backend.domain.probabilistic_models import PredictiveOptionsBundleReport

            predictive_report = PredictiveOptionsBundleReport(
                gamma_flip_level=float(gamma_flip) if gamma_flip is not None else 0.0,
                is_gamma_negative_regime=bool(is_gamma_negative),
                shadow_delta_imbalance=(
                    float(shadow_delta_raw) if shadow_delta_raw is not None else 0.0
                ),
                zero_day_pinning_strike=(
                    float(zero_day_pinning) if zero_day_pinning is not None else 0.0
                ),
                speed_instability_warning=bool(speed_instability),
                tail_risk_severity=str(tail_risk_severity or "LOW"),
                zomma_risk_score=float(zomma_risk) if zomma_risk is not None else 0.0,
                pinning_probability=float(pinning_prob) if pinning_prob is not None else 0.0,
            )

        return OptionsGexDeskState(
            desk_status=DeskReadStatus(
                status="available",
                source=source_tag,
                reason=None,
                quality_score=quality_score,
                latency_ms=latency_ms,
                captured_at=str(as_of),
            ),
            gamma_flip_level=gamma_flip,
            is_gamma_negative_regime=is_gamma_negative,
            shadow_delta_imbalance=shadow_delta_raw,
            zero_day_pinning_strike=zero_day_pinning,
            speed_instability_warning=speed_instability,
            tail_risk_severity=tail_risk_severity,
            zomma_risk_score=zomma_risk,
            atm_iv=atm_iv,
            call_wall=call_wall,
            put_wall=put_wall,
            net_gex_total=net_gex_total,
            dealer_bias=dealer_bias,
            predictive_report=predictive_report,
            combiner=combiner_payload,
        )

    except Exception as exc:
        reason = f"{REASON_DESK_FETCH_FAILED}:{type(exc).__name__}:{str(exc)[:120]}"
        logger.warning(
            "_fetch_options_gex_desk.failed symbol=%s error=%s",
            underlying_symbol,
            reason,
        )
        return OptionsGexDeskState(
            desk_status=_unavailable_desk_status(
                source=source_tag,
                reason=reason,
            )
        )
