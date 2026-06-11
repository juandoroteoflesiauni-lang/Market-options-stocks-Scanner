"""
API Router for Institutional Backtesting and Strategy Calibration (R4).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException

from backend.backtesting.base import BacktestConfig
from backend.backtesting.strategy_trainer import StrategyTrainer, TrainerConfig
from backend.config.logger_setup import get_logger
from backend.layer_1_data.fetchers.fmp_client import FMPClient
from backend.quant_engine.math.technical.technical import TechnicalMath
from backend.services.prediction_backtest_service import (
    DEFAULT_BATCH_SYMBOLS,
    run_prediction_backtest,
    run_prediction_backtest_batch,
    run_walk_forward_oos_backtest,
)

router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])
logger = get_logger(__name__)
DEFAULT_PREDICTIONS_DB = Path("backend/data/predictions.db")


@router.get("/walk-forward/{symbol}")
async def get_walk_forward_backtest(
    symbol: str,
    module: str = "technical",
    timeframe: str = "1D",
    train_window: int = 120,
    test_window: int = 40,
    step: int = 20,
    fee_bps: float = 2.0,
    slippage_bps: float = 1.0,
    half_spread_bps: float = 1.0,
) -> dict[str, Any]:
    """
    Run an institutional walk-forward threshold grid search for a symbol.
    Uses real historical bars (FMP).
    """
    sym = symbol.upper().strip()
    fmp = FMPClient()

    # 1. Fetch bars
    ohlcv = (await fmp.get_historical_prices(sym))[:500]
    if not ohlcv:
        raise HTTPException(status_code=404, detail=f"No historical data found for {sym}")

    df = pd.DataFrame([b.model_dump() for b in ohlcv])
    df = df.sort_values("date").reset_index(drop=True)

    # 2. Compute returns
    returns = df["close"].pct_change().fillna(0.0).tolist()

    # 3. Compute signal based on module
    signal: list[float] = []
    if module == "technical":
        # Example: use RSI as a proxy signal for the grid search
        # In a real scenario, we would use more complex engines.
        rsi = TechnicalMath.rsi(df["close"].to_numpy(dtype=float), 14)
        signal = pd.Series(rsi, index=df.index).fillna(50.0).tolist()
    elif module == "predictive":
        # Placeholder for predictive scores
        signal = [50.0] * len(returns)
    else:
        raise HTTPException(
            status_code=400, detail=f"Module {module} not supported for walk-forward yet"
        )

    # 4. Run Trainer
    trainer = StrategyTrainer(TrainerConfig(symbol=sym))
    cost_cfg = BacktestConfig(
        fee_bps=fee_bps, slippage_bps=slippage_bps, half_spread_bps=half_spread_bps
    )

    # Thresholds to search: 55, 60, 65, 70, 75
    threshold_grid = [55.0, 60.0, 65.0, 70.0, 75.0]

    summary = trainer.walk_forward_thresholds(
        returns_pct=returns,
        signal=signal,
        module=module,
        train_window=train_window,
        test_window=test_window,
        step=step,
        thresholds=threshold_grid,
        cost_config=cost_cfg,
    )

    return {
        "ok": True,
        "symbol": sym,
        "module": module,
        "timeframe": timeframe,
        "summary": {
            "mean_test_sharpe": summary.mean_test_sharpe,
            "folds_count": len(summary.folds),
            "folds": [
                {
                    "idx": f.fold_index,
                    "threshold": f.selected_threshold,
                    "train_sharpe": f.train_sharpe,
                    "test_sharpe": f.test_result.sharpe,
                    "test_trades": f.test_result.trades,
                }
                for f in summary.folds
            ],
        },
    }


@router.get("/prediction-v1")
async def get_prediction_v1_backtest(
    module: str,
    symbol: str | None = None,
    n_days: int = 5,
    min_abs_signal: float = 0.1,
    limit: int = 50_000,
    fee_bps: float = 2.0,
    slippage_bps: float = 1.0,
    half_spread_bps: float = 0.0,
) -> dict[str, Any]:
    """Backtest real V1 over the institutional prediction SQLite backfill."""
    try:
        result = run_prediction_backtest(
            db_path=DEFAULT_PREDICTIONS_DB,
            module=module,
            symbol=symbol,
            n_days=n_days,
            min_abs_signal=min_abs_signal,
            limit=limit,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            half_spread_bps=half_spread_bps,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Prediction backfill not found: {exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail=f"Prediction backfill read failed: {exc}"
        ) from exc
    return {"ok": True, "result": result}


@router.get("/prediction-v1/batch")
async def get_prediction_v1_batch_backtest(
    symbols: str = ",".join(DEFAULT_BATCH_SYMBOLS),
    modules: str = "predictive,technical,options_gex",
    n_days: int = 5,
    limit_per_symbol: int = 5_000,
    fee_bps: float = 2.0,
    slippage_bps: float = 1.0,
    half_spread_bps: float = 0.0,
) -> dict[str, Any]:
    """Batch audit over the institutional prediction SQLite backfill."""
    try:
        report = run_prediction_backtest_batch(
            db_path=DEFAULT_PREDICTIONS_DB,
            symbols=symbols.split(","),
            modules=modules.split(","),
            n_days=n_days,
            limit_per_symbol=limit_per_symbol,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            half_spread_bps=half_spread_bps,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Prediction backfill not found: {exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail=f"Prediction backfill read failed: {exc}"
        ) from exc
    return {"ok": True, "report": report}


@router.get("/prediction-v1/walk-forward-oos")
async def get_prediction_v1_walk_forward_oos(
    module: str,
    symbol: str | None = None,
    n_days: int | str = 5,
    n_folds: int = 3,
    oos_fraction: float = 0.25,
    min_abs_signal: float = 0.1,
    limit: int = 50_000,
    fee_bps: float = 2.0,
    slippage_bps: float = 1.0,
    half_spread_bps: float = 0.0,
) -> dict[str, Any]:
    """Chronological walk-forward OOS backtest over institutional prediction SQLite."""
    try:
        report = run_walk_forward_oos_backtest(
            db_path=DEFAULT_PREDICTIONS_DB,
            module=module,
            symbol=symbol,
            n_days=n_days,
            min_abs_signal=min_abs_signal,
            limit=limit,
            n_folds=n_folds,
            oos_fraction=oos_fraction,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            half_spread_bps=half_spread_bps,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Prediction backfill not found: {exc}"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail=f"Prediction backfill read failed: {exc}"
        ) from exc
    return {"ok": True, "report": report}
