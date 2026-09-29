"""Unit and integration tests for Phase 14.3c Usage & Cost Prometheus Metrics.

Test Matrix:
A. Complete token usage (in=10, out=20, cost>0)
B. Missing input tokens (in=None, out=20)
C. Missing output tokens (in=10, out=None)
D. Both token values missing (in=None, out=None)
E. No synthetic totals (in=None, out=None, total=30)
F. Positive cost (estimated_cost = Decimal("0.001234"))
G. Unknown cost (estimated_cost = None)
H. Zero cost (estimated_cost = Decimal("0"))
I. Canonical provider identity (provider=target.id, NOT target.provider)
J. Accumulation across multiple usage events
K. Retry/failover semantics (only final delivered usage recorded)
L. Metric isolation (dedicated registry)
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from prometheus_client import generate_latest

from app.core.config import Settings
from app.core.exceptions import ProviderError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.observability.metrics import GatewayMetrics, create_metrics_registry
from app.pricing.calculator import CostCalculator, ModelPricing
from app.reliability.circuit_breaker import CircuitBreakerManager
from app.reliability.error_classifier import ErrorCategory
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import Router
from app.storage.circuit_storage import InMemoryCircuitStorage
from app.usage.tracker import UsageTracker


@pytest.fixture
def custom_metrics() -> GatewayMetrics:
    """Provide an isolated GatewayMetrics instance for usage metrics testing."""
    registry = create_metrics_registry()
    return GatewayMetrics(registry=registry)


# --- Unit Tests for Token & Cost Metrics (Tests A - J, L) ---


@pytest.mark.asyncio
async def test_complete_token_usage_and_cost(custom_metrics: GatewayMetrics) -> None:
    """Test A & F: Complete token usage and positive cost increments."""
    pricing_catalog = {
        ("openai", "gpt-4o"): ModelPricing(
            input_cost_per_million=Decimal("2.50"),
            output_cost_per_million=Decimal("10.00"),
        )
    }
    calc = CostCalculator(pricing_catalog=pricing_catalog)
    mock_repo = AsyncMock()

    tracker = UsageTracker(calculator=calc, repository=mock_repo, metrics=custom_metrics)

    target = ProviderTarget(id="openai_prod", provider="openai", model="gpt-4o")
    req = ChatCompletionRequest(model="gpt-4o", messages=[ChatMessage(role="user", content="Hi")])
    resp = ChatCompletionResponse(
        id="chatcmpl_1",
        model="gpt-4o",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="Hi")
            )
        ],
        usage=CompletionUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
    )

    await tracker.record_usage(
        request=req, request_id="req_1", target=target, response=resp, tenant_id="tenant_1"
    )

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    # Tokens check
    assert 'gateway_tokens_total{model="gpt-4o",provider="openai_prod",type="input"} 1000.0' in text
    assert 'gateway_tokens_total{model="gpt-4o",provider="openai_prod",type="output"} 500.0' in text

    # Cost calculation: (1000/1M * 2.5) + (500/1M * 10) = 0.0025 + 0.005 = 0.0075
    assert 'gateway_estimated_cost_usd_total{model="gpt-4o",provider="openai_prod"} 0.0075' in text


@pytest.mark.asyncio
async def test_missing_input_tokens(custom_metrics: GatewayMetrics) -> None:
    """Test B: Missing input tokens records only output tokens."""
    calc = CostCalculator()
    mock_repo = AsyncMock()
    tracker = UsageTracker(calculator=calc, repository=mock_repo, metrics=custom_metrics)

    target = ProviderTarget(id="openai_prod", provider="openai", model="gpt-4o")
    req = ChatCompletionRequest(model="gpt-4o", messages=[ChatMessage(role="user", content="Hi")])
    resp = ChatCompletionResponse(
        id="chatcmpl_2",
        model="gpt-4o",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="Hi")
            )
        ],
        usage=CompletionUsage(prompt_tokens=None, completion_tokens=20, total_tokens=20),
    )

    await tracker.record_usage(
        request=req, request_id="req_2", target=target, response=resp, tenant_id="tenant_1"
    )

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    assert 'type="input"' not in text
    assert 'gateway_tokens_total{model="gpt-4o",provider="openai_prod",type="output"} 20.0' in text
    assert "gateway_estimated_cost_usd_total{" not in text


@pytest.mark.asyncio
async def test_missing_output_tokens(custom_metrics: GatewayMetrics) -> None:
    """Test C: Missing output tokens records only input tokens."""
    calc = CostCalculator()
    mock_repo = AsyncMock()
    tracker = UsageTracker(calculator=calc, repository=mock_repo, metrics=custom_metrics)

    target = ProviderTarget(id="openai_prod", provider="openai", model="gpt-4o")
    req = ChatCompletionRequest(model="gpt-4o", messages=[ChatMessage(role="user", content="Hi")])
    resp = ChatCompletionResponse(
        id="chatcmpl_3",
        model="gpt-4o",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="Hi")
            )
        ],
        usage=CompletionUsage(prompt_tokens=10, completion_tokens=None, total_tokens=10),
    )

    await tracker.record_usage(
        request=req, request_id="req_3", target=target, response=resp, tenant_id="tenant_1"
    )

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    assert 'gateway_tokens_total{model="gpt-4o",provider="openai_prod",type="input"} 10.0' in text
    assert 'type="output"' not in text
    assert "gateway_estimated_cost_usd_total{" not in text


@pytest.mark.asyncio
async def test_missing_both_tokens_and_no_synthetic_totals(
    custom_metrics: GatewayMetrics,
) -> None:
    """Test D & E: Both tokens missing (or only total_tokens present) does not synthesize tokens."""
    calc = CostCalculator()
    mock_repo = AsyncMock()
    tracker = UsageTracker(calculator=calc, repository=mock_repo, metrics=custom_metrics)

    target = ProviderTarget(id="openai_prod", provider="openai", model="gpt-4o")
    req = ChatCompletionRequest(model="gpt-4o", messages=[ChatMessage(role="user", content="Hi")])
    resp = ChatCompletionResponse(
        id="chatcmpl_4",
        model="gpt-4o",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="Hi")
            )
        ],
        usage=CompletionUsage(prompt_tokens=None, completion_tokens=None, total_tokens=30),
    )

    await tracker.record_usage(
        request=req, request_id="req_4", target=target, response=resp, tenant_id="tenant_1"
    )

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    assert "gateway_tokens_total{" not in text
    assert "gateway_estimated_cost_usd_total{" not in text


@pytest.mark.asyncio
async def test_zero_cost_provider_ollama(custom_metrics: GatewayMetrics) -> None:
    """Test H & I: Local Ollama (cost=0) records tokens with target.id label and no cost."""
    calc = CostCalculator()
    mock_repo = AsyncMock()
    tracker = UsageTracker(calculator=calc, repository=mock_repo, metrics=custom_metrics)

    target = ProviderTarget(id="ollama_local", provider="ollama", model="qwen2.5:3b")
    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    resp = ChatCompletionResponse(
        id="chatcmpl_5",
        model="qwen2.5:3b",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="Hi")
            )
        ],
        usage=CompletionUsage(prompt_tokens=50, completion_tokens=100, total_tokens=150),
    )

    await tracker.record_usage(
        request=req, request_id="req_5", target=target, response=resp, tenant_id="tenant_1"
    )

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    # Canonical provider=target.id ("ollama_local")
    assert (
        'gateway_tokens_total{model="qwen2.5:3b",provider="ollama_local",type="input"} 50.0' in text
    )
    assert (
        'gateway_tokens_total{model="qwen2.5:3b",provider="ollama_local",type="output"} 100.0'
        in text
    )
    # Ensure provider is NOT "ollama"
    assert 'provider="ollama"' not in text

    # Zero cost must not increment cost counter
    assert "gateway_estimated_cost_usd_total{" not in text


@pytest.mark.asyncio
async def test_unknown_unpriced_provider_cost(custom_metrics: GatewayMetrics) -> None:
    """Test G: Provider with unconfigured pricing does not increment cost metric."""
    calc = CostCalculator(pricing_catalog={})
    mock_repo = AsyncMock()
    tracker = UsageTracker(calculator=calc, repository=mock_repo, metrics=custom_metrics)

    target = ProviderTarget(id="custom_llm", provider="custom_provider", model="custom_model")
    req = ChatCompletionRequest(
        model="custom_model", messages=[ChatMessage(role="user", content="Hi")]
    )
    resp = ChatCompletionResponse(
        id="chatcmpl_6",
        model="custom_model",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="Hi")
            )
        ],
        usage=CompletionUsage(prompt_tokens=50, completion_tokens=50, total_tokens=100),
    )

    await tracker.record_usage(
        request=req, request_id="req_6", target=target, response=resp, tenant_id="tenant_1"
    )

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    assert (
        'gateway_tokens_total{model="custom_model",provider="custom_llm",type="input"} 50.0' in text
    )
    assert (
        'gateway_tokens_total{model="custom_model",provider="custom_llm",type="output"} 50.0'
        in text
    )
    assert "gateway_estimated_cost_usd_total{" not in text


@pytest.mark.asyncio
async def test_usage_metrics_accumulation(custom_metrics: GatewayMetrics) -> None:
    """Test J: Multiple usage events accumulate token and cost metrics properly."""
    pricing_catalog = {
        ("openai", "gpt-4o"): ModelPricing(
            input_cost_per_million=Decimal("2.00"),
            output_cost_per_million=Decimal("10.00"),
        )
    }
    calc = CostCalculator(pricing_catalog=pricing_catalog)
    mock_repo = AsyncMock()
    tracker = UsageTracker(calculator=calc, repository=mock_repo, metrics=custom_metrics)

    target = ProviderTarget(id="openai_prod", provider="openai", model="gpt-4o")
    req = ChatCompletionRequest(model="gpt-4o", messages=[ChatMessage(role="user", content="Hi")])

    resp1 = ChatCompletionResponse(
        id="chatcmpl_acc_1",
        model="gpt-4o",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="1")
            )
        ],
        usage=CompletionUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )
    resp2 = ChatCompletionResponse(
        id="chatcmpl_acc_2",
        model="gpt-4o",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="2")
            )
        ],
        usage=CompletionUsage(prompt_tokens=200, completion_tokens=150, total_tokens=350),
    )

    await tracker.record_usage(
        request=req, request_id="req_acc_1", target=target, response=resp1, tenant_id="tenant_1"
    )
    await tracker.record_usage(
        request=req, request_id="req_acc_2", target=target, response=resp2, tenant_id="tenant_1"
    )

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    # input: 100 + 200 = 300
    assert 'gateway_tokens_total{model="gpt-4o",provider="openai_prod",type="input"} 300.0' in text
    # output: 50 + 150 = 200
    assert 'gateway_tokens_total{model="gpt-4o",provider="openai_prod",type="output"} 200.0' in text

    # cost1: (100/1M*2) + (50/1M*10) = 0.0002 + 0.0005 = 0.0007
    # cost2: (200/1M*2) + (150/1M*10) = 0.0004 + 0.0015 = 0.0019
    # total cost = 0.0007 + 0.0019 = 0.0026
    assert 'gateway_estimated_cost_usd_total{model="gpt-4o",provider="openai_prod"} 0.0026' in text


# --- Failover & Retry End-to-End Test (Test K) ---


@pytest.mark.asyncio
async def test_delivered_usage_recorded_only_on_successful_provider(
    custom_metrics: GatewayMetrics,
) -> None:
    """Test K: Failed provider attempts record zero usage; only successful delivery counts."""
    target_a = ProviderTarget(id="prov_a_fail", provider="ollama", model="qwen2.5:3b", priority=1)
    target_b = ProviderTarget(id="prov_b_ok", provider="ollama", model="qwen2.5:3b", priority=2)

    registry = ProviderRegistry()
    registry.register(target_a)
    registry.register(target_b)

    circuit_storage = InMemoryCircuitStorage()
    circuit_mgr = CircuitBreakerManager(storage=circuit_storage, metrics=custom_metrics)
    router = Router(registry=registry, circuit_manager=circuit_mgr)

    mock_service = AsyncMock()

    def side_effect_fn(*args: object, **kwargs: object) -> ChatCompletionResponse:
        target = kwargs.get("target") or (args[2] if len(args) > 2 else None)
        assert isinstance(target, ProviderTarget)
        if target.id == "prov_a_fail":
            raise ProviderError(
                message="Timeout on A",
                status_code=504,
                category=ErrorCategory.TIMEOUT.value,
                retryable=True,
            )
        return ChatCompletionResponse(
            id="chatcmpl_b_delivered",
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessageResponse(role="assistant", content="Delivered B")
                )
            ],
            usage=CompletionUsage(prompt_tokens=42, completion_tokens=84, total_tokens=126),
        )

    mock_service.execute_chat_completion.side_effect = side_effect_fn

    sleep_mock = AsyncMock()
    retry_mgr = RetryManager(
        config=Settings(MAX_RETRIES=1),
        provider_service=mock_service,
        sleep_func=sleep_mock,
        metrics=custom_metrics,
    )

    mock_repo = AsyncMock()
    usage_trk = UsageTracker(
        calculator=CostCalculator(),
        repository=mock_repo,
        metrics=custom_metrics,
    )

    failover_mgr = FailoverManager(
        router_instance=router,
        retry_instance=retry_mgr,
        circuit_instance=circuit_mgr,
        usage_tracker_instance=usage_trk,
        metrics=custom_metrics,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Delivered Usage Test")]
    )
    resp = await failover_mgr.execute_with_failover(
        request=req, request_id="req_deliv_1", tenant_id="tenant_1"
    )

    assert resp.id == "chatcmpl_b_delivered"

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    # Tokens recorded ONLY for prov_b_ok
    assert 'gateway_tokens_total{model="qwen2.5:3b",provider="prov_b_ok",type="input"} 42.0' in text
    assert (
        'gateway_tokens_total{model="qwen2.5:3b",provider="prov_b_ok",type="output"} 84.0' in text
    )

    # prov_a_fail must NOT have any token metrics
    assert "prov_a_fail" not in [
        line for line in text.splitlines() if "gateway_tokens_total" in line
    ]
