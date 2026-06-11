"""Smoke test for the Mesa de Dinero Virtual implementation."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config.logger_setup import get_logger
from backend.layer_5_mesa_dinero.report_factory import SpecializedReportGenerator

logger = get_logger(__name__)
HAS_TORCH = importlib.util.find_spec("torch") is not None


def _technical_data() -> dict[str, object]:
    return {
        "technical_data": {
            "trend": "bullish",
            "support_levels": [100, 95, 90],
            "resistance_levels": [110, 115, 120],
            "volatility_regime": "high",
            "regime_probability": 0.75,
        },
        "price_action_analysis": "Bullish breakout pattern detected",
        "trend_implications": "Continuation likely with strong momentum",
    }


def test_report_factory_smoke() -> None:
    """Exercise report creation without optional ML dependencies."""
    report = SpecializedReportGenerator.generate_technical_report("SPY", _technical_data())
    logger.info("Technical report generated")
    logger.info("Report ID: %s", report.metadata.report_id)
    logger.info("Confidence: %s", report.metadata.confidence_score)
    logger.info("Trend: %s", report.technical_data.trend)

    composite = SpecializedReportGenerator.generate_composite_report(
        "SPY",
        [report],
        "Unified bullish thesis across technical and fundamental factors",
    )
    logger.info("Composite report generated")
    logger.info("Unified thesis length: %d characters", len(composite.unified_thesis))
    logger.info("Mesa de Dinero Virtual smoke test passed")


def test_mesa_dinero_orchestrator_smoke() -> None:
    """Exercise the orchestrator when optional ML dependencies are installed."""
    if not HAS_TORCH:
        import pytest

        pytest.skip("torch optional dependency is not installed")

    from backend.layer_5_mesa_dinero.orchestrator import MesaDineroOrchestrator

    async def _run() -> None:
        logger.info("Testing Mesa de Dinero Virtual implementation")
        orchestrator = MesaDineroOrchestrator()
        logger.info("Orchestrator initialized: %s", type(orchestrator).__name__)

    asyncio.run(_run())


if __name__ == "__main__":
    test_report_factory_smoke()
    if HAS_TORCH:
        test_mesa_dinero_orchestrator_smoke()
    else:
        logger.info("torch is not installed; Mesa de Dinero orchestrator smoke skipped")
