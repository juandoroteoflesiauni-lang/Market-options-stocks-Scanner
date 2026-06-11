"""Public exports for the options/GEX specialist package.

The legacy package used to import every engine eagerly, including optional
ML-backed modules. Lazy exports keep backward-compatible package imports while
allowing lightweight integration endpoints to load without torch or other
optional runtime dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "BlackScholesPricer": ".bsm",
    "CONFLUENCE_WEIGHT_GEX": ".confluence_models",
    "CONFLUENCE_WEIGHT_IV": ".confluence_models",
    "CONFLUENCE_WEIGHT_SMC": ".confluence_models",
    "CONFLUENCE_WEIGHT_STRAT": ".confluence_models",
    "CONFLUENCE_WEIGHT_WY_VSA": ".confluence_models",
    "ConfluenceAction": ".confluence_models",
    "ConfluenceConviction": ".confluence_models",
    "DealerExposures": ".options_models",
    "EarningsSetup": ".confluence_models",
    "EarningsStructure": ".confluence_models",
    "ExposureRegime": ".options_models",
    "FMPBalanceSheet": ".fmp_models",
    "FMPCashFlowStatement": ".fmp_models",
    "FMPDCFValuation": ".fmp_models",
    "FMPDividendCalendarItem": ".fmp_models",
    "FMPEarningsCalendarItem": ".fmp_models",
    "FMPEconomicCalendarItem": ".fmp_models",
    "FMPEnterpriseValue": ".fmp_models",
    "FMPFinancialGrowth": ".fmp_models",
    "FMPIPOCalendarItem": ".fmp_models",
    "FMPIncomeStatement": ".fmp_models",
    "FMPIncomeStatementGrowth": ".fmp_models",
    "FMPInstitutionalHolder": ".fmp_models",
    "FMPKeyMetrics": ".fmp_models",
    "FMPKeyMetricsTTM": ".fmp_models",
    "FMPMutualFundHolder": ".fmp_models",
    "FMPNewsItem": ".fmp_models",
    "FMPPressRelease": ".fmp_models",
    "FMPQuote": ".fmp_models",
    "FMPRating": ".fmp_models",
    "FMPShortInterest": ".fmp_models",
    "FMPShortVolume": ".fmp_models",
    "FMPTechnicalIndicator": ".fmp_models",
    "FanChart": ".stochastic_models",
    "GEXLevels": ".confluence_models",
    "GEXMath": ".derivatives",
    "GreekSurface": ".options_models",
    "ImpliedPDFResult": ".derivatives",
    "MicrostructureConfluenceResult": ".confluence_models",
    "OptionType": ".bsm",
    "OptionsConfluenceEngine": ".options_confluence",
    "OptionsEngine": ".options",
    "OptionsResult": ".options_models",
    "OptionsSMCConfluenceResult": ".confluence_models",
    "OptionsSignal": ".options_models",
    "PDFAnalytics": ".options_models",
    "PositioningMetrics": ".options_models",
    "SMCGEXZone": ".confluence_models",
    "SVIParameters": ".derivatives",
    "SpotVsZGL": ".confluence_models",
    "StochasticPredictiveEngine": ".stochastic_predictive",
    "StochasticPredictiveResult": ".stochastic_models",
    "VSAVannaGEXResult": ".confluence_models",
    "VSAVannaSignal": ".confluence_models",
    "VolatilitySurfaceMath": ".derivatives",
    "WyckoffFase": ".confluence_models",
    "WyckoffGEXDecision": ".confluence_models",
    "atm_iv_from_chain": ".iv_primitives",
    "compute_skew_metrics": ".iv_primitives",
    "compute_term_structure": ".iv_primitives",
    "historical_volatility": ".iv_primitives",
    "iv_percentile": ".iv_primitives",
    "iv_rank": ".iv_primitives",
    "rolling_historical_volatility": ".iv_primitives",
    "vrp_log_ratio": ".iv_primitives",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load public package exports only when they are requested."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports to introspection."""
    return sorted(set(globals()) | set(__all__))
