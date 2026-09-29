"""OpenTelemetry tracing management and span utilities for the LLM Gateway."""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span, Status, StatusCode

from app.core.config import settings

logger = logging.getLogger(__name__)

# Global tracker for initialization state and active provider
_active_provider: TracerProvider | None = None
_tracing_initialized = False


def setup_tracing(
    service_name: str | None = None,
    exporter: SpanExporter | None = None,
    endpoint: str | None = None,
    enabled: bool | None = None,
) -> TracerProvider:
    """Initialize OpenTelemetry tracer provider idempotently.

    - If enabled is False, sets a clean TracerProvider without processors.
    - If exporter is provided (e.g. InMemorySpanExporter in tests), adds SimpleSpanProcessor.
    - If endpoint is provided or configured, sets up OTLP HTTP span exporter.
    - If no exporter/endpoint is provided and enabled is True, uses clean TracerProvider.
    """
    global _tracing_initialized, _active_provider

    is_enabled = settings.otel_enabled if enabled is None else enabled
    svc_name = service_name or settings.otel_service_name
    otlp_endpoint = endpoint if endpoint is not None else settings.otel_exporter_endpoint

    if not is_enabled:
        provider = TracerProvider()
        _active_provider = provider
        _tracing_initialized = True
        return provider

    resource = Resource.create({"service.name": svc_name})
    provider = TracerProvider(resource=resource)

    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            otlp_exp = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(otlp_exp))
            logger.info("Configured OTLP Span Exporter with endpoint: %s", otlp_endpoint)
        except Exception as exc:
            logger.warning("Failed to initialize OTLP Span Exporter (%s): %s", otlp_endpoint, exc)

    _active_provider = provider
    _tracing_initialized = True
    return provider


def get_tracer(name: str = "llm-gateway") -> trace.Tracer:
    """Get the active OpenTelemetry tracer."""
    global _active_provider
    if _active_provider is not None:
        return _active_provider.get_tracer(name)
    return trace.get_tracer(name)


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    tracer_name: str = "llm-gateway",
) -> Generator[Span | None, None, None]:
    """Context manager for safely executing an operation inside an OpenTelemetry span.

    Guarantees failure-safety: Any exception raised within tracing helpers or exporter
    will not mask or alter underlying business exceptions or execution results.
    """
    try:
        tracer = get_tracer(tracer_name)
        cm = tracer.start_as_current_span(name)
    except Exception:
        # If tracer creation or span initialization fails, yield None safely
        yield None
        return

    try:
        with cm as span:
            if attributes:
                for k, v in attributes.items():
                    if v is not None:
                        try:
                            span.set_attribute(k, v)
                        except Exception:
                            pass
            try:
                yield span
                try:
                    span.set_status(Status(StatusCode.OK))
                except Exception:
                    pass
            except Exception as exc:
                try:
                    span.set_status(Status(StatusCode.ERROR, description=type(exc).__name__))
                    span.set_attribute("error.type", type(exc).__name__)
                except Exception:
                    pass
                raise
    except Exception as exc:
        # Re-raise underlying business exception if it occurred inside the yield block
        # Only suppress exceptions that originated purely from context manager enter/exit
        if not isinstance(exc, (GeneratorExit, StopIteration)):
            raise exc
