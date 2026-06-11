"""Fábrica de informes institucionales para la Mesa de Dinero Virtual.

Implementa un patrón Factory para crear diferentes tipos de informes especializados:
- Análisis Técnico
- Opciones (GEX/Gamma)
- Fundamental
- Predictivo
- Sentimiento de Mercado
- Riesgo Soberano (Argentina)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReportType(str, Enum):
    """Tipos de informes disponibles"""

    TECHNICAL = "technical"
    OPTIONS = "options"
    FUNDAMENTAL = "fundamental"
    PREDICTIVE = "predictive"
    SENTIMENT = "sentiment"
    SOVEREIGN_RISK = "sovereign_risk"
    COMPOSITE = "composite"


class RiskLevel(str, Enum):
    """Niveles de riesgo"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class DataSourceInfo:
    """Información sobre la fuente de datos"""

    name: str
    reliability: float  # 0.0 - 1.0
    last_updated: datetime
    limitations: list[str] = field(default_factory=list)


class ReportMetadata(BaseModel):
    """Metadatos del informe"""

    report_id: str
    created_at: datetime
    symbol: str
    report_type: ReportType
    data_sources: list[DataSourceInfo]
    confidence_score: float = Field(ge=0.0, le=1.0)
    version: str = "1.0"


class ExecutiveSummary(BaseModel):
    """Resumen ejecutivo del informe"""

    title: str
    key_insights: list[str]
    recommendation: str
    risk_level: RiskLevel
    timeframe: str


class TechnicalReportData(BaseModel):
    """Datos específicos de informe técnico"""

    trend: str  # bullish, bearish, neutral
    support_levels: list[float]
    resistance_levels: list[float]
    volatility_regime: str
    volume_profile: dict[str, Any] = Field(default_factory=dict)
    regime_probability: float
    technical_indicators: dict[str, float] = Field(default_factory=dict)


class OptionsReportData(BaseModel):
    """Datos específicos de informe de opciones"""

    spot_price: float
    gamma_exposure: dict[str, Any] = Field(default_factory=dict)
    volatility_skew: dict[str, Any] = Field(default_factory=dict)
    max_pain: float
    pin_risk: float
    dealer_bias: str  # bullish, bearish, neutral
    gex_safe: bool
    key_strikes: list[float] = Field(default_factory=list)


class FundamentalReportData(BaseModel):
    """Datos específicos de informe fundamental"""

    company_name: str
    sector: str
    market_cap: float
    pe_ratio: float | None
    debt_to_equity: float | None
    roe: float | None
    revenue_growth: float | None
    quality_score: float  # 0.0 - 1.0
    red_flags: list[str] = Field(default_factory=list)
    valuation_metrics: dict[str, float] = Field(default_factory=dict)


class PredictiveReportData(BaseModel):
    """Datos específicos de informe predictivo"""

    tail_risk: float
    var_99: float
    cvar_99: float
    jump_probability: float
    regime_state: str  # ordered, chaotic, transitional
    kelly_fraction: float
    expected_move: float
    confidence_interval: float


class SentimentReportData(BaseModel):
    """Datos específicos de informe de sentimiento"""

    sentiment_score: float  # -1.0 to 1.0
    buzz_score: float
    social_volume: int
    trending_topics: list[str] = Field(default_factory=list)
    sentiment_breakdown: dict[str, float] = Field(default_factory=dict)
    news_sentiment: dict[str, Any] = Field(default_factory=dict)


class SovereignRiskReportData(BaseModel):
    """Datos específicos de informe de riesgo soberano"""

    country: str
    risk_premium: float
    currency_breach: float
    political_risk: float
    economic_indicators: dict[str, float] = Field(default_factory=dict)
    external_debt: float
    reserves_coverage: float


class BaseReport(ABC, BaseModel):
    """Clase base abstracta para todos los informes"""

    metadata: ReportMetadata
    executive_summary: ExecutiveSummary
    created_at: datetime = Field(default_factory=datetime.now)

    @abstractmethod
    def get_report_type(self) -> ReportType:
        """Devuelve el tipo de informe"""
        pass

    @abstractmethod
    def validate_data(self) -> bool:
        """Valida la integridad de los datos del informe"""
        pass


