"""Contratos Pydantic v2 para thesis institucional multi-bloque (ThesisV2).

Campo → fuente (documentación de trazabilidad):
- opciones: layer_3 gex_opciones/service (cadena/GEX; hoy UNAVAILABLE hasta integrar Massive/Polygon + motor).
- tecnico: layer_3 tecnico/service sobre OHLCV interno (histórico FMP u otra fuente de precios).
- fundamental: layer_3 fundamentales/service vía FMP (requiere claves FMP).
- probabilistico: layer_3 ia_probabilistico + retornos OHLCV (EVT, régimen, Kelly, saltos).
- agentes: layer_4 AgentManager.orquestar_analisis (LLM; opcional vía THESIS_ENABLE_AGENTS).
- ejecutivo: síntesis del orquestador LLM o resumen heurístico cuando agentes no corren.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ThesisBlock(BaseModel):
    """Un bloque temático con métricas explícitas y trazabilidad de fuente."""

    model_config = ConfigDict(frozen=False)

    metrics: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(
        ...,
        description='Identificador de procedencia: "FMP", "INTERNAL_OHLCV", '
        '"MULTIMODAL_ENGINE", "LLM_ORCHESTRATION", "UNAVAILABLE", etc.',
    )
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Confianza agregada del bloque (0 = sin señal útil).",
    )
    institutional_narrative: str | None = Field(
        default=None,
        description="Texto explicativo institucional generado por el agente de dominio (si aplica).",
    )
    narrative_agent: str | None = Field(
        default=None,
        description="Nombre del agente LLM que produjo la narrativa (p.ej. options_gex, technical).",
    )


class ThesisV2(BaseModel):
    """Vista institucional estructurada; cada bloque es independiente y auditables sus limitaciones."""

    model_config = ConfigDict(frozen=True)

    opciones: ThesisBlock
    tecnico: ThesisBlock
    fundamental: ThesisBlock
    probabilistico: ThesisBlock
    agentes: ThesisBlock
    ejecutivo: ThesisBlock


class ReportMetric(BaseModel):
    """Metrica lista para renderizar en un reporte institucional."""

    label: str
    value: str | float | int | bool | None = None
    signal: str | None = None
    detail: str | None = None


class ReportSection(BaseModel):
    """Seccion estilo research report, inspirada en formato broker/quant."""

    title: str
    subtitle: str | None = None
    narrative: str | None = None
    metrics: list[ReportMetric] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class InstitutionalReport(BaseModel):
    """Vista ejecutiva tipo informe: cover, dashboard, secciones y risk monitor."""

    title: str
    symbol: str
    report_date: str
    classification: str = "Internal Distribution"
    composite_verdict: str
    risk_state: str
    horizon: str = "swing"
    data_sources: list[str] = Field(default_factory=list)
    cover_metrics: list[ReportMetric] = Field(default_factory=list)
    sections: list[ReportSection] = Field(default_factory=list)
    strategy_matrix: list[ReportMetric] = Field(default_factory=list)
    risk_monitor: list[ReportMetric] = Field(default_factory=list)
    disclaimers: list[str] = Field(default_factory=list)


class AIThesisResponse(BaseModel):
    """Respuesta GET /thesis/{symbol}: compatibilidad legacy + ThesisV2."""

    model_config = ConfigDict(frozen=False)

    symbol: str
    bias: str
    conviction: float
    sentiment: dict[str, Any] | None = None
    fusion_metadata: Any = Field(default_factory=dict)
    thesis: str
    timestamp: str
    thesis_v2: ThesisV2
    institutional_multimodal_thesis: str | None = Field(
        default=None,
        description="Síntesis multimodal (orquestador) a partir de narrativas por dominio.",
    )
    institutional_report: InstitutionalReport | None = Field(
        default=None,
        description="Esquema renderizable tipo research report institucional.",
    )
