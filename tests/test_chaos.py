"""Tests for Phase 15 — Chaos and Fault Injection."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    Timeout,
)

from app.core.config import Settings
from app.core.exceptions import GatewayError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
)
from app.providers.litellm_client import LiteLLMService
from app.reliability.chaos import ChaosManager, FaultType
from app.reliability.circuit_breaker import CircuitBreakerManager
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import Router
from app.storage.circuit_storage import CircuitState, InMemoryCircuitStorage


def create_mock_response(
    model: str = "qwen2.5:3b", content: str = "Test response"
) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test-123",
        model=model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessageResponse(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
    )


class TestChaosManagerUnit:
    """Unit tests for ChaosManager fault injection lifecycle and safety."""

    @pytest.fixture
    def test_registry(self) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg.register(
            ProviderTarget(id="ollama_a", provider="ollama", model="qwen2.5:3b", priority=1)
        )
        reg.register(
            ProviderTarget(id="ollama_b", provider="ollama", model="qwen2.5:3b", priority=2)
        )
        return reg

    @pytest.fixture
    def test_chaos(self, test_registry: ProviderRegistry) -> ChaosManager:
        cfg = Settings(ENVIRONMENT="development", CHAOS_ENABLED=True)
        return ChaosManager(config=cfg, registry=test_registry)

    @pytest.mark.asyncio
    async def test_set_rule_and_get_rule(self, test_chaos: ChaosManager) -> None:
        rule = await test_chaos.set_rule(
            provider_id="ollama_a",
            fault=FaultType.TIMEOUT,
            duration_seconds=60,
            failure_count=5,
        )
        assert rule.provider_id == "ollama_a"
        assert rule.fault == FaultType.TIMEOUT
        assert rule.duration_seconds == 60
        assert rule.failure_count == 5

        fetched = await test_chaos.get_rule("ollama_a")
        assert fetched is not None
        assert fetched.provider_id == "ollama_a"

    @pytest.mark.asyncio
    async def test_set_rule_unknown_provider_rejected(self, test_chaos: ChaosManager) -> None:
        with pytest.raises(GatewayError) as exc_info:
            await test_chaos.set_rule(provider_id="non_existent_provider", fault=FaultType.TIMEOUT)
        assert exc_info.value.status_code == 400
        assert "Unknown or unconfigured provider_id" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_set_rule_excessive_latency_rejected(self, test_chaos: ChaosManager) -> None:
        with pytest.raises(GatewayError) as exc_info:
            await test_chaos.set_rule(
                provider_id="ollama_a",
                fault=FaultType.LATENCY,
                latency_seconds=100.0,
            )
        assert exc_info.value.status_code == 400
        assert "Latency must be between 0.0 and 60.0 seconds" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_clear_rule_and_clear_all(self, test_chaos: ChaosManager) -> None:
        await test_chaos.set_rule(provider_id="ollama_a", fault=FaultType.TIMEOUT)
        await test_chaos.set_rule(provider_id="ollama_b", fault=FaultType.SERVER_ERROR)

        assert len(await test_chaos.list_rules()) == 2

        cleared_a = await test_chaos.clear_rule("ollama_a")
        assert cleared_a is True
        assert len(await test_chaos.list_rules()) == 1

        cleared_count = await test_chaos.clear_all()
        assert cleared_count == 1
        assert len(await test_chaos.list_rules()) == 0

    @pytest.mark.asyncio
    async def test_failure_count_expiration(self, test_chaos: ChaosManager) -> None:
        await test_chaos.set_rule(
            provider_id="ollama_a",
            fault=FaultType.SERVER_ERROR,
            failure_count=2,
        )

        # 1st attempt -> raises
        with pytest.raises(InternalServerError):
            await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")

        # 2nd attempt -> raises
        with pytest.raises(InternalServerError):
            await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")

        # 3rd attempt -> rule expired, does not raise
        await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")


class TestFaultTypesInjection:
    """Validate all individual documented fault types."""

    @pytest.fixture
    def test_chaos(self) -> ChaosManager:
        reg = ProviderRegistry()
        reg.register(ProviderTarget(id="ollama_a", provider="ollama", model="qwen2.5:3b"))
        cfg = Settings(ENVIRONMENT="development", CHAOS_ENABLED=True)
        return ChaosManager(config=cfg, registry=reg)

    @pytest.mark.asyncio
    async def test_inject_timeout(self, test_chaos: ChaosManager) -> None:
        await test_chaos.set_rule("ollama_a", FaultType.TIMEOUT)
        with pytest.raises(Timeout):
            await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")

    @pytest.mark.asyncio
    async def test_inject_server_error(self, test_chaos: ChaosManager) -> None:
        await test_chaos.set_rule("ollama_a", FaultType.SERVER_ERROR)
        with pytest.raises(InternalServerError):
            await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")

    @pytest.mark.asyncio
    async def test_inject_rate_limited(self, test_chaos: ChaosManager) -> None:
        await test_chaos.set_rule("ollama_a", FaultType.RATE_LIMITED)
        with pytest.raises(RateLimitError):
            await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")

    @pytest.mark.asyncio
    async def test_inject_network_error(self, test_chaos: ChaosManager) -> None:
        await test_chaos.set_rule("ollama_a", FaultType.NETWORK_ERROR)
        with pytest.raises(APIConnectionError):
            await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")

    @pytest.mark.asyncio
    async def test_inject_latency(self, test_chaos: ChaosManager) -> None:
        await test_chaos.set_rule("ollama_a", FaultType.LATENCY, latency_seconds=0.05)
        t0 = asyncio.get_event_loop().time()
        await test_chaos.maybe_inject_fault("ollama_a", "qwen2.5:3b")
        t1 = asyncio.get_event_loop().time()
        assert (t1 - t0) >= 0.04


class TestReliabilitySelfHealingWithChaos:
    """End-to-end self-healing verification: Fault Injection -> Retry -> Circuit -> Failover."""

    @pytest.mark.asyncio
    async def test_injected_timeout_failover_to_healthy_provider(self) -> None:
        """Inject TIMEOUT into provider A, verify retry fails on A, trips failover to B."""
        registry = ProviderRegistry()
        target_a = ProviderTarget(id="ollama_a", provider="ollama", model="qwen2.5:3b", priority=1)
        target_b = ProviderTarget(id="ollama_b", provider="ollama", model="qwen2.5:3b", priority=2)
        registry.register(target_a)
        registry.register(target_b)

        chaos_cfg = Settings(
            ENVIRONMENT="development",
            CHAOS_ENABLED=True,
            MAX_RETRIES=1,
            RETRY_BASE_DELAY=0.01,
        )
        test_chaos = ChaosManager(config=chaos_cfg, registry=registry)
        # Inject TIMEOUT on provider A
        await test_chaos.set_rule("ollama_a", FaultType.TIMEOUT)

        provider_svc = LiteLLMService(config=chaos_cfg)

        async def fake_acompletion(**kwargs: Any) -> Any:
            model = kwargs.get("model", "")
            return create_mock_response(content=f"Response for {model}")

        circuit_mgr = CircuitBreakerManager(config=chaos_cfg, storage=InMemoryCircuitStorage())
        router = Router(registry=registry, circuit_manager=circuit_mgr)
        retry_mgr = RetryManager(
            config=chaos_cfg, provider_service=provider_svc, sleep_func=AsyncMock()
        )
        failover_mgr = FailoverManager(
            config=chaos_cfg,
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=circuit_mgr,
        )

        req = ChatCompletionRequest(
            model="qwen2.5:3b",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        with (
            patch("app.reliability.chaos.chaos_manager", test_chaos),
            patch("app.providers.litellm_client.chaos_manager", test_chaos),
            patch("litellm.acompletion", side_effect=fake_acompletion),
        ):
            resp = await failover_mgr.execute_with_failover(req, request_id="req-chaos-failover")
            assert resp.choices[0].message.content == "Response for ollama/qwen2.5:3b"

    @pytest.mark.asyncio
    async def test_repeated_chaos_trips_circuit_to_open(self) -> None:
        """Repeated injected 500 errors trip provider circuit breaker from CLOSED to OPEN."""
        registry = ProviderRegistry()
        target_a = ProviderTarget(id="ollama_a", provider="ollama", model="qwen2.5:3b", priority=1)
        registry.register(target_a)

        cfg = Settings(
            ENVIRONMENT="development",
            CHAOS_ENABLED=True,
            CIRCUIT_FAILURE_THRESHOLD=3,
            MAX_RETRIES=0,
        )
        test_chaos = ChaosManager(config=cfg, registry=registry)
        await test_chaos.set_rule("ollama_a", FaultType.SERVER_ERROR)

        provider_svc = LiteLLMService(config=cfg)

        storage = InMemoryCircuitStorage()
        circuit_mgr = CircuitBreakerManager(config=cfg, storage=storage)
        router = Router(registry=registry, circuit_manager=circuit_mgr)
        retry_mgr = RetryManager(config=cfg, provider_service=provider_svc, sleep_func=AsyncMock())
        failover_mgr = FailoverManager(
            config=cfg,
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=circuit_mgr,
        )

        req = ChatCompletionRequest(
            model="qwen2.5:3b",
            messages=[ChatMessage(role="user", content="Hello")],
        )

        with (
            patch("app.reliability.chaos.chaos_manager", test_chaos),
            patch("app.providers.litellm_client.chaos_manager", test_chaos),
        ):
            # 3 failures -> threshold reached -> circuit OPEN
            for i in range(3):
                with pytest.raises(GatewayError):
                    await failover_mgr.execute_with_failover(req, request_id=f"req-circuit-{i}")

            state = await storage.get_state("ollama_a", cooldown_seconds=30.0)
            assert state == CircuitState.OPEN


class TestChaosAdminAPI:
    """Security and functional tests for /admin/chaos endpoints."""

    @pytest.fixture
    def app_client(self) -> tuple[FastAPI, TestClient]:
        from app.main import create_app

        app = create_app()
        client = TestClient(app)
        return app, client

    def test_chaos_disabled_by_default_returns_403(
        self, app_client: tuple[FastAPI, TestClient]
    ) -> None:
        _, client = app_client
        with patch("app.api.routes_admin.settings.chaos_enabled", False):
            resp = client.post(
                "/admin/chaos",
                json={"provider_id": "ollama_default", "fault": "TIMEOUT"},
                headers={"Authorization": "Bearer test-admin-key"},
            )
            assert resp.status_code == 403

    def test_production_environment_lockout_returns_403(
        self, app_client: tuple[FastAPI, TestClient]
    ) -> None:
        _, client = app_client
        with (
            patch("app.api.routes_admin.settings.environment", "production"),
            patch("app.api.routes_admin.settings.chaos_enabled", True),
            patch("app.api.routes_admin.settings.admin_api_key", "secret-admin-key"),
        ):
            resp = client.post(
                "/admin/chaos",
                json={"provider_id": "ollama_default", "fault": "TIMEOUT"},
                headers={"Authorization": "Bearer secret-admin-key"},
            )
            assert resp.status_code == 403

    def test_missing_and_invalid_admin_credentials_returns_401(
        self, app_client: tuple[FastAPI, TestClient]
    ) -> None:
        _, client = app_client
        with (
            patch("app.api.routes_admin.settings.environment", "development"),
            patch("app.api.routes_admin.settings.chaos_enabled", True),
            patch("app.api.routes_admin.settings.admin_api_key", "valid-admin-secret"),
        ):
            # Missing header
            resp_missing = client.post(
                "/admin/chaos",
                json={"provider_id": "ollama_default", "fault": "TIMEOUT"},
            )
            assert resp_missing.status_code == 401

            # Invalid key
            resp_invalid = client.post(
                "/admin/chaos",
                json={"provider_id": "ollama_default", "fault": "TIMEOUT"},
                headers={"Authorization": "Bearer wrong-secret"},
            )
            assert resp_invalid.status_code == 401

    def test_successful_admin_chaos_crud_lifecycle(
        self, app_client: tuple[FastAPI, TestClient]
    ) -> None:
        _, client = app_client
        with (
            patch("app.api.routes_admin.settings.environment", "development"),
            patch("app.api.routes_admin.settings.chaos_enabled", True),
            patch("app.api.routes_admin.settings.admin_api_key", "my-admin-key"),
            patch("app.reliability.chaos.settings.environment", "development"),
            patch("app.reliability.chaos.settings.chaos_enabled", True),
        ):
            headers = {"Authorization": "Bearer my-admin-key"}

            # 1. Create rule
            create_resp = client.post(
                "/admin/chaos",
                json={
                    "provider_id": "ollama_default",
                    "fault": "SERVER_ERROR",
                    "duration_seconds": 120,
                    "failure_count": 3,
                },
                headers=headers,
            )
            assert create_resp.status_code == 200
            data = create_resp.json()
            assert data["provider_id"] == "ollama_default"
            assert data["fault"] == "SERVER_ERROR"
            assert data["duration_seconds"] == 120
            assert data["failure_count"] == 3

            # 2. List rules
            list_resp = client.get("/admin/chaos", headers=headers)
            assert list_resp.status_code == 200
            rules_list = list_resp.json()
            assert len(rules_list) >= 1
            assert any(r["provider_id"] == "ollama_default" for r in rules_list)

            # 3. Clear specific rule
            del_resp = client.delete("/admin/chaos/ollama_default", headers=headers)
            assert del_resp.status_code == 200
            assert del_resp.json()["cleared"] is True

            # 4. Clear all
            del_all_resp = client.delete("/admin/chaos", headers=headers)
            assert del_all_resp.status_code == 200
            assert del_all_resp.json()["status"] == "success"
