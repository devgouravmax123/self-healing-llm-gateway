"""Tests for structured JSON logging (Phase 14.4)."""

import json
import logging
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from _pytest.logging import LogCaptureFixture
from fastapi.testclient import TestClient

from app.core.auth import get_authenticated_tenant
from app.core.auth_context import TenantContext
from app.core.exceptions import ProviderError, RateLimitError
from app.core.logging import StructuredJSONFormatter, sanitize_value, setup_logging
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
from app.observability.metrics import gateway_metrics
from app.reliability.circuit_breaker import CircuitBreakerManager, CircuitState
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.storage.circuit_storage import InMemoryCircuitStorage


class TestStructuredLogging:
    """Test suite for structured JSON logging implementation."""

    @pytest.fixture(autouse=True)
    def clean_context(self) -> Generator[None, None, None]:
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

    # -------------------------------------------------------------------------
    # A. Valid JSON & Canonical Fields
    # -------------------------------------------------------------------------
    def test_formatter_produces_valid_json(self) -> None:
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test log message",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert isinstance(data, dict)
        assert data["level"] == "INFO"
        assert "timestamp" in data
        assert data["message"] == "Test log message"

    def test_formatter_canonical_fields(self) -> None:
        formatter = StructuredJSONFormatter()
        set_request_id("req_12345")
        set_tenant_id("tenant_abc")
        set_feature("summarize")
        set_provider_id("ollama_local")
        set_requested_model("qwen2.5:3b")

        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Provider call completed",
            args=(),
            exc_info=None,
        )
        record.event = "request_completed"
        record.latency = 0.123
        record.attempt = 1

        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert data["timestamp"]
        assert data["level"] == "INFO"
        assert data["request_id"] == "req_12345"
        assert data["tenant_id"] == "tenant_abc"
        assert data["feature"] == "summarize"
        assert data["provider"] == "ollama_local"
        assert data["model"] == "qwen2.5:3b"
        assert data["event"] == "request_completed"
        assert data["latency"] == 0.123
        assert data["attempt"] == 1

    # -------------------------------------------------------------------------
    # B. Security & Sanitization
    # -------------------------------------------------------------------------
    def test_sensitive_data_sanitization(self) -> None:
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="User credentials received",
            args=(),
            exc_info=None,
        )
        record.api_key = "gw_live_secretkey123456789"
        record.authorization = "Bearer secret_jwt_token"
        record.raw_prompt = "Tell me the secret prompt"
        record.nested = {"password": "mypassword", "safe_field": "hello"}

        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert data["api_key"] == "[REDACTED]"
        assert data["authorization"] == "[REDACTED]"
        assert data["raw_prompt"] == "[REDACTED]"
        assert data["nested"]["password"] == "[REDACTED]"
        assert data["nested"]["safe_field"] == "hello"

    def test_sanitize_value_direct(self) -> None:
        assert sanitize_value("Bearer 12345") == "[REDACTED]"
        assert sanitize_value("gw_live_99999") == "[REDACTED]"
        assert sanitize_value({"Authorization": "Bearer xyz"}) == {"Authorization": "[REDACTED]"}
        assert sanitize_value({"secret": "topsecret"}) == {"secret": "[REDACTED]"}
        assert sanitize_value({"normal": 123}) == {"normal": 123}

    # -------------------------------------------------------------------------
    # C. Lifecycle Events in Reliability & Orchestration
    # -------------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_retry_started_and_provider_timeout_events(
        self, caplog: LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="app.reliability.retry")
        mock_service = MagicMock()
        mock_service.execute_chat_completion = AsyncMock(
            side_effect=[
                ProviderError(
                    message="Timeout",
                    status_code=504,
                    provider="mock_prov",
                    category="TIMEOUT",
                    retryable=True,
                ),
                ChatCompletionResponse(
                    id="chatcmpl-success",
                    model="test-model",
                    choices=[
                        ChatCompletionChoice(
                            index=0,
                            message=ChatCompletionMessageResponse(
                                role="assistant", content="Retry worked"
                            ),
                            finish_reason="stop",
                        )
                    ],
                ),
            ]
        )

        mock_config = MagicMock()
        mock_config.max_retries = 2
        mock_config.retry_base_delay = 0.01
        mock_config.retry_max_delay = 0.05
        mock_config.retry_jitter = False

        sleep_mock = AsyncMock()
        retry_mgr = RetryManager(
            config=mock_config,
            provider_service=mock_service,
            sleep_func=sleep_mock,
        )

        target = ProviderTarget(
            id="prov_test",
            provider="ollama",
            model="test-model",
            priority=1,
            enabled=True,
        )
        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="hi")],
        )

        await retry_mgr.execute_with_retry(request=req, request_id="req_retry_test", target=target)

        # Inspect captured log records
        records = [r for r in caplog.records if hasattr(r, "event")]
        event_names = [r.event for r in records]

        assert "provider_timeout" in event_names
        assert "retry_started" in event_names

        retry_record = next(r for r in records if getattr(r, "event", None) == "retry_started")
        assert getattr(retry_record, "provider", None) == "prov_test"
        assert getattr(retry_record, "model", None) == "test-model"
        assert getattr(retry_record, "attempt", None) == 2
        assert getattr(retry_record, "error_type", None) == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_failover_started_event(self, caplog: LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="app.reliability.failover")

        target1 = ProviderTarget(
            id="prov_1",
            provider="ollama",
            model="test-model",
            priority=1,
            enabled=True,
        )
        target2 = ProviderTarget(
            id="prov_2",
            provider="ollama",
            model="test-model",
            priority=2,
            enabled=True,
        )

        mock_router = MagicMock()
        mock_router.select_provider.side_effect = [target1, target2]

        mock_retry = MagicMock()
        mock_retry.execute_with_retry = AsyncMock(
            side_effect=[
                ProviderError(
                    message="Server error",
                    status_code=502,
                    provider="prov_1",
                    category="SERVER_ERROR",
                    retryable=False,
                ),
                ChatCompletionResponse(
                    id="chatcmpl-prov2",
                    model="test-model",
                    choices=[
                        ChatCompletionChoice(
                            index=0,
                            message=ChatCompletionMessageResponse(
                                role="assistant", content="Failover success"
                            ),
                            finish_reason="stop",
                        )
                    ],
                ),
            ]
        )

        storage = InMemoryCircuitStorage()
        circuit_mgr = CircuitBreakerManager(storage=storage)

        failover_mgr = FailoverManager(
            router_instance=mock_router,
            retry_instance=mock_retry,
            circuit_instance=circuit_mgr,
        )

        req = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="hi")],
        )

        await failover_mgr.execute_with_failover(
            request=req,
            request_id="req_failover_test",
            tenant_id="tenant_123",
        )

        records = [r for r in caplog.records if hasattr(r, "event")]
        event_names = [r.event for r in records]

        assert "provider_selected" in event_names
        assert "failover_started" in event_names

        failover_record = next(
            r for r in records if getattr(r, "event", None) == "failover_started"
        )
        assert getattr(failover_record, "provider", None) == "prov_2"
        assert getattr(failover_record, "model", None) == "test-model"

    @pytest.mark.asyncio
    async def test_circuit_opened_event(self, caplog: LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="app.reliability.circuit_breaker")

        storage = InMemoryCircuitStorage()
        mock_config = MagicMock()
        mock_config.circuit_failure_threshold = 2
        mock_config.circuit_cooldown_seconds = 30.0

        circuit_mgr = CircuitBreakerManager(storage=storage, config=mock_config)

        err = ProviderError(
            message="Server error",
            status_code=502,
            category="SERVER_ERROR",
        )

        await circuit_mgr.record_failure("prov_cb", err)
        assert await circuit_mgr.get_state_async("prov_cb") == CircuitState.CLOSED

        await circuit_mgr.record_failure("prov_cb", err)
        assert await circuit_mgr.get_state_async("prov_cb") == CircuitState.OPEN

        records = [r for r in caplog.records if getattr(r, "event", None) == "circuit_opened"]
        assert len(records) == 1
        assert getattr(records[0], "provider", None) == "prov_cb"

    # -------------------------------------------------------------------------
    # D. End-to-End HTTP Lifecycle Events
    # -------------------------------------------------------------------------
    def test_http_request_completed_and_received_events(self, caplog: LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="app")
        logging.getLogger("app.api.routes_chat").setLevel(logging.INFO)
        logging.getLogger("app.main").setLevel(logging.INFO)
        client = TestClient(app)

        tenant = TenantContext(
            tenant_id="tenant_http_test",
            tenant_name="Test Tenant",
            api_key_id=UUID("00000000-0000-0000-0000-000000000001"),
            key_prefix="gw_live_test",
            is_admin=False,
        )
        app.dependency_overrides[get_authenticated_tenant] = lambda: tenant

        mock_resp = ChatCompletionResponse(
            id="chatcmpl-e2e",
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(
                        role="assistant", content="Hello from e2e"
                    ),
                    finish_reason="stop",
                )
            ],
            usage=CompletionUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )

        with patch(
            "app.api.routes_chat.failover_manager.execute_with_failover",
            AsyncMock(return_value=mock_resp),
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen2.5:3b",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "metadata": {"feature": "chat_assist"},
                },
                headers={"X-Request-ID": "req_e2e_001"},
            )
            assert resp.status_code == 200

        app.dependency_overrides.clear()

        records = [r for r in caplog.records if hasattr(r, "event")]
        event_names = [r.event for r in records]

        assert "request_received" in event_names
        assert "request_completed" in event_names

        rec_record = next(r for r in records if getattr(r, "event", None) == "request_received")
        assert getattr(rec_record, "request_id", None) == "req_e2e_001"
        assert getattr(rec_record, "tenant_id", None) == "tenant_http_test"
        assert getattr(rec_record, "feature", None) == "chat_assist"

        comp_record = next(r for r in records if getattr(r, "event", None) == "request_completed")
        assert getattr(comp_record, "request_id", None) == "req_e2e_001"
        assert getattr(comp_record, "tenant_id", None) == "tenant_http_test"
        assert getattr(comp_record, "feature", None) == "chat_assist"
        latency_val = getattr(comp_record, "latency", None)
        assert latency_val is not None and latency_val > 0

    def test_http_request_failed_event(self, caplog: LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="app")
        logging.getLogger("app.main").setLevel(logging.WARNING)
        client = TestClient(app)

        tenant = TenantContext(
            tenant_id="tenant_fail_test",
            tenant_name="Test Tenant",
            api_key_id=UUID("00000000-0000-0000-0000-000000000002"),
            key_prefix="gw_live_fail",
            is_admin=False,
        )
        app.dependency_overrides[get_authenticated_tenant] = lambda: tenant

        with patch(
            "app.api.routes_chat.rate_limiter.check_rate_limit",
            AsyncMock(side_effect=RateLimitError("Rate limit exceeded", retry_after=30)),
        ):
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen2.5:3b",
                    "messages": [{"role": "user", "content": "Hello"}],
                },
                headers={"X-Request-ID": "req_ratelimit_err"},
            )
            assert resp.status_code == 429

        app.dependency_overrides.clear()

        records = [r for r in caplog.records if getattr(r, "event", None) == "request_failed"]
        assert len(records) >= 1
        fail_record = records[0]
        assert getattr(fail_record, "request_id", None) == "req_ratelimit_err"
        assert getattr(fail_record, "error_type", None) == "RateLimitError"

    # -------------------------------------------------------------------------
    # E. Logging Failure Safety & Prometheus Isolation
    # -------------------------------------------------------------------------
    def test_logging_failure_safety(self) -> None:
        formatter = StructuredJSONFormatter()

        class Unserializable:
            def __str__(self) -> str:
                raise RuntimeError("Cannot stringify")

        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Unserializable object test",
            args=(),
            exc_info=None,
        )
        record.bad_obj = Unserializable()

        # Must not raise an exception
        result = formatter.format(record)
        data = json.loads(result)
        assert isinstance(data, dict)
        assert "timestamp" in data

    def test_prometheus_metrics_isolation(self) -> None:
        # Verify 12 metrics exist and structured log labels are not added to Prometheus
        assert hasattr(gateway_metrics, "requests_total")
        assert hasattr(gateway_metrics, "request_duration_seconds")
        assert hasattr(gateway_metrics, "provider_requests_total")
        assert hasattr(gateway_metrics, "provider_duration_seconds")
        assert hasattr(gateway_metrics, "provider_errors_total")
        assert hasattr(gateway_metrics, "retries_total")
        assert hasattr(gateway_metrics, "failovers_total")
        assert hasattr(gateway_metrics, "circuit_state")
        assert hasattr(gateway_metrics, "auth_failures_total")
        assert hasattr(gateway_metrics, "rate_limit_rejections_total")
        assert hasattr(gateway_metrics, "tokens_total")
        assert hasattr(gateway_metrics, "estimated_cost_usd_total")

        # Verify no high-cardinality labels were added to requests_total
        assert gateway_metrics.requests_total._labelnames == ("model", "status_code")

    # -------------------------------------------------------------------------
    # F. No Duplicate Handlers on setup_logging
    # -------------------------------------------------------------------------
    def test_setup_logging_idempotence(self) -> None:
        root_logger = logging.getLogger()
        setup_logging()
        initial_handlers_count = len(
            [
                h
                for h in root_logger.handlers
                if isinstance(getattr(h, "formatter", None), StructuredJSONFormatter)
            ]
        )
        assert initial_handlers_count == 1

        # Call setup_logging again
        setup_logging()
        after_count = len(
            [
                h
                for h in root_logger.handlers
                if isinstance(getattr(h, "formatter", None), StructuredJSONFormatter)
            ]
        )
        assert after_count == 1
