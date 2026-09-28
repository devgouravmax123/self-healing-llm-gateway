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
