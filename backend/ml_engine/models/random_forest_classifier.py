"""Model for predicting Trade Win Probability. # [TH][IM]"""

import os
import joblib
import pandas as pd
from typing import Any

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(__file__), "rf_model.pkl")
FEATURES_PATH = os.path.join(os.path.dirname(__file__), "features.json")

class TradePredictor:
    def __init__(self):
        self.model = None
        self.feature_names = []

    def train(self, df: pd.DataFrame) -> dict[str, float]:
        """Entrena el modelo usando los features y target extraidos de DuckDB."""
        if df.empty or 'target_win' not in df.columns:
            logger.warning("Empty dataframe or missing target_win for training.")
            return {}

        # Identificar columnas features (ej: empiezan con ind_)
        self.feature_names = [c for c in df.columns if c.startswith("ind_")]
        if not self.feature_names:
            logger.warning("No features found starting with 'ind_'")
            return {}

        X = df[self.feature_names].fillna(0)
        y = df['target_win']

        if len(X) < 10:
            logger.warning("Not enough samples to train (min 10 required).")
            return {}

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        self.model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
        self.model.fit(X_train, y_train)

        # Evaluar
        y_pred = self.model.predict(X_test)
        metrics = {
            "accuracy": float(accuracy_score(y_test, y_pred)),
            "precision": float(precision_score(y_test, y_pred, zero_division=0)),
            "recall": float(recall_score(y_test, y_pred, zero_division=0)),
            "f1_score": float(f1_score(y_test, y_pred, zero_division=0)),
        }
        
        logger.info("Model trained. Metrics: %s", metrics)
        return metrics

    def save(self) -> None:
        if self.model:
            joblib.dump(self.model, MODEL_PATH)
            import json
            with open(FEATURES_PATH, "w") as f:
                json.dump(self.feature_names, f)
            logger.info("Model saved to %s", MODEL_PATH)

    def load(self) -> bool:
        if os.path.exists(MODEL_PATH) and os.path.exists(FEATURES_PATH):
            self.model = joblib.load(MODEL_PATH)
            import json
            with open(FEATURES_PATH, "r") as f:
                self.feature_names = json.load(f)
            return True
        return False

    def predict_prob(self, indicators: dict[str, Any]) -> float:
        """Predice la probabilidad de win (1) dado el dict de indicadores."""
        if not self.model or not self.feature_names:
            return 0.5 # Default neutral probability

        from backend.ml_engine.data_pipeline import _flatten_dict
        features: dict[str, Any] = {}
        _flatten_dict(indicators, features, prefix="ind_")

        # Construir dataframe de 1 fila
        row = {f: features.get(f, 0.0) for f in self.feature_names}
        df_pred = pd.DataFrame([row])
        
        # Probabilidad clase 1 (win)
        prob = self.model.predict_proba(df_pred)[0][1]
        return float(prob)