class TechnicalReport(BaseReport):
    """Informe de análisis técnico"""

    technical_data: TechnicalReportData
    price_action_analysis: str
    trend_implications: str

    def get_report_type(self) -> ReportType:
        return ReportType.TECHNICAL

    def validate_data(self) -> bool:
        return (
            self.technical_data.trend in ["bullish", "bearish", "neutral"]
            and len(self.technical_data.support_levels) > 0
            and len(self.technical_data.resistance_levels) > 0
        )


class OptionsReport(BaseReport):
    """Informe de análisis de opciones"""

    options_data: OptionsReportData
    gamma_analysis: str
    volatility_outlook: str
    hedging_implications: str

    def get_report_type(self) -> ReportType:
        return ReportType.OPTIONS

    def validate_data(self) -> bool:
        return (
            self.options_data.spot_price > 0
            and self.options_data.max_pain > 0
            and self.options_data.dealer_bias in ["bullish", "bearish", "neutral"]
        )


class FundamentalReport(BaseReport):
    """Informe de análisis fundamental"""

    fundamental_data: FundamentalReportData
    financial_health: str
    valuation_assessment: str
    investment_rationale: str

    def get_report_type(self) -> ReportType:
        return ReportType.FUNDAMENTAL

    def validate_data(self) -> bool:
        return (
            self.fundamental_data.company_name is not None
            and self.fundamental_data.sector is not None
            and self.fundamental_data.quality_score >= 0.0
        )


class PredictiveReport(BaseReport):
    """Informe de análisis predictivo"""

    predictive_data: PredictiveReportData
    risk_forecast: str
    sizing_recommendation: str
    scenario_analysis: str

    def get_report_type(self) -> ReportType:
        return ReportType.PREDICTIVE

    def validate_data(self) -> bool:
        return (
            0.0 <= self.predictive_data.tail_risk <= 1.0
            and self.predictive_data.var_99 < 0  # VaR es negativo
            and 0.0 <= self.predictive_data.jump_probability <= 1.0
        )


class SentimentReport(BaseReport):
    """Informe de análisis de sentimiento"""

    sentiment_data: SentimentReportData
    market_mood: str
    contrarian_signals: str
    social_impact: str

    def get_report_type(self) -> ReportType:
        return ReportType.SENTIMENT

    def validate_data(self) -> bool:
        return (
            -1.0 <= self.sentiment_data.sentiment_score <= 1.0
            and self.sentiment_data.buzz_score >= 0
        )


class SovereignRiskReport(BaseReport):
    """Informe de análisis de riesgo soberano"""

    sovereign_data: SovereignRiskReportData
    macro_impact: str
    currency_risk: str
    investment_climate: str

    def get_report_type(self) -> ReportType:
        return ReportType.SOVEREIGN_RISK

    def validate_data(self) -> bool:
        return (
            self.sovereign_data.country is not None
            and self.sovereign_data.risk_premium >= 0
            and 0 <= self.sovereign_data.political_risk <= 1.0
        )


class CompositeReport(BaseReport):
    """Informe compuesto que combina múltiples análisis"""

    technical_report: TechnicalReport | None = None
    options_report: OptionsReport | None = None
    fundamental_report: FundamentalReport | None = None
    predictive_report: PredictiveReport | None = None
    sentiment_report: SentimentReport | None = None
    sovereign_risk_report: SovereignRiskReport | None = None
    unified_thesis: str
    portfolio_implications: str
    risk_consolidation: str

    def get_report_type(self) -> ReportType:
        return ReportType.COMPOSITE

    def validate_data(self) -> bool:
        # Al menos un informe componente debe existir
        return any(
            [
                self.technical_report,
                self.options_report,
                self.fundamental_report,
                self.predictive_report,
                self.sentiment_report,
                self.sovereign_risk_report,
            ]
        )


