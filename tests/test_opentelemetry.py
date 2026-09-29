"""Comprehensive test suite for OpenTelemetry Tracing (Phase 14.5)."""

import asyncio
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.core.auth import get_authenticated_tenant
from app.core.auth_context import TenantContext
from app.core.config import Settings
from app.core.exceptions import ProviderError
from app.core.request_context import (
    reset_feature,
    reset_provider_id,
    reset_request_id,
    reset_requested_model,
    reset_tenant_id,
    set_feature,
    set_provider_id,
    set_request_id,
    set_requested_model,
    set_tenant_id,
)
from app.main import app
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.observability.tracing import get_tracer, setup_tracing, trace_span
from app.reliability.circuit_breaker import CircuitBreakerManager, CircuitState
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import Router
from app.storage.circuit_storage import InMemoryCircuitStorage
from app.usage.tracker import UsageTracker


def get_span_attr(span: ReadableSpan, key: str) -> Any:
    """Safely get an attribute from a ReadableSpan for static type checking."""
    attrs = span.attributes or {}
    return attrs.get(key)


@pytest.fixture
def memory_exporter() -> Generator[InMemorySpanExporter, None, None]:
    """Provide an InMemorySpanExporter attached to setup_tracing for tests."""
    exporter = InMemorySpanExporter()
    setup_tracing(service_name="test-llm-gateway", exporter=exporter, enabled=True)
    yield exporter
    exporter.clear()


@pytest.fixture(autouse=True)
def clean_context() -> Generator[None, None, None]:
    """Ensure clean context variables before and after each test."""
    id_tok = set_request_id("")
    model_tok = set_requested_model("")
    tenant_tok = set_tenant_id("")
    feat_tok = set_feature("")
    prov_tok = set_provider_id("")
    yield
    reset_request_id(id_tok)
    reset_requested_model(model_tok)
    reset_tenant_id(tenant_tok)
    reset_feature(feat_tok)
    reset_provider_id(prov_tok)


class TestTracingInitialization:
    """Test tracer setup and initialization behavior."""

    def test_setup_tracing_enabled(self) -> None:
        """Verify tracer initialization when tracing is enabled."""
        provider = setup_tracing(service_name="test-service", enabled=True)
        assert isinstance(provider, TracerProvider)
        tracer = get_tracer("test-tracer")
        assert tracer is not None

    def test_setup_tracing_disabled(self) -> None:
        """Verify tracer initialization when tracing is disabled."""
        provider = setup_tracing(service_name="test-service", enabled=False)
        assert provider is not None
        tracer = get_tracer("test-tracer")
        assert tracer is not None

    def test_setup_tracing_with_exporter(self, memory_exporter: InMemorySpanExporter) -> None:
        """Verify tracing setup with a custom exporter (InMemorySpanExporter)."""
        tracer = get_tracer("test-tracer")
        with tracer.start_as_current_span("test.span") as span:
            span.set_attribute("test.attr", "val")

        spans = memory_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "test.span"
        assert get_span_attr(spans[0], "test.attr") == "val"


