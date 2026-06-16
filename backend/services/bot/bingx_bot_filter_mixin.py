from __future__ import annotations
from typing import TYPE_CHECKING, Any

from backend.services.bot.bingx_bot_types import *

"""Mixin class for BingX Bot Filter."""


from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import ScannerCustomization
from backend.services.scanner_funding_gate import REASON_SCANNER_UNAVAILABLE

logger = get_logger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.bingx_bot_service import *


class BingXBotFilterMixin:
    pass

    async def _scan_confirmation_rows(
        self,
        signals: tuple[BingXSignal, ...],
        customization: ScannerCustomization | None = None,
    ) -> dict[str, dict[str, Any] | None]:
        candidates = [
            signal
            for signal in signals
            if signal.direction != "FLAT" and REASON_INSUFFICIENT_BARS not in signal.reason_codes
        ]
        if not candidates:
            return {}

        from backend.services.bingx_bot_service import (
            _build_scanner_confirmation_request,
            _row_to_dict,
            _normalize_bingx_symbol_for_scanner,
        )

        request = _build_scanner_confirmation_request(
            (signal.symbol for signal in candidates), customization
        )
        try:
            response = await self._scanner.scan(request)
        except Exception as exc:
            logger.warning(
                "bingx_bot.scanner_confirmation_failed symbols=%s error=%s",
                ",".join(request.symbols),
                str(exc)[:180],
            )
            return {signal.symbol: None for signal in candidates}

        rows_by_symbol: dict[str, dict[str, Any]] = {}
        rows = getattr(response, "rows", None)
        if isinstance(rows, list):
            for raw_row in rows:
                row = _row_to_dict(raw_row)
                if row is None:
                    continue
                scanner_symbol = str(row.get("symbol") or "").strip().upper()
                if scanner_symbol:
                    rows_by_symbol[scanner_symbol] = row

        out: dict[str, dict[str, Any] | None] = {}
        for signal in candidates:
            scanner_symbol = _normalize_bingx_symbol_for_scanner(signal.symbol)
            out[signal.symbol] = rows_by_symbol.get(scanner_symbol)
        return out

    async def _evaluate_signal(
        self,
        signal: BingXSignal,
        scanner_rows: dict[str, dict[str, Any] | None] | None,
    ) -> FilterDecision:
        # Hard-block when the scanner itself already returned insufficient data.
        if REASON_INSUFFICIENT_BARS in signal.reason_codes:
            return FilterDecision(
                symbol=signal.symbol,
                suitability="INSUFFICIENT_DATA",
                probability=None,
                threshold=self._heuristic_prob_floor,
                provider="scanner",
                reason_codes=(REASON_INSUFFICIENT_BARS,),
            )

        if signal.direction == "FLAT":
            return FilterDecision(
                symbol=signal.symbol,
                suitability="BLOCK",
                probability=None,
                threshold=self._heuristic_prob_floor,
                provider="scanner",
                reason_codes=signal.reason_codes or (REASON_NO_VOLUME_SPIKE,),
            )

        if self._meta_provider is not None:
            try:
                prob = await self._meta_provider(signal)
            except Exception as exc:
                logger.warning(
                    "bingx_bot.meta_provider_error symbol=%s error=%s", signal.symbol, exc
                )
                return FilterDecision(
                    symbol=signal.symbol,
                    suitability="BLOCK",
                    probability=None,
                    threshold=self._heuristic_prob_floor,
                    provider="meta_learner",
                    reason_codes=(REASON_META_BLOCK,),
                )
            if prob is None:
                return FilterDecision(
                    symbol=signal.symbol,
                    suitability="INSUFFICIENT_DATA",
                    probability=None,
                    threshold=self._heuristic_prob_floor,
                    provider="meta_learner",
                    reason_codes=(REASON_META_BLOCK,),
                )
            suitability: Suitability
            reasons: tuple[str, ...]
            if prob < self._heuristic_prob_floor:
                suitability = "BLOCK"
                reasons = (REASON_META_LOW_PROB,)
            elif prob < self._heuristic_prob_floor + 0.05:
                suitability = "SIZE_DOWN"
                reasons = (REASON_META_LOW_PROB,)
            else:
                suitability = "ALLOW"
                reasons = ()
            decision = FilterDecision(
                symbol=signal.symbol,
                suitability=suitability,
                probability=float(prob),
                threshold=self._heuristic_prob_floor,
                provider="meta_learner",
                reason_codes=reasons,
            )
            if scanner_rows is None:
                return decision
            return self._apply_scanner_confirmation(signal, decision, scanner_rows)

        # Deterministic heuristic fallback: probability proxy = bounded score.
        heuristic_prob = max(0.5, min(0.95, 0.5 + 0.15 * signal.score))
        if heuristic_prob < self._heuristic_prob_floor:
            return FilterDecision(
                symbol=signal.symbol,
                suitability="BLOCK",
                probability=heuristic_prob,
                threshold=self._heuristic_prob_floor,
                provider="heuristic_vsa",
                reason_codes=(REASON_HEURISTIC_LOW_PROB,),
            )
        suit: Suitability = (
            "SIZE_DOWN" if heuristic_prob < self._heuristic_prob_floor + 0.05 else "ALLOW"
        )
        decision = FilterDecision(
            symbol=signal.symbol,
            suitability=suit,
            probability=heuristic_prob,
            threshold=self._heuristic_prob_floor,
            provider="heuristic_vsa",
            reason_codes=() if suit == "ALLOW" else (REASON_HEURISTIC_LOW_PROB,),
        )
        if scanner_rows is None:
            return decision
        return self._apply_scanner_confirmation(signal, decision, scanner_rows)

    def _apply_scanner_confirmation(
        self,
        signal: BingXSignal,
        decision: FilterDecision,
        scanner_rows: dict[str, dict[str, Any] | None],
    ) -> FilterDecision:
        if decision.suitability in ("BLOCK", "INSUFFICIENT_DATA"):
            return decision

        from backend.services.bingx_bot_service import (
            _funding_gate_decision,
            _reason_tuple,
            _dedupe_reason_codes,
        )
        from backend.services.scanner_funding_gate import evaluate_scanner_confirmation

        provider = f"{decision.provider}+market_scanner"
        row = scanner_rows.get(signal.symbol)
        if row is None:
            return FilterDecision(
                symbol=signal.symbol,
                suitability="BLOCK",
                probability=decision.probability,
                threshold=decision.threshold,
                provider=provider,
                reason_codes=(REASON_SCANNER_UNAVAILABLE,),
            )

        confirmation = evaluate_scanner_confirmation(
            row=row,
            entry_direction=signal.direction,
            min_score=self._scanner_min_score,
        )
        confirmation_reasons = _reason_tuple(confirmation.get("reasons"))
        if confirmation.get("status") != "PASS":
            return FilterDecision(
                symbol=signal.symbol,
                suitability="BLOCK",
                probability=decision.probability,
                threshold=decision.threshold,
                provider=provider,
                reason_codes=confirmation_reasons or (REASON_SCANNER_UNAVAILABLE,),
            )

        funding_suitability, funding_reasons = _funding_gate_decision(row)
        if funding_suitability == "block":
            return FilterDecision(
                symbol=signal.symbol,
                suitability="BLOCK",
                probability=decision.probability,
                threshold=decision.threshold,
                provider=provider,
                reason_codes=funding_reasons or (REASON_SCANNER_UNAVAILABLE,),
            )

        reasons = _dedupe_reason_codes((*decision.reason_codes, *funding_reasons))
        suitability: Suitability = (
            "SIZE_DOWN" if funding_suitability == "size_down" else decision.suitability
        )
        return FilterDecision(
            symbol=signal.symbol,
            suitability=suitability,
            probability=decision.probability,
            threshold=decision.threshold,
            provider=provider,
            reason_codes=reasons,
        )
