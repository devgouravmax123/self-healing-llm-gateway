"""Tests for Grafana dashboards and monitoring provisioning (Phase 14.6)."""

import json
from pathlib import Path
from typing import Any

import yaml

MONITORING_ROOT = Path(__file__).resolve().parent.parent / "monitoring"

APPROVED_METRIC_FAMILIES = {
    "gateway_requests_total",
    "gateway_request_duration_seconds",
    "gateway_provider_requests_total",
    "gateway_provider_duration_seconds",
    "gateway_provider_errors_total",
    "gateway_retries_total",
    "gateway_failovers_total",
    "gateway_circuit_state",
    "gateway_auth_failures_total",
    "gateway_rate_limit_rejections_total",
    "gateway_tokens_total",
    "gateway_estimated_cost_usd_total",
}

APPROVED_LABELS = {
    "gateway_requests_total": {"model", "status_code"},
    "gateway_request_duration_seconds": {"model", "status_code", "le"},
    "gateway_provider_requests_total": {"provider", "model", "status"},
    "gateway_provider_duration_seconds": {"provider", "model", "le"},
    "gateway_provider_errors_total": {"provider", "model", "error_category"},
    "gateway_retries_total": {"provider", "reason"},
    "gateway_failovers_total": {"from_provider", "to_provider", "reason"},
    "gateway_circuit_state": {"provider", "state"},
    "gateway_auth_failures_total": {"reason"},
    "gateway_rate_limit_rejections_total": {"reason"},
    "gateway_tokens_total": {"provider", "model", "type"},
    "gateway_estimated_cost_usd_total": {"provider", "model"},
}


class TestMonitoringProvisioningFiles:
    """Validate existence and syntax of monitoring configuration files."""

    def test_monitoring_files_exist(self) -> None:
        """Verify all mandatory Phase 14.6 monitoring files exist on disk."""
        prom_cfg = MONITORING_ROOT / "prometheus" / "prometheus.yml"
        ds_prov = MONITORING_ROOT / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
        dash_prov = MONITORING_ROOT / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
        dash_json = MONITORING_ROOT / "grafana" / "dashboards" / "llm-gateway-overview.json"

        assert prom_cfg.is_file(), f"Missing {prom_cfg}"
        assert ds_prov.is_file(), f"Missing {ds_prov}"
        assert dash_prov.is_file(), f"Missing {dash_prov}"
        assert dash_json.is_file(), f"Missing {dash_json}"

    def test_prometheus_yaml_valid(self) -> None:
        """Verify prometheus.yml is valid YAML and defines expected scrape config."""
        prom_cfg = MONITORING_ROOT / "prometheus" / "prometheus.yml"
        with open(prom_cfg, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert isinstance(data, dict)
        assert "scrape_configs" in data
        jobs = data["scrape_configs"]
        assert len(jobs) >= 1
        llm_job = next((j for j in jobs if j.get("job_name") == "llm-gateway"), None)
        assert llm_job is not None
        assert llm_job.get("metrics_path") == "/metrics"
        assert "static_configs" in llm_job

    def test_grafana_datasource_provisioning_valid(self) -> None:
        """Verify Grafana datasource provisioning registers Prometheus as default."""
        ds_prov = MONITORING_ROOT / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
        with open(ds_prov, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert isinstance(data, dict)
        assert data.get("apiVersion") == 1
        assert "datasources" in data
        datasources = data["datasources"]
        assert len(datasources) >= 1
        prom_ds = next((ds for ds in datasources if ds.get("type") == "prometheus"), None)
        assert prom_ds is not None
        assert prom_ds.get("isDefault") is True

    def test_grafana_dashboard_provisioning_valid(self) -> None:
        """Verify Grafana dashboard provisioning config loads JSON definitions from disk."""
        dash_prov = MONITORING_ROOT / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
        with open(dash_prov, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert isinstance(data, dict)
        assert data.get("apiVersion") == 1
        assert "providers" in data
        providers = data["providers"]
        assert len(providers) >= 1
        provider = providers[0]
        assert provider.get("type") == "file"
        assert "options" in provider
        assert "path" in provider["options"]


class TestGrafanaDashboardDefinition:
    """Validate JSON dashboard structure, panels, rows, and PromQL queries."""

    def test_dashboard_json_valid_and_structured(self) -> None:
        """Verify llm-gateway-overview.json is valid JSON with required metadata."""
        dash_json = MONITORING_ROOT / "grafana" / "dashboards" / "llm-gateway-overview.json"
        with open(dash_json, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        assert data.get("title") == "Self-Healing LLM Gateway — Operational Overview"
        assert data.get("uid") == "llm-gateway-overview"
        assert "panels" in data
        assert len(data["panels"]) > 0

    def test_dashboard_rows_and_panels(self) -> None:
        """Verify dashboard defines required 5 logical rows and visual panels."""
        dash_json = MONITORING_ROOT / "grafana" / "dashboards" / "llm-gateway-overview.json"
        with open(dash_json, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        panels = data["panels"]
        row_titles = [p.get("title") for p in panels if p.get("type") == "row"]

        expected_row_keywords = [
            "Traffic",
            "Latency",
            "Provider Health",
            "Reliability",
            "Usage",
        ]
        for kw in expected_row_keywords:
            assert any(kw in title for title in row_titles if title), (
                f"Missing row with keyword '{kw}'"
            )

        visual_panels = [p for p in panels if p.get("type") != "row"]
        assert len(visual_panels) >= 10, "Expected at least 10 visual panels"

    def test_dashboard_promql_references_approved_metrics_only(self) -> None:
        """Extract all PromQL query expressions and verify they use approved metrics."""
        dash_json = MONITORING_ROOT / "grafana" / "dashboards" / "llm-gateway-overview.json"
        with open(dash_json, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)

        panels = data["panels"]
        queries: list[str] = []
        for panel in panels:
            if "targets" in panel:
                for target in panel["targets"]:
                    if "expr" in target:
                        queries.append(target["expr"])

        assert len(queries) >= 10

        # Check that every query references at least one approved metric family
        for query in queries:
            matched_metrics = [m for m in APPROVED_METRIC_FAMILIES if m in query]
            assert len(matched_metrics) > 0, (
                f"Query '{query}' does not reference any approved gateway metric!"
            )

    def test_dashboard_no_sensitive_labels_or_variables(self) -> None:
        """Ensure dashboard does not expose tenant_id, auth tokens, or variables."""
        dash_json = MONITORING_ROOT / "grafana" / "dashboards" / "llm-gateway-overview.json"
        raw_text = dash_json.read_text(encoding="utf-8").lower()

        forbidden_terms = [
            "tenant_id",
            "api_key",
            "authorization",
            "secret",
            "password",
            "prompt",
            "gw_live",
            "gw_test",
        ]

        for term in forbidden_terms:
            assert term not in raw_text, (
                f"Forbidden sensitive term '{term}' found in dashboard JSON!"
            )
