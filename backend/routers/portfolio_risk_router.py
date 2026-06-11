"""Portfolio & funding-risk desk API."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerFilters,
    MarketScannerRequest,
    ScannerCustomization,
)
from backend.domain.portfolio_risk_models import (
    PortfolioRiskRequest,
    PortfolioRiskResponse,
    TradeCandidate,
)
from backend.services.market_scanner_service import MarketScannerService
from backend.services.portfolio_risk_service import (
    candidate_from_scanner_row,
    portfolio_risk_service,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/portfolio-risk", tags=["portfolio-risk"])
_LIVE_SOURCE_TIMEOUT_S = 8.0


@router.get("/presets")
async def get_portfolio_risk_presets() -> dict[str, object]:
    service = portfolio_risk_service()
    presets = service.presets()
    return {
        "presets": presets,
        "presets_by_id": {preset.id: preset for preset in presets},
    }


@router.post("/evaluate")
async def evaluate_portfolio_risk(request: PortfolioRiskRequest) -> PortfolioRiskResponse:
    return portfolio_risk_service().evaluate(request)


@router.post("/candidate-gate")
async def evaluate_candidate_gate(request: PortfolioRiskRequest) -> PortfolioRiskResponse:
    return portfolio_risk_service().evaluate(request)


@router.post("/session-reset")
async def reset_portfolio_risk_session(request: PortfolioRiskRequest) -> dict[str, object]:
    result = portfolio_risk_service().evaluate(request)
    return {
        "ok": True,
        "mode": "stateless_v1",
        "message": "Session reset preview generated. Persistence is not enabled in v1.",
        "next_state": {
            "start_of_day_balance": request.account_state.current_equity,
            "realized_daily_pnl": 0.0,
            "unrealized_pnl": 0.0,
        },
        "evaluation": result,
    }


@router.get("/live-candidate/{symbol}")
async def get_live_candidate(symbol: str) -> dict[str, object]:
    sym = symbol.upper().strip()
    scanner_row = None
    meta_signal: dict[str, object] = {}
    options_snapshot: dict[str, object] = {}
    backtest_grade: dict[str, object] = {}
    sources = {
        "market_scanner": "source unavailable",
        "predictive": "source unavailable",
        "options_gex": "source unavailable",
        "backtest_v1": "source unavailable",
    }

    try:
        scan = await asyncio.wait_for(
            MarketScannerService().scan(
                MarketScannerRequest(
                    universe="custom",
                    symbols=[sym],
                    timeframes=["15m", "1h", "1D"],
                    filters=MarketScannerFilters(
                        min_price=0.0,
                        min_volume=0.0,
                        min_relative_volume=0.0,
                        min_score=0.0,
                        allow_reversal=True,
                        include_vetoed=True,
                    ),
                    direction="both",
                    max_rows=1,
                    include_deep_metrics=True,
                    customization=ScannerCustomization(
                        enabled_modules=["technical", "probabilistic", "options_gex"],
                        module_synthesis_limit=1,
                        primary_timeframe="15m",
                    ),
                )
            ),
            timeout=_LIVE_SOURCE_TIMEOUT_S,
        )
        scanner_row = scan.rows[0] if scan.rows else None
        if scanner_row is not None:
            sources["market_scanner"] = "available"
    except Exception as exc:
        sources["market_scanner"] = f"source unavailable: {type(exc).__name__}"

    try:
        meta_signal = await asyncio.wait_for(
            _fetch_meta_signal(sym), timeout=_LIVE_SOURCE_TIMEOUT_S
        )
        if meta_signal:
            sources["predictive"] = "available"
    except Exception as exc:
        sources["predictive"] = f"source unavailable: {type(exc).__name__}"

    try:
        options_snapshot = await asyncio.wait_for(
            _fetch_options_snapshot(sym),
            timeout=_LIVE_SOURCE_TIMEOUT_S,
        )
        if options_snapshot:
            sources["options_gex"] = "available"
    except Exception as exc:
        sources["options_gex"] = f"source unavailable: {type(exc).__name__}"

    try:
        backtest_grade = await asyncio.wait_for(
            _fetch_backtest_grade(sym), timeout=_LIVE_SOURCE_TIMEOUT_S
        )
        if backtest_grade:
            sources["backtest_v1"] = "available"
    except Exception as exc:
        sources["backtest_v1"] = f"source unavailable: {type(exc).__name__}"

    candidate = _build_live_candidate(
        sym, scanner_row, meta_signal, options_snapshot, backtest_grade
    )
    return {
        "candidate": candidate,
        "sources": sources,
        "scanner_row": scanner_row,
        "predictive": meta_signal,
        "options_gex": _compact_options_snapshot(options_snapshot),
    }


async def _fetch_meta_signal(symbol: str) -> dict[str, object]:
    from backend.routers.probabilistic_router import get_meta_signal_endpoint

    payload = await get_meta_signal_endpoint(symbol)
    return _as_dict(payload)


async def _fetch_options_snapshot(symbol: str) -> dict[str, object]:
    from backend.routers.options_router import options_snapshot_service

    payload = await options_snapshot_service(symbol, None, 0.04)
    return _as_dict(payload)


async def _fetch_backtest_grade(symbol: str) -> dict[str, object]:
    from backend.services.prediction_backtest_service import run_prediction_backtest

    result = await asyncio.to_thread(
        run_prediction_backtest,
        db_path="backend/data/predictions.db",
        module="predictive",
        symbol=symbol,
        limit=5_000,
        min_abs_signal=0.1,
    )
    return _as_dict(result)


def _build_live_candidate(
    symbol: str,
    scanner_row: object | None,
    meta_signal: dict[str, object],
    options_snapshot: dict[str, object],
    backtest_grade: dict[str, object] | None = None,
) -> TradeCandidate:
    row = _as_dict(scanner_row)
    options = _as_dict(options_snapshot)
    gex = _as_dict(options.get("gex_levels"))
    options_features = _as_dict(options.get("options_gex_features"))
    iv_surface = _as_dict(options.get("iv_surface"))
    term = _as_dict(iv_surface.get("term_structure"))
    overlay = _as_dict(row.get("institutional_overlay"))
    row_signals = _as_dict(row.get("signals"))
    backtest = _as_dict(backtest_grade)

    entry = _float_or_none(options.get("spot")) or _float_or_none(row.get("price")) or 1.0
    scanner_direction = str(row.get("direction") or "").lower()
    predictive_direction = str(meta_signal.get("direction") or "").upper()
    direction = (
        "SHORT" if scanner_direction == "bearish" or predictive_direction == "DOWN" else "LONG"
    )
    atr_pct = (
        _extract_atr_pct(row_signals)
        or _float_or_none(row.get("risk_hints", {}).get("var_proxy_pct"))
        or 0.8
    )
    stop_distance = max(0.35, min(3.5, float(atr_pct))) / 100.0
    if direction == "LONG":
        stop = entry * (1.0 - stop_distance)
        target = entry * (1.0 + stop_distance * 2.2)
    else:
        stop = entry * (1.0 + stop_distance)
        target = entry * (1.0 - stop_distance * 2.2)

    component_signals = _as_dict(meta_signal.get("component_signals"))
    tail_risk = abs(_float_or_none(component_signals.get("tail_risk")) or 0.0)
    jump_risk = abs(_float_or_none(component_signals.get("jump_risk")) or 0.0)
    signal = _float_or_none(meta_signal.get("signal")) or 0.0
    confidence = _float_or_none(meta_signal.get("confidence"))
    if confidence is None:
        confidence = _scanner_confidence(row)
    win_prob = max(0.05, min(0.95, 0.50 + (abs(signal) * confidence * 0.35)))

    backwardation = bool(term.get("backwardation")) or bool(
        _as_dict(overlay.get("iv_term_structure")).get("backwardation")
    )
    gamma_regime = (
        str(gex.get("dealer_bias") or overlay.get("dealer_bias") or "NEUTRAL").upper().strip()
    )
    payload: dict[str, Any] = dict(row)
    if not payload.get("evidence_by_module"):
        (
            evidence_by_module,
            best_supporting_module,
            weakest_link_module,
            recommended_size_multiplier,
        ) = _extract_module_evidence(row)
        payload["evidence_by_module"] = evidence_by_module
        payload["best_supporting_module"] = best_supporting_module
        payload["weakest_link_module"] = weakest_link_module
        if recommended_size_multiplier is not None:
            payload["recommended_size_multiplier"] = recommended_size_multiplier
    else:
        evidence_by_module = _as_dict(payload.get("evidence_by_module"))
        best_supporting_module = payload.get("best_supporting_module")
        weakest_link_module = payload.get("weakest_link_module")
        recommended_size_multiplier = _float_or_none(payload.get("recommended_size_multiplier"))
    logger.info(
        "portfolio_risk.live_candidate_module_evidence symbol=%s best=%s weakest=%s size_multiplier=%s",
        symbol,
        best_supporting_module,
        weakest_link_module,
        recommended_size_multiplier,
    )
    payload.update(
        {
            "symbol": symbol,
            "direction": direction,
            "entry": round(entry, 4),
            "stop": round(stop, 4),
            "target": round(target, 4),
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "source_module": "market_scanner+predictive+options_gex",
            "expected_win_prob": round(win_prob, 4),
            "rr_ratio": 2.2,
            "scanner_score": _float_or_none(row.get("scanner_score")),
            "conflict_score": _extract_conflict_score(row),
            "tail_risk": round(tail_risk, 4),
            "jump_risk": round(jump_risk, 4),
            "gamma_regime": gamma_regime,
            "iv_term_structure": "backwardation" if backwardation else "normal",
            "squeeze_probability": _float_or_none(gex.get("squeeze_probability")),
            "atr_pct": round(float(atr_pct), 4),
            "module_backtest_trades": _int_or_none(backtest.get("module_backtest_trades"))
            or _int_or_none(backtest.get("trades")),
            "module_backtest_sharpe": _float_or_none(backtest.get("module_backtest_sharpe"))
            or _float_or_none(backtest.get("sharpe")),
            "module_backtest_profit_factor": _float_or_none(
                backtest.get("module_backtest_profit_factor")
            )
            or _float_or_none(backtest.get("profit_factor")),
            "options_gex_source_tier": str(options_features.get("source_tier") or "") or None,
            "options_gex_data_quality_score": _float_or_none(
                options_features.get("data_quality_score")
            ),
            "options_gex_missing_components": [
                str(item) for item in options_features.get("missing_components") or []
            ],
        }
    )
    backtest_grade_value = backtest.get("module_backtest_grade")
    if backtest_grade_value is not None:
        payload["module_backtest_grade"] = backtest_grade_value
    return candidate_from_scanner_row(payload)


def _extract_module_evidence(
    scanner_row: dict[str, object],
) -> tuple[dict[str, Any], str | None, str | None, float | None]:
    """Extract Scanner module evidence and sizing hints for TradeCandidate.

    Reads ``scanner_row["signals"]`` as a module-keyed mapping and returns:
    evidence_by_module, best_supporting_module, weakest_link_module and a
    clamped scanner recommended size multiplier.
    """

    evidence_by_module: dict[str, Any] = {}
    scores_by_module: dict[str, float] = {}

    signals = _as_dict(scanner_row.get("signals"))
    for module_name, module_data in signals.items():
        module_key = str(module_name)
        module_dict = _as_dict(module_data)
        reasons = _top_reasons(module_dict.get("reasons"))
        score = _float_or_none(module_dict.get("score")) or 0.0
        evidence_by_module[module_key] = {
            "signal": module_dict.get("signal"),
            "confidence": module_dict.get("confidence"),
            "score": module_dict.get("score"),
            "bias": module_dict.get("bias"),
            "reasons": reasons,
        }
        scores_by_module[module_key] = score

    best_supporting_module = (
        max(scores_by_module, key=scores_by_module.get) if scores_by_module else None
    )
    weakest_link_module = (
        min(scores_by_module, key=scores_by_module.get) if scores_by_module else None
    )

    overlay = _as_dict(scanner_row.get("institutional_overlay"))
    size_multiplier = _float_or_none(overlay.get("size_recommendation"))
    if size_multiplier is None:
        size_multiplier = _float_or_none(scanner_row.get("recommended_size_multiplier"))
    if size_multiplier is not None:
        size_multiplier = max(0.0, min(1.0, size_multiplier))

    return (
        evidence_by_module,
        best_supporting_module,
        weakest_link_module,
        size_multiplier,
    )


def _compact_options_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    gex = _as_dict(snapshot.get("gex_levels"))
    iv_surface = _as_dict(snapshot.get("iv_surface"))
    return {
        "spot": snapshot.get("spot"),
        "dealer_bias": gex.get("dealer_bias"),
        "squeeze_probability": gex.get("squeeze_probability"),
        "term_structure": iv_surface.get("term_structure"),
        "options_gex_features": snapshot.get("options_gex_features"),
    }


def _as_dict(value: object) -> dict[str, object]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return value if isinstance(value, dict) else {}


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _int_or_none(value: object) -> int | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return int(number)


def _top_reasons(value: object) -> list[object]:
    if isinstance(value, list | tuple):
        return list(value)[:2]
    if value is None:
        return []
    return [value]


def _extract_atr_pct(signals: dict[str, object]) -> float | None:
    for key in ("15m", "1h", "1D", "5m"):
        signal = _as_dict(signals.get(key))
        metrics = _as_dict(signal.get("metrics"))
        value = _float_or_none(metrics.get("atr_pct"))
        if value is not None and value > 0:
            return value
    return None


def _scanner_confidence(row: dict[str, object]) -> float:
    signals = _as_dict(row.get("signals"))
    values: list[float] = []
    for raw in signals.values():
        conf = _float_or_none(_as_dict(raw).get("confidence"))
        if conf is not None:
            values.append(conf)
    return sum(values) / len(values) if values else 0.35


def _extract_conflict_score(row: dict[str, object]) -> float | None:
    audit = _as_dict(row.get("score_audit"))
    phase_b = _as_dict(audit.get("phase_b_blend"))
    dispersion = _float_or_none(phase_b.get("module_dispersion"))
    if dispersion is not None:
        return max(0.0, min(1.0, dispersion / 50.0))
    return None
