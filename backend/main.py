"""Main entrypoint for Deep Funnel Station Backend API."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.router import api_router
from backend.bus.event_bus import EventBus
from backend.config.settings import MarketDataSettings
from backend.engine.quantitative_engine import QuantitativeEngine
from backend.hub.market_data_hub import MarketDataHub
from backend.hub.streams.alpaca_streamer import AlpacaStreamer

# Setup logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backend.main")

DEFAULT_UNIVERSE = [
    "AAPL",
    "MSFT",
    "TSLA",
    "GOOGL",
    "META",
    "NVDA",
    "AMZN",
    "SPY",
    "NFLX",
    "AMD",
    "PLTR",
    "COIN",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Manages the startup and shutdown lifecycles of backend services."""
    logger.info("Initializing system services...")

    # Load configuration settings from environment
    settings = MarketDataSettings()

    # Initialize core decoupled event-driven components
    event_bus = EventBus()
    hub = MarketDataHub(settings=settings, event_bus=event_bus)
    engine = QuantitativeEngine(event_bus=event_bus)
    emitter = AlpacaStreamer(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
        universe=DEFAULT_UNIVERSE,
        hub=hub,
    )

    # Save instances to app state for access within route handlers
    app.state.settings = settings
    app.state.event_bus = event_bus
    app.state.hub = hub
    app.state.engine = engine
    app.state.emitter = emitter

    # Initialize BingX Bot components
    import os
    from pathlib import Path

    from backend.layer_1_data.datos.bingx_client import BINGX_REST_VST_BASE, BingXClient
    from backend.layer_1_data.fetchers.fmp_client import FMPClient
    from backend.layer_1_data.fetchers.massive_client import MassiveClient
    from backend.routers.bingx_bot_router import _hc_cache_fresh as bingx_healthcheck_cache_fresh
    from backend.routers.bingx_bot_router import (
        configure_audit_store as configure_bingx_audit_store,
    )
    from backend.routers.bingx_bot_router import configure_scheduler as configure_bingx_scheduler
    from backend.routers.bingx_bot_router import configure_service as configure_bingx_service
    from backend.routers.options_router import options_snapshot_service
    from backend.services.bingx_audit_store import BingXAuditStore
    from backend.services.bingx_bot_service import BingXBotService
    from backend.services.bingx_live_ticker_hub import configure_live_ticker_hub
    from backend.services.bingx_universe import BingXUniverseService
    from backend.services.market_breadth_tracker import MarketBreadthTracker
    from backend.tasks.bingx_bot_scheduler import BingXBotScheduler
    from backend.tasks.scanner_scheduler import ScannerScheduler, ScannerSchedulerConfig

    async def _bingx_venue_technical_fetcher(
        sym: str,
        candles: list[dict[str, Any]],
        timeframe: str,
    ) -> dict[str, Any]:
        from backend.services.technical_terminal_payload import (
            build_technical_terminal_payload_from_candles,
        )

        return await build_technical_terminal_payload_from_candles(sym, candles, timeframe)

    _trading_env = settings.bingx_bot_trading_env.strip().lower()
    _env_dry_run = os.getenv("BINGX_DRY_RUN", "").strip().lower()
    _force_venue_live = _env_dry_run in {"0", "false", "no", "live"}
    _live = (
        _trading_env in {"prod-vst", "prod-live"}
        or settings.bingx_bot_enable_live
        or _force_venue_live
    )

    _bx_key = settings.bingx_api_key.get_secret_value() if settings.bingx_api_key else None
    _bx_secret = settings.bingx_secret.get_secret_value() if settings.bingx_secret else None

    if _trading_env == "prod-vst" or (_force_venue_live and _trading_env != "prod-live"):
        _bingx_client = BingXClient(
            api_key=_bx_key,
            secret_key=_bx_secret,
            base_url=BINGX_REST_VST_BASE,
            dry_run=False,
            allow_env_dry_run_override=False,
        )
    elif _trading_env == "prod-live" and _live:
        _bingx_client = BingXClient(
            api_key=_bx_key,
            secret_key=_bx_secret,
            dry_run=False,
            allow_env_dry_run_override=False,
        )
    else:
        _bingx_client = BingXClient(
            api_key=_bx_key,
            secret_key=_bx_secret,
            dry_run=not _live,
            allow_env_dry_run_override=False,
        )

    _fmp_client = FMPClient()
    _massive_client = MassiveClient()
    _universe_service = BingXUniverseService(
        client=_bingx_client,
        fmp_client=_fmp_client,
        massive_client=_massive_client,
    )
    _bingx_service = BingXBotService(
        client=_bingx_client,
        options_snapshot_fn=options_snapshot_service,
        venue_technical_fn=_bingx_venue_technical_fetcher,
        fmp_client=_fmp_client,
        massive_client=_massive_client,
        universe_service=_universe_service,
    )
    _audit_path = Path(settings.bingx_bot_audit_db_path)
    os.makedirs(_audit_path.parent, exist_ok=True)
    _audit_store = BingXAuditStore(_audit_path)
    configure_bingx_service(_bingx_service)
    configure_bingx_audit_store(_audit_store)
    configure_bingx_scheduler(
        BingXBotScheduler(
            service=_bingx_service,
            audit_store=_audit_store,
            hc_ok_fn=bingx_healthcheck_cache_fresh,
        )
    )

    # Initialize Audit Complex store
    from backend.audit.audit_complex_store import AuditComplexStore
    from backend.routers.audit_complex_router import (
        configure_audit_complex_store as configure_audit_complex,
    )

    _audit_complex_path = Path(settings.audit_db_path)
    os.makedirs(_audit_complex_path.parent, exist_ok=True)
    _audit_complex_store = AuditComplexStore(_audit_complex_path)
    configure_audit_complex(_audit_complex_store)

    _live_ticker_hub = configure_live_ticker_hub(client=_bingx_client)
    await _live_ticker_hub.ensure_started()
    app.state.bingx_live_ticker_hub = _live_ticker_hub

    # Initialize Market Breadth tracker
    _market_breadth = MarketBreadthTracker()
    app.state.market_breadth = _market_breadth

    # Initialize Phase A Scanner scheduler
    _scanner_scheduler = ScannerScheduler(
        hub=hub,
        api_keys=[settings.fmp_api_key.get_secret_value()],
        event_bus=event_bus,
        universe=DEFAULT_UNIVERSE,
        breadth_tracker=_market_breadth,
        config=ScannerSchedulerConfig(
            scan_interval_s=settings.phase_a_scan_interval_s,
            respect_market_hours=True,
            publish_to_bus=True,
        ),
    )
    app.state.scanner_scheduler = _scanner_scheduler

    # Spawn mathematical quantitative engine background loop
    logger.info("Starting QuantitativeEngine background task...")
    engine_task = asyncio.create_task(engine.start_processing())

    logger.info("Starting AlpacaStreamer background task...")
    emitter_task = asyncio.create_task(emitter.start())

    logger.info(
        "Starting Phase A Scanner scheduler (interval=%ds)...",
        settings.phase_a_scan_interval_s,
    )
    await _scanner_scheduler.start()

    yield

    # Shutdown lifecycle
    logger.info("Stopping system services...")
    engine_task.cancel()
    emitter_task.cancel()
    try:
        await asyncio.gather(engine_task, emitter_task, return_exceptions=True)
    except Exception as exc:
        logger.error("Error during task cancellation: %s", exc, exc_info=True)

    await _scanner_scheduler.stop()
    await app.state.bingx_live_ticker_hub.shutdown()
    await hub.close()

    # Gracefully close persistent HTTP connection pools
    from backend.layer_1_data.fetchers.fmp_client import FMPClient
    from backend.layer_1_data.fetchers.massive_client import MassiveClient

    await FMPClient.aclose_shared_client()
    await MassiveClient.aclose_shared_client()

    logger.info("System shutdown complete.")


# Instantiate FastAPI application
app = FastAPI(
    title="Deep Funnel Station API",
    description="Quantitative trading terminal filtering engine and options scanner API",
    version="3.0",
    lifespan=lifespan,
)

# Configure Cross-Origin Resource Sharing (CORS)
# Allows Next.js frontend to securely query REST endpoints and connect to WebSockets
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://192.168.0.55:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Correlation ID middleware — propagates a unique request ID through all logs
from backend.audit.middleware import CorrelationIdMiddleware

app.add_middleware(CorrelationIdMiddleware)

# Include the centralized API router
app.include_router(api_router)


if __name__ == "__main__":
    # Start ASGI server programmatically if executed directly
    logger.info("Starting uvicorn server programmatically...")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
