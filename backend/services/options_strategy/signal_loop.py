"""Signal loop operativo: corre el pipeline sobre R1 periódicamente. # [PD-3][TH]"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.config.alpaca_priority_route import ALPACA_ROUTE1_WATCHLIST
from backend.config.logger_setup import get_logger
from backend.config.options_strategy_loader import (
    OptionsStrategyConfigBundle,
    get_options_strategy_config,
)
from backend.layer_1_data.datos.alpaca_client import AlpacaClient
from backend.models.options_strategy import (
    OptionsStrategyAuditLog,
    RiskSessionState,
    StrategyDecision,
)
from backend.services.options_strategy.input_builder import build_strategy_input
from backend.services.options_strategy.pipeline import OptionsStrategyPipeline

logger = get_logger(__name__)


@dataclass(frozen=True)
class SignalLoopEntry:
    """Resumen de la decisión por ticker en una pasada del loop."""

    symbol: str
    audit_id: str
    decision: str
    structure: str
    direction: str
    confidence: float
    playbook_family: str | None
    veto: str | None
    reason_codes: tuple[str, ...]
    executed: bool = False
    execution_ok: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "audit_id": self.audit_id,
            "decision": self.decision,
            "structure": self.structure,
            "direction": self.direction,
            "confidence": round(self.confidence, 4),
            "playbook_family": self.playbook_family,
            "veto": self.veto,
            "reason_codes": list(self.reason_codes),
            "executed": self.executed,
            "execution_ok": self.execution_ok,
        }


@dataclass(frozen=True)
class SignalLoopReport:
    """Resultado agregado de una pasada del signal loop sobre R1."""

    as_of: datetime
    scanned: int
    execute_count: int
    no_trade_count: int
    error_count: int
    entries: tuple[SignalLoopEntry, ...] = ()
    errors: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "scanned": self.scanned,
            "execute_count": self.execute_count,
            "no_trade_count": self.no_trade_count,
            "error_count": self.error_count,
            "entries": [entry.as_dict() for entry in self.entries],
            "errors": [{"symbol": sym, "error": msg} for sym, msg in self.errors],
        }


def _entry_from_log(log: OptionsStrategyAuditLog) -> SignalLoopEntry:
    decision = log.playbook_decision
    return SignalLoopEntry(
        symbol=log.input.symbol,
        audit_id=log.audit_id,
        decision=str(decision.decision),
        structure=str(decision.recommended_structure),
        direction=str(decision.direction),
        confidence=decision.confidence,
        playbook_family=decision.playbook_family,
        veto=decision.veto_triggered,
        reason_codes=decision.reason_codes,
    )


class OptionsStrategySignalLoop:
    """Orquesta una o varias pasadas del pipeline Options Strategy sobre R1."""

    @classmethod
    def scan_once(
        cls,
        *,
        symbols: tuple[str, ...] | None = None,
        as_of: datetime | None = None,
        config: OptionsStrategyConfigBundle | None = None,
        session: RiskSessionState | None = None,
        persist: bool = False,
    ) -> SignalLoopReport:
        """Pasada dry-run sobre R1: genera y (opcional) persiste señales."""
        active = config or get_options_strategy_config()
        moment = as_of or datetime.now(tz=UTC)
        universe = symbols or ALPACA_ROUTE1_WATCHLIST

        entries: list[SignalLoopEntry] = []
        errors: list[tuple[str, str]] = []
        execute_count = 0
        no_trade_count = 0

        for symbol in universe:
            try:
                inp = build_strategy_input(symbol, as_of=moment)
                log = OptionsStrategyPipeline.run_dry(
                    inp,
                    config=active,
                    session=session,
                    persist=persist,
                )
                entry = _entry_from_log(log)
                entries.append(entry)
                if log.playbook_decision.decision == StrategyDecision.EXECUTE:
                    execute_count += 1
                else:
                    no_trade_count += 1
            except Exception as exc:  # noqa: BLE001 - aislar fallo por ticker
                logger.warning("signal_loop.symbol_failed symbol=%s error=%s", symbol, exc)
                errors.append((symbol, str(exc)))

        logger.info(
            "signal_loop.scan_complete scanned=%d execute=%d no_trade=%d errors=%d",
            len(universe),
            execute_count,
            no_trade_count,
            len(errors),
        )
        return SignalLoopReport(
            as_of=moment,
            scanned=len(universe),
            execute_count=execute_count,
            no_trade_count=no_trade_count,
            error_count=len(errors),
            entries=tuple(entries),
            errors=tuple(errors),
        )

    @classmethod
    async def scan_and_execute(
        cls,
        *,
        symbols: tuple[str, ...] | None = None,
        as_of: datetime | None = None,
        config: OptionsStrategyConfigBundle | None = None,
        session: RiskSessionState | None = None,
        persist: bool = True,
        client: AlpacaClient | None = None,
    ) -> SignalLoopReport:
        """Pasada completa: ejecuta en Alpaca las señales EXECUTE de R1."""
        active = config or get_options_strategy_config()
        moment = as_of or datetime.now(tz=UTC)
        universe = symbols or ALPACA_ROUTE1_WATCHLIST

        entries: list[SignalLoopEntry] = []
        errors: list[tuple[str, str]] = []
        execute_count = 0
        no_trade_count = 0

        for symbol in universe:
            try:
                inp = build_strategy_input(symbol, as_of=moment)
                result = await OptionsStrategyPipeline.run(
                    inp,
                    config=active,
                    session=session,
                    persist=persist,
                    execute=True,
                    client=client,
                )
                log = result.audit_log
                entry = _entry_from_log(log)
                if result.execution is not None:
                    entry = SignalLoopEntry(
                        **{
                            **entry.__dict__,
                            "executed": True,
                            "execution_ok": result.execution.ok,
                        }
                    )
                entries.append(entry)
                if log.playbook_decision.decision == StrategyDecision.EXECUTE:
                    execute_count += 1
                else:
                    no_trade_count += 1
            except Exception as exc:  # noqa: BLE001 - aislar fallo por ticker
                logger.warning("signal_loop.execute_failed symbol=%s error=%s", symbol, exc)
                errors.append((symbol, str(exc)))

        logger.info(
            "signal_loop.execute_complete scanned=%d execute=%d errors=%d",
            len(universe),
            execute_count,
            len(errors),
        )
        return SignalLoopReport(
            as_of=moment,
            scanned=len(universe),
            execute_count=execute_count,
            no_trade_count=no_trade_count,
            error_count=len(errors),
            entries=tuple(entries),
            errors=tuple(errors),
        )


__all__ = [
    "OptionsStrategySignalLoop",
    "SignalLoopEntry",
    "SignalLoopReport",
]