class ReportFactory:
    """Fábrica para crear diferentes tipos de informes"""

    @staticmethod
    def create_report(
        report_type: ReportType, symbol: str, data: dict[str, Any], sources: list[DataSourceInfo]
    ) -> BaseReport:
        """Crea un informe del tipo especificado"""

        metadata = ReportMetadata(
            report_id=f"{report_type}_{symbol}_{datetime.now().timestamp()}",
            created_at=datetime.now(),
            symbol=symbol,
            report_type=report_type,
            data_sources=sources,
            confidence_score=data.get("confidence_score", 0.5),
            version="1.0",
        )

        executive_summary = ExecutiveSummary(
            title=f"Informe {report_type.value.capitalize()} para {symbol}",
            key_insights=data.get("key_insights", []),
            recommendation=data.get("recommendation", "Mantener"),
            risk_level=data.get("risk_level", RiskLevel.MEDIUM),
            timeframe=data.get("timeframe", "1-4 semanas"),
        )

        if report_type == ReportType.TECHNICAL:
            technical_data = TechnicalReportData(**data.get("technical_data", {}))
            return TechnicalReport(
                metadata=metadata,
                executive_summary=executive_summary,
                technical_data=technical_data,
                price_action_analysis=data.get("price_action_analysis", ""),
                trend_implications=data.get("trend_implications", ""),
            )

        elif report_type == ReportType.OPTIONS:
            options_data = OptionsReportData(**data.get("options_data", {}))
            return OptionsReport(
                metadata=metadata,
                executive_summary=executive_summary,
                options_data=options_data,
                gamma_analysis=data.get("gamma_analysis", ""),
                volatility_outlook=data.get("volatility_outlook", ""),
                hedging_implications=data.get("hedging_implications", ""),
            )

        elif report_type == ReportType.FUNDAMENTAL:
            fundamental_data = FundamentalReportData(**data.get("fundamental_data", {}))
            return FundamentalReport(
                metadata=metadata,
                executive_summary=executive_summary,
                fundamental_data=fundamental_data,
                financial_health=data.get("financial_health", ""),
                valuation_assessment=data.get("valuation_assessment", ""),
                investment_rationale=data.get("investment_rationale", ""),
            )

        elif report_type == ReportType.PREDICTIVE:
            predictive_data = PredictiveReportData(**data.get("predictive_data", {}))
            return PredictiveReport(
                metadata=metadata,
                executive_summary=executive_summary,
                predictive_data=predictive_data,
                risk_forecast=data.get("risk_forecast", ""),
                sizing_recommendation=data.get("sizing_recommendation", ""),
                scenario_analysis=data.get("scenario_analysis", ""),
            )

        elif report_type == ReportType.SENTIMENT:
            sentiment_data = SentimentReportData(**data.get("sentiment_data", {}))
            return SentimentReport(
                metadata=metadata,
                executive_summary=executive_summary,
                sentiment_data=sentiment_data,
                market_mood=data.get("market_mood", ""),
                contrarian_signals=data.get("contrarian_signals", ""),
                social_impact=data.get("social_impact", ""),
            )

        elif report_type == ReportType.SOVEREIGN_RISK:
            sovereign_data = SovereignRiskReportData(**data.get("sovereign_data", {}))
            return SovereignRiskReport(
                metadata=metadata,
                executive_summary=executive_summary,
                sovereign_data=sovereign_data,
                macro_impact=data.get("macro_impact", ""),
                currency_risk=data.get("currency_risk", ""),
                investment_climate=data.get("investment_climate", ""),
            )

        else:
            raise ValueError(f"Tipo de informe no soportado: {report_type}")


