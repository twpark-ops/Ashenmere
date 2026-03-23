"""FastAPI application entry point."""

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import generate_latest

from agentburg_server.api.routes import router as api_router
from agentburg_server.api.ws import router as ws_router
from agentburg_server.config import settings
from agentburg_server.db import engine
from agentburg_server.engine.tick import tick_engine
from agentburg_server.metrics import http_duration_seconds, http_requests
from agentburg_server.services import rate_limiter

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def _ensure_active_season() -> None:
    """Create a default season if none exists."""
    import agentburg_server.db as _db
    from agentburg_server.models.season import Season, SeasonStatus

    async with _db.get_session_factory()() as session:
        from sqlalchemy import select

        existing = await session.scalar(
            select(Season).where(Season.status == SeasonStatus.ACTIVE).limit(1)
        )
        if existing:
            logger.info("Active season: %s (tick %s–%s)", existing.name, existing.start_tick, existing.end_tick)
            return

        # Calculate end_tick: 7 real days = 1008 macro ticks (at 600s/tick)
        ticks_per_day = settings.ticks_per_day  # 6
        season_days = 168  # simulated days
        end_tick = tick_engine.tick + (ticks_per_day * season_days)

        season = Season(
            name="Season 1: Age of Iron",
            description="The first season of Ashenmere. Prove your worth in the frontier economy.",
            status=SeasonStatus.ACTIVE,
            theme="frontier",
            rules={},
            start_tick=tick_engine.tick,
            end_tick=end_tick,
            max_agents=settings.season_max_agents,
        )
        session.add(season)
        await session.commit()
        logger.info("Created Season 1: Age of Iron (ticks %d–%d)", tick_engine.tick, end_tick)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup and shutdown lifecycle."""
    logger.info("Ashenmere server starting...")

    # Redis rate-limiter pool
    await rate_limiter.connect()

    await tick_engine.start()
    logger.info("Tick engine started (interval=%.1fs)", settings.tick_interval_seconds)

    # Ensure an active season exists
    await _ensure_active_season()

    yield
    logger.info("Ashenmere server shutting down...")
    await tick_engine.stop()
    await rate_limiter.close()
    await engine.dispose()


app = FastAPI(
    title="Ashenmere",
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
async def security_headers_middleware(request: Request, call_next) -> Response:  # type: ignore[type-arg]
    """Attach security headers to every HTTP response."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if not settings.debug:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next) -> Response:  # type: ignore[type-arg]
    """Enforce per-IP sliding-window rate limits on HTTP requests."""
    if request.url.path in ("/metrics", "/health"):
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    allowed = await rate_limiter.check_rate_limit(
        key=f"rl:http:{client_ip}",
        limit=settings.api_rate_limit_per_minute,
        window=60,
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests"},
        )

    return await call_next(request)


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
    return {
        "status": "ok",
        "version": "0.1.0",
        "tick": tick_engine.tick,
        "tick_running": tick_engine.running,
    }


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