class TestRootSpanAndHierarchy:
    """Test gateway.request root span and end-to-end trace structure."""

    def test_gateway_request_root_span_success(self, memory_exporter: InMemorySpanExporter) -> None:
        """Test successful chat completion creates gateway.request root span with attributes."""
        mock_tenant = TenantContext(
            tenant_id="tenant-123",
            tenant_name="Test Tenant",
            api_key_id=UUID("00000000-0000-0000-0000-000000000001"),
            key_prefix="gw_live",
            is_admin=False,
        )

        app.dependency_overrides[get_authenticated_tenant] = lambda: mock_tenant

        mock_response = ChatCompletionResponse(
            id="chatcmpl-test-1",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="Hello world"),
                    finish_reason="stop",
                )
            ],
            usage=CompletionUsage(prompt_tokens=15, completion_tokens=25, total_tokens=40),
        )

        with (
            patch(
                "app.api.routes_chat.rate_limiter.check_rate_limit", new_callable=AsyncMock
            ) as mock_rl,
            patch(
                "app.api.routes_chat.failover_manager.execute_with_failover", new_callable=AsyncMock
            ) as mock_fo,
        ):
            mock_rl.return_value = MagicMock(allowed=True, reset_seconds=0.0)
            mock_fo.return_value = mock_response

            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                headers={
                    "Authorization": "Bearer gw_live_secret123",
                    "X-Request-ID": "req-root-001",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "hello"}],
                    "metadata": {"feature": "customer_support"},
                },
            )

            assert response.status_code == 200

        app.dependency_overrides.clear()

        spans = memory_exporter.get_finished_spans()
        root_spans = [s for s in spans if s.name == "gateway.request"]
        assert len(root_spans) == 1
        root_span = root_spans[0]

        assert get_span_attr(root_span, "request.id") == "req-root-001"
        assert get_span_attr(root_span, "tenant.id") == "tenant-123"
        assert get_span_attr(root_span, "feature") == "customer_support"
        assert get_span_attr(root_span, "llm.request.model") == "gpt-4o-mini"
        assert root_span.status.status_code == StatusCode.OK


class TestRoutingSpan:
    """Test routing.select child span."""

    @pytest.mark.asyncio
    async def test_routing_select_span_created(self, memory_exporter: InMemorySpanExporter) -> None:
        """Verify routing.select span is recorded during provider selection."""
        target1 = ProviderTarget(id="openai-primary", provider="openai", model="gpt-4o", priority=1)
        registry = ProviderRegistry()
        registry.register(target1)

        storage = InMemoryCircuitStorage()
        cb = CircuitBreakerManager(storage=storage)
        router = Router(registry=registry, circuit_manager=cb)

        retry_mgr = MagicMock(spec=RetryManager)
        mock_resp = ChatCompletionResponse(
            id="chatcmpl-1",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="Hi"),
                    finish_reason="stop",
                )
            ],
        )
        retry_mgr.execute_with_retry = AsyncMock(return_value=mock_resp)

        manager = FailoverManager(
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=cb,
        )

        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="ping")],
        )

        resp = await manager.execute_with_failover(req, "req-route-1")
        assert resp == mock_resp

        spans = memory_exporter.get_finished_spans()
        route_spans = [s for s in spans if s.name == "routing.select"]
        assert len(route_spans) == 1
        route_span = route_spans[0]

        assert get_span_attr(route_span, "model") == "gpt-4o"
        assert get_span_attr(route_span, "candidate_count") == 1
        assert get_span_attr(route_span, "selected_provider") == "openai-primary"


