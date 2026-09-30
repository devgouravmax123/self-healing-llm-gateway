"""Structural validation tests for Docker and Docker Compose configuration (Phase 18)."""

from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent


class TestDockerConfiguration:
    """Validate Dockerfile, .dockerignore, Nginx, and Docker Compose configuration statically."""

    def test_required_docker_files_exist(self) -> None:
        """Verify all Phase 18 configuration files exist on disk."""
        dockerfile = ROOT_DIR / "Dockerfile"
        dockerignore = ROOT_DIR / ".dockerignore"
        compose_file = ROOT_DIR / "docker-compose.yml"
        nginx_conf = ROOT_DIR / "docker" / "nginx" / "nginx.conf"
        prometheus_conf = ROOT_DIR / "monitoring" / "prometheus" / "prometheus.yml"

        assert dockerfile.is_file(), f"Missing {dockerfile}"
        assert dockerignore.is_file(), f"Missing {dockerignore}"
        assert compose_file.is_file(), f"Missing {compose_file}"
        assert nginx_conf.is_file(), f"Missing {nginx_conf}"
        assert prometheus_conf.is_file(), f"Missing {prometheus_conf}"

    def test_dockerfile_python_version_and_non_root(self) -> None:
        """Verify Dockerfile uses Python 3.12, multi-stage uv build, and non-root execution."""
        dockerfile = ROOT_DIR / "Dockerfile"
        content = dockerfile.read_text(encoding="utf-8")

        # Python version check
        assert "python3.12" in content or "python:3.12" in content
        # Tooling
        assert "uv" in content
        assert "multi-stage" in content.lower() or "AS builder" in content
        # Non-root user creation and switch
        assert "useradd" in content or "adduser" in content
        assert "USER appuser" in content or "USER " in content
        # Expose port
        assert "EXPOSE 8000" in content
        # Alembic migration and uvicorn startup
        assert "alembic upgrade head" in content
        assert "uvicorn app.main:app" in content

    def test_dockerignore_excludes_secrets_and_caches(self) -> None:
        """Verify .dockerignore excludes virtual environments, git history, and secrets."""
        dockerignore = ROOT_DIR / ".dockerignore"
        content = dockerignore.read_text(encoding="utf-8")

        forbidden_patterns = [".git", ".venv", "__pycache__", ".env"]
        for pat in forbidden_patterns:
            assert pat in content, f"Expected {pat} in .dockerignore"

    def test_docker_compose_structure_and_services(self) -> None:
        """Verify docker-compose.yml defines all 7 required services and valid networking."""
        compose_file = ROOT_DIR / "docker-compose.yml"
        content = compose_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)

        assert "services" in data
        services = data["services"]

        expected_services = [
            "gateway",
            "redis",
            "postgres",
            "ollama",
            "prometheus",
            "grafana",
            "nginx",
        ]
        for svc in expected_services:
            assert svc in services, f"Missing service '{svc}' in docker-compose.yml"

        # Check networks and volumes
        assert "networks" in data
        assert "gateway-network" in data["networks"]
        assert "volumes" in data
        assert "postgres_data" in data["volumes"]
        assert "ollama_data" in data["volumes"]

    def test_internal_services_not_host_exposed(self) -> None:
        """Verify PostgreSQL, Redis, and Ollama are strictly internal without host port mappings."""
        compose_file = ROOT_DIR / "docker-compose.yml"
        content = compose_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        services = data["services"]

        # Redis, Postgres, Ollama must NOT have published ports
        for internal_svc in ["redis", "postgres", "ollama", "gateway"]:
            assert "ports" not in services[internal_svc], (
                f"Service '{internal_svc}' must not expose host ports"
            )

        # Nginx must expose port 8000 on host mapping to 80 internal
        assert "ports" in services["nginx"]
        assert "8000:80" in services["nginx"]["ports"]

    def test_gateway_service_environment_and_dependencies(self) -> None:
        """Verify gateway service environment variables point to Compose DNS hostnames."""
        compose_file = ROOT_DIR / "docker-compose.yml"
        content = compose_file.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        gateway = data["services"]["gateway"]

        env = gateway.get("environment", {})
        assert "redis://redis:6379" in env.get("REDIS_URL", "")
        assert "@postgres:5432" in env.get("DATABASE_URL", "")
        assert "http://ollama:11434" in env.get("OLLAMA_BASE_URL", "")

        # Dependencies
        depends_on = gateway.get("depends_on", {})
        assert "postgres" in depends_on
        assert "redis" in depends_on
        assert depends_on["postgres"].get("condition") == "service_healthy"
        assert depends_on["redis"].get("condition") == "service_healthy"

    def test_nginx_configuration_routing_and_metrics_protection(self) -> None:
        """Verify Nginx config proxies /v1/, /health, /ready and blocks public /metrics."""
        nginx_conf = ROOT_DIR / "docker" / "nginx" / "nginx.conf"
        content = nginx_conf.read_text(encoding="utf-8")

        assert "upstream gateway_backend" in content
        assert "gateway:8000" in content
        assert "location /health" in content
        assert "location /ready" in content
        assert "location /v1/" in content
        assert "location /metrics" in content
        assert "return 404" in content  # Blocks public exposure of /metrics

    def test_prometheus_scrapes_gateway_container(self) -> None:
        """Verify Prometheus config targets gateway:8000 inside container network."""
        prom_conf = ROOT_DIR / "monitoring" / "prometheus" / "prometheus.yml"
        content = prom_conf.read_text(encoding="utf-8")

        assert "gateway:8000" in content
        assert "/metrics" in content
