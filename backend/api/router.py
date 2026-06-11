"""API router — mounts all route modules."""

from fastapi import APIRouter

from backend.api.routes.funnel import router as funnel_router
from backend.api.routes.health import router as health_router
from backend.api.routes.scanner import router as scanner_router
from backend.api.routes.signals import router as signals_router
from backend.api.routes.signals import ws_router as signals_ws_router
from backend.api.routes.weights import router as weights_router
from backend.routers.audit_complex_router import router as audit_complex_router
from backend.routers.auth_router import router as auth_router
from backend.routers.bingx_bot_router import router as bingx_bot_router
from backend.routers.consumption_router import router as consumption_router
from backend.routers.market_scanner_router import router as market_scanner_router
from backend.routers.options_router import router as options_router

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
api_router.include_router(options_router)
api_router.include_router(consumption_router)
api_router.include_router(audit_complex_router)
