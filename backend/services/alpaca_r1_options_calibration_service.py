"""Calibración C5: pesos por familia + isotonic por motor (R1 opciones). # [PD-3][TH]"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from backend.backtesting.base import SimpleEquityCurve
from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.alpaca_r1_options_scoring_config import (
    R1_CLASSIC_WEIGHT,
    R1_FAMILY_ENGINES,
    R1_OPTIONS_WEIGHT,
    default_calibration_path,
)
from backend.config.logger_setup import get_logger
from backend.domain.alpaca_options_models import OptionsDirection, OptionsEngineSignal
from backend.domain.alpaca_r1_calibration_models import (
    R1FamilyWeights,
    R1OptionsCalibrationMetrics,
    R1OptionsCalibrationResult,
)
from backend.services.alpaca_r1_options_confluence import OptionsConfluenceScorer
from backend.services.motor_calibrator import MotorCalibrator
from backend.config.sqlite_db_paths import OPTIONS_GEX_SNAPSHOTS_DB
from backend.services.research.research_types import _safe_float

logger = get_logger(__name__)

_GRID_MOMENTUM = (0.20, 0.25, 0.33, 0.40, 0.45)
_GRID_VOLUME = (0.20, 0.25, 0.33, 0.40)
_DEFAULT_ENTRY_THRESHOLD = 0.55
_MIN_SAMPLES = 12


@dataclass(frozen=True)
class _CalibrationSample:
    symbol: str
    as_of: str
    forward_return: float
    label_long_win: int
    signals: tuple[OptionsEngineSignal, ...]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _direction_from_value(value: float) -> OptionsDirection:
    if value > 0.15:
        return "BULL"
    if value < -0.15:
        return "BEAR"
    return "NEUTRAL"


def _bull_score(value: float | None) -> float:
    if value is None:
        return 0.5
    return _clamp01(0.5 + float(value) / 2.0)


def _engine_family(engine: str) -> str:
    for family, engines in R1_FAMILY_ENGINES.items():
        if engine in engines:
            return family
    return "momentum"


def extract_proxy_engine_signals(
    features: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[OptionsEngineSignal]:
    """Proxy histórico desde features/snapshot (~5min broadcast)."""
    engine_signal = snapshot.get("engine_signal") or {}
    shadow = _safe_float(features.get("shadow_delta_signal")) or 0.0
    composite = _safe_float(features.get("composite_directional_signal")) or 0.0
    gf_dir = _safe_float(features.get("gamma_flip_directional_signal")) or 0.0
    ndde_raw = _safe_float(features.get("ndde_signal")) or 0.0
    net_gex = _safe_float(engine_signal.get("total_gex")) or 0.0
    gex_norm = float(np.tanh(net_gex / 1_000_000.0))

    specs: list[tuple[str, float, OptionsDirection]] = [
        ("delta_rsi", _bull_score(composite), _direction_from_value(composite)),
        ("shadow_macd", _bull_score(shadow), _direction_from_value(shadow)),
        ("vidya_iv_gamma", _bull_score(gf_dir), _direction_from_value(gf_dir)),
        (
            "cvd_ndde_gamma",
            _bull_score(ndde_raw / 1000.0),
            _direction_from_value(ndde_raw),
        ),
        ("volume_profile_oi", _bull_score(gex_norm), _direction_from_value(gex_norm)),
        ("bb_gex", _bull_score(gex_norm), _direction_from_value(gex_norm)),
        (
            "sma_gamma",
            _bull_score(gex_norm * (1.0 if gf_dir >= 0 else -1.0)),
            _direction_from_value(gex_norm * gf_dir),
        ),
        (
            "hybrid_ribbon",
            _bull_score(shadow * composite),
            _direction_from_value(shadow * composite),
        ),
    ]
    return [
        OptionsEngineSignal(
            engine=engine,
            family=_engine_family(engine),  # type: ignore[arg-type]
            direction=direction,
            score=score,
            detail={"proxy": True},
        )
        for engine, score, direction in specs
    ]


def load_r1_snapshot_samples(
    *,
    db_path: Path | None = None,
    symbols: tuple[str, ...] | None = None,
    limit_per_symbol: int = 500,
) -> list[_CalibrationSample]:
    """Carga pares consecutivos snapshot → retorno forward entre spots."""
    path = db_path or OPTIONS_GEX_SNAPSHOTS_DB
    if not path.exists():
        return []

    target_symbols = symbols or ALPACA_ROUTE1_WATCHLIST
    samples: list[_CalibrationSample] = []

    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5.0, check_same_thread=False)
    except sqlite3.DatabaseError as exc:
        logger.warning("r1_calibration.db_open_failed error=%s", str(exc)[:120])
        return []
    try:
        for symbol in target_symbols:
            rows = con.execute(
                "SELECT as_of, features_json, snapshot_json "
                "FROM options_gex_snapshots WHERE symbol = ? "
                "ORDER BY as_of ASC LIMIT ?",
                (symbol.upper(), max(2, limit_per_symbol)),
            ).fetchall()
            parsed: list[tuple[str, dict[str, Any], dict[str, Any], float | None]] = []
            for as_of, features_raw, snapshot_raw in rows:
                features = json.loads(features_raw) if features_raw else {}
                snapshot = json.loads(snapshot_raw) if snapshot_raw else {}
                spot = _safe_float(snapshot.get("spot"))
                parsed.append((str(as_of), features, snapshot, spot))

            for idx in range(len(parsed) - 1):
                as_of, features, snapshot, spot = parsed[idx]
                next_spot = parsed[idx + 1][3]
                if spot is None or next_spot is None or spot <= 0:
                    continue
                forward_return = (next_spot - spot) / spot
                signals = tuple(extract_proxy_engine_signals(features, snapshot))
                samples.append(
                    _CalibrationSample(
                        symbol=symbol.upper(),
                        as_of=as_of,
                        forward_return=forward_return,
                        label_long_win=1 if forward_return > 0 else 0,
                        signals=signals,
                    )
                )
    except sqlite3.DatabaseError as exc:
        logger.warning("r1_calibration.db_read_failed error=%s", str(exc)[:120])
        return samples
    finally:
        con.close()

    return samples


def _score_sample(
    sample: _CalibrationSample,
    family_weights: dict[str, float],
    *,
    calibrator: MotorCalibrator | None = None,
    entry_threshold: float = _DEFAULT_ENTRY_THRESHOLD,
) -> tuple[float | None, float]:
    """Retorna (confluence_score, strategy_return)."""
    signals = sample.signals
    if calibrator is not None:
        calibrated: list[OptionsEngineSignal] = []
        for sig in signals:
            cal_score = calibrator.transform(sig.engine, sig.score)
            calibrated.append(sig.model_copy(update={"score": cal_score}))
        signals = calibrated

    confluence = OptionsConfluenceScorer.score_with_weights(
        list(signals), family_weights=family_weights
    )
    if confluence is None or confluence.score < entry_threshold:
        return confluence.score if confluence else None, 0.0
    return confluence.score, sample.forward_return


def _backtest_weights(
    samples: list[_CalibrationSample],
    family_weights: dict[str, float],
    *,
    calibrator: MotorCalibrator | None = None,
    entry_threshold: float = _DEFAULT_ENTRY_THRESHOLD,
) -> R1OptionsCalibrationMetrics:
    trade_returns: list[float] = []
    for sample in samples:
        _, strat_ret = _score_sample(
            sample,
            family_weights,
            calibrator=calibrator,
            entry_threshold=entry_threshold,
        )
        if strat_ret != 0.0:
            trade_returns.append(strat_ret)

    curve = SimpleEquityCurve(trade_returns if trade_returns else [0.0])
    wins = sum(1 for r in trade_returns if r > 0)
    losses = [abs(r) for r in trade_returns if r < 0]
    gains = [r for r in trade_returns if r > 0]
    pf = (sum(gains) / sum(losses)) if losses and gains else None

    return R1OptionsCalibrationMetrics(
        n_samples=len(samples),
        n_trades=len(trade_returns),
        sharpe=round(curve.sharpe(), 4) if trade_returns else None,
        profit_factor=round(pf, 4) if pf is not None else None,
        win_rate=round(wins / len(trade_returns), 4) if trade_returns else None,
        total_return_pct=round(sum(trade_returns) * 100.0, 4) if trade_returns else None,
        engine="simple",
    )


def grid_search_family_weights(
    samples: list[_CalibrationSample],
    *,
    calibrator: MotorCalibrator | None = None,
    entry_threshold: float = _DEFAULT_ENTRY_THRESHOLD,
) -> tuple[dict[str, float], R1OptionsCalibrationMetrics]:
    """Búsqueda discreta normalizada; maximiza Sharpe (fallback PF)."""
    if len(samples) < _MIN_SAMPLES:
        equal = {k: 1.0 / 3.0 for k in R1_FAMILY_ENGINES}
        metrics = _backtest_weights(samples, equal, calibrator=calibrator)
        return equal, metrics

    best_weights = {k: 1.0 / 3.0 for k in R1_FAMILY_ENGINES}
    best_metrics = _backtest_weights(
        samples, best_weights, calibrator=calibrator, entry_threshold=entry_threshold
    )
    best_score = best_metrics.sharpe if best_metrics.sharpe is not None else -999.0

    for mom in _GRID_MOMENTUM:
        for vol in _GRID_VOLUME:
            struct = round(1.0 - mom - vol, 4)
            if struct < 0.15:
                continue
            weights = {"momentum": mom, "volume": vol, "structure": struct}
            metrics = _backtest_weights(
                samples, weights, calibrator=calibrator, entry_threshold=entry_threshold
            )
            candidate = metrics.sharpe
            if candidate is None:
                candidate = metrics.profit_factor
            if candidate is None:
                continue
            if candidate > best_score:
                best_score = candidate
                best_weights = weights
                best_metrics = metrics

    return best_weights, best_metrics


def fit_engine_calibrators(samples: list[_CalibrationSample]) -> MotorCalibrator:
    """Isotonic por motor: score raw → P(LONG correct)."""
    calibrator = MotorCalibrator()
    by_engine: dict[str, list[tuple[float, int]]] = {
        engine: [] for engines in R1_FAMILY_ENGINES.values() for engine in engines
    }
    for sample in samples:
        for sig in sample.signals:
            by_engine.setdefault(sig.engine, []).append((sig.score, sample.label_long_win))

    for engine, pairs in by_engine.items():
        if len(pairs) < 3:
            continue
        scores, labels = zip(*pairs, strict=False)
        calibrator.fit(engine, list(scores), list(labels))
    return calibrator


def run_vectorbt_validation(
    samples: list[_CalibrationSample],
    family_weights: dict[str, float],
    *,
    calibrator: MotorCalibrator | None = None,
    entry_threshold: float = _DEFAULT_ENTRY_THRESHOLD,
) -> R1OptionsCalibrationMetrics | None:
    """Validación opcional con vectorbt si está instalado."""
    try:
        import importlib

        vbt = importlib.import_module("vectorbt")
    except ImportError:
        logger.info("r1_calibration.vectorbt_skipped reason=not_installed")
        return None

    prices = [100.0]
    entries = [False]
    exits = [False]
    for sample in samples:
        score, strat_ret = _score_sample(
            sample,
            family_weights,
            calibrator=calibrator,
            entry_threshold=entry_threshold,
        )
        active = strat_ret != 0.0
        entries.append(active)
        exits.append(False)
        prices.append(prices[-1] * (1.0 + (sample.forward_return if active else 0.0)))

    exits[-1] = True
    pf = vbt.Portfolio.from_signals(
        close=np.array(prices, dtype=float),
        entries=np.array(entries, dtype=bool),
        exits=np.array(exits, dtype=bool),
        init_cash=100_000.0,
        fees=0.0002,
        freq="5min",
    )
    sharpe = float(pf.sharpe_ratio()) if not math.isnan(float(pf.sharpe_ratio())) else None
    total_ret = float(pf.total_return()) if not math.isnan(float(pf.total_return())) else None
    return R1OptionsCalibrationMetrics(
        n_samples=len(samples),
        n_trades=int(pf.trades.count()),
        sharpe=round(sharpe, 4) if sharpe is not None else None,
        total_return_pct=round(total_ret * 100.0, 4) if total_ret is not None else None,
        engine="vectorbt",
    )


def run_r1_options_calibration(
    *,
    db_path: Path | str | None = None,
    symbols: tuple[str, ...] | None = None,
    output_path: Path | str | None = None,
    calibrator_path: Path | str | None = None,
    limit_per_symbol: int = 500,
    entry_threshold: float = _DEFAULT_ENTRY_THRESHOLD,
) -> R1OptionsCalibrationResult:
    """Pipeline C5 completo: fit calibrators → grid weights → persist."""
    path = Path(db_path) if db_path else OPTIONS_GEX_SNAPSHOTS_DB
    out_path = Path(output_path) if output_path else default_calibration_path()
    cal_path = (
        Path(calibrator_path)
        if calibrator_path
        else out_path.parent.parent / "data" / "alpaca_r1_engine_calibrators.joblib"
    )

    samples = load_r1_snapshot_samples(
        db_path=path, symbols=symbols, limit_per_symbol=limit_per_symbol
    )
    notes: list[str] = []
    if len(samples) < _MIN_SAMPLES:
        notes.append(f"insufficient_samples:{len(samples)}")

    calibrator = fit_engine_calibrators(samples)
    cal_path.parent.mkdir(parents=True, exist_ok=True)
    calibrator.save(str(cal_path))

    best_weights, metrics = grid_search_family_weights(
        samples, calibrator=calibrator, entry_threshold=entry_threshold
    )
    vbt_metrics = run_vectorbt_validation(
        samples, best_weights, calibrator=calibrator, entry_threshold=entry_threshold
    )
    if vbt_metrics is not None:
        notes.append(f"vectorbt_sharpe:{vbt_metrics.sharpe}")

    result = R1OptionsCalibrationResult(
        calibrated_at=datetime.now(tz=UTC).isoformat(),
        family_weights=R1FamilyWeights(
            momentum=best_weights["momentum"],
            volume=best_weights["volume"],
            structure=best_weights["structure"],
        ),
        classic_weight=R1_CLASSIC_WEIGHT,
        options_weight=R1_OPTIONS_WEIGHT,
        entry_threshold=entry_threshold,
        calibrator_path=str(cal_path),
        metrics=metrics,
        symbols=tuple(symbols or ALPACA_ROUTE1_WATCHLIST),
        notes=tuple(notes),
    )
    persist_calibration_result(result, out_path)
    return result


def persist_calibration_result(
    result: R1OptionsCalibrationResult,
    path: Path | str,
) -> None:
    """Persiste JSON para override en runtime."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("r1_calibration.saved path=%s sharpe=%s", target, result.metrics.sharpe)


__all__ = [
    "extract_proxy_engine_signals",
    "fit_engine_calibrators",
    "grid_search_family_weights",
    "load_r1_snapshot_samples",
    "persist_calibration_result",
    "run_r1_options_calibration",
]
