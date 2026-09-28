"""Router module responsible for provider selection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.exceptions import GatewayError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest
from app.routing.provider_registry import ProviderRegistry, provider_registry
from app.storage.circuit_storage import CircuitState

if TYPE_CHECKING:
    from app.reliability.circuit_breaker import CircuitBreakerManager

logger = logging.getLogger(__name__)


class NoHealthyProviderError(GatewayError):
    """Exception raised when no eligible provider is available for the request."""

    def __init__(
        self,
        message: str = "No eligible provider available",
        status_code: int = 503,
    ) -> None:
        super().__init__(message, status_code=status_code)


class Router:
    """Router for candidate filtering and provider target selection."""

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        circuit_manager: CircuitBreakerManager | None = None,
    ) -> None:
        self.registry = registry or provider_registry
        self._circuit_manager = circuit_manager

    @property
    def circuit_manager(self) -> CircuitBreakerManager:
        if self._circuit_manager is not None:
            return self._circuit_manager
        from app.reliability.circuit_breaker import circuit_breaker_manager

        return circuit_breaker_manager

    def get_candidates(
        self,
        request: ChatCompletionRequest,
        exclude_provider_ids: set[str] | None = None,
        check_circuit: bool = True,
    ) -> list[ProviderTarget]:
        """Find eligible provider targets ordered by priority.

        Filtering rules:
        1. Only enabled providers.
        2. Exclude any provider IDs in exclude_provider_ids (e.g. already attempted).
        3. If check_circuit=True, exclude providers whose circuit state is OPEN.
        4. Match model:
           - If providers exist matching the requested model, return those.
           - If generic (e.g. 'default') or no exact match exists, allow all eligible providers.
        5. Sort remaining eligible candidates by priority (ascending).
        """
        excluded = exclude_provider_ids or set()
        enabled_providers = [p for p in self.registry.list_enabled() if p.id not in excluded]

        if check_circuit:
            # Keep only providers that are not OPEN (CLOSED or HALF_OPEN eligible for probe)
            enabled_providers = [
                p
                for p in enabled_providers
                if self.circuit_manager.get_state(p.id) != CircuitState.OPEN
            ]

        if not enabled_providers:
            return []

        # Model matching logic
        clean_model = request.model
        if clean_model.startswith("ollama/"):
            clean_model = clean_model.removeprefix("ollama/")

        candidates: list[ProviderTarget]
        model_matches = [p for p in enabled_providers if p.model == clean_model]
        if model_matches:
            candidates = model_matches
        else:
            # Check if any provider in registry matches this specific model
            # If so, do NOT blindly fallback to an incompatible model
            all_registry_providers = self.registry.list_all()
            exact_known_model = any(p.model == clean_model for p in all_registry_providers)
            if exact_known_model:
                # Known model requested, but no eligible candidate is currently available
                candidates = []
            else:
                # Generic model request or placeholder -> fallback to all eligible providers
                candidates = enabled_providers

        # Sort candidates by priority (ascending: lower integer = higher priority)
        return sorted(candidates, key=lambda p: p.priority)

    def select_provider(
        self,
        request: ChatCompletionRequest,
        exclude_provider_ids: set[str] | None = None,
        check_circuit: bool = False,
    ) -> ProviderTarget:
        """Select the highest-priority eligible provider target for the request.

        Raises:
            NoHealthyProviderError: If no eligible candidates are available.
        """
        candidates = self.get_candidates(
            request=request,
            exclude_provider_ids=exclude_provider_ids,
            check_circuit=check_circuit,
        )
        if not self.registry.list_enabled():
            raise NoHealthyProviderError("No enabled LLM providers found in registry.")
        if not candidates:
            raise NoHealthyProviderError(
                "No eligible or healthy LLM providers found for the request."
            )
        return candidates[0]


# Global router instance
router = Router()
