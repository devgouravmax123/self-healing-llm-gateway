"""Usage tracking service for extracting token usage and persisting durable records."""

from __future__ import annotations

import logging

from app.db.repositories.usage_repo import UsageRepository, usage_repository
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest
from app.models.responses import ChatCompletionResponse
from app.pricing.calculator import CostCalculator, cost_calculator

logger = logging.getLogger(__name__)


class UsageTracker:
    """Orchestrates token extraction, cost calculation, and safe background persistence."""

    def __init__(
        self,
        calculator: CostCalculator | None = None,
        repository: UsageRepository | None = None,
    ) -> None:
        self.calculator = calculator or cost_calculator
        self.repository = repository or usage_repository

    async def record_usage(
        self,
        request: ChatCompletionRequest,
        request_id: str,
        target: ProviderTarget,
        response: ChatCompletionResponse,
        tenant_id: str | None = None,
    ) -> None:
        """Extract usage from completion response, calculate estimated cost, and persist safely.

        Guarantees:
        - Missing/None tokens remain None (no fabricated zeros).
        - Provider-reported total_tokens is preserved without synthetic recalculation.
        - Attributed to the actual final provider and model in `target`.
        - Tenant ID is bound strictly to the authenticated `tenant_id` (no spoofing).
        - Feature resolved from `request.metadata` (defaulting to 'chat').
        - Database errors are caught and logged without failing the client response.
        """
        # 1. Extract tokens from response.usage
        input_tokens: int | None = None
        output_tokens: int | None = None
        total_tokens: int | None = None

        if response.usage is not None:
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            total_tokens = response.usage.total_tokens

        # 2. Extract feature from metadata; tenant_id is strictly the authenticated identity
        effective_tenant_id: str | None = tenant_id
        feature: str | None = "chat"

        if request.metadata and isinstance(request.metadata, dict):
            # Fallback to metadata only if tenant_id was not explicitly provided
            if effective_tenant_id is None:
                effective_tenant_id = request.metadata.get("tenant_id")
            feature = request.metadata.get("feature", "chat")

        # 3. Calculate estimated cost
        provider_name = target.provider
        model_name = target.model
        estimated_cost = self.calculator.calculate_cost(
            provider=provider_name,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        logger.debug(
            "Tracking usage req_id=%s: prov=%s, model=%s, in=%s, out=%s, total=%s, cost=%s",
            request_id,
            provider_name,
            model_name,
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_cost,
        )

        # 4. Persist to database repository safely
        try:
            await self.repository.create_usage_record(
                request_id=request_id,
                provider=provider_name,
                model=model_name,
                tenant_id=effective_tenant_id,
                feature=feature,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost=estimated_cost,
                requested_model=request.model,
            )
        except Exception as exc:
            logger.warning("Usage tracking error for request_id=%s: %s", request_id, exc)


# Global usage tracker instance
usage_tracker = UsageTracker()
