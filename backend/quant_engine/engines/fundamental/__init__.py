from __future__ import annotations
"""Contratos y motores del especialista de Fundamentales."""


from backend.domain.regulatory_models import (
    RegulatoryActionDirective,
    RegulatorySeverityLevel,
    RegulatoryVetoResult,
)

from .credit_models import (
    CreditRegime,
    CreditRiskResult,
    DTSResult,
    FixedIncomeResult,
    MertonResult,
)
from .event_models import (
    BuilderPayload,
    EarningsReportData,
    EarningsReportDataExtended,
    FedTone,
    FinancialEventType,
    GuidanceDirection,
    MacroEventData,
    MacroEventDataExtended,
    StructuredEventResult,
    TypedParseResult,
)
from .flow_models import (
    BacktestInput,
    BacktestResult,
    DIXResult,
    InsiderFlowProfile,
    InstitutionalFlowProfile,
    InstitutionalHolder,
    MacroLiquidityInput,
    NetLiquidityMetrics,
    RawTransaction,
    TradeRecord,
)
from .forensic_models import (
    AltmanForensicInput,
    AltmanForensicResult,
    BeneishForensicInput,
    BeneishForensicResult,
    ForensicAuditEnvelope,
    PiotroskiForensicInput,
    PiotroskiForensicResult,
)
from .fundamental_models import ValueCreationInput, ValueCreationResult
from .pillar_scorer import PillarScorer, PillarScores, PillarWeights
from .scoring_engine import calcular_score, calcular_scores, get_calificacion
from .smart_money import InsiderFlowCalculator, InstitutionalFlowCalculator
from .statements import (
    BalanceSheet,
    CashFlowStatement,
    FinancialStatements,
    IncomeStatement,
    OptionsChainSnapshot,
    ValuationMetrics,
)
from .valuation import DCFValuationModel, FundamentalValuation
from .value_creation import ValueCreationCalculator

__all__ = [
    "AltmanForensicInput",
    "AltmanForensicResult",
    "BacktestInput",
    "BacktestResult",
    "BalanceSheet",
    "BeneishForensicInput",
    "BeneishForensicResult",
    "BuilderPayload",
    "CashFlowStatement",
    "CreditRegime",
    "CreditRiskResult",
    "DCFValuationModel",
    "DIXResult",
    "DTSResult",
    "EarningsReportData",
    "EarningsReportDataExtended",
    "FedTone",
    "FinancialEventType",
    "FinancialStatements",
    "FixedIncomeResult",
    "ForensicAuditEnvelope",
    "FundamentalValuation",
    "GuidanceDirection",
    "IncomeStatement",
    "InsiderFlowCalculator",
    "InsiderFlowProfile",
    "InstitutionalFlowCalculator",
    "InstitutionalFlowProfile",
    "InstitutionalHolder",
    "MacroEventData",
    "MacroEventDataExtended",
    "MacroLiquidityInput",
    "MertonResult",
    "NetLiquidityMetrics",
    "OptionsChainSnapshot",
    "PillarScorer",
    "PillarScores",
    "PillarWeights",
    "PiotroskiForensicInput",
    "PiotroskiForensicResult",
    "RawTransaction",
    "RegulatoryActionDirective",
    "RegulatorySeverityLevel",
    "RegulatoryVetoResult",
    "StructuredEventResult",
    "TradeRecord",
    "TypedParseResult",
    "ValuationMetrics",
    "ValueCreationCalculator",
    "ValueCreationInput",
    "ValueCreationResult",
    "calcular_score",
    "calcular_scores",
    "get_calificacion",
]
