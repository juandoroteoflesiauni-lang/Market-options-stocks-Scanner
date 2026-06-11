"""Ensamblado de ThesisV2: opciones (snapshot), técnico, fundamental, probabilístico + agentes LLM."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np
import pandas as pd

from backend.config.logger_setup import get_logger
from backend.domain.thesis_v2 import (
    InstitutionalReport,
    ReportMetric,
    ReportSection,
    ThesisBlock,
    ThesisV2,
)
from backend.layer_1_data.fetchers.fmp_client import FMPClient
from backend.layer_3_specialists.fundamentales.service import (
    build_fundamental_thesis_block_from_snapshots,
)
from backend.layer_3_specialists.gex_opciones.service import (
    build_options_thesis_block_from_snapshot,
)
from backend.layer_3_specialists.ia_probabilistico.domain.probabilistic_models import JumpRisk
from backend.layer_3_specialists.ia_probabilistico.engines.probabilistic_engine import (
    calculate_kelly_sizing,
    calibrate_heston_vov,
    compute_etv,
    estimate_mjd_params,
    estimate_payoff_ratio,
    fit_gpd,
    run_particle_filter,
)
from backend.layer_3_specialists.tecnico.service import build_technical_thesis_block_from_ohlcv
from backend.layer_4_orchestration.ai_core.agent_manager import AgentManager
from backend.services.ai_ready_payload import AIReadyPayloadEngine
from backend.services.thesis_domain_narratives import (
    DomainNarratives,
    get_risk_free_for_options_snapshot,
    run_domain_narratives_and_multimodal,
)

if TYPE_CHECKING:
    from backend.layer_3_specialists.ia_probabilistico.engines.multimodal_predictive import (
        MultimodalPredictiveEngine,
    )
    from backend.layer_3_specialists.ia_probabilistico.engines.sentiment_engine import (
        SentimentEngine,
    )

logger = get_logger(__name__)


class _SnapshotRepositoryLike(Protocol):
    def save(self: _SnapshotRepositoryLike, snapshot: object) -> object | None: ...


class _SnapshotServiceLike(Protocol):
    def generate_snapshot(
        self: _SnapshotServiceLike,
        thesis: ThesisV2,
        symbol: str,
        horizon: str,
        market: str,
        inputs: dict[str, Any],
    ) -> object: ...


# ─── Feedback store (persisted to disk, keyed by symbol) ────────────────────


def _feedback_store_path() -> Path:
    """Ruta del archivo de persistencia. Configurable via FEEDBACK_STORE_PATH."""
    raw = (os.environ.get("FEEDBACK_STORE_PATH", "") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".quantum_feedback.json"


def _load_feedback_store() -> dict[str, list[dict]]:
    """Carga el feedback store desde disco. Retorna dict vacío si no existe o falla."""
    try:
        p = _feedback_store_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, list)}
    except Exception:
        pass
    return {}


def _save_feedback_store(store: dict[str, list[dict]]) -> None:
    """Persiste el feedback store a disco. Falla silenciosamente."""
    try:
        p = _feedback_store_path()
        p.write_text(json.dumps(store, default=str, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_FEEDBACK_STORE: dict[str, list[dict]] = _load_feedback_store()
_FEEDBACK_STORE_MAX_RECORDS: int = 50


def _normalize_ai_signal(raw_prob: float) -> float:
    if raw_prob >= 0.33:
        return 0.5 + 0.5 * (raw_prob - 0.33) / 0.67
    return 0.5 * (raw_prob / 0.33)


def _agents_env_enabled() -> bool:
    return (os.environ.get("THESIS_ENABLE_AGENTS", "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _domain_narratives_enabled() -> bool:
    """Narrativas por dominio + orquestador (por defecto activo si hay agentes)."""
    if not _agents_env_enabled():
        return False
    v = (os.environ.get("THESIS_DOMAIN_NARRATIVES", "1") or "").strip().lower()
    return v not in ("0", "false", "no", "off")


async def _fetch_options_snapshot(sym: str) -> object:
    from backend.routers.options_router import options_snapshot_service

    r = get_risk_free_for_options_snapshot()
    return await options_snapshot_service(sym, None, r)


async def _run_catalyst_nlp_safe(
    sym: str,
    fmp_client: FMPClient,
) -> object | None:
    """Run CatalystNLPEngine with a 12-second timeout; return None on failure."""
    try:
        from backend.layer_3_specialists.ia_probabilistico.engines.catalyst_nlp_engine import (
            CatalystNLPEngine,
        )

        engine = CatalystNLPEngine()
        return await asyncio.wait_for(
            engine.analyze(sym, fmp_client),
            timeout=12.0,
        )
    except Exception as exc:
        logger.warning("CatalystNLP failed for %s: %s", sym, exc)
        return None


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
        if np.isfinite(out):
            return out
    except (TypeError, ValueError):
        pass
    return default


def _options_snapshot_to_gamma_flip_df(options_snapshot: object) -> pd.DataFrame:
    """Convert OptionsSnapshotResponse-like data into GammaFlipEngine long rows."""
    spot = _as_float(getattr(options_snapshot, "spot", None))
    rows: list[dict[str, float | str]] = []
    for row in list(getattr(options_snapshot, "chain", []) or []):
        strike = _as_float(getattr(row, "strike", None))
        if strike <= 0 or spot <= 0:
            continue
        call_oi = _as_float(getattr(row, "call_oi", None))
        call_gamma = getattr(row, "call_gamma", None)
        if call_oi > 0 and call_gamma is not None:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "call",
                    "gamma": _as_float(call_gamma),
                    "open_interest": call_oi,
                    "current_spot": spot,
                }
            )
        put_oi = _as_float(getattr(row, "put_oi", None))
        put_gamma = getattr(row, "put_gamma", None)
        if put_oi > 0 and put_gamma is not None:
            rows.append(
                {
                    "strike": strike,
                    "option_type": "put",
                    "gamma": _as_float(put_gamma),
                    "open_interest": put_oi,
                    "current_spot": spot,
                }
            )
    return pd.DataFrame(
        rows,
        columns=["strike", "option_type", "gamma", "open_interest", "current_spot"],
    )


def _options_snapshot_to_term_structure_df(options_snapshot: object) -> pd.DataFrame:
    """Extract one ATM IV observation per expiry for VolatilityTermStructureEngine."""
    spot = _as_float(getattr(options_snapshot, "spot", None))
    as_of = str(getattr(options_snapshot, "as_of", "") or "")
    snapshot_date = as_of[:10] if len(as_of) >= 10 else datetime.now().strftime("%Y-%m-%d")
    grouped: dict[str, list[object]] = {}

    surface = getattr(getattr(options_snapshot, "iv_surface", None), "surface", None)
    if surface:
        for point in surface:
            grouped.setdefault(str(getattr(point, "expiration", "")), []).append(point)
    else:
        for row in list(getattr(options_snapshot, "chain", []) or []):
            grouped.setdefault(str(getattr(row, "expiration", "")), []).append(row)

    rows: list[dict[str, float | str]] = []
    for expiry, points in grouped.items():
        if not expiry or not points:
            continue
        closest = min(points, key=lambda item: abs(_as_float(getattr(item, "strike", None)) - spot))
        dte = _as_float(getattr(closest, "dte", None), default=-1.0)
        if dte <= 0:
            try:
                exp_date = datetime.strptime(expiry[:10], "%Y-%m-%d")
                snap_date = datetime.strptime(snapshot_date, "%Y-%m-%d")
                dte = float((exp_date - snap_date).days)
            except ValueError:
                dte = -1.0
        if dte <= 0:
            continue

        call_iv = _as_float(getattr(closest, "call_iv", None))
        put_iv = _as_float(getattr(closest, "put_iv", None))
        if call_iv > 0 and put_iv > 0:
            iv_atm = (call_iv + put_iv) / 2.0
        else:
            iv_atm = call_iv if call_iv > 0 else put_iv
        if iv_atm > 0:
            rows.append({"snapshot_date": snapshot_date, "dte": dte, "iv_atm": iv_atm})

    return pd.DataFrame(rows, columns=["snapshot_date", "dte", "iv_atm"])


def _options_snapshot_to_squeeze_inputs(
    sym: str,
    df: pd.DataFrame,
    options_snapshot: object,
) -> tuple[object, object]:
    """Build SqueezeIgnitionEngine dataclasses from OHLCV + options snapshot."""
    from backend.layer_3_specialists.ia_probabilistico.engines.squeeze_engine import (
        OptionChainData,
        UnderlyingData,
    )

    close = pd.to_numeric(df.get("close", pd.Series(dtype=float)), errors="coerce").dropna()
    volume = pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce").dropna()
    fallback_spot = _as_float(close.iloc[-1], 0.0) if len(close) else 0.0
    spot = _as_float(getattr(options_snapshot, "spot", None), fallback_spot)
    prev_spot = _as_float(close.iloc[-2], spot) if len(close) >= 2 else spot
    last_volume = _as_float(volume.iloc[-1], 0.0) if len(volume) else 0.0
    volume_sma = _as_float(volume.tail(20).mean(), last_volume) if len(volume) else last_volume

    call_volume = 0.0
    put_volume = 0.0
    call_oi = 0.0
    for row in list(getattr(options_snapshot, "chain", []) or []):
        call_volume += _as_float(getattr(row, "call_volume", None))
        put_volume += _as_float(getattr(row, "put_volume", None))
        call_oi += _as_float(getattr(row, "call_oi", None))
    pcr = put_volume / call_volume if call_volume > 0 else 0.0

    gex_levels = getattr(options_snapshot, "gex_levels", None)
    engine_signal = getattr(options_snapshot, "engine_signal", {}) or {}
    dealer_gamma = _as_float(
        getattr(gex_levels, "net_gex_total", None),
        _as_float(engine_signal.get("total_gex") if isinstance(engine_signal, dict) else None),
    )
    call_wall = _as_float(getattr(gex_levels, "call_wall", None), spot * 1.05 if spot else 0.0)
    gamma_zero = _as_float(getattr(gex_levels, "zero_gamma_level", None), spot)

    return (
        UnderlyingData(
            ticker=sym,
            spot_price=spot,
            prev_spot_price=prev_spot,
            volume=last_volume,
            volume_sma_20=volume_sma if volume_sma > 0 else max(last_volume, 1.0),
            short_interest_ratio=0.0,
            days_to_cover=0.0,
        ),
        OptionChainData(
            call_volume=call_volume,
            call_volume_sma_20=max(call_volume, 1.0),
            call_open_interest=call_oi,
            put_call_ratio_volume=pcr,
            dealer_net_gamma=dealer_gamma,
            call_wall_level=call_wall,
            gamma_zero_level=gamma_zero,
        ),
    )


def _serialize_item(item: object) -> object:
    if item is None:
        return None
    if hasattr(item, "model_dump"):
        try:
            return item.model_dump(mode="json")  # type: ignore[attr-defined]
        except TypeError:
            return item.model_dump()  # type: ignore[attr-defined]
    if isinstance(item, list):
        return [_serialize_item(value) for value in item]
    if isinstance(item, dict):
        return {str(key): _serialize_item(value) for key, value in item.items()}
    return item


async def _safe_fetch(name: str, awaitable: Awaitable[object]) -> tuple[str, object | None]:
    try:
        return name, await awaitable
    except Exception as exc:
        logger.warning("FMP enrichment fetch %s failed: %s", name, str(exc)[:180])
        return name, None


async def _fetch_fundamental_enrichment(sym: str, fmp_client: FMPClient) -> dict[str, object]:
    today = datetime.now().date()
    earnings_to = today + timedelta(days=120)
    fetches = await asyncio.gather(
        _safe_fetch(
            "income_statements",
            fmp_client.get_income_statements(sym, limit=4, period="quarter"),
        ),
        _safe_fetch(
            "balance_sheets",
            fmp_client.get_balance_sheets(sym, limit=4, period="quarter"),
        ),
        _safe_fetch(
            "cash_flow_statements",
            fmp_client.get_cash_flow_statements(sym, limit=4, period="quarter"),
        ),
        _safe_fetch("key_metrics_ttm", fmp_client.get_key_metrics_ttm(sym)),
        _safe_fetch("earnings_surprises", fmp_client.get_earnings_surprises(sym)),
        _safe_fetch("analyst_estimates", fmp_client.get_analyst_estimates(sym, limit=6)),
        _safe_fetch("price_target_consensus", fmp_client.get_price_target_consensus(sym)),
        _safe_fetch("stock_recommendations", fmp_client.get_stock_recommendations(sym)),
        _safe_fetch("insider_trades", fmp_client.get_insider_trades(sym, limit=20)),
        _safe_fetch("short_interest", fmp_client.get_short_interest(sym)),
        _safe_fetch(
            "institutional_ownership_history",
            fmp_client.get_institutional_ownership_history(sym),
        ),
        _safe_fetch("etf_exposure", fmp_client.get_etf_exposure(sym)),
        _safe_fetch("financial_scores", fmp_client.get_financial_scores(sym)),
        _safe_fetch(
            "earnings_calendar",
            fmp_client.get_earnings_calendar(today.isoformat(), earnings_to.isoformat()),
        ),
    )
    enrichment = {name: _serialize_item(value) for name, value in fetches}
    enrichment["income_statements_4q"] = enrichment.get("income_statements")
    enrichment["balance_sheets_4q"] = enrichment.get("balance_sheets")
    enrichment["cash_flow_statements_4q"] = enrichment.get("cash_flow_statements")
    return enrichment


async def _fetch_macro_context(sym: str, fmp_client: FMPClient) -> dict[str, object]:
    today = datetime.now().date()
    history_from = today - timedelta(days=365)
    events_to = today + timedelta(days=60)
    fetches = await asyncio.gather(
        _safe_fetch(
            "treasury_rates",
            fmp_client.get_treasury_rates(history_from.isoformat(), today.isoformat()),
        ),
        _safe_fetch(
            "economic_calendar",
            fmp_client.get_economic_calendar(today.isoformat(), events_to.isoformat()),
        ),
        _safe_fetch("stock_peers", fmp_client.get_stock_peers(sym)),
        _safe_fetch("revenue_segments_product", fmp_client.get_revenue_segments(sym, "product")),
        _safe_fetch("revenue_segments_geo", fmp_client.get_revenue_segments(sym, "geo")),
    )
    return {name: _serialize_item(value) for name, value in fetches}


async def _fetch_fred_macro_snapshot() -> dict[str, object]:
    try:
        from backend.layer_1_data.fetchers.fred_fetcher import FredFetcher

        fetcher = FredFetcher()
        snapshot = await asyncio.wait_for(fetcher.get_macro_snapshot(), timeout=10.0)
        return dict(snapshot) if isinstance(snapshot, dict) else {}
    except Exception as exc:
        logger.warning("FRED macro snapshot failed: %s", str(exc)[:180])
        return {"_error": str(exc)[:180]}


async def _fetch_latest_transcript(sym: str, fmp_client: FMPClient) -> dict[str, object]:
    try:
        transcript_list = await fmp_client.get_transcript_list(sym)
        if not transcript_list:
            return {"available": False, "reason": "no transcript list from FMP"}
        latest = max(
            transcript_list,
            key=lambda item: (
                int(getattr(item, "year", 0) or 0),
                int(getattr(item, "quarter", 0) or 0),
                str(getattr(item, "date", "") or ""),
            ),
        )
        year = int(getattr(latest, "year", 0) or 0)
        quarter = int(getattr(latest, "quarter", 0) or 0)
        if year <= 0 or quarter <= 0:
            return {"available": False, "reason": "latest transcript has invalid period"}
        transcript = await fmp_client.get_transcript(sym, year, quarter)
        content = str(getattr(transcript, "content", "") or "") if transcript else ""
        evidence_pack = AIReadyPayloadEngine().build_transcript_intelligence_pack(
            sym,
            {
                "available": bool(content),
                "symbol": sym.upper(),
                "year": year,
                "quarter": quarter,
                "date": getattr(latest, "date", None),
                "content": content,
            },
        )
        return {
            "available": bool(content),
            "symbol": sym.upper(),
            "year": year,
            "quarter": quarter,
            "date": getattr(latest, "date", None),
            "evidence_pack": evidence_pack.to_dict(),
            "raw_chars": len(content),
        }
    except Exception as exc:
        logger.warning("Latest transcript fetch failed for %s: %s", sym, str(exc)[:180])
        return {"available": False, "error": str(exc)[:180]}


async def _build_probabilistic_block(
    sym: str,
    df: pd.DataFrame,
    sentiment_score: float,
    fusion: dict[str, Any],
    options_chain: object | None = None,
    catalyst_profile: object | None = None,
) -> ThesisBlock:
    returns = df["close"].pct_change().dropna().values
    tail_res = fit_gpd(returns)
    state_res = run_particle_filter(df)
    mjd_params = estimate_mjd_params(returns)
    jump_res = JumpRisk(
        intensity=mjd_params["jump_intensity"],
        mu_j=mjd_params["mu_j"],
        sigma_j=mjd_params["sigma_j"],
        probability=mjd_params["jump_prob"],
    )
    ai_conviction_raw = float(fusion.get("conviction", 0.33))
    ai_win_prob = _normalize_ai_signal(ai_conviction_raw)
    win_prob = (0.7 * ai_win_prob) + (0.3 * state_res.pr_ordered)
    payoff_b = estimate_payoff_ratio(returns)
    kelly = calculate_kelly_sizing(win_prob, payoff_b)
    vov = calibrate_heston_vov(returns, np.full(len(returns), np.std(returns)))
    etv = compute_etv(win_prob, payoff_b, jump_res.probability, tail_res.cvar_99)

    # ── Prompt 2: CatalystNLP modulation ─────────────────────────────────────
    _catalyst_metrics: dict[str, Any] = {}
    if catalyst_profile is not None:
        try:
            jump_intensity_adj = float(getattr(catalyst_profile, "jump_intensity_adj", 1.0))
            mjd_params = {
                **mjd_params,
                "jump_intensity": mjd_params["jump_intensity"] * jump_intensity_adj,
            }
            _catalyst_metrics = {
                "catalyst_event_risk_score": float(
                    getattr(catalyst_profile, "event_risk_score", 0.0)
                ),
                "catalyst_jump_intensity_adj": jump_intensity_adj,
                "catalyst_tone": str(getattr(catalyst_profile, "tone", "NEUTRAL")),
                "catalyst_upcoming": list(getattr(catalyst_profile, "upcoming_catalysts", [])),
            }
            logger.debug(
                "CatalystNLP applied: adj=%.3f tone=%s",
                jump_intensity_adj,
                _catalyst_metrics["catalyst_tone"],
            )
        except Exception as exc:
            logger.warning("CatalystNLP modulation failed: %s", exc)

    # ── Prompt 6: FeedbackCalibration ────────────────────────────────────────
    _feedback_metrics: dict[str, Any] = {}
    try:
        from backend.layer_3_specialists.ia_probabilistico.engines.feedback_engine import (
            FeedbackCalibration,
        )

        _fb = FeedbackCalibration(storage_service=None)
        _history = _FEEDBACK_STORE.get(sym, [])
        current_price = float(df["close"].iloc[-1])
        _fb_result = _fb.calculate_model_error(_history, current_price)
        _adapted_mjd = _fb.adapt_parameters(mjd_params, _fb_result)
        mjd_params = _adapted_mjd
        _feedback_metrics = {
            "feedback_bias": _fb_result.get("bias", 0.0),
            "feedback_is_hit": _fb_result.get("is_hit"),
            "feedback_error_factor": _fb_result.get("error_factor", 1.0),
            "feedback_realized_return": _fb_result.get("realized_return"),
            "feedback_history_len": len(_history),
        }
    except Exception as exc:
        logger.warning("FeedbackCalibration failed: %s", exc)

    # Recompute JumpRisk with adapted params
    jump_res = JumpRisk(
        intensity=mjd_params.get("jump_intensity", jump_res.intensity),
        mu_j=mjd_params.get("mu_j", jump_res.mu_j),
        sigma_j=mjd_params.get("sigma_j", jump_res.sigma_j),
        probability=mjd_params.get("jump_prob", jump_res.probability),
    )
    kelly_full = kelly.full_kelly
    kelly_half = kelly.half_kelly
    win_prob_final = win_prob

    # ── FearGreedEngine ───────────────────────────────────────────────────────
    _fear_greed_metrics: dict[str, Any] = {}
    try:
        from backend.layer_3_specialists.ia_probabilistico.engines.fear_greed_engine import (
            FearGreedEngine,
        )

        _fg_engine = FearGreedEngine()
        _fg_result = _fg_engine.compute(
            symbol=sym,
            returns=returns,
            sentiment_score=sentiment_score,
            win_prob=win_prob_final,
            vov=vov,
        )
        _fear_greed_metrics = {
            "fear_greed_score": float(getattr(_fg_result, "score", 50.0)),
            "fear_greed_label": str(getattr(_fg_result, "label", "NEUTRAL")),
        }
    except Exception as exc:
        logger.warning("FearGreedEngine failed: %s", exc)

    # ── MarkovRegimeEngine ────────────────────────────────────────────────────
    _markov_metrics: dict[str, Any] = {}
    try:
        from backend.layer_3_specialists.ia_probabilistico.engines.markov_regime_engine import (
            MarkovRegimeEngine,
        )

        _mk_engine = MarkovRegimeEngine()
        _mk_result = _mk_engine.analyze(sym, df)
        _markov_metrics = {
            "markov_current_regime": str(getattr(_mk_result, "current_state", "UNKNOWN")),
            "markov_regime_prob": float(getattr(_mk_result, "state_confidence", 0.5)),
            "markov_transition_matrix": getattr(_mk_result, "transition_matrix", None),
        }
    except Exception as exc:
        logger.warning("MarkovRegimeEngine failed: %s", exc)

    # ── Options-chain-dependent engines ──────────────────────────────────────
    _options_metrics: dict[str, Any] = {}
    _cor3m_metrics: dict[str, Any] = {}
    _ml_metrics: dict[str, Any] = {}
    _factor_cal_metrics: dict[str, Any] = {}

    if options_chain is not None:
        # ── GammaFlipEngine ───────────────────────────────────────────────────
        try:
            from backend.layer_3_specialists.ia_probabilistico.engines.gamma_flip_engine import (
                GammaFlipEngine,
            )

            _gf_df = _options_snapshot_to_gamma_flip_df(options_chain)
            if len(_gf_df) >= 4:
                _iv_atm = _as_float(
                    getattr(getattr(options_chain, "iv_surface", None), "atm_iv", None), 0.22
                )
                _gf_engine = GammaFlipEngine(
                    _gf_df,
                    contract_size=100,
                    r=0.04,
                    sigma=max(0.08, min(_iv_atm, 1.5)),
                    range_pct=0.18,
                    n_points=140,
                )
                _gf_flip = _gf_engine.find_flip_point()
                _gf_regime = _gf_engine.volatility_regime()
                _options_metrics["gamma_flip_level"] = (
                    float(_gf_flip) if _gf_flip is not None else None
                )
                _options_metrics["gamma_flip_regime"] = str(_gf_regime.get("regime", "UNKNOWN"))
        except Exception as exc:
            logger.warning("GammaFlipEngine failed: %s", exc)

        # ── VolTermEngine ─────────────────────────────────────────────────────
        try:
            from backend.layer_3_specialists.ia_probabilistico.engines.vol_term_engine import (
                VolatilityTermStructureEngine,
            )

            _vt_df = _options_snapshot_to_term_structure_df(options_chain)
            if len(_vt_df) >= 2:
                _vt_engine = VolatilityTermStructureEngine()
                _vt_engine.load_option_chain(_vt_df)
                _vt_engine.build_term_structure()
                _vt_engine.compute_metrics()
                _vt_alerts = _vt_engine.generate_alerts()
                _options_metrics["vol_term_contango"] = not _vt_alerts.get(
                    "inversion_alert",
                    False,
                )
                _options_metrics["vol_term_slope"] = float(_vt_alerts.get("slope_bps", 0.0))
                _options_metrics["vol_term_regime"] = str(_vt_alerts.get("regime", "UNKNOWN"))
        except Exception as exc:
            logger.warning("VolTermEngine failed: %s", exc)

        # ── SqueezeIgnitionEngine ─────────────────────────────────────────────
        try:
            from backend.layer_3_specialists.ia_probabilistico.engines.squeeze_engine import (
                SqueezeIgnitionEngine,
            )

            _sq_engine = SqueezeIgnitionEngine(sym, verbose=False)
            _sq_underlying, _sq_options = _options_snapshot_to_squeeze_inputs(
                sym,
                df,
                options_chain,
            )
            _sq_result = _sq_engine.evaluate(_sq_underlying, _sq_options)
            _options_metrics["squeeze_ignition_signal"] = str(
                getattr(getattr(_sq_result, "signal_type", None), "value", "NEUTRAL")
            )
            _options_metrics["squeeze_ignition_state"] = str(
                getattr(getattr(_sq_result, "state", None), "name", "MONITORING")
            )
            _options_metrics["squeeze_ignition_score"] = float(
                getattr(_sq_result, "squeeze_vulnerability_score", 0.0)
            )
        except Exception as exc:
            logger.warning("SqueezeIgnitionEngine failed: %s", exc)

        # ── COR3M_Signal_Engine ───────────────────────────────────────────────
        try:
            from backend.layer_3_specialists.ia_probabilistico.engines.cor3m_engine import (
                COR3M_Signal_Engine,
            )

            _cor3m_engine = COR3M_Signal_Engine()
            # Synthetic vol proxy: rolling 21d annualized vol from df["close"]
            _close_series = df["close"].dropna()
            _ret_series = _close_series.pct_change().dropna()
            _vol_proxy = _ret_series.rolling(21).std() * np.sqrt(252)
            _vol_series = _vol_proxy.dropna()
            _cor3m_df = _cor3m_engine.run(_vol_series)
            if _cor3m_df is not None and not _cor3m_df.empty:
                _last_row = _cor3m_df.iloc[-1]
                _cor3m_metrics = {
                    "cor3m_market_state": str(_last_row.get("market_state", "NORMAL")),
                    "cor3m_signal": str(_last_row.get("signal", "NEUTRAL")),
                    "cor3m_percentile_rank": float(_last_row.get("percentile_rank", 50.0)),
                }
        except Exception as exc:
            logger.warning("COR3M_Signal_Engine failed: %s", exc)

        # ── MLOptimizer ───────────────────────────────────────────────────────
        try:
            from backend.layer_3_specialists.ia_probabilistico.engines.ml_optimizer import (
                get_ml_optimizer,
            )

            _ml_opt = get_ml_optimizer()
            _close = df["close"].dropna()
            _rets = _close.pct_change().dropna()
            if len(_rets) >= 21:
                _feature_vec = {
                    "momentum_5d": float(_rets.tail(5).mean()),
                    "momentum_21d": float(_rets.tail(21).mean()),
                    "vol_5d": float(_rets.tail(5).std()),
                    "vol_21d": float(_rets.tail(21).std()),
                    "skew_21d": float(_rets.tail(21).skew()),
                    "kurt_21d": float(_rets.tail(21).kurt()),
                    "rel_vol": float(_rets.tail(5).std() / (_rets.tail(21).std() + 1e-8)),
                }
                _ml_opt.add_sample(_feature_vec, win_prob_final)
                _ml_result = _ml_opt.get_optimal_weights(method="auto")
                _fi = _ml_result.feature_importance
                _ml_top = max(_fi, key=_fi.get) if isinstance(_fi, dict) and len(_fi) > 0 else None
                _ml_metrics = {
                    "ml_method": _ml_result.method,
                    "ml_score_r2": float(_ml_result.score),
                    "ml_weights": _ml_result.weights,
                    "ml_top_feature": _ml_top,
                }
        except Exception as exc:
            logger.warning("MLOptimizer failed: %s", exc)

        # ── FactorCalibrationEngine ───────────────────────────────────────────
        try:
            from backend.layer_3_specialists.ia_probabilistico.engines.factor_calibration import (
                get_calibration_engine,
            )

            _cal_engine = get_calibration_engine()
            _factor_obs: dict[str, float] = {}
            if _fear_greed_metrics.get("fear_greed_score") is not None:
                _factor_obs["fear_greed"] = float(_fear_greed_metrics["fear_greed_score"]) / 100.0
            if _cor3m_metrics.get("cor3m_percentile_rank") is not None:
                _factor_obs["cor3m_rank"] = float(_cor3m_metrics["cor3m_percentile_rank"]) / 100.0
            if _options_metrics.get("squeeze_ignition_score") is not None:
                _factor_obs["squeeze_score"] = float(_options_metrics["squeeze_ignition_score"])
            _factor_obs["win_prob"] = float(win_prob_final)
            _factor_obs["jump_prob"] = float(jump_res.probability)
            _factor_obs["vov"] = float(vov)
            _factor_obs["sentiment"] = float(sentiment_score)
            _cal_engine.add_observation(_factor_obs, win_prob_final)
            _cal_report = _cal_engine.get_calibration_report()
            _factor_cal_metrics = {
                "factor_cal_observations": _cal_report.get("observation_count", 0),
                "factor_cal_pca_weights": _cal_report.get("pca_weights", {}),
                "factor_cal_recommendations": _cal_report.get("recommendations", []),
            }
        except Exception as exc:
            logger.warning("FactorCalibrationEngine failed: %s", exc)

    else:
        logger.debug(
            "No options_chain for %s — skipping GammaFlip/VolTerm/Squeeze/COR3M/ML/FactorCal",
            sym,
        )
        _options_metrics["options_chain_available"] = False

    try:
        _store_record: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "context_price": float(df["close"].iloc[-1]),
            "kelly_full": kelly_full,
            "win_prob": win_prob_final,
            "raw_json": {
                "context_price": float(df["close"].iloc[-1]),
                "mjd_params": mjd_params,
            },
        }
        _FEEDBACK_STORE.setdefault(sym, []).insert(0, _store_record)
        if len(_FEEDBACK_STORE[sym]) > _FEEDBACK_STORE_MAX_RECORDS:
            _FEEDBACK_STORE[sym] = _FEEDBACK_STORE[sym][:_FEEDBACK_STORE_MAX_RECORDS]
        _save_feedback_store(_FEEDBACK_STORE)
    except Exception as exc:
        logger.warning("Failed to save to _FEEDBACK_STORE: %s", exc)

    # ── Assemble final metrics dict ───────────────────────────────────────────
    metrics: dict[str, Any] = {
        "cvar_99": tail_res.cvar_99,
        "var_99": tail_res.var_99,
        "jump_probability": jump_res.probability,
        "pr_ordered_regime": state_res.pr_ordered,
        "trend_strength": state_res.trend_strength,
        "heston_vov": vov,
        "etv": etv,
        "kelly_full": kelly_full,
        "kelly_half": kelly_half,
        "win_prob_fused": win_prob_final,
        "sentiment_input": sentiment_score,
        "gate_veto": not (state_res.pr_ordered > 0.55 and jump_res.probability < 0.05),
        **_catalyst_metrics,
        **_fear_greed_metrics,
        **_markov_metrics,
        **_options_metrics,
        **_cor3m_metrics,
        **_ml_metrics,
        **_factor_cal_metrics,
        "feedback_calibration": _feedback_metrics,
    }
    conf = min(1.0, max(0.2, 0.5 + 0.5 * float(state_res.pr_ordered)))
    return ThesisBlock(
        metrics=metrics,
        source="INTERNAL_OHLCV_MULTIMODAL",
        limitations=[
            "EVT / régimen / Kelly fusionados con motor multimodal; revisar bloque Opciones para gamma.",
        ],
        confidence=conf,
    )


async def _run_llm_pipeline(contexto: str) -> tuple[ThesisBlock, ThesisBlock]:
    """AgentManager.orquestar_analisis — modo legacy."""
    try:
        manager = AgentManager()
        out = await asyncio.wait_for(
            manager.orquestar_analisis(contexto),
            timeout=float(os.environ.get("THESIS_AGENTS_TIMEOUT_SEC", "120")),
        )
    except (TimeoutError, asyncio.CancelledError) as e:
        lim = f"Agent pipeline timed out or cancelled: {e!s}"
        ub = ThesisBlock(
            metrics={},
            source="UNAVAILABLE",
            limitations=[lim],
            confidence=0.0,
        )
        return ub, ub
    except Exception as e:
        lim = f"Agent pipeline failed: {e!s}"
        ub = ThesisBlock(
            metrics={},
            source="UNAVAILABLE",
            limitations=[lim],
            confidence=0.0,
        )
        return ub, ub

    agents_metrics = {k: (v[:800] + ("…" if len(v) > 800 else "")) for k, v in out.items()}
    orch = out.get("orchestrator", "")
    agents_block = ThesisBlock(
        metrics=agents_metrics,
        source="LLM_ORCHESTRATION",
        limitations=[
            "Full legacy orchestration (orquestar_analisis). Not investment advice.",
        ],
        confidence=0.7,
    )
    ejecutivo_block = ThesisBlock(
        metrics={
            "orchestrator_summary": orch[:4000] + ("…" if len(orch) > 4000 else ""),
            "orchestrator_chars": len(orch),
        },
        source="LLM_ORCHESTRATION",
        institutional_narrative=orch,
        narrative_agent="orchestrator",
        limitations=["Synthesized by orchestrator agent from specialist outputs."],
        confidence=0.72,
    )
    return agents_block, ejecutivo_block


def _unavailable_agents(reason: str) -> tuple[ThesisBlock, ThesisBlock]:
    b = ThesisBlock(
        metrics={},
        source="UNAVAILABLE",
        limitations=[reason],
        confidence=0.0,
    )
    return b, b


def _build_pillar_state_adapter(
    sym: str,
    tech_block: ThesisBlock,
    opt_block: ThesisBlock,
    fund_block: ThesisBlock,
    prob_block: ThesisBlock,
    macro_block: ThesisBlock | None = None,
) -> SimpleNamespace:
    """
    Translate ThesisBlock.metrics dicts into a SimpleNamespace that duck-types
    QuantumState so PillarScorer can consume it without a real QuantumState object.
    """
    tech_m = tech_block.metrics if isinstance(tech_block.metrics, dict) else {}
    opt_m = opt_block.metrics if isinstance(opt_block.metrics, dict) else {}
    fund_m = fund_block.metrics if isinstance(fund_block.metrics, dict) else {}
    prob_m = prob_block.metrics if isinstance(prob_block.metrics, dict) else {}
    macro_m = macro_block.metrics if macro_block and isinstance(macro_block.metrics, dict) else {}

    smc_result = SimpleNamespace(
        bias=tech_m.get("smc_bias") or tech_m.get("trend_regime", "NEUTRAL"),
        confidence=float(tech_m.get("smc_confidence") or 0.5),
        ob_count_active=int(tech_m.get("smc_ob_count") or 0),
        fvg_count_active=int(tech_m.get("smc_fvg_count") or 0),
        wyckoff_accumulation=False,
        structure=tech_m.get("structure_proxy_20d", {}),
        order_blocks=[],
        fvgs=[],
        bos_choch=[],
        swing_highs=[],
        swing_lows=[],
        premium_zone=tech_m.get("resistance_20d"),
        discount_zone=tech_m.get("support_20d"),
        rsi_14=tech_m.get("rsi_14"),
        atr_14=tech_m.get("atr_14"),
        ema_21=tech_m.get("ema_21"),
        ema_50=tech_m.get("ema_50"),
        realized_vol=tech_m.get("realized_vol_annualized_20d"),
        vwap=tech_m.get("vwap_approx_60d"),
        volume_ratio=tech_m.get("relative_volume_20d"),
        smc_score=tech_m.get("smc_score"),
        fractal_score=tech_m.get("fractal_score"),
        vsa_score=tech_m.get("vsa_score"),
        volume_profile_score=tech_m.get("volume_profile_score"),
    )

    vsa_result = SimpleNamespace(
        stopping_volume=bool(tech_m.get("vsa_stopping_volume", False)),
        no_supply=bool(tech_m.get("vsa_no_supply", False)),
        no_demand=bool(tech_m.get("vsa_no_demand", False)),
        selling_climax=bool(tech_m.get("vsa_selling_climax", False)),
        buy_absorption=bool(tech_m.get("vsa_buy_absorption", False)),
        rvol=float(tech_m.get("vsa_rvol") or 1.0),
        signal=str(tech_m.get("vsa_signal") or "NEUTRAL"),
        composite_score=float(tech_m.get("vsa_composite_score") or 0.0),
    )

    gex_result = SimpleNamespace(
        gex_total=opt_m.get("gex_total"),
        gamma_flip_level=opt_m.get("gamma_flip_level") or prob_m.get("gamma_flip_level"),
        gamma_flip_regime=opt_m.get("gamma_flip_regime") or prob_m.get("gamma_flip_regime"),
        put_call_ratio=opt_m.get("put_call_ratio"),
        iv_rank=opt_m.get("iv_rank"),
        skew_25delta=opt_m.get("iv_skew_25delta"),
        vol_term_contango=prob_m.get("vol_term_contango"),
        vol_term_slope=prob_m.get("vol_term_slope"),
        squeeze_signal=prob_m.get("squeeze_ignition_signal"),
        squeeze_score=prob_m.get("squeeze_ignition_score"),
        smc_confluence=opt_m.get("smc_options_confluence"),
        net_gamma=opt_m.get("net_gamma"),
        vanna_exposure=opt_m.get("vanna_exposure"),
        charm_decay=opt_m.get("charm_decay"),
    )

    sentiment_result = SimpleNamespace(
        sentiment_score=prob_m.get("sentiment_input", 0.5),
        tone=prob_m.get("catalyst_tone", "NEUTRAL"),
        event_risk_score=prob_m.get("catalyst_event_risk_score", 0.0),
        jump_intensity_adj=prob_m.get("catalyst_jump_intensity_adj", 1.0),
        fear_greed_score=prob_m.get("fear_greed_score"),
        fear_greed_label=prob_m.get("fear_greed_label"),
        cor3m_state=prob_m.get("cor3m_market_state"),
        cor3m_signal=prob_m.get("cor3m_signal"),
        cor3m_rank=prob_m.get("cor3m_percentile_rank"),
    )

    macro_result = SimpleNamespace(
        fed_funds_rate=macro_m.get("fed_funds", 5.25),
        cpi_yoy=macro_m.get("cpi_val", 3.1),
        curve_regime=prob_m.get("markov_current_regime", "UNKNOWN"),
        unemployment=macro_m.get("UNRATE"),
        yield_spread_10_2=macro_m.get("YIELD_SPREAD_10_2"),
        pce_yoy=macro_m.get("PCEPI_YOY"),
        macro_regime=macro_m.get("macro_regime", "UNKNOWN"),
    )

    _alt_lbl = fund_m.get("altman_label")
    _ben_lbl = fund_m.get("beneish_label")
    _pio_lbl = fund_m.get("piotroski_label")
    _pio_f = fund_m.get("piotroski_f_score")
    forensic_result = SimpleNamespace(
        altman_z=fund_m.get("altman_z_score"),
        piotroski_f=_pio_f,
        f_score=_pio_f,
        beneish_m=fund_m.get("beneish_m_score"),
        forensic_risk=fund_m.get("forensic_risk_label", "UNKNOWN"),
        earnings_quality=fund_m.get("earnings_quality"),
        is_distressed=(_alt_lbl == "DISTRESS" or _ben_lbl == "MANIPULATOR" or _pio_lbl == "WEAK"),
    )

    valuation_result = SimpleNamespace(
        dcf_value=fund_m.get("dcf_intrinsic_value"),
        graham_number=fund_m.get("graham_number"),
        upside_pct=fund_m.get("dcf_upside_pct"),
        pe_ratio=fund_m.get("pe_ratio_ttm"),
        price_to_book=fund_m.get("price_to_book_ratio_ttm"),
        roe=fund_m.get("roe_ttm"),
    )

    markov_result = SimpleNamespace(
        current_regime=prob_m.get("markov_current_regime", "UNKNOWN"),
        regime_probability=prob_m.get("markov_regime_prob", 0.5),
        transition_matrix=prob_m.get("markov_transition_matrix"),
        pr_ordered=prob_m.get("pr_ordered_regime", 0.5),
        trend_strength=prob_m.get("trend_strength"),
        curve_regime=prob_m.get("markov_current_regime", "UNKNOWN"),  # Alias for PillarScorer
    )

    return SimpleNamespace(
        ticker=sym,
        curve_regime=prob_m.get("markov_current_regime", "UNKNOWN"),
        smc_result=smc_result,
        vsa_result=vsa_result,
        gex_result=gex_result,
        sentiment_result=sentiment_result,
        macro_result=macro_result,
        forensic_result=forensic_result,
        valuation_result=valuation_result,
        markov_result=markov_result,
        tech_metrics=tech_m,
        opt_metrics=opt_m,
        fund_metrics=fund_m,
        prob_metrics=prob_m,
        macro_metrics=macro_m,
    )


def _run_pillar_scorer(
    sym: str,
    pillar_state: SimpleNamespace,
    has_options: bool = True,
) -> dict[str, Any]:
    """Wrap PillarScorer and return a fully serializable dict."""
    try:
        from backend.layer_3_specialists.fundamentales.pillar_scorer import PillarScorer

        scorer = PillarScorer.for_ticker(sym, has_options=has_options)
        scores = scorer.score(pillar_state)
        comp = float(scores.composite)
        conviction_label = (
            "SNIPER_LONG"
            if comp >= 7.5
            else "LONG" if comp >= 6.5 else "WATCH" if comp >= 5.0 else "CASH"
        )
        return {
            "technical_score": float(scores.technical),
            "options_score": float(scores.options),
            "news_score": float(scores.news),
            "macro_score": float(scores.macro),
            "fundamental_score": float(scores.fundamentals),
            "composite_score": comp,
            "conviction_label": conviction_label,
        }
    except Exception as exc:
        logger.warning("PillarScorer failed for %s: %s", sym, exc)
        return {
            "composite_score": 5.0,
            "conviction_label": "WATCH",
            "pillar_scorer_error": str(exc),
        }


async def assemble_thesis_v2(
    sym: str,
    df: pd.DataFrame,
    fmp_client: FMPClient,
    predictive_engine: MultimodalPredictiveEngine,
    sentiment_engine: SentimentEngine,
    *,
    fusion_res: dict[str, Any] | None = None,
    sentiment_score: float | None = None,
    agent_manager: AgentManager | None = None,
) -> tuple[ThesisV2, str | None, DomainNarratives | None]:
    """Devuelve ThesisV2 y texto multimodal del orquestador (si se generó)."""
    if sentiment_score is None:
        social_data = await fmp_client.get_social_sentiment(sym, limit=10)
        sentiment_signal = sentiment_engine.analyze_social(social_data, sym)
        sent_score = float(sentiment_signal.sentiment_score) if sentiment_signal else 0.5
        sentiment_from_social_fetch = True
    else:
        sent_score = float(sentiment_score)
        social_data = []
        sentiment_from_social_fetch = False

    sentiment_ctx: dict[str, Any] = {
        "social_sentiment": [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in social_data
        ],
        "computed_score": sent_score,
    }
    if sentiment_from_social_fetch and isinstance(social_data, list) and not social_data:
        logger.info("social_sentiment vacío para %s; sent_score forzado a 0.5 (neutral)", sym)
        sent_score = 0.5
        sentiment_ctx["computed_score"] = sent_score
        sentiment_ctx["data_availability_note"] = (
            "social_sentiment no disponible — score neutral por defecto"
        )

    if fusion_res is None:
        fusion_res = predictive_engine.run_fusion_inference(
            symbol=sym,
            ohlcv_df=df,
            sentiment_score=sent_score,
            gex_data={},
        )

    # ── Parallel: options snapshot + catalyst NLP ─────────────────────────────
    snap, catalyst_result = await asyncio.gather(
        _fetch_options_snapshot(sym),
        _run_catalyst_nlp_safe(sym, fmp_client),
    )

    _fred_block: ThesisBlock | None = None

    opt_block = build_options_thesis_block_from_snapshot(sym, snap)
    prob_block = await _build_probabilistic_block(
        sym,
        df,
        sent_score,
        fusion_res,
        options_chain=snap,
        catalyst_profile=catalyst_result,
    )
    tech_block = build_technical_thesis_block_from_ohlcv(sym, df)
    (
        profile_pair,
        ratios_pair,
        enrichment,
        macro_ctx,
        fred_macro,
        transcript_ctx,
    ) = await asyncio.gather(
        _safe_fetch("profile", fmp_client.get_profile(sym)),
        _safe_fetch("ratios_ttm", fmp_client.get_ratios_ttm(sym)),
        _fetch_fundamental_enrichment(sym, fmp_client),
        _fetch_macro_context(sym, fmp_client),
        _fetch_fred_macro_snapshot(),
        _fetch_latest_transcript(sym, fmp_client),
    )
    profile = profile_pair[1]
    ratios_ttm = ratios_pair[1]
    macro_ctx_enriched = {**macro_ctx, "fred_macro": fred_macro}
    if isinstance(fred_macro, dict) and "_error" not in fred_macro:
        _fred_block = ThesisBlock(
            metrics=fred_macro,
            source="FRED_API",
            limitations=["FRED macro data; 1h TTL cache."],
            confidence=0.95,
        )
    fund_block = build_fundamental_thesis_block_from_snapshots(
        sym,
        profile,
        ratios_ttm,
        enrichment,
    )

    # ── Prompt 4: PillarScorer ────────────────────────────────────────────────
    try:
        _pillar_state = _build_pillar_state_adapter(
            sym=sym,
            tech_block=tech_block,
            opt_block=opt_block,
            fund_block=fund_block,
            prob_block=prob_block,
            macro_block=_fred_block,
        )
        _has_options = opt_block.source != "UNAVAILABLE"
        pillar_scores = _run_pillar_scorer(sym, _pillar_state, has_options=_has_options)
        prob_block = prob_block.model_copy(
            update={"metrics": {**prob_block.metrics, "pillar_scores": pillar_scores}}
        )
    except Exception as exc:
        logger.warning("PillarScorer integration failed: %s", exc)

    multimodal_text: str | None = None
    narr: DomainNarratives | None = None
    agents_block: ThesisBlock
    ejecutivo_block: ThesisBlock

    if _agents_env_enabled() and _domain_narratives_enabled():
        narr = await run_domain_narratives_and_multimodal(
            sym,
            opt_block,
            tech_block,
            fund_block,
            prob_block,
            manager=agent_manager,
            macro_context=macro_ctx_enriched,
            transcript_context=transcript_ctx,
            quant_metrics=prob_block.metrics,
            sentiment_context=sentiment_ctx,
        )
        multimodal_text = narr.multimodal if isinstance(narr.multimodal, str) else None
        _mm = multimodal_text
        _has_mm = bool(_mm and _mm.strip())

        opt_block = opt_block.model_copy(
            update={
                "institutional_narrative": narr.opciones,
                "narrative_agent": (
                    "options_gex"
                    if isinstance(narr.opciones, str) and narr.opciones.strip()
                    else None
                ),
            }
        )
        tech_block = tech_block.model_copy(
            update={
                "institutional_narrative": narr.tecnico,
                "narrative_agent": (
                    "technical" if isinstance(narr.tecnico, str) and narr.tecnico.strip() else None
                ),
            }
        )
        fund_block = fund_block.model_copy(
            update={
                "institutional_narrative": narr.fundamental,
                "narrative_agent": (
                    "forensic"
                    if isinstance(narr.fundamental, str) and narr.fundamental.strip()
                    else None
                ),
            }
        )
        prob_block = prob_block.model_copy(
            update={
                "institutional_narrative": narr.probabilistico,
                "narrative_agent": (
                    "microstructure"
                    if isinstance(narr.probabilistico, str) and narr.probabilistico.strip()
                    else None
                ),
            }
        )

        agents_block = ThesisBlock(
            metrics={
                "pipeline": "domain_narratives_v1",
                "agents": [
                    "options_gex",
                    "technical",
                    "forensic",
                    "microstructure",
                    "orchestrator",
                ],
            },
            source="LLM_ORCHESTRATION",
            limitations=narr.errors[:8]
            or ["Domain specialists + orchestrator; see each tab for narrative."],
            confidence=0.68 if _has_mm else 0.35,
        )
        ejecutivo_block = ThesisBlock(
            metrics={
                "chars": len(_mm or ""),
                "has_multimodal": _has_mm,
            },
            source="LLM_ORCHESTRATION",
            institutional_narrative=_mm,
            narrative_agent="orchestrator",
            limitations=["Unified thesis from orchestrator over domain narratives."],
            confidence=0.78 if _has_mm else 0.25,
        )
    elif _agents_env_enabled():
        # Select specific metrics for legacy LLM pipeline
        selected_metrics = {}
        keys_to_include = [
            "cvar_99",
            "var_99",
            "kelly_full",
            "win_prob_fused",
            "gate_veto",
            "markov_regime",
            "fear_greed",
            "pillar_scores",
            "cor3m_systemic",
        ]
        for key in keys_to_include:
            if key in prob_block.metrics:
                selected_metrics[key] = prob_block.metrics[key]

        # Serialize with size limit
        metrics_str = json.dumps(selected_metrics, default=str, ensure_ascii=False)
        if len(metrics_str) > 3000:
            metrics_str = metrics_str[:2997] + "..."

        ctx = (
            f"Symbol: {sym}\n"
            f"As of: {datetime.now(tz=None).isoformat()}\n"
            f"Fusion bias context: {fusion_res}\n"
            f"Probabilistic metrics: {metrics_str}\n"
        )
        agents_block, ejecutivo_block = await _run_llm_pipeline(ctx)
        multimodal_text = ejecutivo_block.institutional_narrative
    else:
        agents_block, _ = _unavailable_agents(
            "LLM disabled. Set THESIS_ENABLE_AGENTS=1 and API keys. "
            "Optional: THESIS_DOMAIN_NARRATIVES=1 (default) for per-domain narratives + multimodal orchestrator."
        )
        ejecutivo_block = ThesisBlock(
            metrics={
                "symbol": sym,
                "fusion_conviction": fusion_res.get(
                    "conviction", fusion_res.get("fusion_conviction")
                ),
                "prob_gate_veto": prob_block.metrics.get("gate_veto"),
                "prob_kelly_full": prob_block.metrics.get("kelly_full"),
            },
            source="HEURISTIC_SYNTHESIS",
            limitations=[
                "Heuristic block without LLM; enable THESIS_ENABLE_AGENTS for institutional narratives.",
            ],
            confidence=0.4,
        )

    tv = ThesisV2(
        opciones=opt_block,
        tecnico=tech_block,
        fundamental=fund_block,
        probabilistico=prob_block,
        agentes=agents_block,
        ejecutivo=ejecutivo_block,
    )
    return tv, multimodal_text, narr


def legacy_thesis_sentence(
    sym: str,
    bias: str,
    fusion_meta: object,
    thesis_v2: ThesisV2,
) -> str:
    """Narrativa corta heurística (fallback si no hay orquestador multimodal)."""
    parts = [f"The AI engine suggests a {bias} stance on {sym}."]
    fm: dict[str, Any] = cast(dict[str, Any], fusion_meta) if isinstance(fusion_meta, dict) else {}
    if fm.get("vsa_expansion_forecast"):
        parts.append("Volume profile expansion is imminent.")
    if not fm.get("gex_gating_safe", True):
        parts.append("Warning: GEX/Vanna gating indicates unstable dealer positioning.")
    pb = thesis_v2.probabilistico.metrics
    if isinstance(pb, dict) and pb.get("gate_veto"):
        parts.append("Probabilistic gates suggest elevated tail or regime risk.")
    return " ".join(parts)


def _fmt_report_value(value: object) -> str | float | int | bool | None:
    if value is None:
        return None
    if isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return round(value, 4)
    return str(value)[:120]


def _metric(
    label: str,
    value: object,
    *,
    signal: str | None = None,
    detail: str | None = None,
) -> ReportMetric:
    return ReportMetric(label=label, value=_fmt_report_value(value), signal=signal, detail=detail)


def _first_metric(metrics: dict[str, Any], names: tuple[str, ...]) -> object | None:
    for name in names:
        if name in metrics and metrics[name] is not None:
            return metrics[name]
    return None


def _risk_state_from_blocks(thesis_v2: ThesisV2) -> str:
    pb = thesis_v2.probabilistico.metrics
    if pb.get("gate_veto") is True:
        return "VETO ACTIVE"
    if thesis_v2.ejecutivo.confidence >= 0.7:
        return "RISK ACCEPTABLE"
    if thesis_v2.ejecutivo.confidence <= 0.35:
        return "DEGRADED"
    return "WATCH"


def _report_section(
    title: str,
    block: ThesisBlock,
    *,
    subtitle: str | None = None,
    metric_names: tuple[tuple[str, tuple[str, ...]], ...] = (),
) -> ReportSection:
    metrics = [
        _metric(label, _first_metric(block.metrics, keys))
        for label, keys in metric_names
        if _first_metric(block.metrics, keys) is not None
    ]
    return ReportSection(
        title=title,
        subtitle=subtitle,
        narrative=block.institutional_narrative,
        metrics=metrics,
        bullets=[f"Fuente: {block.source}", f"Confianza: {block.confidence:.2f}"],
        limitations=block.limitations[:5],
    )


def build_institutional_report(
    sym: str,
    thesis_v2: ThesisV2,
    *,
    bias: str,
    conviction: float,
    horizon: str = "swing",
) -> InstitutionalReport:
    """Construye esquema tipo broker/quant report desde ThesisV2 sin llamar LLM."""
    opt = thesis_v2.opciones.metrics
    tech = thesis_v2.tecnico.metrics
    prob = thesis_v2.probabilistico.metrics
    risk_state = _risk_state_from_blocks(thesis_v2)
    verdict = f"{bias.upper()} - {risk_state}" if bias else risk_state
    data_sources = [
        source
        for source in (
            thesis_v2.opciones.source,
            thesis_v2.tecnico.source,
            thesis_v2.fundamental.source,
            thesis_v2.probabilistico.source,
            thesis_v2.agentes.source,
        )
        if source and source != "UNAVAILABLE"
    ]
    cover_metrics = [
        _metric("Composite Verdict", verdict, signal=bias),
        _metric(
            "Conviction",
            round(conviction, 3),
            signal="HIGH" if conviction >= 0.7 else "LOW" if conviction <= 0.35 else "MED",
        ),
        _metric("Risk State", risk_state),
        _metric(
            "Spot / Close",
            _first_metric(tech, ("close", "last_close", "price", "spot"))
            or _first_metric(opt, ("spot",)),
        ),
        _metric("Kelly Full", prob.get("kelly_full")),
        _metric("CVaR 99%", prob.get("cvar_99")),
        _metric("Gate Veto", prob.get("gate_veto")),
        _metric("GEX Regime", _first_metric(opt, ("gex_regime", "gamma_regime", "net_gex_regime"))),
    ]
    sections = [
        ReportSection(
            title="Executive Summary",
            subtitle="Sintesis del desk cuantitativo",
            narrative=thesis_v2.ejecutivo.institutional_narrative,
            metrics=cover_metrics[:4],
            limitations=thesis_v2.ejecutivo.limitations[:5],
        ),
        _report_section(
            "Market Structure - Key Levels",
            thesis_v2.tecnico,
            subtitle="Estructura tecnica, momentum y niveles de invalidacion",
            metric_names=(
                ("Trend/Bias", ("trend", "bias", "technical_bias", "smc_bias")),
                ("RSI", ("rsi", "rsi_14")),
                ("ATR", ("atr", "atr_14")),
                ("VWAP", ("vwap",)),
                ("POC", ("vol_profile_poc", "poc")),
                ("Support", ("support", "nearest_support")),
                ("Resistance", ("resistance", "nearest_resistance")),
            ),
        ),
        _report_section(
            "Gamma Exposure - Dealer Positioning",
            thesis_v2.opciones,
            subtitle="Regimen gamma, walls, max pain y volatilidad implicita",
            metric_names=(
                ("Net GEX", ("net_gex", "total_gex", "gex")),
                ("Call Wall", ("call_wall", "call_wall_strike")),
                ("Put Wall", ("put_wall", "put_wall_strike")),
                ("Max Pain", ("max_pain",)),
                ("Zero Gamma", ("zero_gamma", "gamma_flip_level")),
                ("ATM IV", ("atm_iv", "iv_atm")),
            ),
        ),
        _report_section(
            "Quant Risk - EVT / Regime / Veto",
            thesis_v2.probabilistico,
            subtitle="Riesgo de cola, filtros probabilisticos y sizing",
            metric_names=(
                ("VaR 99%", ("var_99",)),
                ("CVaR 99%", ("cvar_99",)),
                ("Kelly Full", ("kelly_full",)),
                ("Kelly Half", ("kelly_half",)),
                ("Win Prob", ("win_prob_fused",)),
                ("Regime", ("markov_current_regime",)),
                ("Gate Veto", ("gate_veto",)),
            ),
        ),
        _report_section(
            "Fundamental / Forensic Lens",
            thesis_v2.fundamental,
            subtitle="Calidad de earnings, balance, solvencia y valuacion",
            metric_names=(
                ("Revenue", ("revenue", "revenue_ttm")),
                ("Net Income", ("net_income", "net_income_ttm")),
                ("CFO", ("operating_cash_flow", "cfo")),
                ("Debt", ("debt", "total_debt")),
                ("Beneish", ("beneish_m_score", "beneish")),
                ("Altman", ("altman_z_score", "altman")),
                ("Piotroski", ("piotroski_score", "piotroski")),
            ),
        ),
        ReportSection(
            title="Backtest & Strategy Discipline",
            subtitle="Walk-forward, costos y motores especializados",
            narrative=(
                "Use `run_walk_forward_threshold_grid` en `backend.backtesting.base` con "
                "`BacktestConfig` (fee_bps + slippage_bps) para evaluar umbrales sin lookahead; "
                "`StrategyTrainer.walk_forward_thresholds` expone el mismo flujo por símbolo. "
                "Combine con `layer_5_risk` antes de escalar riesgo."
            ),
            metrics=[
                _metric("Backtest package", "backend.backtesting", signal="READY"),
                _metric("Walk-forward", "run_walk_forward_threshold_grid", signal="WF"),
                _metric(
                    "Trainer CLI", "scripts/strategy_trainer.py (--walk-forward)", signal="CLI"
                ),
            ],
            bullets=[
                "Separar hipótesis por motor y auditar degradación de datos.",
                "Registrar slippage y comisiones en todo walk-forward.",
            ],
            limitations=["Resultados históricos no garantizan desempeño futuro."],
        ),
        ReportSection(
            title="Desk Governance (Wall Street)",
            subtitle="Trazabilidad y límites",
            narrative=(
                "Toda señal incluye fuente y confianza; vetos probabilísticos y liquidez "
                "prevalecen sobre narrativa. Mantenga playbooks de riesgo alineados a "
                "políticas de compliance internas."
            ),
            metrics=[
                _metric("Risk layer", "layer_5_risk.portfolio_risk", signal="ENFORCED_HINTS"),
                _metric("Scanner risk hints", "Kelly-lite / VaR proxy", signal="DIAGNOSTIC"),
            ],
            bullets=[
                "Documentar data-as-of y proveedor para cada bloque.",
                "Revisar correlaciones cruzadas antes de concentrar capital.",
            ],
            limitations=["No ejecución automática; salida orientativa únicamente."],
        ),
    ]
    strategy_matrix = [
        _metric(
            "Directional",
            "Allowed only if risk gate is clear",
            signal="WAIT" if risk_state == "VETO ACTIVE" else bias,
        ),
        _metric(
            "Risk-defined Options", "Preferred when veto/tail risk is active", signal="DEFINED_RISK"
        ),
        _metric(
            "Max Sizing",
            (
                prob.get("kelly_half")
                if prob.get("kelly_half") is not None
                else prob.get("kelly_full")
            ),
            detail="From backend Kelly metrics when available",
        ),
        _metric("Invalidation", _first_metric(tech, ("invalidation", "stop", "stop_level"))),
    ]
    from backend.layer_5_risk.portfolio_risk import component as prisk

    l5_kelly = prisk.fractional_kelly(float(prob.get("win_prob_fused") or 0.5))
    risk_monitor = [
        _metric(
            "Gate Veto", prob.get("gate_veto"), signal="CRITICAL" if prob.get("gate_veto") else "OK"
        ),
        _metric("CVaR 99%", prob.get("cvar_99"), signal="TAIL_RISK"),
        _metric("Vol Regime", _first_metric(prob, ("vol_regime", "markov_current_regime"))),
        _metric("GEX Regime", _first_metric(opt, ("gex_regime", "gamma_regime", "net_gex_regime"))),
        _metric(
            "Data Quality",
            thesis_v2.agentes.confidence,
            signal="DEGRADED" if thesis_v2.agentes.confidence < 0.4 else "OK",
        ),
        _metric("L5 Fractional Kelly", round(l5_kelly, 4), signal="RISK_ENGINE"),
    ]
    return InstitutionalReport(
        title=f"{sym} Quantitative Intelligence Report",
        symbol=sym,
        report_date=datetime.now().isoformat(),
        composite_verdict=verdict,
        risk_state=risk_state,
        horizon=horizon,
        data_sources=list(dict.fromkeys(data_sources)),
        cover_metrics=[metric for metric in cover_metrics if metric.value is not None],
        sections=sections,
        strategy_matrix=[metric for metric in strategy_matrix if metric.value is not None],
        risk_monitor=[metric for metric in risk_monitor if metric.value is not None],
        disclaimers=[
            "Informational research output; not investment advice.",
            "Generated from QuantumAnalyzer backend metrics and LLM narratives when enabled.",
            "Missing inputs are marked as unavailable/degraded rather than inferred.",
            "Layer 5 outputs (Kelly/VaR/stress) are diagnostics until wired to execution OMS.",
        ],
    )


async def assemble_thesis_v2_with_snapshot(
    sym: str,
    df: pd.DataFrame,
    fmp_client: FMPClient,
    predictive_engine: MultimodalPredictiveEngine,
    sentiment_engine: SentimentEngine,
    *,
    include_snapshot: bool = False,
    horizon: str = "default",
    market: str = "US",
    snapshot_inputs: dict[str, Any] | None = None,
    snapshot_repository: _SnapshotRepositoryLike | None = None,
    snapshot_service: _SnapshotServiceLike | None = None,
    **kwargs: object,
) -> (
    tuple[ThesisV2, str | None, DomainNarratives | None]
    | tuple[
        ThesisV2,
        str | None,
        DomainNarratives | None,
        object | None,
    ]
):
    """Assemble ThesisV2 and optionally create/persist a snapshot without changing legacy output."""
    result = await assemble_thesis_v2(
        sym,
        df,
        fmp_client,
        predictive_engine,
        sentiment_engine,
        **kwargs,
    )
    enabled = (os.getenv("THESIS_ENABLE_SNAPSHOTS", "false") or "").strip().lower() == "true"
    if not enabled:
        return (*result, None) if include_snapshot else result

    snapshot = None
    try:
        from backend.infrastructure.repositories.thesis_snapshot_repository import (
            ThesisSnapshotRepository,
        )
        from backend.services.thesis_snapshot_service import ThesisSnapshotService

        service = snapshot_service or ThesisSnapshotService()
        repository = snapshot_repository or ThesisSnapshotRepository()
        thesis, _, _ = result
        inputs = snapshot_inputs or {
            "config": {
                "horizon": horizon,
                "market": market,
                "include_snapshot": include_snapshot,
            },
            "data": {
                "symbol": sym.upper().strip(),
                "bars": int(len(df)) if hasattr(df, "__len__") else None,
                "columns": list(getattr(df, "columns", [])),
            },
        }
        snapshot = service.generate_snapshot(
            thesis=thesis,
            symbol=sym,
            horizon=horizon,
            market=market,
            inputs=inputs,
        )
        repository.save(snapshot)
    except Exception as exc:
        logger.warning("Thesis snapshot generation failed for %s: %s", sym, str(exc)[:180])

    return (*result, snapshot) if include_snapshot else result
