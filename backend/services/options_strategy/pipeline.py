"""Pipeline del módulo Options Strategy (Fases 1–6). # [PD-3][TH]"""

from __future__ import annotations

from pathlib import Path

from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.models.options_strategy import (
    OptionsStrategyAuditLog,
    OptionsStrategyInput,
    OptionsStrategyRunResult,
    OptionsStructure,
    RiskSessionState,
    StrategyDecision,
    merge_all_layer_features,
)
from backend.services.options_strategy.alpaca_executor import AlpacaOptionsExecutor
from backend.services.options_strategy.audit_store import OptionsStrategyAuditStore
from backend.services.options_strategy.execution_store import OptionsStrategyExecutionStore
from backend.services.options_strategy.fusion_router import FusionRouter, fuse_features
from backend.services.options_strategy.options_layer import OptionsLayer
from backend.services.options_strategy.predictive_layer import PredictiveLayer
from backend.services.options_strategy.risk_engine import RiskEngine
from backend.services.options_strategy.structure_selector import StructureSelector
from backend.services.options_strategy.technical_layer import TechnicalLayer


def _neutral_predictive(inp: OptionsStrategyInput):
    from backend.models.options_strategy import PredictiveLayerOutput

    return PredictiveLayerOutput(
        symbol=inp.symbol,
        as_of=inp.as_of,
        insufficient_data=True,
    )


def _neutral_options(inp: OptionsStrategyInput):
    from backend.models.options_strategy import OptionsLayerOutput

    return OptionsLayerOutput(
        symbol=inp.symbol,
        as_of=inp.as_of,
        insufficient_data=True,
    )


class OptionsStrategyPipeline:
    """Orquesta capas → candidato → fusión → riesgo → auditoría → ejecución."""

    @classmethod
    def run_dry(
        cls,
        inp: OptionsStrategyInput,
        *,
        config: OptionsStrategyConfigBundle | None = None,
        session: RiskSessionState | None = None,
        persist: bool = False,
        audit_db_path: Path | str | None = None,
    ) -> OptionsStrategyAuditLog:
        active = config or get_options_strategy_config()
        enabled = set(active.omni_engine.enabled_layers)
        tech = TechnicalLayer.run(inp)
        pred = (
            PredictiveLayer.run(inp, config=active)
            if "predictive" in enabled
            else _neutral_predictive(inp)
        )
        options = (
            OptionsLayer.run(inp, config=active)
            if "options" in enabled
            else _neutral_options(inp)
        )
        merged = merge_all_layer_features(tech, pred, options)
        fused = fuse_features(merged, config=active)
        candidate = StructureSelector.build_candidate(inp, fused, options, config=active)
        decision, payload = FusionRouter.decide(inp, fused, candidate, config=active)

        risk_eval = RiskEngine.evaluate_entry(
            decision,
            payload,
            fused,
            session=session,
            config=active,
        )
        if decision.decision == StrategyDecision.EXECUTE and not risk_eval.passed:
            decision = decision.model_copy(
                update={
                    "decision": StrategyDecision.NO_TRADE,
                    "execution_ready": False,
                    "recommended_structure": OptionsStructure.NO_TRADE,
                    "veto_triggered": risk_eval.veto_code,
                    "reason_codes": decision.reason_codes + risk_eval.reason_codes,
                }
            )
            payload = None
        elif payload is not None and risk_eval.passed:
            payload = RiskEngine.apply_to_payload(payload, risk_eval)
            decision = decision.model_copy(
                update={
                    "risk_budget_pct": risk_eval.adjusted_risk_budget_pct,
                    "reason_codes": decision.reason_codes + risk_eval.reason_codes,
                }
            )

        log = OptionsStrategyAuditLog(
            input=inp,
            features=fused,
            playbook_decision=decision,
            execution_payload=payload,
            config_version="phase6-mvp",
            pipeline_phase="phase5-risk-audit",
        )
        if persist:
            store = OptionsStrategyAuditStore(db_path=audit_db_path)
            store.persist(log)
        return log

    @classmethod
    async def run(
        cls,
        inp: OptionsStrategyInput,
        *,
        config: OptionsStrategyConfigBundle | None = None,
        session: RiskSessionState | None = None,
        persist: bool = False,
        execute: bool = False,
        audit_db_path: Path | str | None = None,
        client: AlpacaClient | None = None,
    ) -> OptionsStrategyRunResult:
        """Ejecuta pipeline completo; con ``execute=True`` envía a Alpaca."""
        log = cls.run_dry(
            inp,
            config=config,
            session=session,
            persist=False,
            audit_db_path=audit_db_path,
        )
        execution = None
        if execute and log.execution_payload is not None:
            alpaca = client or AlpacaClient(dry_run=log.execution_payload.dry_run)
            payload = log.execution_payload.model_copy(
                update={"dry_run": alpaca.dry_run},
            )
            execution = await AlpacaOptionsExecutor.execute(payload, alpaca)
            log = log.model_copy(update={"pipeline_phase": "phase6-alpaca-execution"})
            if persist:
                audit_store = OptionsStrategyAuditStore(db_path=audit_db_path)
                audit_store.persist(log)
                OptionsStrategyExecutionStore(db_path=audit_db_path).persist(
                    log.audit_id,
                    execution,
                )
        elif persist:
            OptionsStrategyAuditStore(db_path=audit_db_path).persist(log)
        return OptionsStrategyRunResult(audit_log=log, execution=execution)


__all__ = ["OptionsStrategyPipeline"]
