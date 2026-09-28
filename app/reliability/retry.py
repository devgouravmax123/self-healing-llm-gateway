"""Retry manager and backoff strategy for resilient LLM provider execution."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from app.core.config import Settings, settings
from app.core.exceptions import ProviderError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest
from app.models.responses import ChatCompletionResponse
from app.providers.litellm_client import LiteLLMService, litellm_service

logger = logging.getLogger(__name__)


class RetryManager:
    """Manages bounded retries with exponential backoff and jitter for provider executions."""

    def __init__(
        self,
        config: Settings | None = None,
        provider_service: LiteLLMService | None = None,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config or settings
        self.provider_service = provider_service or litellm_service
        self._sleep = sleep_func or asyncio.sleep

    def calculate_backoff(self, attempt: int) -> float:
        """Calculate backoff delay for a given attempt index (0-indexed).

        Formula:
            delay = min(retry_max_delay, retry_base_delay * (2 ** attempt))
            if retry_jitter:
                delay = delay * random.uniform(0.5, 1.5)
            delay = min(retry_max_delay, delay)
        """
        base_delay = self.config.retry_base_delay * (2**attempt)
        capped_delay = min(self.config.retry_max_delay, base_delay)

        if self.config.retry_jitter:
            # Full/decorrelated jitter factor between 0.5 and 1.5
            jitter_factor = random.uniform(0.5, 1.5)
            final_delay = min(self.config.retry_max_delay, capped_delay * jitter_factor)
        else:
            final_delay = capped_delay

        return float(max(0.0, final_delay))

    async def execute_with_retry(
        self,
        request: ChatCompletionRequest,
        request_id: str,
        target: ProviderTarget,
    ) -> ChatCompletionResponse:
        """Execute chat completion against the target provider with retry policy.

        - Total allowed attempts = 1 + max_retries.
        - Immediately returns successful response.
        - If error is non-retryable, raises immediately without retrying.
        - If error is retryable, waits with backoff and retries the SAME target.
        - If all retries are exhausted, raises the final ProviderError.
        """
        max_retries = self.config.max_retries
        total_attempts = 1 + max_retries
        last_error: ProviderError | None = None

        for attempt in range(total_attempts):
            attempt_number = attempt + 1
            try:
                logger.debug(
                    "Executing provider call: request_id=%s, provider=%s, model=%s, attempt=%d/%d",
                    request_id,
                    target.id,
                    target.model,
                    attempt_number,
                    total_attempts,
                )
                return await self.provider_service.execute_chat_completion(
                    request=request,
                    request_id=request_id,
                    target=target,
                )
            except ProviderError as exc:
                last_error = exc
                logger.warning(
                    "Provider attempt failed: request_id=%s, provider=%s, attempt=%d/%d, "
                    "category=%s, retryable=%s, status_code=%d",
                    request_id,
                    target.id,
                    attempt_number,
                    total_attempts,
                    exc.category,
                    exc.retryable,
                    exc.status_code,
                )

                # If the error is not retryable, do not retry further
                if not exc.retryable:
                    logger.info(
                        "Error is non-retryable (category=%s). Halting retries for request_id=%s",
                        exc.category,
                        request_id,
                    )
                    raise exc

                # If this was the last attempt, halt
                if attempt_number >= total_attempts:
                    logger.error(
                        "Max retries exhausted (%d retries, %d total attempts) for request_id=%s",
                        max_retries,
                        total_attempts,
                        request_id,
                    )
                    raise exc

                # Calculate backoff delay and wait before next attempt
                delay = self.calculate_backoff(attempt)
                logger.info(
                    "Retrying same provider '%s' for request_id=%s in %.3fs (attempt %d/%d)",
                    target.id,
                    request_id,
                    delay,
                    attempt_number + 1,
                    total_attempts,
                )
                await self._sleep(delay)

        # Fallback if somehow loop exited without raising
        if last_error is not None:
            raise last_error
        raise ProviderError(message="Provider execution failed with unknown error", status_code=500)


# Global retry manager instance
retry_manager = RetryManager()
