# Multi-Container Physical Failover Demonstration

This document details the multi-container physical failover architecture and reproduction guide for the Self-Healing LLM Gateway.

## Architecture

The demonstration environment runs two separate, real Ollama container processes on the Docker bridge network (`llm-gateway-network`) with isolated data volumes:

```text
                    Client
                      │
                      ▼
                 Nginx (:8000)
                      │
                      ▼
               Gateway (:8000)
                      │
         ┌────────────┴────────────┐
         ▼                         ▼
   ollama_primary            ollama_secondary
  (priority=1, Port 11434)  (priority=2, Port 11434)
   Container:                Container:
   llm-gateway-ollama-primary llm-gateway-ollama-secondary
         │                         │
     qwen2.5:3b                qwen2.5:3b
```

### Key Differences from Chaos Injection
- **No Mocking/Simulation**: The primary container is physically terminated using Docker (`docker stop llm-gateway-ollama-primary`).
- **Real Network Socket Failures**: The gateway encounters actual TCP connection drops / HTTP 504 timeouts at the socket level.
- **Independent Local Inference**: Both Ollama instances have their own dedicated volume mounts (`ollama_primary_data`, `ollama_secondary_data`) and run independent inference engines.

---

## Configuration

The physical failover environment is **opt-in** using `docker-compose.demo.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
```

Demo settings applied via `docker-compose.demo.yml`:
- `PROVIDER_TARGETS_JSON`: Configures `ollama_primary` (`http://ollama-primary:11434`, priority 1) and `ollama_secondary` (`http://ollama-secondary:11434`, priority 2).
- `CIRCUIT_FAILURE_THRESHOLD`: `2` (demo override for responsive tripping).
- `CIRCUIT_COOLDOWN_SECONDS`: `5.0` (demo override for rapid recovery).

---

## Running the Demonstration

1. **Start the stack**:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d
   ```

2. **Pull the model into both Ollama containers**:
   ```bash
   docker exec llm-gateway-ollama-primary ollama pull qwen2.5:3b
   docker exec llm-gateway-ollama-secondary ollama pull qwen2.5:3b
   ```

3. **Execute the automated demonstration script**:
   ```bash
   uv run python scripts/demo_physical_failover.py --api-key <YOUR_TENANT_KEY>
   ```

---

## Observed Lifecycle & Verification Evidence

1. **Step 1: Baseline Request**: Sent when both containers are healthy. Gateway routes to `ollama_primary` (priority 1). Success.
2. **Step 2: Primary Outage**: `docker stop llm-gateway-ollama-primary` executes.
3. **Step 3: Seamless Failover**: Gateway sends request to primary, detects network failure, performs bounded retry, trips circuit breaker on primary, and fails over to `ollama_secondary`. Request completes successfully with HTTP 200.
4. **Step 4: Metrics Verification**:
   - `gateway_failovers_total{from_provider="ollama_primary", to_provider="ollama_secondary"}` increments by 1.
   - `gateway_provider_requests_total{provider="ollama_secondary", status="success"}` increments by 1.
5. **Step 5: Recovery**: `docker start llm-gateway-ollama-primary` restarts the primary container.
6. **Step 6: Post-Recovery Request**: After the 5s cooldown, a request probes the primary container, transitions circuit state from HALF-OPEN to CLOSED, and completes successfully.

---

## Limitations & Honesty Disclosures

- **Single Host / Single Engine**: While containers are physically isolated processes with distinct networking namespaces and storage volumes, they reside on the same host Docker daemon.
- **Hardware Resources**: Running two simultaneous Ollama instances with `qwen2.5:3b` requires sufficient host RAM/VRAM.
