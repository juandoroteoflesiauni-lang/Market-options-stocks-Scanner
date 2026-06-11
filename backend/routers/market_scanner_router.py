"""Market Scanner API routes."""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import ORJSONResponse, PlainTextResponse, Response

from backend.config.logger_setup import get_logger
from backend.domain.market_scanner_models import (
    MarketScannerContextRequest,
    MarketScannerContextResponse,
    MarketScannerLivePriceRow,
    MarketScannerLivePricesRequest,
    MarketScannerLivePricesResponse,
    MarketScannerRequest,
    MarketScannerResponse,
    MarketScannerRow,
    NaturalLanguageScannerRequest,
    ScannerExecutionSimRequest,
    ScannerExecutionSimResponse,
    ScannerFusionEnrichRequest,
    ScannerFusionEnrichResponse,
    ScannerLeadersThesisRequest,
    ScannerLeadersThesisResponse,
    ScannerNaturalLanguageResponse,
    ScannerPortfolioOptimizeRequest,
    ScannerPortfolioOptimizeResponse,
)
from backend.routers.auth_router import OperatorProfile, get_current_user
from backend.services.market_scanner_charts_service import fetch_mini_chart
from backend.services.market_scanner_context_service import (
    build_market_scanner_context,
    fetch_argentina_summary,
    fetch_fear_greed_dashboard,
    fetch_symbol_catalyst,
    fetch_symbol_news,
    fetch_symbol_sentiment,
)
from backend.services.market_scanner_export import render_scanner_markdown_report
from backend.services.market_scanner_pdf import render_scanner_pdf_bytes
from backend.services.market_scanner_service import (
    MarketScannerService,
    fetch_market_scanner_live_prices,
    list_market_scanner_indicators,
    list_market_scanner_presets,
    list_market_scanner_universes,
)
from backend.services.nautilus_scanner_bridge import run_scanner_execution_sim
from backend.services.scanner_leaders_thesis_service import run_leaders_thesis
from backend.services.scanner_nl_interpreter import interpret_scanner_query
from backend.services.scanner_portfolio_optimizer import optimize_scanner_portfolio
from backend.services.scanner_sentiment_fusion import enrich_scanner_rows

router = APIRouter(prefix="/api/v1/market-scanner", tags=["market-scanner"])
logger = get_logger(__name__)


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"ping": "pong"}


def _json_sanitize(obj: object) -> object:
    """Replace NaN/Inf values without importing the full API app."""
    if obj is None or isinstance(obj, bool | int | str):
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {str(key): _json_sanitize(value) for key, value in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_sanitize(value) for value in obj]
    try:
        value = float(obj)
    except (TypeError, ValueError):
        return obj
    return None if math.isnan(value) or math.isinf(value) else value


@router.get("/universes")
async def get_market_scanner_universes(
    _user: OperatorProfile = Depends(get_current_user),
) -> dict[str, dict[str, object]]:
    """Return seed symbol universes for the scanner."""
    return {
        key: universe.model_dump(mode="json")
        for key, universe in list_market_scanner_universes().items()
    }


@router.get("/presets")
async def get_market_scanner_presets(
    _user: OperatorProfile = Depends(get_current_user),
) -> list[dict[str, object]]:
    """Return built-in scanner presets."""
    return [preset.model_dump(mode="json") for preset in list_market_scanner_presets()]


@router.get("/indicators")
async def get_market_scanner_indicators(
    _user: OperatorProfile = Depends(get_current_user),
) -> dict[str, object]:
    """Return the versioned scanner indicator catalog for Strategy Control."""
    from fastapi.responses import JSONResponse

    from backend.services.market_scanner_indicator_catalog import CATALOG_VERSION

    payload = {
        "catalog_version": CATALOG_VERSION,
        "indicators": [
            indicator.model_dump(mode="json") for indicator in list_market_scanner_indicators()
        ],
    }
    body = _json_sanitize(payload)
    return JSONResponse(
        content=body,
        headers={"Cache-Control": "no-store, max-age=0"},
    )  # type: ignore[return-value]


@router.get("/mini-chart/{symbol}")
async def get_market_scanner_mini_chart(
    symbol: str,
    limit: int = 96,
    _user: OperatorProfile = Depends(get_current_user),
) -> dict[str, object]:
    """Return lightweight 5m candles for scanner cards via FMP Enterprise or Alpaca."""
    result = await fetch_mini_chart(symbol, limit=limit)
    return _json_sanitize(result)  # type: ignore[return-value]


