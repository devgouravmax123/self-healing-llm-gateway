"""Custom exception types and error handling for the gateway."""

from typing import Any


class GatewayError(Exception):
    """Base exception for all LLM Gateway errors."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ProviderError(GatewayError):
    """Exception raised when an upstream LLM provider fails."""

    def __init__(
        self,
        message: str = "Provider execution failed",
        status_code: int = 502,
        provider: str | None = None,
        category: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, details=details)
        self.provider = provider
        self.category = category
        self.retryable = retryable


class CircuitBreakerError(GatewayError):
    """Exception raised when a request is blocked because the provider circuit is OPEN."""

    def __init__(
        self,
        provider: str,
        message: str | None = None,
        status_code: int = 503,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_message = message or (
            f"Provider '{provider}' is temporarily unavailable due to circuit protection."
        )
        circuit_details = details or {}
        circuit_details.setdefault("provider", provider)
        super().__init__(safe_message, status_code=status_code, details=circuit_details)
        self.provider = provider


class AuthenticationError(GatewayError):
    """Raised when authentication fails (missing, invalid, revoked, or expired credentials)."""

    def __init__(
        self,
        message: str = "Authentication required",
        status_code: int = 401,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, details=details)


class PermissionDeniedError(GatewayError):
    """Raised when authenticated identity lacks permissions (inactive tenant or non-admin)."""

    def __init__(
        self,
        message: str = "Access forbidden",
        status_code: int = 403,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, details=details)


class RateLimitError(GatewayError):
    """Exception raised when a tenant exceeds their configured rate limit."""

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        status_code: int = 429,
        retry_after: int | None = None,
        limit: int | None = None,
        remaining: int | None = 0,
        reset_time: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        error_details = details or {}
        if retry_after is not None:
            error_details["retry_after"] = retry_after
        if limit is not None:
            error_details["limit"] = limit
        if remaining is not None:
            error_details["remaining"] = remaining
        if reset_time is not None:
            error_details["reset"] = reset_time
        super().__init__(message, status_code=status_code, details=error_details)
        self.retry_after = retry_after
        self.limit = limit
        self.remaining = remaining
        self.reset_time = reset_time
