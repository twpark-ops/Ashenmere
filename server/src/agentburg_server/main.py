"""FastAPI application entry point."""

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import generate_latest

from agentburg_server.api.routes import router as api_router
from agentburg_server.api.ws import router as ws_router
from agentburg_server.config import settings
from agentburg_server.db import engine
from agentburg_server.engine.tick import tick_engine
from agentburg_server.metrics import http_duration_seconds, http_requests

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup and shutdown lifecycle."""
    from agentburg_server.plugins.builtin.economy_stats import EconomyStatsPlugin
    from agentburg_server.plugins.manager import plugin_manager

    logger.info("AgentBurg server starting...")

    # Register built-in plugins
    plugin_manager.register(EconomyStatsPlugin())
    await plugin_manager.startup()
    logger.info("Plugin system ready (%d plugins)", len(plugin_manager.plugins))

    await tick_engine.start()
    logger.info("Tick engine started (interval=%.1fs)", settings.tick_interval_seconds)
    yield
    logger.info("AgentBurg server shutting down...")
    await tick_engine.stop()
    await plugin_manager.shutdown()
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
    allow_origins=["*"] if settings.debug else settings.cors_origins,
    allow_credentials=not settings.debug,  # Never allow credentials with wildcard origins
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next) -> Response:  # type: ignore[type-arg]
    """Collect HTTP request metrics for Prometheus."""
    if request.url.path in ("/metrics", "/health"):
        return await call_next(request)

    method = request.method
    path = request.url.path
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration = time.perf_counter() - start

    http_requests.labels(method=method, endpoint=path, status=response.status_code).inc()
    http_duration_seconds.labels(method=method, endpoint=path).observe(duration)
    return response


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    from agentburg_server.plugins.manager import plugin_manager

    return {
        "status": "ok",
        "version": "0.1.0",
        "tick": tick_engine.tick,
        "tick_running": tick_engine.running,
        "plugins": plugin_manager.plugin_names,
    }


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
