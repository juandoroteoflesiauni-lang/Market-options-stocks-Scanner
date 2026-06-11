"""Live Market Scanner confirmation adapter for Funding Lab.

This module is intentionally thin: it runs a single-symbol scanner request and
delegates all pass/fail policy to ``scanner_funding_gate``. Scanner failures
are fail-closed so Funding Lab never treats missing live confirmation as an
authorization.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerFilters,
    MarketScannerRequest,
    ScannerCustomization,
)
from backend.services.funding_lab_side_meta_learner import get_side_meta_confirmation
from backend.services.market_scanner_service import MarketScannerService
from backend.services.scanner_funding_gate import (
    REASON_SCANNER_UNAVAILABLE,
    evaluate_scanner_confirmation,
)

logger = get_logger(__name__)


class ScannerConfirmationService(Protocol):
    async def scan(self, request: MarketScannerRequest) -> object:
        """Return a scanner response-like object with a ``rows`` attribute."""


class SideMetaConfirmationProvider(Protocol):
    async def __call__(
        self,
        *,
        scanner_row: dict[str, Any],
        entry_direction: str,
        min_probability: float = 0.65,
    ) -> dict[str, Any]:
        """Return side-specific Meta-Learner confirmation."""


async def get_scanner_confirmation(
    *,
    symbol: str,
    entry_direction: str,
    min_score: float,
    scanner_service: ScannerConfirmationService | None = None,
    side_meta_provider: SideMetaConfirmationProvider | None = None,
) -> dict[str, Any]:
    """Run a live scanner pass for one symbol and evaluate Funding Lab confirmation."""
    normalized_symbol = str(symbol).strip().upper()
    scanner = scanner_service or MarketScannerService()
    request = MarketScannerRequest(
        universe="custom",
        symbols=[normalized_symbol],
        timeframes=["5m", "15m", "1h", "1D"],
        direction="both",
        max_rows=1,
        include_deep_metrics=True,
        include_funding_gate=False,
        filters=MarketScannerFilters(
            min_price=0.0,
            min_volume=0.0,
            min_relative_volume=0.0,
            min_score=0.0,
            allow_reversal=True,
            include_vetoed=True,
        ),
        customization=ScannerCustomization(
            enabled_modules=["technical", "probabilistic", "options_gex"],
            module_synthesis_limit=1,
            primary_timeframe="15m",
        ),
    )

    try:
        response = await scanner.scan(request)
    except Exception as exc:
        logger.info(
            "funding_lab.scanner_confirmation_failed symbol=%s error=%s",
            normalized_symbol,
            str(exc)[:180],
        )
        return _scanner_unavailable()

    row = _select_row(response, normalized_symbol)
    if row is None:
        return _scanner_unavailable()

    result = evaluate_scanner_confirmation(
        row=row,
        entry_direction=entry_direction,
        min_score=min_score,
    )
    provider = side_meta_provider or get_side_meta_confirmation

    try:
        side_result = await provider(
            scanner_row=row,
            entry_direction=entry_direction,
        )
    except Exception as exc:
        logger.info(
            "funding_lab.side_meta_confirmation_failed symbol=%s error=%s",
            normalized_symbol,
            str(exc)[:180],
        )
        side_result = {
            "status": "FAIL",
            "side": "unknown",
            "probability": 0.0,
            "threshold": 0.65,
            "model_path": "",
            "reasons": ["side_meta_unavailable"],
        }

    result["side_meta_confirmation"] = side_result
    if side_result.get("status") != "PASS":
        side_reasons = side_result.get("reasons")
        result["status"] = "FAIL"
        result["reasons"] = _dedupe(
            [
                *result.get("reasons", []),
                *(side_reasons if isinstance(side_reasons, list) else []),
            ]
        )
    return result


def _select_row(response: object, symbol: str) -> dict[str, Any] | None:
    rows = getattr(response, "rows", None)
    if not isinstance(rows, list) or not rows:
        return None
    normalized_symbol = symbol.upper()
    for raw_row in rows:
        row = _row_to_dict(raw_row)
        if row is None:
            continue
        row_symbol = str(row.get("symbol") or "").strip().upper()
        if row_symbol == normalized_symbol:
            return row
    return None


def _row_to_dict(row: object) -> dict[str, Any] | None:
    if isinstance(row, dict):
        return row
    model_dump = getattr(row, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        return dumped if isinstance(dumped, dict) else None
    return None


def _scanner_unavailable() -> dict[str, Any]:
    return {
        "status": "FAIL",
        "reasons": [REASON_SCANNER_UNAVAILABLE],
        "trend_score": 0.0,
    }


def _dedupe(values: list[Any]) -> list[Any]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
