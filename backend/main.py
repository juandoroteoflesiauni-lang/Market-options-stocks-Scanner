from typing import Any
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.router import api_router
from backend.config.settings import MarketDataSettings
from backend.api.bootstrap import bootstrap_application, shutdown_application
from backend.audit.middleware import CorrelationIdMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("backend.main")

@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    logger.info("Initializing system services...")
    settings = MarketDataSettings()
    
    engine_task, emitter_task, hub = await bootstrap_application(app, settings)
    
    yield
    
    await shutdown_application(app, engine_task, emitter_task, hub)

app = FastAPI(
    title="Deep Funnel Station API",
    description="Quantitative trading terminal filtering engine and options scanner API",
    version="3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://192.168.0.55:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(CorrelationIdMiddleware)
app.include_router(api_router)

if __name__ == "__main__":
    logger.info("Starting uvicorn server programmatically...")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
