from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from app.main import create_app
from app.observability.metrics import (
    GatewayMetrics,
    create_metrics_registry,
    gateway_metrics,
)


def test_metrics_registry_creation() -> None:
    """Test 1: Verify that a metrics container and custom registry are created cleanly."""
    metrics = GatewayMetrics()
    assert metrics.registry is not None
    assert metrics.registry is not gateway_metrics.registry or metrics is gateway_metrics


def test_registry_isolation() -> None:
    """Test 2: Verify that two metrics containers have isolated registries without cross-talk."""
    registry_a = create_metrics_registry()
    registry_b = create_metrics_registry()

    assert registry_a is not registry_b

    # Register a counter only in registry_a
    counter_a = Counter(
        "test_metric_a_total",
        "Test counter A",
        registry=registry_a,
    )
    counter_a.inc(5)

    output_a = generate_latest(registry_a).decode("utf-8")
    output_b = generate_latest(registry_b).decode("utf-8")

    assert "test_metric_a_total 5.0" in output_a
    assert "test_metric_a_total" not in output_b


def test_duplicate_application_creation_no_timeseries_collision() -> None:
    """Test 3: Verify multiple app instances do not cause duplicate timeseries errors."""
    app1 = create_app()
    app2 = create_app()
    app3 = create_app()

    client1 = TestClient(app1)
    client2 = TestClient(app2)
    client3 = TestClient(app3)

    resp1 = client1.get("/metrics")
    resp2 = client2.get("/metrics")
    resp3 = client3.get("/metrics")

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp3.status_code == 200


def test_get_metrics_endpoint_exposition_format() -> None:
    """Test 4: Verify GET /metrics returns 200 with standard Prometheus format and no leaks."""
    app = create_app()
    client = TestClient(app)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert CONTENT_TYPE_LATEST in response.headers["content-type"]

    # Verify no sensitive leaked fields or keys
    body = response.text
    assert "password" not in body.lower()
    assert "api_key" not in body.lower()
    assert "secret" not in body.lower()


def test_existing_endpoints_regression() -> None:
    """Test 5: Verify that adding /metrics does not break existing routes (/health, /ready, /)."""
    app = create_app()
    client = TestClient(app)

    # Health check
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "healthy"

    # Ready check
    ready_resp = client.get("/ready")
    assert ready_resp.status_code == 200
    assert ready_resp.json()["ready"] is True

    # Root
    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert root_resp.json()["status"] == "operational"


def test_metrics_endpoint_exposes_phase_14_2_14_3a_and_14_3b_metrics() -> None:
    """Test 6: Verify GET /metrics exposes all Phase 14.2, 14.3a, and 14.3b metric families."""
    app = create_app()
    client = TestClient(app)

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    assert "gateway_requests_total" in body
    assert "gateway_request_duration_seconds" in body
    assert "gateway_provider_requests_total" in body
    assert "gateway_provider_duration_seconds" in body
    assert "gateway_provider_errors_total" in body
    assert "gateway_retries_total" in body
    assert "gateway_failovers_total" in body
    assert "gateway_circuit_state" in body
    assert "gateway_auth_failures_total" in body
    assert "gateway_rate_limit_rejections_total" in body
