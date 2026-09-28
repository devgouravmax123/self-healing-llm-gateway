"""Centralized error classification system for upstream LLM provider failures."""

from enum import StrEnum
from typing import Any

from litellm.exceptions import (
    APIConnectionError,
    APIError,
    APIResponseValidationError,
    AuthenticationError,
    BadGatewayError,
    BadRequestError,
    ContentPolicyViolationError,
    ContextWindowExceededError,
    InternalServerError,
    InvalidRequestError,
    JSONSchemaValidationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
    UnprocessableEntityError,
    UnsupportedParamsError,
)
from pydantic import BaseModel, Field


class ErrorCategory(StrEnum):
    """Normalized categories for provider and gateway errors."""

    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    UNKNOWN = "UNKNOWN"


# Mapping from error category to whether it is considered retryable policy
RETRYABLE_POLICY: dict[ErrorCategory, bool] = {
    ErrorCategory.TIMEOUT: True,
    ErrorCategory.RATE_LIMITED: True,
    ErrorCategory.SERVER_ERROR: True,
    ErrorCategory.UPSTREAM_ERROR: True,
    ErrorCategory.CONNECTION_ERROR: True,
    ErrorCategory.AUTH_ERROR: False,
    ErrorCategory.BAD_REQUEST: False,
    ErrorCategory.UNKNOWN: False,
}

# Mapping from error category to default HTTP status code to return to API client
CATEGORY_HTTP_STATUS: dict[ErrorCategory, int] = {
    ErrorCategory.TIMEOUT: 504,
    ErrorCategory.RATE_LIMITED: 429,
    ErrorCategory.SERVER_ERROR: 502,
    ErrorCategory.UPSTREAM_ERROR: 502,
    ErrorCategory.CONNECTION_ERROR: 503,
    ErrorCategory.AUTH_ERROR: 502,
    ErrorCategory.BAD_REQUEST: 400,
    ErrorCategory.UNKNOWN: 500,
}


class ClassifiedError(BaseModel):
    """Internal structured representation of a classified provider error."""

    category: ErrorCategory = Field(description="Normalized error category.")
    message: str = Field(description="Sanitized error description.")
    original_type: str = Field(description="Type name of the original exception.")
    http_status: int = Field(description="HTTP status code for API response.")
    retryable: bool = Field(description="Whether this error category is eligible for retry.")
    provider: str | None = Field(default=None, description="Identifier of the failing provider.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Safe metadata for tracing/logging."
    )


class ErrorClassifier:
    """Centralized classifier converting provider exceptions into ClassifiedError."""

    @classmethod
    def classify(
        cls,
        exc: Exception,
        provider: str | None = None,
        status_code: int | None = None,
    ) -> ClassifiedError:
        """Classify any exception into a structured ClassifiedError."""
        exc_type_name = type(exc).__name__

        extracted_status = status_code
        if extracted_status is None and hasattr(exc, "status_code"):
            raw_status = getattr(exc, "status_code", None)
            if raw_status is not None:
                try:
                    extracted_status = int(raw_status)
                except (ValueError, TypeError):
                    extracted_status = None

        category = cls._determine_category(exc, extracted_status)
        retryable = RETRYABLE_POLICY.get(category, False)
        final_http_status = (
            extracted_status if extracted_status in (400, 429) else CATEGORY_HTTP_STATUS[category]
        )

        safe_message = cls._format_safe_message(category, exc)

        return ClassifiedError(
            category=category,
            message=safe_message,
            original_type=exc_type_name,
            http_status=final_http_status,
            retryable=retryable,
            provider=provider,
            details={"original_status_code": extracted_status} if extracted_status else {},
        )

    @classmethod
    def _determine_category(cls, exc: Exception, status_code: int | None = None) -> ErrorCategory:
        """Determine category from exception type and status code."""
        # Timeout exceptions
        if isinstance(exc, (Timeout, TimeoutError)):
            return ErrorCategory.TIMEOUT

        # Connection / Network exceptions
        if isinstance(exc, (APIConnectionError, ConnectionError)):
            return ErrorCategory.CONNECTION_ERROR

        # Rate limit exceptions (or HTTP 429)
        if isinstance(exc, RateLimitError) or status_code == 429:
            return ErrorCategory.RATE_LIMITED

        # Authentication / Permission exceptions (or HTTP 401/403)
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)) or status_code in (
            401,
            403,
        ):
            return ErrorCategory.AUTH_ERROR

        # Bad Request / Validation exceptions (or HTTP 400/422)
        if isinstance(
            exc,
            (
                BadRequestError,
                InvalidRequestError,
                JSONSchemaValidationError,
                APIResponseValidationError,
                UnprocessableEntityError,
                UnsupportedParamsError,
                ContextWindowExceededError,
                ContentPolicyViolationError,
                NotFoundError,
            ),
        ) or status_code in (400, 422):
            return ErrorCategory.BAD_REQUEST

        # Status code-based classification
        if status_code == 500 or isinstance(exc, InternalServerError):
            return ErrorCategory.SERVER_ERROR

        if status_code in (502, 503, 504) or isinstance(
            exc, (ServiceUnavailableError, BadGatewayError)
        ):
            return ErrorCategory.UPSTREAM_ERROR

        # Generic APIError fallback with status inspection
        if isinstance(exc, APIError):
            if status_code == 500:
                return ErrorCategory.SERVER_ERROR
            if status_code in (502, 503, 504):
                return ErrorCategory.UPSTREAM_ERROR
            return ErrorCategory.UPSTREAM_ERROR

        return ErrorCategory.UNKNOWN

    @classmethod
    def _format_safe_message(cls, category: ErrorCategory, exc: Exception) -> str:
        """Format a safe error message without leaking sensitive internal details."""
        match category:
            case ErrorCategory.TIMEOUT:
                return "Upstream LLM provider request timed out."
            case ErrorCategory.RATE_LIMITED:
                return "Upstream LLM provider rate limit exceeded."
            case ErrorCategory.SERVER_ERROR:
                return "Upstream LLM provider encountered an internal server error."
            case ErrorCategory.UPSTREAM_ERROR:
                return "Upstream LLM provider service is temporarily unavailable."
            case ErrorCategory.CONNECTION_ERROR:
                return "Failed to establish network connection to upstream LLM provider."
            case ErrorCategory.AUTH_ERROR:
                return "Authentication or permission error with upstream LLM provider."
            case ErrorCategory.BAD_REQUEST:
                return "Invalid request sent to LLM provider."
            case _:
                return "An unexpected provider error occurred."


# Global error classifier instance
error_classifier = ErrorClassifier()
