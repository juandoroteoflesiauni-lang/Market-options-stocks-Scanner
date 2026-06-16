"""API router — mounts all route modules."""

from fastapi import APIRouter

from backend.api.routes.agents_stream_router import router as agents_stream_router
from backend.api.routes.alpaca_bot_router import router as alpaca_bot_router
from backend.api.routes.audit_complex_router import router as audit_complex_router
from backend.api.routes.audit_export_router import router as audit_export_router
from backend.api.routes.auth_router import router as auth_router
from backend.api.routes.bingx_bot_router import router as bingx_bot_router
from backend.api.routes.builder_router import router as builder_router
from backend.api.routes.consumption_router import router as consumption_router
from backend.api.routes.convergence_router import router as convergence_router
from backend.api.routes.equity_l2_router import router as equity_l2_router
from backend.api.routes.funnel import router as funnel_router
from backend.api.routes.global_context_router import router as global_context_router
from backend.api.routes.health import router as health_router
from backend.api.routes.market_scanner_router import router as market_scanner_router
from backend.api.routes.monte_carlo_router import router as monte_carlo_router
from backend.api.routes.options_router import router as options_router
from backend.api.routes.options_strategy_router import router as options_strategy_router
from backend.api.routes.risk_metrics_router import router as risk_metrics_router
from backend.api.routes.scanner import router as scanner_router
from backend.api.routes.signals import router as signals_router
from backend.api.routes.signals import ws_router as signals_ws_router
from backend.api.routes.sizing_router import router as sizing_router
from backend.api.routes.trade_rationale_router import router as trade_rationale_router
from backend.api.routes.websocket_router import router as websocket_router
from backend.api.routes.weights import router as weights_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(health_router)
api_router.include_router(funnel_router)
api_router.include_router(scanner_router)
api_router.include_router(market_scanner_router)
api_router.include_router(signals_router)
api_router.include_router(signals_ws_router)
api_router.include_router(weights_router)
api_router.include_router(bingx_bot_router)
api_router.include_router(alpaca_bot_router)
api_router.include_router(options_router)
api_router.include_router(options_strategy_router)
api_router.include_router(consumption_router)
api_router.include_router(audit_complex_router)
api_router.include_router(risk_metrics_router)
api_router.include_router(global_context_router)
api_router.include_router(sizing_router)
api_router.include_router(builder_router)
api_router.include_router(convergence_router)
api_router.include_router(monte_carlo_router)
api_router.include_router(audit_export_router)
api_router.include_router(agents_stream_router)
api_router.include_router(trade_rationale_router)
api_router.include_router(websocket_router)
api_router.include_router(equity_l2_router)
