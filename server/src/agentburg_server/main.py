"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentburg_server.config import settings
from agentburg_server.db import engine
from agentburg_server.api.routes import router as api_router
from agentburg_server.api.ws import router as ws_router
from agentburg_server.engine.tick import tick_engine

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle."""
    logger.info("AgentBurg server starting...")
    await tick_engine.start()
    logger.info("Tick engine started (interval=%.1fs)", settings.tick_interval_seconds)
    yield
    logger.info("AgentBurg server shutting down...")
    await tick_engine.stop()
    await engine.dispose()


app = FastAPI(
    title="AgentBurg",
    description="Autonomous AI agent economy simulation platform",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "tick": tick_engine.tick,
        "tick_running": tick_engine.running,
    }
