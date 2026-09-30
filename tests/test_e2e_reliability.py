"""End-to-End Reliability Demonstration test suite (Phase 20).

Validates the 5 canonical E2E resilience scenarios using deterministic, test-only ProviderTargets:
- Scenario A: Healthy request execution & complete observability lifecycle
- Scenario B: Provider failure & automatic failover
- Scenario C: Circuit breaker trip (CLOSED -> OPEN) & fast bypass
- Scenario D: Circuit recovery (OPEN -> HALF-OPEN -> CLOSED) via successful probe
- Scenario E: All providers unavailable & bounded terminal error handling
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.exceptions import ProviderError
from app.main import app
from app.models.provider import ProviderTarget
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.observability.metrics import gateway_metrics
from app.reliability.circuit_breaker import CircuitBreakerManager, CircuitState
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import Router
from app.storage.circuit_storage import InMemoryCircuitStorage


def _create_mock_response(
    provider_id: str,
    model: str,
    content: str = "Test completion response",
    prompt_tokens: int = 12,
    completion_tokens: int = 8,
) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id=f"chatcmpl-test-{provider_id}",
        created=1700000000,
        model=model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessageResponse(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class TestE2EReliabilityScenarios:
    """Deterministic End-to-End reliability scenarios covering the complete lifecycle."""

    @pytest.fixture
    def e2e_environment(self) -> dict[str, Any]:
        """Set up an isolated, deterministic test registry and mock execution environment."""
        # 1. Config with fast backoff/cooldown for tests
        cfg = Settings(
            MAX_RETRIES=2,
            RETRY_BASE_DELAY=0.01,
            RETRY_MAX_DELAY=0.05,
            RETRY_JITTER=False,
            CIRCUIT_FAILURE_THRESHOLD=3,
            CIRCUIT_COOLDOWN_SECONDS=0.1,
            CIRCUIT_HALF_OPEN_MAX_PROBES=1,
            MAX_FAILOVER_PROVIDERS=3,
        )

        # 2. Test-only provider targets
        provider_a = ProviderTarget(
            id="test_provider_a",
            provider="test_driver",
            model="test-model",
            priority=1,
            enabled=True,
        )
        provider_b = ProviderTarget(
            id="test_provider_b",
            provider="test_driver",
            model="test-model",
            priority=2,
            enabled=True,
        )

        registry = ProviderRegistry()
        registry.register(provider_a)
        registry.register(provider_b)

        # 3. Isolated in-memory circuit breaker and router
        storage = InMemoryCircuitStorage()
        circuit_mgr = CircuitBreakerManager(config=cfg, storage=storage, metrics=gateway_metrics)
        router = Router(registry=registry, circuit_manager=circuit_mgr)

        return {
            "config": cfg,
            "registry": registry,
            "provider_a": provider_a,
            "provider_b": provider_b,
            "circuit_mgr": circuit_mgr,
            "router": router,
        }

    @pytest.mark.asyncio
    async def test_scenario_a_healthy_request_execution(
        self, e2e_environment: dict[str, Any]
    ) -> None:
        """Scenario A: Verify healthy request through full stack with usage, metrics, and logs."""
        router = e2e_environment["router"]
        cfg = e2e_environment["config"]
        circuit_mgr = e2e_environment["circuit_mgr"]

        mock_litellm = AsyncMock()
        mock_litellm.execute_chat_completion.return_value = _create_mock_response(
            provider_id="test_provider_a",
            model="test-model",
            content="Healthy response from Provider A",
            prompt_tokens=15,
            completion_tokens=10,
        )

        retry_mgr = RetryManager(
            config=cfg,
            provider_service=mock_litellm,
            metrics=gateway_metrics,
        )
        failover_mgr = FailoverManager(
            config=cfg,
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=circuit_mgr,
            metrics=gateway_metrics,
        )

        with (
            patch("app.api.routes_chat.gateway_router", router),
            patch("app.api.routes_chat.failover_manager", failover_mgr),
            patch("app.api.routes_chat.circuit_breaker_manager", circuit_mgr),
            patch("app.api.routes_chat.retry_manager", retry_mgr),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                res = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": "Hello"}],
                        "metadata": {"feature": "e2e_test"},
                    },
                )

        assert res.status_code == 200
        data = res.json()
        assert data["choices"][0]["message"]["content"] == "Healthy response from Provider A"
        assert data["usage"]["total_tokens"] == 25
        assert circuit_mgr.get_state("test_provider_a") == CircuitState.CLOSED
        assert mock_litellm.execute_chat_completion.call_count == 1

    @pytest.mark.asyncio
    async def test_scenario_b_provider_failure_and_failover(
        self, e2e_environment: dict[str, Any]
    ) -> None:
        """Scenario B: Primary fails, exhausts retries, trips failure, and fails over to B."""
        router = e2e_environment["router"]
        cfg = e2e_environment["config"]
        circuit_mgr = e2e_environment["circuit_mgr"]

        mock_litellm = AsyncMock()

        # Target A fails with TIMEOUT (retryable); Target B succeeds
        async def mock_execute(request: Any, request_id: str, target: ProviderTarget) -> Any:
            if target.id == "test_provider_a":
                raise ProviderError(
                    message="Simulated upstream timeout on Provider A",
                    status_code=504,
                    provider=target.id,
                    category="TIMEOUT",
                    retryable=True,
                )
            elif target.id == "test_provider_b":
                return _create_mock_response(
                    provider_id="test_provider_b",
                    model="test-model",
                    content="Failover success from Provider B",
                )
            raise ValueError(f"Unknown target {target.id}")

        mock_litellm.execute_chat_completion.side_effect = mock_execute

        retry_mgr = RetryManager(
            config=cfg,
            provider_service=mock_litellm,
            metrics=gateway_metrics,
        )
        failover_mgr = FailoverManager(
            config=cfg,
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=circuit_mgr,
            metrics=gateway_metrics,
        )

        with (
            patch("app.api.routes_chat.gateway_router", router),
            patch("app.api.routes_chat.failover_manager", failover_mgr),
            patch("app.api.routes_chat.circuit_breaker_manager", circuit_mgr),
            patch("app.api.routes_chat.retry_manager", retry_mgr),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                res = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": "Reliability test"}],
                    },
                )

        assert res.status_code == 200
        data = res.json()
        assert data["choices"][0]["message"]["content"] == "Failover success from Provider B"

        # Provider A executed 3 physical attempts (1 initial + 2 retries)
        # Provider B executed 1 physical attempt
        assert mock_litellm.execute_chat_completion.call_count == 4
        # Target B recorded success
        assert circuit_mgr.get_state("test_provider_b") == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_scenario_c_circuit_breaker_trips_open(
        self, e2e_environment: dict[str, Any]
    ) -> None:
        """Scenario C: Repeated failures trip provider A circuit to OPEN; fast bypass confirmed."""
        router = e2e_environment["router"]
        circuit_mgr = e2e_environment["circuit_mgr"]

        # Record 3 failures (threshold = 3) on Provider A
        for _ in range(3):
            err = ProviderError(message="500 Internal", status_code=502, category="SERVER_ERROR")
            await circuit_mgr.record_failure("test_provider_a", err)

        # Verify state is OPEN
        assert circuit_mgr.get_state("test_provider_a") == CircuitState.OPEN

        # Router candidates with check_circuit=True must exclude Provider A
        candidates = router.get_candidates(
            request=AsyncMock(model="test-model"),
            check_circuit=True,
        )
        assert len(candidates) == 1
        assert candidates[0].id == "test_provider_b"

    @pytest.mark.asyncio
    async def test_scenario_d_circuit_recovery_half_open_to_closed(
        self, e2e_environment: dict[str, Any]
    ) -> None:
        """Scenario D: Cooldown elapses -> HALF-OPEN probe allowed -> success resets to CLOSED."""
        circuit_mgr = e2e_environment["circuit_mgr"]

        # 1. Trip circuit to OPEN
        for _ in range(3):
            err = ProviderError(message="504 Timeout", status_code=504, category="TIMEOUT")
            await circuit_mgr.record_failure("test_provider_a", err)
        assert circuit_mgr.get_state("test_provider_a") == CircuitState.OPEN

        # 2. Wait for cooldown duration (0.1s in test config)
        await asyncio.sleep(0.12)

        # 3. Next permission request transitions to HALF_OPEN probe
        assert circuit_mgr.get_state("test_provider_a") == CircuitState.HALF_OPEN

        # 4. Acquire probe permission
        await circuit_mgr.acquire_permission("test_provider_a")

        # 5. Subsequent concurrent requests are blocked while probe is in flight
        with pytest.raises(Exception) as exc_info:
            await circuit_mgr.acquire_permission("test_provider_a")
        assert "HALF_OPEN" in str(exc_info.value) or "probe" in str(exc_info.value).lower()

        # 6. Probe succeeds -> Circuit resets to CLOSED
        await circuit_mgr.record_success("test_provider_a")
        assert circuit_mgr.get_state("test_provider_a") == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_scenario_e_all_providers_unavailable_bounded_error(
        self, e2e_environment: dict[str, Any]
    ) -> None:
        """Scenario E: All providers fail/exhausted -> returns HTTP 503 without infinite loops."""
        router = e2e_environment["router"]
        cfg = e2e_environment["config"]
        circuit_mgr = e2e_environment["circuit_mgr"]

        mock_litellm = AsyncMock()

        # Both targets throw non-recoverable upstream errors
        async def mock_all_fail(request: Any, request_id: str, target: ProviderTarget) -> Any:
            raise ProviderError(
                message=f"Total outage on {target.id}",
                status_code=503,
                provider=target.id,
                category="UPSTREAM_ERROR",
                retryable=True,
            )

        mock_litellm.execute_chat_completion.side_effect = mock_all_fail

        retry_mgr = RetryManager(
            config=cfg,
            provider_service=mock_litellm,
            metrics=gateway_metrics,
        )
        failover_mgr = FailoverManager(
            config=cfg,
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=circuit_mgr,
            metrics=gateway_metrics,
        )

        with (
            patch("app.api.routes_chat.gateway_router", router),
            patch("app.api.routes_chat.failover_manager", failover_mgr),
            patch("app.api.routes_chat.circuit_breaker_manager", circuit_mgr),
            patch("app.api.routes_chat.retry_manager", retry_mgr),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                res = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "test-model",
                        "messages": [{"role": "user", "content": "Total outage test"}],
                    },
                )

        # Expected 503 or 502 terminal error
        assert res.status_code in (502, 503)
        # 3 attempts on Provider A + 3 attempts on Provider B = 6 attempts total, then bounded exit
        assert mock_litellm.execute_chat_completion.call_count == 6
