"""Main application module and FastAPI app factory."""

import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_chat import router as chat_router
from app.api.routes_health import router as health_router
from app.api.routes_metrics import router as metrics_router
from app.core.config import settings
from app.core.exceptions import GatewayError
from app.core.request_context import (
    generate_request_id,
    get_requested_model,
    reset_request_id,
    reset_requested_model,
    set_request_id,
    set_requested_model,
)
from app.db.session import database_manager
from app.observability.metrics import gateway_metrics
from app.storage.redis import redis_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    # Startup: Initialize Redis connection pool and PostgreSQL engine
    logger.info(
        "Starting up %s (Phase 11 — PostgreSQL Durable Storage Foundation)...", settings.app_name
    )
    await redis_manager.initialize()
    await database_manager.initialize()
    yield
    # Shutdown: Close Redis and PostgreSQL connection pools
    logger.info("Shutting down %s and releasing resources...", settings.app_name)
    await redis_manager.close()
    await database_manager.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI gateway application."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Self-Healing LLM Gateway — A resilient, OpenAI-compatible API gateway "
            "with provider routing, circuit breaking, failover, and observability."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # CORS middleware configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID and Chat Request Metrics middleware
    @app.middleware("http")
    async def request_id_and_metrics_middleware(
        request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        # Extract existing X-Request-ID header or generate a new one
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        id_token = set_request_id(request_id)
        model_token = set_requested_model("")

        is_chat_request = request.url.path == "/v1/chat/completions"
        start_time = time.perf_counter() if is_chat_request else 0.0

        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = request_id

            if is_chat_request:
                duration_sec = time.perf_counter() - start_time
                status_str = str(response.status_code)
                model_str = (
                    getattr(request.state, "requested_model", None)
                    or get_requested_model()
                    or "unknown"
                )

                gateway_metrics.requests_total.labels(
                    model=model_str,
                    status_code=status_str,
                ).inc()
                gateway_metrics.request_duration_seconds.labels(
                    model=model_str,
                    status_code=status_str,
                ).observe(duration_sec)

            return response
        finally:
            reset_request_id(id_token)
            reset_requested_model(model_token)

    # Exception Handlers
    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        request_id = request.headers.get("X-Request-ID") or "req_unknown"
        headers = {"X-Request-ID": request_id}

        # Add rate limit headers if exc is RateLimitError
        from app.core.exceptions import RateLimitError

        if isinstance(exc, RateLimitError):
            if exc.retry_after is not None:
                headers["Retry-After"] = str(exc.retry_after)
            if exc.limit is not None:
                headers["X-RateLimit-Limit"] = str(exc.limit)
            if exc.remaining is not None:
                headers["X-RateLimit-Remaining"] = str(exc.remaining)
            if exc.reset_time is not None:
                headers["X-RateLimit-Reset"] = str(exc.reset_time)

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": exc.message,
                    "type": type(exc).__name__,
                    "details": exc.details,
                    "request_id": request_id,
                }
            },
            headers=headers,
        )

    # Include API Routers
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(metrics_router)

    # Root endpoint
    @app.get("/", tags=["Root"], summary="Gateway Root")
    async def root() -> JSONResponse:
        return JSONResponse(
            content={
                "name": settings.app_name,
                "version": settings.app_version,
                "status": "operational",
                "phase": "Phase 09 — Redis Operational State",
                "docs_url": "/docs",
            }
        )

    return app


app = create_app()