@router.post("/interpret-query", response_model=ScannerNaturalLanguageResponse)
async def interpret_nl_scanner_query(
    request: NaturalLanguageScannerRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> ScannerNaturalLanguageResponse:
    """Heuristic NL hints for universe/modules/filters (no LLM)."""
    return interpret_scanner_query(request.query, request.active_universe)


@router.post("/leaders-thesis", response_model=ScannerLeadersThesisResponse)
async def post_scanner_leaders_thesis(
    request: ScannerLeadersThesisRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> ScannerLeadersThesisResponse:
    """Concise leaders research via SCANNER_RESEARCH_ENGINE=focused|full_agents|finrobot."""
    return await run_leaders_thesis(
        request.symbols,
        request.row_summaries,
        universe=request.universe,
        regime_summary=request.universe_regime_summary,
    )


@router.post("/fusion-enrich", response_model=ScannerFusionEnrichResponse)
async def post_scanner_fusion_enrich(
    request: ScannerFusionEnrichRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> ScannerFusionEnrichResponse:
    """Deterministic sentiment x technical fusion into deep_metrics for the active timeframe."""
    enriched = enrich_scanner_rows(
        request.rows,
        sentiment_by_symbol=request.sentiment_by_symbol,
        catalysts_by_symbol=request.catalysts_by_symbol,
        primary_timeframe=request.primary_timeframe,
        argentina_summary=request.argentina_summary,
    )
    rows = [MarketScannerRow.model_validate(_json_sanitize(r)) for r in enriched]
    return ScannerFusionEnrichResponse(rows=rows)


@router.post("/portfolio-optimize", response_model=ScannerPortfolioOptimizeResponse)
async def post_scanner_portfolio_optimize(
    request: ScannerPortfolioOptimizeRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> ScannerPortfolioOptimizeResponse:
    """Optimize Scanner leaders as a non-binding basket diagnostic."""
    from backend.services.scanner_risk_stack import enrich_portfolio_optimize_response

    result = optimize_scanner_portfolio(request)
    if request.rows:
        result = enrich_portfolio_optimize_response(
            result,
            request.rows,
            constraints=request.constraints,
        )
    return ScannerPortfolioOptimizeResponse.model_validate(_json_sanitize(result.model_dump()))


@router.post("/execution-sim", response_model=ScannerExecutionSimResponse)
async def post_scanner_execution_sim(
    request: ScannerExecutionSimRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> ScannerExecutionSimResponse:
    """Run an optional execution simulation for existing scanner candidates."""
    result = await run_scanner_execution_sim(request)
    return ScannerExecutionSimResponse.model_validate(_json_sanitize(result.model_dump()))


@router.post("/scan/pdf")
async def scan_market_pdf(
    request: MarketScannerRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> Response:
    """Run scan and return a compact PDF (requires ``fpdf2``)."""
    logger.info("PDF export v2.1 started")
    try:
        service = MarketScannerService()
        result = await service.scan(request)
        pdf_bytes = render_scanner_pdf_bytes(result)
    except Exception as exc:
        logger.error("PDF export failed: %s", exc, exc_info=True)
        return ORJSONResponse(
            status_code=500,
            content={"ok": False, "error": "PDF generation failed"},
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="market-scanner.pdf"'},
    )


@router.post("/scan/markdown")
async def scan_market_markdown(
    request: MarketScannerRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> PlainTextResponse:
    """Run scan and return a desk-ready Markdown report (UTF-8)."""
    service = MarketScannerService()
    result = await service.scan(request)
    md = render_scanner_markdown_report(result)
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@router.post("/scan", response_model=MarketScannerResponse)
async def scan_market(
    request: MarketScannerRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> MarketScannerResponse:
    """Rank a universe into a compact list of trade candidates."""
    known_universes = list_market_scanner_universes()
    if request.universe != "custom" and request.universe not in known_universes:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown universe: {request.universe}",
        )
    service = MarketScannerService()
    result = await service.scan(request)
    return MarketScannerResponse.model_validate(_json_sanitize(result.model_dump()))


@router.post("/context", response_model=MarketScannerContextResponse)
async def get_market_scanner_context(
    request: MarketScannerContextRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> MarketScannerContextResponse:
    """Return market briefing context for the institutional scanner dashboard."""
    result = await build_market_scanner_context(
        request,
        news_provider=fetch_symbol_news,
        sentiment_provider=fetch_symbol_sentiment,
        catalyst_provider=fetch_symbol_catalyst,
        fear_greed_provider=fetch_fear_greed_dashboard,
        argentina_provider=fetch_argentina_summary,
    )
    return MarketScannerContextResponse.model_validate(_json_sanitize(result.model_dump()))


@router.post("/prices", response_model=MarketScannerLivePricesResponse)
async def get_market_scanner_live_prices(
    request: MarketScannerLivePricesRequest,
    _user: OperatorProfile = Depends(get_current_user),
) -> MarketScannerLivePricesResponse:
    """Return lightweight current prices for visible scanner rows."""
    prices = await fetch_market_scanner_live_prices(request.symbols)
    result = MarketScannerLivePricesResponse(
        prices={
            symbol: MarketScannerLivePriceRow(
                symbol=symbol,
                price=live.price,
                change_pct=live.change_pct,
                source=live.source,
                timestamp_ms=live.timestamp_ms,
            )
            for symbol, live in prices.items()
        }
    )
    return MarketScannerLivePricesResponse.model_validate(_json_sanitize(result.model_dump()))
