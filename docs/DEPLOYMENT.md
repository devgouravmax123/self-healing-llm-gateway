# Deployment Guide — Containerized and Local Setup

This document provides the operational guide for deploying and running the **Self-Healing LLM Gateway** in both local development mode and containerized Docker Compose mode (Phase 18).

---

## 1. Architectural Overview

The gateway is completely configuration-driven. The exact same application code runs in three deployment tiers without modification:

1. **Local Host Development**:
   - Gateway runs via `uv run uvicorn app.main:app`
   - Connects to local Redis (`localhost:6379`), local PostgreSQL (`localhost:5432`), and local Ollama (`localhost:11434`).
2. **Containerized Docker Compose (Phase 18)**:
   - Full 7-service stack containerized with private bridge network.
   - Nginx serves as the single public reverse-proxy entrypoint on host port `8000`.
   - Internal services (`postgres`, `redis`, `ollama`, `gateway`) are unexposed to the host.
   - Prometheus and Grafana provide local observability.
3. **Future Cloud / Hosted Deployment (Phase 20+)**:
   - Gateway container is supplied with hosted RDS PostgreSQL, ElastiCache/Redis Cloud, and remote provider endpoints (e.g. OpenAI/Anthropic/Groq) purely via environment variables.

---

## 2. Docker Compose Topology

| Service | Container Name | Internal Port | Host Port | Role & Exposure |
| :--- | :--- | ---: | ---: | :--- |
| **nginx** | `llm-gateway-nginx` | 80 | **8000** | **Public Reverse Proxy**: Entrypoint for all client traffic (`/v1/`, `/health`, `/ready`). Blocks public `/metrics`. |
| **gateway** | `llm-gateway-api` | 8000 | *None* | **FastAPI Core**: Executes routing, rate-limiting, circuit breaking, failover, and metrics. |
| **postgres** | `llm-gateway-postgres` | 5432 | *None* | **Durable Storage**: PostgreSQL 16 storing tenants, API keys, and usage records. |
| **redis** | `llm-gateway-redis` | 6379 | *None* | **Operational State**: Redis 7 managing circuit breaker states, sliding-window rate limits, and health counters. |
| **ollama** | `llm-gateway-ollama` | 11434 | *None* | **LLM Inference Provider**: Local Ollama serving `qwen2.5:3b`. |
| **prometheus** | `llm-gateway-prometheus` | 9090 | **9090** | **Metrics Engine**: Scrapes `gateway:8000/metrics` over internal network. Exposed for local debugging. |
| **grafana** | `llm-gateway-grafana` | 3000 | **3000** | **Monitoring Dashboard**: Pre-provisioned Prometheus datasource & gateway overview dashboard. |

---

## 3. Quickstart: Running with Docker Compose

### Prerequisites
- Docker Engine 24+ and Docker Compose v2+
- Valid local `.env` file (copied from `.env.example`)

### 1. Build and Start the Stack

```bash
# Start all 7 services in the background with health-checked dependency ordering
docker compose up --build -d
```

### 2. Verify Container Health and Status

```bash
docker compose ps
```

All services will show `Up (healthy)`.

### 3. Pull / Verify the Ollama Model

Inside the running Ollama container, download the target model (`qwen2.5:3b`):

```bash
docker compose exec ollama ollama pull qwen2.5:3b
```

### 4. Verify Gateway Health and Endpoints

Through Nginx (port 8000 on host):

```bash
# Gateway Health
curl -s http://localhost:8000/health

# Gateway Readiness
curl -s http://localhost:8000/ready
```

### 5. Send an Authenticated Chat Completion Request

```bash
# Ensure you have created an API key in PostgreSQL (or created one via admin/migration)
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <YOUR_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5:3b",
    "messages": [{"role": "user", "content": "Hello world"}]
  }'
```

---

## 4. Observability and Monitoring

- **Grafana**: Access dashboards at [http://localhost:3000](http://localhost:3000) (Default login: `admin` / `admin`).
  - Pre-provisioned dashboard: *Self-Healing LLM Gateway Overview*.
- **Prometheus**: Access Prometheus query UI at [http://localhost:9090](http://localhost:9090).
- **Protected Metrics**: Querying `http://localhost:8000/metrics` via Nginx returns `404 Not Found`. Metrics are accessible strictly inside the Docker network.

---

## 5. Teardown and Cleanup

To stop and remove containers:

```bash
docker compose down
```

To stop containers and delete persistent volume data:

```bash
docker compose down -v
```

---

## 6. Environment Configuration Reference

The following environment variables configure the containerized gateway in `docker-compose.yml`:

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `REDIS_URL` | `redis://redis:6379/0` | Internal connection URL to Compose Redis service |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@postgres:5432/llm_gateway` | Async SQLAlchemy PostgreSQL connection URL |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Internal connection URL to Compose Ollama service |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Target default LLM model name |
| `DEFAULT_TENANT_RPM` | `60` | Default tenant sliding-window rate limit |
| `CHAOS_ENABLED` | `false` | Enable/disable chaos fault-injection endpoints |
| `ADMIN_API_KEY` | *None* | Required Bearer key for administrative endpoints |
| `OTEL_ENABLED` | `true` | Enable/disable OpenTelemetry instrumentation |

---

## 7. Scope & Portability Statement

> **Note**: Phase 18 provides reproducible local containerized deployment via Docker Compose. Actual cloud/hosted deployment (Kubernetes, AWS/GCP, multi-region database replication, managed TLS) is intentionally deferred to later phases.
