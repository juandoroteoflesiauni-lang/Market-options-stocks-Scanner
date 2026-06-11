"""Orquestador de agentes y motor de tesis institucional para la Mesa de Dinero Virtual.

Implementa un patrón de orquestador que coordina múltiples agentes LLM especializados,
delegando la construcción de bloques a thesis_assembler.assemble_thesis_v2()
y generando tesis de inversión complejas con streaming en tiempo real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from backend.config.logger_setup import get_logger
from backend.domain.thesis_v2 import ThesisV2
from backend.layer_1_data.datos.predictive_storage import PredictiveStorage
from backend.layer_1_data.fetchers.fmp_client import FMPClient
from backend.layer_3_specialists.ia_probabilistico.engines.sentiment_engine import SentimentEngine
from backend.layer_4_orchestration.ai_core.agent_manager import AgentManager
from backend.services.thesis_assembler import assemble_thesis_v2

logger = get_logger(__name__)


def _create_multimodal_predictive_engine() -> Any:
    try:
        from backend.layer_3_specialists.ia_probabilistico.engines.multimodal_predictive import (
            MultimodalPredictiveEngine,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "torch":
            raise
        missing_dependency = exc.name

        class MissingMultimodalPredictiveEngine:
            """Placeholder used when the optional torch stack is not installed."""

            def __init__(self) -> None:
                self.missing_dependency = missing_dependency

            def run_fusion_inference(self, *args: object, **kwargs: object) -> object:
                raise RuntimeError(
                    "MultimodalPredictiveEngine requires optional dependency 'torch'."
                )

        return MissingMultimodalPredictiveEngine()

    return MultimodalPredictiveEngine()


class DataSource(str, Enum):
    """Fuentes de datos para la Mesa de Dinero"""

    OPTIONS = "options"
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    PROBABILISTIC = "probabilistic"
    SENTIMENT = "sentiment"
    ARGENTINA = "argentina"


class AgentType(str, Enum):
    """Tipos de agentes especializados"""

    OPTIONS_GEX = "options_gex"
    TECHNICAL = "technical"
    FORENSIC = "forensic"
    MICROSTRUCTURE = "microstructure"
    MACRO_MICRO = "macro_micro"
    TRANSCRIPT = "transcript_analyst"
    SENTIMENT = "sentiment"
    ORCHESTRATOR = "orchestrator"
    ARGENTINA = "argentina"


@dataclass
class AgentNarrative:
    """Narrativa generada por un agente especializado"""

    agent_type: AgentType
    content: str | None = None
    confidence: float = 0.0
    error: str | None = None


class ThesisReport(BaseModel):
    """Informe estructurado de tesis institucional"""

    symbol: str
    timestamp: datetime
    bias: str
    conviction: float
    narratives: dict[AgentType, AgentNarrative]
    multimodal_synthesis: str | None = None
    data_sources: list[DataSource]
    risk_assessment: dict[str, Any] = Field(default_factory=dict)
    tactical_recommendation: str = ""
    invalidations: list[str] = Field(default_factory=list)


class MesaDineroOrchestrator:
    """Orquestador principal de la Mesa de Dinero Virtual"""

    def __init__(self):
        self.fmp_client = FMPClient()
        self.sentiment_engine = SentimentEngine()
        self.predictive_engine = _create_multimodal_predictive_engine()
        self.predictive_storage = PredictiveStorage()
        self.agent_manager = AgentManager()

    # ── CAMBIO 1: generate_thesis() unificado con assemble_thesis_v2 ───────

    async def generate_thesis(self, symbol: str) -> ThesisReport:
        """Genera una tesis institucional completa delegando a assemble_thesis_v2."""
        symbol = symbol.upper().strip()

        # 1. Fetch OHLCV (necesario para el assembler)
        date_from = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        ohlcv_raw = await self.fmp_client.get_historical_prices(symbol, date_from=date_from)

        if not ohlcv_raw:
            logger.warning(f"No OHLCV data for {symbol}; generating empty thesis.")
            return ThesisReport(
                symbol=symbol,
                timestamp=datetime.now(),
                bias="NEUTRAL",
                conviction=0.0,
                narratives={},
                data_sources=[],
                risk_assessment={
                    "tail_risk": 0.0,
                    "jump_risk": 0.0,
                    "regime_risk": 0.0,
                    "kelly_fraction": 0.0,
                    "etv": 0.0,
                },
                tactical_recommendation="Sin datos suficientes para generar recomendación.",
                invalidations=["Sin datos OHLCV — tesis no generada."],
            )

        # Build DataFrame
        rows = []
        for bar in ohlcv_raw:
            rows.append(
                {
                    "date": getattr(bar, "date", None),
                    "open": getattr(bar, "open", None),
                    "high": getattr(bar, "high", None),
                    "low": getattr(bar, "low", None),
                    "close": getattr(bar, "close", None),
                    "volume": getattr(bar, "volume", None),
                }
            )
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # 2. Delegar al assembler unificado
        try:
            thesis_result = await assemble_thesis_v2(
                sym=symbol,
                df=df,
                fmp_client=self.fmp_client,
                predictive_engine=self.predictive_engine,
                sentiment_engine=self.sentiment_engine,
                agent_manager=self.agent_manager,
            )
        except TypeError as exc:
            if "agent_manager" not in str(exc):
                raise
            thesis_result = await assemble_thesis_v2(
                sym=symbol,
                df=df,
                fmp_client=self.fmp_client,
                predictive_engine=self.predictive_engine,
                sentiment_engine=self.sentiment_engine,
            )
        if len(thesis_result) == 3:
            thesis_v2, multimodal_text, narr = thesis_result
        else:
            thesis_v2, multimodal_text = thesis_result
            narr = None

        # 3. Extraer bias y conviction del ejecutivo_block
        bias = "NEUTRAL"
        conviction = 0.5
        exec_narrative = thesis_v2.ejecutivo.institutional_narrative or ""
        exec_confidence = thesis_v2.ejecutivo.confidence

        if exec_narrative:
            lower_narr = exec_narrative.lower()
            if "bullish" in lower_narr or "alcista" in lower_narr or "comprar" in lower_narr:
                bias = "BULLISH"
                conviction = max(0.65, exec_confidence)
            elif "bearish" in lower_narr or "bajista" in lower_narr or "vender" in lower_narr:
                bias = "BEARISH"
                conviction = max(0.65, exec_confidence)
            else:
                conviction = exec_confidence

        # 4. Build narratives dict from ThesisV2 blocks
        narratives: dict[AgentType, AgentNarrative] = {}
        # Mapa AgentType → nombre interno de agente (para buscar errores en narr.agent_errors)
        _agent_key_map = {
            AgentType.OPTIONS_GEX: "options_gex",
            AgentType.TECHNICAL: "technical",
            AgentType.FORENSIC: "forensic",
            AgentType.MICROSTRUCTURE: "microstructure",
            AgentType.ORCHESTRATOR: "orchestrator",
        }
        block_map = {
            AgentType.OPTIONS_GEX: thesis_v2.opciones,
            AgentType.TECHNICAL: thesis_v2.tecnico,
            AgentType.FORENSIC: thesis_v2.fundamental,
            AgentType.MICROSTRUCTURE: thesis_v2.probabilistico,
            AgentType.ORCHESTRATOR: thesis_v2.ejecutivo,
        }
        _narr_errors = narr.agent_errors if narr is not None else {}
        for agent_type, block in block_map.items():
            agent_key = _agent_key_map.get(agent_type, "")
            narratives[agent_type] = AgentNarrative(
                agent_type=agent_type,
                content=block.institutional_narrative,
                confidence=block.confidence,
                error=_narr_errors.get(agent_key),
            )
        if narr is not None:
            if narr.macro:
                narratives[AgentType.MACRO_MICRO] = AgentNarrative(
                    agent_type=AgentType.MACRO_MICRO,
                    content=narr.macro,
                    confidence=0.75,
                )
            elif _narr_errors.get("macro_micro"):
                narratives[AgentType.MACRO_MICRO] = AgentNarrative(
                    agent_type=AgentType.MACRO_MICRO,
                    error=_narr_errors["macro_micro"],
                )
            if narr.transcript:
                narratives[AgentType.TRANSCRIPT] = AgentNarrative(
                    agent_type=AgentType.TRANSCRIPT,
                    content=narr.transcript,
                    confidence=0.75,
                )
            elif _narr_errors.get("transcript_analyst"):
                narratives[AgentType.TRANSCRIPT] = AgentNarrative(
                    agent_type=AgentType.TRANSCRIPT,
                    error=_narr_errors["transcript_analyst"],
                )
            if narr.sentiment:
                narratives[AgentType.SENTIMENT] = AgentNarrative(
                    agent_type=AgentType.SENTIMENT,
                    content=narr.sentiment,
                    confidence=0.75,
                )
            elif _narr_errors.get("sentiment"):
                narratives[AgentType.SENTIMENT] = AgentNarrative(
                    agent_type=AgentType.SENTIMENT,
                    error=_narr_errors["sentiment"],
                )

        # 5. Data sources
        data_sources = [DataSource.TECHNICAL, DataSource.FUNDAMENTAL, DataSource.PROBABILISTIC]
        if thesis_v2.opciones.source != "UNAVAILABLE":
            data_sources.append(DataSource.OPTIONS)

        # 6. Risk + invalidations from ThesisV2
        risk_assessment = self._assess_risk(thesis_v2)
        invalidations = self._check_invalidations(thesis_v2)

        return ThesisReport(
            symbol=symbol,
            timestamp=datetime.now(),
            bias=bias,
            conviction=conviction,
            narratives=narratives,
            multimodal_synthesis=multimodal_text,
            data_sources=data_sources,
            risk_assessment=risk_assessment,
            tactical_recommendation=self._generate_tactical_recommendation(thesis_v2),
            invalidations=invalidations,
        )

    # ── CAMBIO 2: _assess_risk y _check_invalidations con datos reales ─────

    def _assess_risk(self, thesis_v2: ThesisV2) -> dict[str, Any]:
        """Evalúa el riesgo basado en métricas reales del bloque probabilístico."""
        metrics = thesis_v2.probabilistico.metrics or {}
        return {
            "tail_risk": float(metrics.get("cvar_99", 0.0)),
            "jump_risk": float(metrics.get("jump_probability", 0.0)),
            "regime_risk": 1.0 - float(metrics.get("pr_ordered_regime", 0.5)),
            "kelly_fraction": float(metrics.get("kelly_full", 0.0)),
            "etv": float(metrics.get("etv", 0.0)),
        }

    def _generate_tactical_recommendation(self, thesis_v2: ThesisV2) -> str:
        """Genera recomendación táctica basada en métricas reales y narrativa ejecutiva."""
        metrics = thesis_v2.probabilistico.metrics or {}
        exec_narr = thesis_v2.ejecutivo.institutional_narrative or ""
        lower = exec_narr.lower()

        bias = "neutral"
        if "bullish" in lower or "alcista" in lower:
            bias = "bullish"
        elif "bearish" in lower or "bajista" in lower:
            bias = "bearish"

        kelly = float(metrics.get("kelly_full", 0.0))
        cvar = float(metrics.get("cvar_99", 0.0))
        jump = float(metrics.get("jump_probability", 0.0))
        gate_veto = bool(metrics.get("gate_veto", True))

        if gate_veto or cvar > 0.07 or jump > 0.20:
            return "Condiciones de riesgo elevado activas — reducir exposición o esperar confirmación antes de operar."

        if bias == "bullish":
            if kelly > 0.15:
                return f"Sesgo alcista con Kelly {kelly:.1%} — posición larga moderada sugerida con stops bajo soporte técnico."
            return "Sesgo alcista débil — sizing conservador; aguardar confirmación de volumen antes de agregar."
        elif bias == "bearish":
            if kelly > 0.15:
                return f"Sesgo bajista con Kelly {kelly:.1%} — reducir exposición larga o considerar cobertura."
            return "Sesgo bajista débil — mantener stops ajustados; no agregar posición hasta nuevo catalizador."

        return "Régimen sin sesgo claro — mantener posición actual con sizing reducido hasta definición direccional."

    def _check_invalidations(self, thesis_v2: ThesisV2) -> list[str]:
        """Verifica invalidaciones reales basadas en umbrales de métricas probabilísticas."""
        invalidations: list[str] = []
        metrics = thesis_v2.probabilistico.metrics or {}

        cvar_99 = float(metrics.get("cvar_99", 0.0))
        jump_prob = float(metrics.get("jump_probability", 0.0))
        kelly_full = float(metrics.get("kelly_full", 0.0))
        pr_ordered = float(metrics.get("pr_ordered_regime", 0.5))

        if cvar_99 > 0.05:
            invalidations.append("CVaR 99% supera 5% — riesgo de cola elevado")
        if jump_prob > 0.15:
            invalidations.append("Probabilidad de salto > 15% — evitar posiciones no protegidas")
        if kelly_full < 0.05:
            invalidations.append("Kelly fraction < 5% — sizing mínimo recomendado")
        if pr_ordered < 0.4:
            invalidations.append("Régimen de mercado incierto — reducir convicción direccional")

        exec_narr = thesis_v2.ejecutivo.institutional_narrative or ""
        if "[INFERIDO]" in exec_narr:
            invalidations.append(
                "Tesis parcialmente inferida — validar con datos adicionales antes de operar"
            )

        return invalidations


# Funciones utilitarias para optimización de tokens
def truncate_dataframe(df: pd.DataFrame, max_rows: int = 50) -> pd.DataFrame:
    """Trunca un DataFrame para minimizar el uso de tokens"""
    if len(df) > max_rows:
        return df.tail(max_rows)
    return df


def summarize_dict(data: dict[str, Any], max_length: int = 1000) -> str:
    """Resume un diccionario para minimizar el uso de tokens"""
    try:
        json_str = json.dumps(data, default=str)
        if len(json_str) > max_length:
            return json_str[: max_length - 3] + "..."
        return json_str
    except Exception:
        return str(data)[:max_length]


def cache_context_data(symbol: str, context: str, redis_client=None) -> None:
    """Cachea datos de contexto para evitar llamadas API redundantes"""
    if redis_client:
        try:
            cache_key = f"context:{symbol}"
            redis_client.setex(cache_key, 600, context)
        except Exception as e:
            logger.warning(f"Failed to cache context data: {e}")


# Sistema de streaming para respuestas en tiempo real
class ThesisStreamManager:
    """Gestiona el streaming de respuestas de tesis en tiempo real"""

    def __init__(self):
        self.active_streams = {}

    async def start_thesis_stream(self, symbol: str, client_id: str) -> str:
        stream_id = f"{symbol}_{client_id}_{datetime.now().timestamp()}"
        self.active_streams[stream_id] = {
            "symbol": symbol,
            "client_id": client_id,
            "start_time": datetime.now(),
            "status": "started",
        }
        return stream_id

    async def update_stream(self, stream_id: str, content: str, is_complete: bool = False) -> None:
        if stream_id in self.active_streams:
            self.active_streams[stream_id]["last_update"] = datetime.now()
            self.active_streams[stream_id]["content"] = content
            self.active_streams[stream_id]["is_complete"] = is_complete

    async def close_stream(self, stream_id: str) -> None:
        if stream_id in self.active_streams:
            del self.active_streams[stream_id]


# Punto de entrada para la API
async def generate_institutional_thesis(symbol: str) -> ThesisReport:
    """Punto de entrada principal para generar una tesis institucional"""
    orchestrator = MesaDineroOrchestrator()
    return await orchestrator.generate_thesis(symbol)
