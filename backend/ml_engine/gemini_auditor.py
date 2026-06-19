"""Módulo de Auditoría Quantitativa con Gemini. # [TH][IM][PD-1]"""

from __future__ import annotations

import json
import os

import httpx
import pandas as pd

from backend.config.logger_setup import get_logger

logger = get_logger(__name__)

_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/" "gemini-2.5-flash:generateContent"
)


def _resolve_gemini_api_key() -> str | None:
    """Load Gemini API key from settings or GEMINI_API_KEY env — never hardcoded."""
    try:
        from backend.config.settings import load_settings

        settings = load_settings()
        if settings.gemini_api_key is not None:
            value = settings.gemini_api_key.get_secret_value().strip()
            if value:
                return value
    except Exception as exc:
        logger.debug("gemini_auditor.settings_fallback reason=%s", exc)

    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    return env_key or None


def analyze_trading_performance(df: pd.DataFrame) -> str:
    """Envía un resumen de las peores y mejores operaciones a Gemini para análisis."""
    api_key = _resolve_gemini_api_key()
    if not api_key:
        logger.warning("GEMINI_API_KEY not configured, skipping Gemini analysis.")
        return "Error: GEMINI_API_KEY no configurada en .env"

    if df.empty or "pnl_pct" not in df.columns:
        return "No hay suficientes datos de operaciones para analizar."

    # Filtrar operaciones reales (donde haya pnl_pct real, no solo flat)
    df_trades = df.dropna(subset=["pnl_pct"]).copy()
    if df_trades.empty:
        return "No hay operaciones cerradas para analizar."

    # Filtrar columnas para no enviar ruido excesivo a la API y ahorrar tokens
    essential_cols = [
        c
        for c in df_trades.columns
        if c.startswith("ind_") or c in ["symbol", "pnl_pct", "exit_reason"]
    ]
    df_essential = df_trades[essential_cols]

    # Tomar los 5 peores y 5 mejores trades
    df_sorted = df_essential.sort_values(by="pnl_pct")
    worst_trades = df_sorted.head(5).to_dict(orient="records")
    best_trades = df_sorted.tail(5).to_dict(orient="records")

    win_rate = (df_trades["target_win"] == 1).mean() * 100
    total_pnl = df_trades["pnl_pct"].sum()

    prompt = f"""Eres un Analista Cuantitativo Jefe de un Hedge Fund.
He aquí un resumen tabular del rendimiento reciente de nuestro bot de trading algorítmico.

### Métricas Globales
- Operaciones Totales: {len(df_trades)}
- Win Rate: {win_rate:.2f}%
- PnL Acumulado (Porcentual): {total_pnl:.2f}%

### Top 5 Peores Operaciones (Pérdidas)
{json.dumps(worst_trades, indent=2)}

### Top 5 Mejores Operaciones (Ganancias)
{json.dumps(best_trades, indent=2)}

Por favor, analiza estos datos (variables 'ind_') y redacta un reporte Markdown con:
1. **Patrones en las Pérdidas**: indicadores presentes en trades perdedores.
2. **Patrones en las Ganancias**: qué funcionó bien.
3. **Recomendaciones**: 3 acciones para ajustar umbrales del bot y mejorar Win Rate.
Sé directo, profesional y muy analítico. Responde en español.
"""

    url = _GEMINI_GENERATE_URL
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {
            "parts": [{"text": "Act as a professional Quantitative Trading Analyst."}]
        },
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048,
        },
    }

    try:
        logger.info("Enviando datos a Gemini para análisis...")
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

            text_response = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            if not text_response:
                return "Gemini devolvió una respuesta vacía."

            return text_response

    except Exception as exc:
        logger.error("Error al comunicarse con Gemini: %s", exc)
        return f"Error en la auditoría IA: {exc}"