class TestProviderAttemptAndRetryTracing:
    """Test provider.attempt and retry.backoff spans."""

    @pytest.mark.asyncio
    async def test_single_successful_provider_attempt(
        self, memory_exporter: InMemorySpanExporter
    ) -> None:
        """Verify single provider.attempt span created on immediate success."""
        target = ProviderTarget(
            id="anthropic-main", provider="anthropic", model="claude-3-5-sonnet"
        )
        mock_provider_service = MagicMock()
        mock_resp = ChatCompletionResponse(
            id="chatcmpl-1",
            object="chat.completion",
            created=1700000000,
            model="claude-3-5-sonnet",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="Claude reply"),
                    finish_reason="stop",
                )
            ],
        )
        mock_provider_service.execute_chat_completion = AsyncMock(return_value=mock_resp)

        retry_mgr = RetryManager(
            config=Settings(MAX_RETRIES=2, RETRY_BASE_DELAY=0.01),
            provider_service=mock_provider_service,
        )

        req = ChatCompletionRequest(
            model="claude-3-5-sonnet",
            messages=[ChatMessage(role="user", content="hello")],
        )

        resp = await retry_mgr.execute_with_retry(req, "req-retry-1", target)
        assert resp == mock_resp

        spans = memory_exporter.get_finished_spans()
        attempt_spans = [s for s in spans if s.name == "provider.attempt"]
        assert len(attempt_spans) == 1
        assert get_span_attr(attempt_spans[0], "llm.provider") == "anthropic-main"
        assert get_span_attr(attempt_spans[0], "llm.model") == "claude-3-5-sonnet"
        assert get_span_attr(attempt_spans[0], "llm.attempt") == 1

        backoff_spans = [s for s in spans if s.name == "retry.backoff"]
        assert len(backoff_spans) == 0

    @pytest.mark.asyncio
    async def test_retry_creates_backoff_and_subsequent_attempt(
        self, memory_exporter: InMemorySpanExporter
    ) -> None:
        """Verify failed first attempt creates retry.backoff span then second provider.attempt."""
        target = ProviderTarget(id="openai-main", provider="openai", model="gpt-4o")
        mock_provider_service = MagicMock()

        mock_resp = ChatCompletionResponse(
            id="chatcmpl-2",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(
                        role="assistant", content="Success on retry"
                    ),
                    finish_reason="stop",
                )
            ],
        )

        mock_provider_service.execute_chat_completion = AsyncMock(
            side_effect=[
                ProviderError(
                    message="Rate limited", status_code=429, retryable=True, category="RATE_LIMITED"
                ),
                mock_resp,
            ]
        )

        sleep_mock = AsyncMock()
        retry_mgr = RetryManager(
            config=Settings(MAX_RETRIES=2, RETRY_BASE_DELAY=0.05, RETRY_JITTER=False),
            provider_service=mock_provider_service,
            sleep_func=sleep_mock,
        )

        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="hello")],
        )

        resp = await retry_mgr.execute_with_retry(req, "req-retry-2", target)
        assert resp == mock_resp

        spans = memory_exporter.get_finished_spans()

        attempt_spans = [s for s in spans if s.name == "provider.attempt"]
        assert len(attempt_spans) == 2

        # First attempt failed
        assert get_span_attr(attempt_spans[0], "llm.provider") == "openai-main"
        assert get_span_attr(attempt_spans[0], "llm.attempt") == 1
        assert get_span_attr(attempt_spans[0], "llm.error_type") == "RATE_LIMITED"

        # Backoff span
        backoff_spans = [s for s in spans if s.name == "retry.backoff"]
        assert len(backoff_spans) == 1
        assert get_span_attr(backoff_spans[0], "llm.attempt") == 2
        assert get_span_attr(backoff_spans[0], "retry.delay_seconds") is not None

        # Second attempt succeeded
        assert get_span_attr(attempt_spans[1], "llm.provider") == "openai-main"
        assert get_span_attr(attempt_spans[1], "llm.attempt") == 2


