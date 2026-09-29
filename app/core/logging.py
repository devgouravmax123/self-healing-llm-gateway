"""Structured JSON logging configuration and formatter for the LLM Gateway."""

from __future__ import annotations

import datetime
import json
import logging
import sys
from typing import Any

from app.core.config import settings
from app.core.request_context import (
    get_feature,
    get_provider_id,
    get_request_id,
    get_requested_model,
    get_tenant_id,
)

# Canonical field order for JSON output as defined in ADR-012 and Phase 14.4 specification
CANONICAL_KEYS = [
    "timestamp",
    "level",
    "request_id",
    "tenant_id",
    "feature",
    "provider",
    "model",
    "event",
    "error_type",
    "latency",
    "attempt",
]

# Sensitive keys that must NEVER appear in log records
SENSITIVE_KEY_NAMES = {
    "authorization",
    "api_key",
    "apikey",
    "provider_api_key",
    "secret",
    "secrets",
    "password",
    "token",
    "access_token",
    "database_url",
    "redis_url",
    "prompt",
    "raw_prompt",
    "response",
    "raw_response",
    "messages",
}


def sanitize_value(value: Any) -> Any:
    """Sanitize data structures recursively to prevent leaking sensitive information."""
    try:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for k, v in value.items():
                k_lower = str(k).lower()
                if any(sens in k_lower for sens in SENSITIVE_KEY_NAMES):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = sanitize_value(v)
            return sanitized
        if isinstance(value, (list, tuple, set)):
            return [sanitize_value(item) for item in value]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        # Check if string looks like an authorization header or API key
        val_str = str(value)
        if (
            val_str.startswith("Bearer ")
            or val_str.startswith("gw_live_")
            or val_str.startswith("gw_test_")
        ):
            return "[REDACTED]"
        return val_str
    except Exception:
        return "[UNSERIALIZABLE]"


class StructuredJSONFormatter(logging.Formatter):
    """Logging formatter that formats log records into valid structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record into a structured JSON string."""
        try:
            # 1. Generate ISO-8601 UTC timestamp
            timestamp_str = datetime.datetime.fromtimestamp(
                record.created, tz=datetime.UTC
            ).isoformat()

            # 2. Extract context from request_context or record attributes
            request_id = getattr(record, "request_id", None) or get_request_id() or None
            tenant_id = getattr(record, "tenant_id", None) or get_tenant_id() or None
            feature = getattr(record, "feature", None) or get_feature() or None

            # 3. Extract provider and model (explicitly passed or from request context)
            provider = getattr(record, "provider", None) or get_provider_id() or None
            model = getattr(record, "model", None) or get_requested_model() or None

            # 4. Extract lifecycle event / metadata fields
            event = getattr(record, "event", None)
            error_type = getattr(record, "error_type", None)
            latency = getattr(record, "latency", None)
            if latency is None and hasattr(record, "latency_ms"):
                latency = record.latency_ms
            attempt = getattr(record, "attempt", None)

            # Build payload dictionary with canonical fields
            log_dict: dict[str, Any] = {
                "timestamp": timestamp_str,
                "level": record.levelname,
            }

            # Include contextual fields only if present/available
            if request_id:
                log_dict["request_id"] = request_id
            if tenant_id:
                log_dict["tenant_id"] = tenant_id
            if feature:
                log_dict["feature"] = feature
            if provider:
                log_dict["provider"] = provider
            if model:
                log_dict["model"] = model
            if event:
                log_dict["event"] = event
            if error_type:
                log_dict["error_type"] = error_type
            if latency is not None:
                log_dict["latency"] = latency
            if attempt is not None:
                log_dict["attempt"] = attempt

            # Include message if no event is defined or distinct
            try:
                msg = record.getMessage()
                if msg and not event:
                    log_dict["message"] = msg
            except Exception:
                log_dict["message"] = str(record.msg)

            # Include custom extra fields if present (excluding internal logging attributes)
            standard_attrs = {
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
                "request_id",
                "tenant_id",
                "feature",
                "provider",
                "model",
                "event",
                "error_type",
                "latency",
                "latency_ms",
                "attempt",
            }
            for attr, val in record.__dict__.items():
                if attr not in standard_attrs and not attr.startswith("_"):
                    attr_lower = attr.lower()
                    if any(sens in attr_lower for sens in SENSITIVE_KEY_NAMES):
                        log_dict[attr] = "[REDACTED]"
                    else:
                        log_dict[attr] = sanitize_value(val)

            # Handle exception information if present (without leaking sensitive traces)
            if record.exc_info and not error_type:
                exc = record.exc_info[1]
                if exc:
                    log_dict["error_type"] = type(exc).__name__

            return json.dumps(log_dict, ensure_ascii=False)
        except Exception:
            # Fallback safe serialization
            try:
                created_ts = record.created
            except Exception:
                created_ts = datetime.datetime.now(datetime.UTC).timestamp()
            timestamp_fallback = datetime.datetime.fromtimestamp(
                created_ts, tz=datetime.UTC
            ).isoformat()
            fallback_dict = {
                "timestamp": timestamp_fallback,
                "level": getattr(record, "levelname", "INFO"),
                "event": getattr(record, "event", None) or "log_formatting_error",
                "message": "Failed to serialize log record to JSON",
            }
            return json.dumps(fallback_dict)


def setup_logging(log_level: str | None = None) -> None:
    """Initialize structured JSON logging configuration exactly once."""
    level_name = (log_level or settings.log_level).upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Check if a StructuredJSONFormatter handler already exists to prevent duplication
    has_json_handler = any(
        isinstance(getattr(h, "formatter", None), StructuredJSONFormatter)
        for h in root_logger.handlers
    )

    if not has_json_handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJSONFormatter())
        handler.setLevel(level)
        root_logger.addHandler(handler)

    # Configure app loggers
    app_logger = logging.getLogger("app")
    app_logger.setLevel(level)