# Sistema de generación de informes especializados
class SpecializedReportGenerator:
    """Generador de informes especializados para diferentes dominios"""

    @staticmethod
    def generate_technical_report(symbol: str, data: dict[str, Any]) -> TechnicalReport:
        """Genera un informe técnico especializado"""
        sources = [
            DataSourceInfo(
                name="FMP API",
                reliability=0.95,
                last_updated=datetime.now(),
                limitations=["Delayed data", "Limited historical depth"],
            )
        ]

        return ReportFactory.create_report(ReportType.TECHNICAL, symbol, data, sources)

    @staticmethod
    def generate_options_report(symbol: str, data: dict[str, Any]) -> OptionsReport:
        """Genera un informe de opciones especializado"""
        sources = [
            DataSourceInfo(
                name="Polygon API",
                reliability=0.90,
                last_updated=datetime.now(),
                limitations=["Snapshot data only", "No real-time Greeks"],
            )
        ]

        return ReportFactory.create_report(ReportType.OPTIONS, symbol, data, sources)

    @staticmethod
    def generate_fundamental_report(symbol: str, data: dict[str, Any]) -> FundamentalReport:
        """Genera un informe fundamental especializado"""
        sources = [
            DataSourceInfo(
                name="FMP Financials",
                reliability=0.98,
                last_updated=datetime.now(),
                limitations=["Quarterly data", "Potential restatements"],
            )
        ]

        return ReportFactory.create_report(ReportType.FUNDAMENTAL, symbol, data, sources)

    @staticmethod
    def generate_predictive_report(symbol: str, data: dict[str, Any]) -> PredictiveReport:
        """Genera un informe predictivo especializado"""
        sources = [
            DataSourceInfo(
                name="Internal Probabilistic Engine",
                reliability=0.85,
                last_updated=datetime.now(),
                limitations=["Model assumptions", "Historical data dependency"],
            )
        ]

        return ReportFactory.create_report(ReportType.PREDICTIVE, symbol, data, sources)

    @staticmethod
    def generate_sentiment_report(symbol: str, data: dict[str, Any]) -> SentimentReport:
        """Genera un informe de sentimiento especializado"""
        sources = [
            DataSourceInfo(
                name="Social Media APIs",
                reliability=0.75,
                last_updated=datetime.now(),
                limitations=["Sampling bias", "Language processing errors"],
            )
        ]

        return ReportFactory.create_report(ReportType.SENTIMENT, symbol, data, sources)

    @staticmethod
    def generate_sovereign_risk_report(country: str, data: dict[str, Any]) -> SovereignRiskReport:
        """Genera un informe de riesgo soberano especializado"""
        sources = [
            DataSourceInfo(
                name="ArgentinaDatos API",
                reliability=0.88,
                last_updated=datetime.now(),
                limitations=["Government data delays", "Political volatility"],
            )
        ]

        return ReportFactory.create_report(ReportType.SOVEREIGN_RISK, country, data, sources)

    @staticmethod
    def generate_composite_report(
        symbol: str, reports: list[BaseReport], unified_analysis: str
    ) -> CompositeReport:
        """Genera un informe compuesto que combina múltiples análisis"""

        # Organizar informes por tipo
        report_map = {report.get_report_type(): report for report in reports}

        # Crear fuentes combinadas
        all_sources = []
        for report in reports:
            all_sources.extend(report.metadata.data_sources)

        metadata = ReportMetadata(
            report_id=f"composite_{symbol}_{datetime.now().timestamp()}",
            created_at=datetime.now(),
            symbol=symbol,
            report_type=ReportType.COMPOSITE,
            data_sources=all_sources,
            confidence_score=(
                sum(r.metadata.confidence_score for r in reports) / len(reports) if reports else 0.5
            ),
            version="1.0",
        )

        executive_summary = ExecutiveSummary(
            title=f"Informe Compuesto para {symbol}",
            key_insights=[],
            recommendation="Análisis en progreso",
            risk_level=RiskLevel.MEDIUM,
            timeframe="1-12 meses",
        )

        return CompositeReport(
            metadata=metadata,
            executive_summary=executive_summary,
            technical_report=report_map.get(ReportType.TECHNICAL),
            options_report=report_map.get(ReportType.OPTIONS),
            fundamental_report=report_map.get(ReportType.FUNDAMENTAL),
            predictive_report=report_map.get(ReportType.PREDICTIVE),
            sentiment_report=report_map.get(ReportType.SENTIMENT),
            sovereign_risk_report=report_map.get(ReportType.SOVEREIGN_RISK),
            unified_thesis=unified_analysis,
            portfolio_implications="Análisis de implicaciones de cartera en progreso",
            risk_consolidation="Consolidación de riesgos en progreso",
        )
