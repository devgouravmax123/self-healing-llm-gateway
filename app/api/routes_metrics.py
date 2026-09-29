"""Prometheus metrics endpoint routes."""

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.observability.metrics import gateway_metrics

router = APIRouter(tags=["Observability"])


@router.get(
    "/metrics",
    summary="Prometheus Metrics",
    description="Expose gateway operational metrics in standard Prometheus exposition format.",
    response_class=Response,
)
def get_metrics() -> Response:
    """Generate and return Prometheus metrics exposition text from the application registry.

    Does not trigger LLM provider executions, database calls, or Redis network calls.
    Exposes only in-memory operational metrics without user payloads, prompts, or secrets.
    """
    output = generate_latest(gateway_metrics.registry)
    return Response(content=output, media_type=CONTENT_TYPE_LATEST)