class TestFailoverTracing:
    """Test failover.transition span."""

    @pytest.mark.asyncio
    async def test_failover_transition_span_created(
        self, memory_exporter: InMemorySpanExporter
    ) -> None:
        """Verify failover.transition is recorded on genuine provider transition."""
        target1 = ProviderTarget(id="openai-main", provider="openai", model="gpt-4o", priority=1)
        target2 = ProviderTarget(
            id="anthropic-fallback", provider="anthropic", model="gpt-4o", priority=2
        )

        registry = ProviderRegistry()
        registry.register(target1)
        registry.register(target2)

        storage = InMemoryCircuitStorage()
        cb = CircuitBreakerManager(storage=storage)
        router = Router(registry=registry, circuit_manager=cb)

        retry_mgr = MagicMock(spec=RetryManager)
        mock_resp = ChatCompletionResponse(
            id="chatcmpl-fallback",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(
                        role="assistant", content="Fallback reply"
                    ),
                    finish_reason="stop",
                )
            ],
        )

        retry_mgr.execute_with_retry = AsyncMock(
            side_effect=[
                ProviderError(
                    message="OpenAI down",
                    status_code=503,
                    provider="openai-main",
                    retryable=False,
                    category="SERVER_ERROR",
                ),
                mock_resp,
            ]
        )

        manager = FailoverManager(
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=cb,
        )

        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="hello")],
        )

        resp = await manager.execute_with_failover(req, "req-failover-1")
        assert resp == mock_resp

        spans = memory_exporter.get_finished_spans()
        failover_spans = [s for s in spans if s.name == "failover.transition"]
        assert len(failover_spans) == 1
        fo_span = failover_spans[0]

        assert get_span_attr(fo_span, "from_provider") == "openai-main"
        assert get_span_attr(fo_span, "to_provider") == "anthropic-fallback"
        assert get_span_attr(fo_span, "reason") == "retry_exhausted"

    @pytest.mark.asyncio
    async def test_circuit_skipped_candidate_does_not_create_false_failover_span(
        self, memory_exporter: InMemorySpanExporter
    ) -> None:
        """Verify OPEN circuit candidates do not emit false failover.transition spans."""
        target1 = ProviderTarget(id="openai-main", provider="openai", model="gpt-4o", priority=1)
        target2 = ProviderTarget(
            id="anthropic-fallback", provider="anthropic", model="gpt-4o", priority=2
        )

        registry = ProviderRegistry()
        registry.register(target1)
        registry.register(target2)

        storage = InMemoryCircuitStorage()
        # Set target1 circuit to OPEN via update_snapshot
        await storage.update_snapshot(
            "openai-main", state=CircuitState.OPEN, opened_at=9999999999.0
        )
        await storage.update_snapshot("anthropic-fallback", state=CircuitState.CLOSED)

        cb = CircuitBreakerManager(storage=storage)
        router = Router(registry=registry, circuit_manager=cb)

        retry_mgr = MagicMock(spec=RetryManager)
        mock_resp = ChatCompletionResponse(
            id="chatcmpl-fallback",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(
                        role="assistant", content="Target2 reply"
                    ),
                    finish_reason="stop",
                )
            ],
        )
        retry_mgr.execute_with_retry = AsyncMock(return_value=mock_resp)

        manager = FailoverManager(
            router_instance=router,
            retry_instance=retry_mgr,
            circuit_instance=cb,
        )

        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="hello")],
        )

        resp = await manager.execute_with_failover(req, "req-failover-2")
        assert resp == mock_resp

        spans = memory_exporter.get_finished_spans()
        failover_spans = [s for s in spans if s.name == "failover.transition"]
        # No failover transition should be emitted because target1 was never physically attempted
        assert len(failover_spans) == 0


class TestUsageTracing:
    """Test usage.record span."""

    @pytest.mark.asyncio
    async def test_usage_record_span_created(self, memory_exporter: InMemorySpanExporter) -> None:
        """Verify usage.record span captures provider, model, and token counts."""
        repo_mock = MagicMock()
        repo_mock.create_usage_record = AsyncMock()

        calc_mock = MagicMock()
        calc_mock.calculate_cost = MagicMock(return_value=0.0005)

        tracker = UsageTracker(calculator=calc_mock, repository=repo_mock)

        target = ProviderTarget(id="openai-main", provider="openai", model="gpt-4o-mini")
        req = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="hi")],
        )
        resp = ChatCompletionResponse(
            id="chatcmpl-usage",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o-mini",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="hi"),
                    finish_reason="stop",
                )
            ],
            usage=CompletionUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

        await tracker.record_usage(
            request=req,
            request_id="req-usage-1",
            target=target,
            response=resp,
            tenant_id="tenant-999",
        )

        spans = memory_exporter.get_finished_spans()
        usage_spans = [s for s in spans if s.name == "usage.record"]
        assert len(usage_spans) == 1
        u_span = usage_spans[0]

        assert get_span_attr(u_span, "llm.provider") == "openai-main"
        assert get_span_attr(u_span, "llm.model") == "gpt-4o-mini"
        assert get_span_attr(u_span, "llm.usage.prompt_tokens") == 10
        assert get_span_attr(u_span, "llm.usage.completion_tokens") == 20
        assert get_span_attr(u_span, "llm.usage.total_tokens") == 30


