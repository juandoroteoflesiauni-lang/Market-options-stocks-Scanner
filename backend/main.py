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

# Setup logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backend.main")


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

    # Save instances to app state for access within route handlers
    app.state.settings = settings
    app.state.event_bus = event_bus
    app.state.hub = hub
    app.state.engine = engine

    # Spawn mathematical quantitative engine background loop
    logger.info("Starting QuantitativeEngine background task...")
    engine_task = asyncio.create_task(engine.start_processing())

    yield

    # Shutdown lifecycle
    logger.info("Stopping system services...")
    engine_task.cancel()
    try:
        await engine_task
    except asyncio.CancelledError:
        logger.info("QuantitativeEngine processing task canceled successfully.")
    except Exception as exc:
        logger.error("Error during QuantitativeEngine task cancellation: %s", exc, exc_info=True)

    await hub.close()

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
    allow_origins=["*"],  # Adjust in production environments to specific hostnames
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the centralized API router
app.include_router(api_router)


if __name__ == "__main__":
    # Start ASGI server programmatically if executed directly
    logger.info("Starting uvicorn server programmatically...")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
