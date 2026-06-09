"""API router — mounts all route modules."""

from fastapi import APIRouter

from backend.api.routes.funnel import router as funnel_router
from backend.api.routes.health import router as health_router
from backend.api.routes.scanner import router as scanner_router
from backend.api.routes.signals import router as signals_router
from backend.api.routes.signals import ws_router as signals_ws_router

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(funnel_router)
api_router.include_router(scanner_router)
api_router.include_router(signals_router)
api_router.include_router(signals_ws_router)
