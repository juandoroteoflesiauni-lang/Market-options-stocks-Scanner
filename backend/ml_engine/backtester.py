"""Simulador/Backtester estático sobre DuckDB Snapshots. # [TH][IM]"""

import pandas as pd
from backend.config.logger_setup import get_logger
from backend.ml_engine.data_pipeline import build_training_dataset
from backend.ml_engine.models.random_forest_classifier import TradePredictor

logger = get_logger(__name__)

def run_backtest(threshold: float = 0.5) -> dict[str, float]:
    """Evalúa PnL/Sharpe si solo se hubieran tomado trades con prob >= threshold."""
    logger.info("Building dataset for backtest...")
    df = build_training_dataset()
    if df.empty:
        logger.warning("Empty dataset, nothing to backtest.")
        return {}

    predictor = TradePredictor()
    if not predictor.load():
        logger.error("Failed to load model. Please run train_ml_model.py first.")
        return {}

    features = [c for c in df.columns if c.startswith("ind_")]
    X = df[features].fillna(0)

    # Solo mantenemos los features que el modelo conoce
    for col in predictor.feature_names:
        if col not in X.columns:
            X[col] = 0.0

    X = X[predictor.feature_names]
    
    # Predict probabilities for the 1 class
    probs = predictor.model.predict_proba(X)[:, 1]
    
    df['ml_prob'] = probs
    
    # Filter trades taken by ML filter
    trades_taken = df[df['ml_prob'] >= threshold]
    trades_ignored = df[df['ml_prob'] < threshold]
    
    total_trades = len(df)
    taken_count = len(trades_taken)
    ignored_count = len(trades_ignored)
    
    win_rate_taken = (trades_taken['pnl_pct'] > 0.1).mean() * 100 if taken_count > 0 else 0.0
    win_rate_ignored = (trades_ignored['pnl_pct'] > 0.1).mean() * 100 if ignored_count > 0 else 0.0
    win_rate_base = (df['pnl_pct'] > 0.1).mean() * 100 if total_trades > 0 else 0.0
    
    pnl_taken = trades_taken['pnl_pct'].sum()
    pnl_base = df['pnl_pct'].sum()
    
    metrics = {
        "threshold": threshold,
        "total_trades": float(total_trades),
        "trades_taken": float(taken_count),
        "trades_ignored": float(ignored_count),
        "win_rate_base": float(win_rate_base),
        "win_rate_taken": float(win_rate_taken),
        "win_rate_ignored": float(win_rate_ignored),
        "pnl_base_pct": float(pnl_base),
        "pnl_taken_pct": float(pnl_taken),
        "pnl_improvement": float(pnl_taken - pnl_base),
    }
    
    logger.info("Backtest results: %s", metrics)
    return metrics
