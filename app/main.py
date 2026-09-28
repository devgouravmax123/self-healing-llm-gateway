"""Main application module and FastAPI app factory."""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes_chat import router as chat_router
from app.api.routes_health import router as health_router
from app.core.config import settings
from app.core.exceptions import GatewayError
from app.core.request_context import (
    generate_request_id,
    reset_request_id,
    set_request_id,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    # Startup logic (resources initialized here in future phases)
    yield
    # Shutdown logic (cleanup resources here in future phases)


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

    # Request ID middleware
    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        # Extract existing X-Request-ID header or generate a new one
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        token = set_request_id(request_id)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)

    # Exception Handlers
    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        request_id = request.headers.get("X-Request-ID") or "req_unknown"
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
            headers={"X-Request-ID": request_id},
        )

    # Include API Routers
    app.include_router(health_router)
    app.include_router(chat_router)

    # Root endpoint
    @app.get("/", tags=["Root"], summary="Gateway Root")
    async def root() -> JSONResponse:
        return JSONResponse(
            content={
                "name": settings.app_name,
                "version": settings.app_version,
                "status": "operational",
                "phase": "Phase 08 — Provider Failover",
                "docs_url": "/docs",
            }
        )

    return app


app = create_app()
