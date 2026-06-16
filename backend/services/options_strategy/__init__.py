"""Módulo Options Strategy — orquestación R1. # [PD-3]"""

from backend.models.options_strategy import (
    NormalizedFeatures,
    OptionsLayerOutput,
    OptionsStrategyAuditLog,
    OptionsStrategyCalibrationReport,
    OptionsStrategyCandidate,
    OptionsStrategyRunResult,
    OptionsTradeOutcome,
    PlaybookCalibrationStats,
    PlaybookDecision,
    PredictiveLayerOutput,
    StructureSelection,
    TechnicalLayerOutput,
    merge_all_layer_features,
    merge_layer_features,
)
from backend.services.options_strategy.alpaca_executor import (
    AlpacaOptionsExecutor,
    build_alpaca_options_order,
)
from backend.services.options_strategy.audit_store import (
    AuditPersistResult,
    OptionsStrategyAuditStore,
)
from backend.services.options_strategy.calibration_loop import (
    OptionsStrategyCalibrationLoop,
    load_calibrated_config_bundle,
)
from backend.services.options_strategy.calibration_store import (
    CalibrationPersistResult,
    OptionsStrategyCalibrationStore,
)
from backend.services.options_strategy.contract_selector import ContractSelector
from backend.services.options_strategy.execution_store import (
    ExecutionPersistResult,
    OptionsStrategyExecutionStore,
)
from backend.services.options_strategy.exit_manager import ExitManager
from backend.services.options_strategy.fusion_router import FusionRouter, fuse_features
from backend.services.options_strategy.options_layer import OptionsLayer
from backend.services.options_strategy.outcome_store import (
    OptionsStrategyOutcomeStore,
    OutcomePersistResult,
)
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline
from backend.services.options_strategy.playbook_matcher import PlaybookMatcher
from backend.services.options_strategy.predictive_layer import PredictiveLayer
from backend.services.options_strategy.risk_engine import RiskEngine
from backend.services.options_strategy.signal_loop import (
    OptionsStrategySignalLoop,
    SignalLoopEntry,
    SignalLoopReport,
)
from backend.services.options_strategy.structure_selector import StructureSelector
from backend.services.options_strategy.technical_layer import TechnicalLayer
from backend.services.options_strategy.veto_engine import VetoEngine, VetoResult

__all__ = [
    "AlpacaOptionsExecutor",
    "AuditPersistResult",
    "CalibrationPersistResult",
    "ContractSelector",
    "ExecutionPersistResult",
    "ExitManager",
    "FusionRouter",
    "NormalizedFeatures",
    "OptionsLayer",
    "OptionsLayerOutput",
    "OptionsStrategyAuditLog",
    "OptionsStrategyAuditStore",
    "OptionsStrategyCalibrationLoop",
    "OptionsStrategyCalibrationReport",
    "OptionsStrategyCalibrationStore",
    "OptionsStrategyCandidate",
    "OptionsStrategyExecutionStore",
    "OptionsStrategyOutcomeStore",
    "OptionsStrategyPipeline",
    "OptionsStrategyRunResult",
    "OptionsStrategySignalLoop",
    "OptionsTradeOutcome",
    "OutcomePersistResult",
    "PlaybookCalibrationStats",
    "PlaybookDecision",
    "PlaybookMatcher",
    "PredictiveLayer",
    "PredictiveLayerOutput",
    "RiskEngine",
    "SignalLoopEntry",
    "SignalLoopReport",
    "StructureSelection",
    "StructureSelector",
    "TechnicalLayer",
    "TechnicalLayerOutput",
    "VetoEngine",
    "VetoResult",
    "build_alpaca_options_order",
    "fuse_features",
    "load_calibrated_config_bundle",
    "merge_all_layer_features",
    "merge_layer_features",
]
