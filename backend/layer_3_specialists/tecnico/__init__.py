"""Lazy exports for the technical specialist package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, str] = {
    "AVWAPAnchorType": ".avwap_models",
    "AVWAPBands": ".avwap_models",
    "AVWAPMath": ".technical",
    "AVWAPResult": ".avwap_models",
    "ConfluenceAction": ".confluence_models",
    "ConfluenceConviction": ".confluence_models",
    "CVDResult": ".volume",
    "DeltaVolumeProfile": ".volume",
    "DeltaVolumeResult": ".volume",
    "DirectionalBias": ".vsa",
    "EntropyScore": ".fractal_models",
    "FairValueGap": ".smc",
    "Candle": ".fvg_engine",
    "FVGAnalysisOutput": ".fvg_engine",
    "FVGConfig": ".fvg_engine",
    "FVGEngine": ".fvg_engine",
    "FVGEvent": ".fvg_engine",
    "FVGStatus": ".fvg_engine",
    "FVGType": ".fvg_engine",
    "FVGZone": ".fvg_engine",
    "FootprintNode": ".vsa_footprint_engine",
    "FractalSignal": ".fractal_models",
    "HMMAnalysisOutput": ".hmm_engine",
    "HMMInferenceEngine": ".hmm_engine",
    "HMMParameters": ".hmm_engine",
    "HMMRegimeResult": ".hmm_engine",
    "IVRegime": ".iv_surface_models",
    "IVSurfaceOutput": ".iv_surface_models",
    "IVSurfaceSignal": ".iv_surface_models",
    "LOBDynamicsAnalysis": ".lob_dynamics_engine",
    "LOBDynamicsEngine": ".lob_dynamics_engine",
    "LOBDynamicsResult": ".lob_dynamics_engine",
    "LOBConfig": ".lob_dynamics_engine",
    "LOBEvent": ".lob_dynamics_engine",
    "LOBEventType": ".lob_dynamics_engine",
    "LOBLevel": ".lob_dynamics_engine",
    "LOBSide": ".lob_dynamics_engine",
    "LOBSnapshot": ".lob_dynamics_engine",
    "MarketState": ".snapshot_models",
    "MarketObservation": ".hmm_engine",
    "MicrostructureConfluenceResult": ".confluence_models",
    "MigrationState": ".vpoc_migration",
    "NodeType": ".volume",
    "L1Snapshot": ".ofi_engine",
    "OFIAnalysisOutput": ".ofi_engine",
    "OFIEngine": ".ofi_engine",
    "OFIEngineConfig": ".ofi_engine",
    "OFIRegime": ".ofi_engine",
    "OFIResult": ".ofi_engine",
    "PDFTailRisk": ".iv_surface_models",
    "PolygonMarketStatus": ".polygon_models",
    "PolygonQuote": ".polygon_models",
    "PolygonSnapshotResponse": ".polygon_models",
    "PolygonSnapshotTicker": ".polygon_models",
    "PutSkewRegime": ".iv_surface_models",
    "SMCFractalEngine": ".smc_fractal_engine",
    "SMCGEXZone": ".confluence_models",
    "SMCOrderBlock": ".smc",
    "SMCResult": ".smc",
    "SMCScore": ".microstructure_confluence",
    "SMCEngine": ".smc",
    "SMCDirectionalBias": ".smc",
    "OHLCBar": ".single_prints",
    "SnapshotBar": ".snapshot_models",
    "SpotVsZGL": ".confluence_models",
    "StructureEventType": ".smc",
    "ScanResult": ".single_prints",
    "SinglePrintConfig": ".single_prints",
    "SinglePrintEngine": ".single_prints",
    "SinglePrintType": ".single_prints",
    "SinglePrintZone": ".single_prints",
    "TechnicalMath": ".technical",
    "TermStructureRegime": ".iv_surface_models",
    "TPOLevel": ".tpo_skewness",
    "TPOProfile": ".tpo_skewness",
    "ProfileShape": ".tpo_skewness",
    "TPOSkewnessConfig": ".tpo_skewness",
    "TPOSkewnessEngine": ".tpo_skewness",
    "TPOSkewnessSignal": ".tpo_skewness",
    "TotalConfluenceScorer": ".microstructure_confluence",
    "VRPSignal": ".iv_surface_models",
    "VSAVannaGEXConfluence": ".microstructure_confluence",
    "VSAVannaGEXResult": ".confluence_models",
    "VSAVannaSignal": ".confluence_models",
    "VSAConfig": ".vsa",
    "VSAEngine": ".vsa",
    "VSAFootprintEngine": ".vsa_footprint_engine",
    "VSAFootprintResult": ".vsa_footprint_engine",
    "VSALabel": ".vsa",
    "VSAResult": ".vsa",
    "PricePoint": ".vwap_engine",
    "VWAPAnalysisOutput": ".vwap_engine",
    "VWAPBands": ".vwap_engine",
    "VWAPCrossDirection": ".avwap_models",
    "VWAPCrossEvent": ".avwap_models",
    "VWAPConfig": ".vwap_engine",
    "VWAPEngine": ".vwap_engine",
    "VWAPService": ".vwap_engine",
    "VWAPSnapshot": ".vwap_engine",
    "VWAPStackConviction": ".avwap_models",
    "VWAPStackResult": ".avwap_models",
    "VWAPState": ".avwap_models",
    "VolumeAnalytics": ".volume",
    "VolumeNodeConfig": ".volume_node_engine",
    "VolumeNodeEngine": ".volume_node_engine",
    "VolumeNodeTopography": ".volume_node_engine",
    "VolumeNodeType": ".volume_node_engine",
    "VolumeProfileEngine": ".volume_profile",
    "VolumeProfileOutput": ".volume_profile",
    "VolumeProfileResult": ".volume",
    "VPOCConfig": ".vpoc_migration",
    "VPOCMigrationEngine": ".vpoc_migration",
    "VPOCMigrationSignal": ".vpoc_migration",
    "VPOCProfile": ".vpoc_migration",
    "TrackingResult": ".single_prints",
    "WyckoffFase": ".confluence_models",
    "WyckoffGEXDecision": ".confluence_models",
    "WyckoffGEXTiming": ".microstructure_confluence",
    "ZoneStatus": ".single_prints",
    "_simple_graph_snapshot_restoration": ".snapshot",
}
_ALIASES: dict[str, tuple[str, str]] = {
    "SMCDirectionalBias": (".smc", "DirectionalBias"),
    "SMCOrderBlock": (".smc", "OrderBlock"),
}

__all__ = sorted(set(_EXPORTS) | set(_ALIASES))


def __getattr__(name: str) -> Any:
    """Load technical exports only when requested."""
    module_name = _EXPORTS.get(name)
    target_name = name
    if module_name is None and name in _ALIASES:
        module_name, target_name = _ALIASES[name]
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, target_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports to introspection."""
    return sorted(set(globals()) | set(__all__))