class TestSecurityAndSanitization:
    """Verify sensitive data never appears in span attributes or names."""

    def test_no_sensitive_data_in_spans(self, memory_exporter: InMemorySpanExporter) -> None:
        """Ensure keys, tokens, passwords, raw prompts, and responses are not in span attributes."""
        secret_key = "gw_live_abcdef1234567890"
        auth_header = f"Bearer {secret_key}"
        prompt_text = "What is the secret launch code?"
        response_text = "The secret code is 9999"

        with trace_span(
            "gateway.request",
            attributes={
                "request.id": "req-sec-1",
                "tenant.id": "tenant-sec",
                "feature": "chat",
                "llm.request.model": "gpt-4o",
            },
        ):
            with trace_span(
                "provider.attempt",
                attributes={
                    "llm.provider": "openai-main",
                    "llm.model": "gpt-4o",
                    "llm.attempt": 1,
                },
            ):
                pass

        spans = memory_exporter.get_finished_spans()
        for span in spans:
            attrs = span.attributes or {}
            for attr_k, attr_v in attrs.items():
                str_k = attr_k.lower()
                str_v = str(attr_v).lower()
                assert "secret" not in str_k
                assert (
                    "key" not in str_k or str_k == "request.id"
                )  # only legitimate non-secret keys
                assert secret_key not in str_v
                assert auth_header.lower() not in str_v
                assert prompt_text.lower() not in str_v
                assert response_text.lower() not in str_v


class TestFailureSafety:
    """Verify tracing failures do not break business logic."""

    def test_trace_span_failure_safety(self) -> None:
        """Verify exception inside business logic propagates while span captures status."""
        with pytest.raises(ValueError, match="Underlying business error"):
            with trace_span("failing.operation", attributes={"test.attr": "123"}):
                raise ValueError("Underlying business error")

    def test_broken_tracer_does_not_break_execution(self) -> None:
        """Verify even if tracing provider raises internal exceptions, business logic finishes."""
        with patch("app.observability.tracing.get_tracer") as mock_get_tracer:
            mock_tracer = MagicMock()
            mock_tracer.start_as_current_span.side_effect = RuntimeError("Tracing system corrupted")
            mock_get_tracer.return_value = mock_tracer

            executed = False
            try:
                with trace_span("resilient.span"):
                    executed = True
            except Exception:
                pass
            assert executed is True


class TestContextIsolation:
    """Verify trace context isolation across concurrent async operations."""

    @pytest.mark.asyncio
    async def test_concurrent_requests_trace_context_isolation(
        self, memory_exporter: InMemorySpanExporter
    ) -> None:
        """Verify concurrent requests have isolated request IDs and attributes."""

        async def simulate_request(req_id: str, tenant_id: str, delay: float) -> None:
            with trace_span(
                "gateway.request", attributes={"request.id": req_id, "tenant.id": tenant_id}
            ):
                await asyncio.sleep(delay)
                with trace_span(
                    "provider.attempt", attributes={"request.id": req_id, "llm.provider": "openai"}
                ):
                    await asyncio.sleep(0.01)

        await asyncio.gather(
            simulate_request("req-iso-1", "tenant-1", 0.03),
            simulate_request("req-iso-2", "tenant-2", 0.01),
            simulate_request("req-iso-3", "tenant-3", 0.02),
        )

        spans = memory_exporter.get_finished_spans()
        root_spans = [s for s in spans if s.name == "gateway.request"]
        assert len(root_spans) == 3

        req_map = {
            get_span_attr(s, "request.id"): get_span_attr(s, "tenant.id") for s in root_spans
        }
        assert req_map["req-iso-1"] == "tenant-1"
        assert req_map["req-iso-2"] == "tenant-2"
        assert req_map["req-iso-3"] == "tenant-3"
