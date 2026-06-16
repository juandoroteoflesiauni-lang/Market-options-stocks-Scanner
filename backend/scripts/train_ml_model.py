"""Script para re-entrenar el modelo de ML basado en la DB de paper-trading. # [TH][IM]"""

import argparse
import sys
from backend.config.logger_setup import get_logger
from backend.ml_engine.data_pipeline import build_training_dataset
from backend.ml_engine.models.random_forest_classifier import TradePredictor

logger = get_logger(__name__)

def main() -> None:
    parser = argparse.ArgumentParser(description="Train ML model with DuckDB audit data.")
    parser.parse_args()

    logger.info("Building training dataset from DuckDB snapshots...")
    df = build_training_dataset()
    
    if df.empty:
        logger.error("Dataset is empty. Are there enough trades in audit_trade_results?")
        sys.exit(1)
        
    logger.info("Dataset built. Total samples: %d", len(df))
    logger.info("Win rate in dataset: %.2f%%", (df['target_win'].mean() * 100))

    predictor = TradePredictor()
    metrics = predictor.train(df)
    
    if not metrics:
        logger.error("Training failed or not enough data.")
        sys.exit(1)

    predictor.save()
    logger.info("Training completed successfully.")

    # --- GEMINI AUDIT ---
    logger.info("Iniciando auditoría con Gemini...")
    from backend.ml_engine.gemini_auditor import analyze_trading_performance
    report_text = analyze_trading_performance(df)
    
    import os
    from datetime import datetime
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, "gemini_insights_latest.md")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Auditoría Gemini - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(report_text)
    
    logger.info("Reporte de Gemini guardado en %s", report_path)

if __name__ == "__main__":
    main()
