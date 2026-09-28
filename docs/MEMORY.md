\# Project Memory



\## 1. Purpose



This file stores important long-term project context that should not be lost between development sessions.



It exists so that developers and coding agents can understand:



\* What has already been decided

\* Why important decisions were made

\* What is currently implemented

\* What remains incomplete

\* Important lessons learned

\* Constraints that must continue to apply

\* Known problems and their solutions



This file should be updated whenever important project context changes.



\---



\# 2. Project Identity



\*\*Project Name:\*\* Self-Healing LLM Gateway



\*\*Project Type:\*\* AI infrastructure / reliability engineering project



\*\*Primary Goal:\*\*



Build an OpenAI-compatible LLM gateway that can automatically detect provider failures, manage provider health, retry eligible failures, fail over to healthy providers, and recover providers using circuit-breaker behavior.



The project should demonstrate real backend and infrastructure engineering rather than being only an LLM API wrapper.



\---



\# 3. Core Engineering Idea



The gateway sits between an application and multiple LLM providers.



Instead of:



```text

Application → One LLM Provider

```



the system provides:



```text

Application

&#x20;   ↓

LLM Gateway

&#x20;   ↓

Multiple LLM Providers

```



The gateway is responsible for reliability.



A simplified failure scenario:



```text

Application

&#x20;   ↓

Gateway

&#x20;   ↓

Provider A

&#x20;   ↓

Failure

&#x20;   ↓

Retry / classify error

&#x20;   ↓

Circuit opens

&#x20;   ↓

Failover

&#x20;   ↓

Provider B

&#x20;   ↓

Success

```



Later, the system should detect that Provider A has recovered:



```text

Provider A

&#x20;   ↓

OPEN

&#x20;   ↓

Cooldown

&#x20;   ↓

HALF-OPEN

&#x20;   ↓

Probe request

&#x20;   ↓

Success

&#x20;   ↓

CLOSED

```



This recovery behavior is a central part of the project's "self-healing" concept.



\---



\# 4. Locked Technology Direction



The current technology direction is:



```text

Language             Python 3.12

Package Manager      uv



API                  FastAPI

Validation           Pydantic v2

HTTP Client          httpx



LLM Abstraction      LiteLLM

Local Inference      Ollama



Operational State    Redis



Database             PostgreSQL

ORM                  SQLAlchemy 2.x

Migrations           Alembic



Metrics              Prometheus

Dashboards           Grafana

Tracing              OpenTelemetry

Logging              Structured JSON



Testing              pytest

Async Testing        pytest-asyncio

Load Testing         k6

Chaos Testing        Custom fault injection



Containers            Docker

Orchestration         Docker Compose

Reverse Proxy         Nginx



CI/CD                GitHub Actions

Linting              Ruff

Type Checking        mypy

Git Hooks            pre-commit

```



These technologies should not be replaced casually.



If a technology needs to be changed, document the reason in:



```text

docs/DECISIONS.md

```



\---



\# 5. Local-First Requirement



The project must be usable locally without requiring paid cloud APIs.



The core demonstration should work at:



```text

₹0 mandatory cost

```



The preferred development path is:



```text

FastAPI Gateway

&#x20;     ↓

LiteLLM

&#x20;     ↓

Ollama

&#x20;     ↓

Local Models

```



External providers may be added later as optional integrations.



A paid provider must never be required for the core project demonstration.



\---



\# 6. Current Architecture



The intended high-level architecture is:



```text

&#x20;                   ┌───────────────┐

&#x20;                   │   Client/App  │

&#x20;                   └───────┬───────┘

&#x20;                           │

&#x20;                           ▼

&#x20;                   ┌───────────────┐

&#x20;                   │     Nginx     │

&#x20;                   └───────┬───────┘

&#x20;                           │

&#x20;                           ▼

&#x20;             ┌───────────────────────────┐

&#x20;             │      FastAPI Gateway      │

&#x20;             │                           │

&#x20;             │ Authentication             │

&#x20;             │ Validation                 │

&#x20;             │ Request IDs                │

&#x20;             │ Rate Limiting              │

&#x20;             │ Routing                    │

&#x20;             │ Circuit Breaker            │

&#x20;             │ Retry / Timeout            │

&#x20;             │ Failover                   │

&#x20;             │ Cost Attribution           │

&#x20;             └─────────────┬─────────────┘

&#x20;                           │

&#x20;            ┌──────────────┼───────────────┐

&#x20;            │              │               │

&#x20;            ▼              ▼               ▼

&#x20;         Redis        PostgreSQL       LiteLLM

&#x20;            │              │               │

&#x20;            │              │       ┌───────┼────────┐

&#x20;            │              │       ▼       ▼        ▼

&#x20;            │              │    Ollama   Provider  Provider

&#x20;            │              │              B        C

&#x20;            │

&#x20;            ▼

&#x20;      Operational State



Prometheus ─────→ Grafana



OpenTelemetry ──→ Tracing



k6 ─────────────→ Load Testing



Chaos Injection → Failure Testing

```



\---



\# 7. Important State Ownership



One important architectural distinction:



\## Redis



Redis stores frequently changing operational state.



Examples:



\* Provider health state

\* Circuit-breaker state

\* Rolling counters

\* Rate-limit counters

\* Temporary operational data

\* Short-lived coordination state



Redis is optimized for fast-changing runtime information.



\---



\## PostgreSQL



PostgreSQL stores durable information.



Examples:



\* Tenants

\* API keys or authentication records

\* Usage records

\* Token counts

\* Cost records

\* Request history

\* Audit information

\* Persistent configuration where appropriate



Do not use PostgreSQL as a replacement for every Redis operation.



Do not use Redis as the permanent source of truth for durable business records.



\---



\# 8. Circuit Breaker Memory



The circuit breaker has three primary states.



\## CLOSED



Provider is considered healthy.



Requests are allowed normally.



```text

CLOSED

&#x20;  ↓

normal traffic

```



Repeated eligible failures can cause the circuit to open.



\---



\## OPEN



Provider is considered unhealthy.



Normal requests should not continue being sent to the provider.



The gateway should fail over to another eligible provider when possible.



```text

OPEN

&#x20;  ↓

block normal traffic

&#x20;  ↓

failover

```



A cooldown period should be used before testing recovery.



\---



\## HALF-OPEN



The provider gets a controlled recovery probe.



The purpose is to answer:



> "Has this provider recovered?"



If the probe succeeds:



```text

HALF-OPEN → CLOSED

```



If the probe fails:



```text

HALF-OPEN → OPEN

```



Half-open behavior should be controlled so that a recovering provider is not immediately flooded with traffic.



\---



\# 9. Failure Categories



The gateway should classify provider failures instead of treating every error identically.



Initial error categories:



```text

RATE\_LIMIT

TIMEOUT

SERVER\_ERROR

AUTH\_ERROR

CONTENT\_FILTER

NETWORK\_ERROR

BAD\_REQUEST

UNKNOWN

```



The classification determines whether an error should:



\* Be retried

\* Open the circuit

\* Trigger failover

\* Be returned directly to the client

\* Be recorded as a provider-health event



For example:



```text

TIMEOUT

&#x20;   ↓

Potentially retry

&#x20;   ↓

If repeated

&#x20;   ↓

Provider health degradation

&#x20;   ↓

Possible circuit opening

```



Whereas:



```text

BAD\_REQUEST

&#x20;   ↓

Do not blindly retry

&#x20;   ↓

Return client error

```



The exact retry policy should be explicitly implemented and tested.



\---



\# 10. Retry Memory



Retries are not automatically appropriate for every failure.



The gateway should:



\* Use bounded retries

\* Apply request timeouts

\* Retry only eligible failures

\* Use exponential backoff

\* Add jitter

\* Avoid retry storms

\* Record retry attempts

\* Stop retrying after the configured limit



Conceptually:



```text

Request

&#x20; ↓

Provider failure

&#x20; ↓

Is error retryable?

&#x20; ├── No → classify/failover/return

&#x20; │

&#x20; └── Yes

&#x20;       ↓

&#x20;     Wait

&#x20;       ↓

&#x20;     Retry

&#x20;       ↓

&#x20;     Still failing?

&#x20;       ↓

&#x20;     Failover / circuit logic

```



\---



\# 11. Failover Memory



Providers should be represented through a configurable provider/model registry.



The gateway should be able to maintain a preference such as:



```text

Provider A

&#x20;   ↓

Provider B

&#x20;   ↓

Provider C

```



If Provider A is unavailable:



```text

A → unavailable

B → eligible

C → eligible



Select B

```



The gateway should consider:



\* Provider health

\* Circuit state

\* Error history

\* Provider/model availability

\* Routing configuration



Every failover should be observable.



\---



\# 12. Health Monitoring Memory



Provider health should be measurable.



The system should track information such as:



```text

Total requests

Successful requests

Failed requests



Success rate

Failure rate



p50 latency

p95 latency

p99 latency



Current circuit state



Last successful request

Last failure



Retry count

Failover count

```



Health information should be used by the reliability mechanisms rather than existing only as dashboard decoration.



\---



\# 13. Observability Memory



Observability is a core requirement, not an optional add-on.



Every important request should have a request ID.



Useful request metadata includes:



```text

request\_id

tenant\_id

feature

provider

model

latency

status

error\_type

retry\_count

failover\_count

circuit\_state

token\_usage

estimated\_cost

```



The project should provide three major observability layers:



\### Logs



Structured JSON logs.



\### Metrics



Prometheus metrics.



\### Traces



OpenTelemetry traces.



Grafana should provide dashboards that make the reliability behavior visible.



\---



\# 14. Cost Attribution Memory



The gateway should be designed so that usage can be attributed to:



```text

Tenant

Feature

Request

Provider

Model

```



Where supported, record:



```text

Input tokens

Output tokens

Total tokens

Estimated cost

```



For local Ollama models, monetary cost may be zero while token and usage statistics should still be tracked where available.



Cost calculations must be clearly identified as:



\* Provider-reported

\* Gateway-estimated

\* Or unavailable



Do not invent cost values.



\---



\# 15. Multi-Tenancy Memory



The gateway should support tenant-aware request metadata.



Important identifiers:



```text

tenant\_id

feature

request\_id

```



These identifiers should be available for:



\* Usage tracking

\* Cost attribution

\* Rate limiting

\* Logging

\* Metrics

\* Tracing

\* Auditing



Tenant isolation must be maintained when implementing persistent or operational data.



\---



\# 16. Rate Limiting Memory



Rate limiting should use Redis-backed state.



Potential dimensions include:



```text

Tenant

API key

Request class

```



Rate limiting must be implemented in a way that works correctly across multiple gateway instances if the system is later scaled horizontally.



The first version does not need distributed deployment, but the design should avoid unnecessarily preventing it.



\---



\# 17. Chaos Testing Memory



The project must be able to intentionally simulate failures.



Examples:



```text

Provider outage

Provider timeout

Provider 429

Provider 500

Artificial latency

Network-like failure

```



Chaos/fault injection must be:



\* Explicit

\* Disabled by default

\* Restricted to development/testing

\* Clearly observable

\* Impossible to accidentally expose as an unrestricted production endpoint



The purpose is to demonstrate that the gateway actually heals from failures.



\---



\# 18. Main Reliability Demonstration



The most important project demonstration should show:



```text

1\. Normal request

&#x20;       ↓

2\. Provider A handles request

&#x20;       ↓

3\. Inject Provider A failure

&#x20;       ↓

4\. Gateway detects/classifies failure

&#x20;       ↓

5\. Retry policy executes where appropriate

&#x20;       ↓

6\. Circuit for A opens

&#x20;       ↓

7\. Gateway fails over to Provider B

&#x20;       ↓

8\. Request succeeds

&#x20;       ↓

9\. Provider A remains OPEN temporarily

&#x20;       ↓

10\. Cooldown expires

&#x20;       ↓

11\. Provider A becomes HALF-OPEN

&#x20;       ↓

12\. Recovery probe succeeds

&#x20;       ↓

13\. Provider A returns to CLOSED

```



This flow should be visible through:



\* Logs

\* Metrics

\* Grafana

\* Traces

\* Tests



\---



\# 19. Current Development Philosophy



The project should be developed incrementally.



Do not attempt to implement the complete system in one step.



Preferred workflow:



```text

Read documentation

&#x20;       ↓

Implement one small task

&#x20;       ↓

Run tests

&#x20;       ↓

Verify behavior

&#x20;       ↓

Update documentation/status

&#x20;       ↓

Commit

&#x20;       ↓

Move to next task

```



A working simple system should exist before adding advanced infrastructure.



\---



\# 20. Coding-Agent Memory



When using Antigravity or another coding agent:



1\. Read the `docs/` files before modifying architecture.

2\. Treat documentation as the source of truth.

3\. Implement small, testable changes.

4\. Do not silently replace technologies.

5\. Do not introduce unnecessary infrastructure.

6\. Do not create unnecessary microservices.

7\. Do not add Kubernetes unless explicitly justified.

8\. Do not add Kafka unless a real requirement appears.

9\. Do not add cloud infrastructure when local infrastructure is sufficient.

10\. Do not add paid services to the mandatory path.

11\. Do not claim performance numbers without measurement.

12\. Do not store secrets in source control.

13\. Update `TASKS.md` when implementation status changes.

14\. Record significant architectural changes in `DECISIONS.md`.

15\. Preserve existing working behavior when adding new features.



\---



\# 21. Known Environment Information



Initial development environment:



```text

Operating System: Windows

Development IDE/Agent: Antigravity

Python environment: virtual environment

```



The project initially encountered a file naming issue:



```text

main.py.py

```



instead of:



```text

main.py

```



This caused:



```text

Could not import module "main"

```



The important lesson is to verify filenames and project structure before debugging application logic.



Python 3.12 is the preferred project version for dependency compatibility, even though the development machine has also used Python 3.14.



\---



\# 22. Initial FastAPI State



The project began with a minimal FastAPI application.



The basic API concept is:



```text

GET /

&#x20;   → health/basic gateway response



POST /v1/chat/completions

&#x20;   → OpenAI-compatible chat request

```



The `/v1/chat/completions` endpoint should eventually become the main gateway entry point.



The initial endpoint was only a request-receiving skeleton.



It is not considered the finished gateway implementation.



\---



\# 23. OpenAI-Compatible API Memory



The gateway should expose an API shape familiar to applications using OpenAI-compatible APIs.



The client should not need to understand the internal provider-selection logic.



Conceptually:



```text

Client

&#x20; ↓

POST /v1/chat/completions

&#x20; ↓

Gateway decides provider

&#x20; ↓

LiteLLM

&#x20; ↓

Selected provider/model

&#x20; ↓

Response

&#x20; ↓

Gateway

&#x20; ↓

Client

```



Provider-specific complexity should remain behind the gateway abstraction as much as reasonably possible.



\---



\# 24. What "Self-Healing" Means in This Project



"Self-healing" does not mean the system can fix arbitrary software bugs automatically.



In this project, self-healing specifically means:



```text

Detect failure

&#x20;   ↓

Contain unhealthy provider

&#x20;   ↓

Route around failure

&#x20;   ↓

Continue serving when another provider is available

&#x20;   ↓

Periodically test failed provider

&#x20;   ↓

Detect recovery

&#x20;   ↓

Return provider to service

```



The term should be used in this concrete reliability-engineering sense.



\---



\# 25. Performance Memory



Performance claims must be measured.



Do not claim:



```text

Handles 10,000 requests/sec

```



unless a reproducible test demonstrates it.



Important measurements include:



```text

Gateway overhead

Provider latency

End-to-end latency

p50

p95

p99

Requests/sec

Error rate

Retry rate

Failover rate

```



LLM inference latency and gateway overhead should be distinguished.



\---



\# 26. Security Memory



Security must be considered throughout development.



Important rules:



\* Never commit API keys.

\* Never commit passwords.

\* Use environment variables or secret management.

\* Protect administrative endpoints.

\* Restrict chaos functionality.

\* Validate incoming requests.

\* Avoid logging sensitive user content unnecessarily.

\* Maintain tenant isolation.

\* Use secure defaults.

\* Maintain `.env.example` without real secrets.



Security decisions should be recorded when they materially affect architecture.



\---



\# 27. Testing Memory



Testing should cover more than successful requests.



Required categories:



\### Unit Tests



Examples:



\* Error classification

\* Retry decisions

\* Backoff calculation

\* Circuit transitions

\* Routing decisions

\* Cost calculations



\### Integration Tests



Examples:



\* FastAPI + Redis

\* FastAPI + PostgreSQL

\* Gateway + LiteLLM

\* Gateway + Ollama



\### Failure Tests



Examples:



\* Timeout

\* 429

\* 500

\* Network failure

\* Provider outage

\* Recovery



\### Load Tests



Use k6.



Performance results must be measured rather than assumed.



\---



\# 28. Current Documentation Structure



The documentation directory is intended to contain:



```text

docs/

├── PRD.md

├── ARCHITECTURE.md

├── DESIGN.md

├── RULES.md

├── TASKS.md

├── MEMORY.md

├── SECURITY.md

└── DECISIONS.md

```



Each document has a different purpose.



Do not duplicate entire documents inside `MEMORY.md`.



Memory should preserve important context, not become a second PRD or design document.



\---



\# 29. Important Constraints



The following constraints remain active unless explicitly changed:



```text

1\. Core system must run locally.

2\. No mandatory paid APIs.

3\. Reliability is more important than feature count.

4\. Observability is required.

5\. Failure behavior must be tested.

6\. Performance claims must be measured.

7\. Architecture should remain understandable.

8\. Avoid unnecessary infrastructure.

9\. Secrets must never be committed.

10\. Documentation must remain synchronized with implementation.

```



\---



\# 30. Current Project Status



The project is currently in the documentation and foundation stage.



Documentation is being created before major implementation.



Current documentation progress:



```text

PRD.md             → Created / defined

ARCHITECTURE.md    → Created / defined

DESIGN.md          → Created / defined

RULES.md           → Created / defined

TASKS.md           → Created / defined

MEMORY.md          → Current document

SECURITY.md        → Next

DECISIONS.md       → Next

```



Implementation should proceed according to `TASKS.md`.



\---



\# 31. Memory Update Rules



Update this file when:



\* A major architectural decision is made.

\* A technology is replaced.

\* A significant implementation milestone is completed.

\* A major bug and its solution should be remembered.

\* A constraint changes.

\* An important project assumption changes.

\* A significant reliability behavior is changed.



Do not update this file for every small code change.



For significant architectural decisions, also update:



```text

docs/DECISIONS.md

```



For implementation progress, update:



```text

docs/TASKS.md

```



For security-specific decisions, update:



```text

docs/SECURITY.md

```



\---



\# 32. Source-of-Truth Hierarchy



When documents appear to conflict, use this priority:



```text

PRD.md

&#x20;  ↓

ARCHITECTURE.md

&#x20;  ↓

DESIGN.md

&#x20;  ↓

RULES.md

&#x20;  ↓

TASKS.md

&#x20;  ↓

MEMORY.md

```



However, if an implementation decision changes the intended architecture, do not silently override the documentation.



Instead:



```text

Identify conflict

&#x20;     ↓

Discuss/decide change

&#x20;     ↓

Update relevant documentation

&#x20;     ↓

Record significant decision in DECISIONS.md

&#x20;     ↓

Implement

```



\---



\# 33. Final Project Principle



The project should demonstrate one central engineering idea:



> An LLM application should not have to depend blindly on a single unreliable provider.



The gateway should make provider failures:



```text

Detectable

Containable

Recoverable

Observable

Testable

```



The goal is not to build the largest system.



The goal is to build a system where the reliability mechanisms are real, measurable, understandable, and demonstrable.





---

# 34. Phase 03 — LiteLLM + Ollama Provider Integration Status

Phase 03 completed successfully.

Key deliverables implemented:
* Created LiteLLM provider service module (pp/providers/litellm_client.py).
* Integrated LiteLLM with Ollama backend targeting qwen2.5:3b at http://localhost:11434.
* Added configuration settings for LLM_PROVIDER, OLLAMA_BASE_URL, and OLLAMA_MODEL in pp/core/config.py.
* Preserved OpenAI-compatible API format on POST /v1/chat/completions and X-Request-ID propagation.
* Implemented clean provider exception handling via ProviderError and GatewayError (pp/core/exceptions.py).
* Added comprehensive unit test suite covering success conversion and error mapping with provider mocking.
* Verified real end-to-end inference against local Ollama qwen2.5:3b.


---

# 35. Phase 04 — Provider Registry + Basic Routing Status

Phase 04 completed successfully.

Key deliverables implemented:
* Defined ProviderTarget schema (pp/models/provider.py) specifying provider identity, model, base URL, and enabled flag.
* Implemented ProviderRegistry (pp/routing/provider_registry.py) providing registration, lookup, all listing, and enabled listing initialized from environment configuration.
* Created Router (pp/routing/router.py) and NoHealthyProviderError (HTTP 503) for decoupling provider selection from FastAPI route handlers.
* Refactored POST /v1/chat/completions (pp/api/routes_chat.py) to select provider target via the router before delegating to LiteLLMService.
* Added comprehensive unit test suite (	ests/test_router.py) validating provider registration, retrieval, enabled filtering, router fallback, model matching, and 503 error handling.
* Verified live end-to-end inference against local Ollama qwen2.5:3b through the routing pipeline.


---

# 36. Phase 05 — Error Classification Status

Phase 05 completed successfully.

Key deliverables implemented:
* Defined standard error enum ErrorCategory (TIMEOUT, RATE_LIMITED, SERVER_ERROR, UPSTREAM_ERROR, CONNECTION_ERROR, AUTH_ERROR, BAD_REQUEST, UNKNOWN) in pp/reliability/error_classifier.py.
* Created structured ClassifiedError model and ErrorClassifier with explicit classification rules based on LiteLLM exception types and HTTP status codes.
* Established retryability classification policy mapping (RETRYABLE_POLICY) without executing retries.
* Integrated ErrorClassifier into LiteLLMService in pp/providers/litellm_client.py and structured ProviderError in pp/core/exceptions.py to prevent raw stack trace leakage.
* Added comprehensive unit test suite in `tests/test_error_classifier.py` testing each category, retryability flags, and status code mappings.
* Verified all existing 33 tests pass and live Ollama inference continues functioning normally.

---

# 37. Phase 06 — Timeout & Retry Status

Phase 06 completed successfully.

Key deliverables implemented:
* Extended application configuration in `app/core/config.py` with provider timeout (`PROVIDER_TIMEOUT=30.0`), retry bounds (`MAX_RETRIES=2`), base and max delays (`RETRY_BASE_DELAY=0.5`, `RETRY_MAX_DELAY=5.0`), and jitter toggle (`RETRY_JITTER=true`).
* Integrated explicit `timeout` parameter into `litellm.acompletion` within `app/providers/litellm_client.py`.
* Created centralized `RetryManager` in `app/reliability/retry.py` implementing exponential backoff with full jitter and a max delay cap, with injectable sleep for clean testing.
* Enforced strict retryability checks via `ErrorClassifier.is_retryable` (`TIMEOUT`, `RATE_LIMITED`, `SERVER_ERROR`, `UPSTREAM_ERROR`, `CONNECTION_ERROR` are retried; `AUTH_ERROR`, `BAD_REQUEST`, `UNKNOWN` fail immediately).
* Retried on the same selected `ProviderTarget` without cross-provider failover.
* Integrated `RetryManager` into `app/api/routes_chat.py` to decouple retry flow from routing and endpoint logic.
* Added comprehensive unit and integration test suite (`tests/test_retry.py`) covering backoff math, jitter bounds, retry exhaustion, non-retryable bypass, and request ID preservation across HTTP route retries (all 45 tests passing).
* Verified live end-to-end inference against local Ollama `qwen2.5:3b`.

---

# 38. Phase 07 — Circuit Breaker Status

Phase 07 completed successfully.

Key deliverables implemented:
* Defined `CircuitState` enum (`CLOSED`, `OPEN`, `HALF_OPEN`) and created `CircuitBreakerManager` in `app/reliability/circuit_breaker.py`.
* Extended application configuration with `CIRCUIT_FAILURE_THRESHOLD=5`, `CIRCUIT_COOLDOWN_SECONDS=30.0`, and `CIRCUIT_HALF_OPEN_MAX_PROBES=1` in `app/core/config.py` and `.env.example`.
* Created dedicated `CircuitBreakerError` (HTTP 503) inheriting from `GatewayError` in `app/core/exceptions.py`.
* Provider-specific circuit isolation ensures unhealthy state on one provider does not impact other providers.
* Integrated error classification filtering: transient provider errors (`TIMEOUT`, `CONNECTION_ERROR`, `RATE_LIMITED`, `SERVER_ERROR`, `UPSTREAM_ERROR`) increment failure count, while client errors (`BAD_REQUEST`, `AUTH_ERROR`) do not trip the circuit breaker.
* Concurrency protection via `asyncio.Lock` per provider guarantees that `HALF_OPEN` state allows only a single probe request, blocking concurrent requests to avoid thundering herds against recovering providers.
* Integrated circuit breaker check and result recording into `app/api/routes_chat.py` upstream of `RetryManager`, ensuring retry attempts for a single request record as one failure event.
* Added comprehensive unit and integration test suite in `tests/test_circuit_breaker.py` covering state transitions (`CLOSED -> OPEN`, `OPEN -> HALF_OPEN`, `HALF_OPEN -> CLOSED`, `HALF_OPEN -> OPEN`), cooldown enforcement, concurrency probe bounds, request ID preservation, and error response formatting (58/58 tests passing across entire suite).
* Verified live end-to-end inference against local Ollama `qwen2.5:3b`.

---

# 39. Phase 08 — Provider Failover Status

Phase 08 completed successfully.

Key deliverables implemented:
* Created centralized `FailoverManager` in `app/reliability/failover.py` to orchestrate multi-provider candidate selection, circuit checking, retry execution, and cross-provider failover.
* Maintained clean architectural separation:
  - **RetryManager:** Retries on the *same* selected provider up to `MAX_RETRIES`.
  - **FailoverManager:** Switches to a *different* eligible candidate provider up to `MAX_FAILOVER_PROVIDERS` (default: 3).
* Enhanced `Router` candidate filtering (`get_candidates` / `select_provider`) to support excluding attempted provider IDs, skipping `OPEN` circuit providers, and enforcing model compatibility constraints without blind fallback.
* Added `priority` integer field (default: 1) to `ProviderTarget` schema in `app/models/provider.py` for deterministic candidate ordering.
* Added `MAX_FAILOVER_PROVIDERS=3` configuration setting in `app/core/config.py` and `.env.example`.
* Integrated error classification filtering for failover eligibility: transient provider errors (`TIMEOUT`, `CONNECTION_ERROR`, `RATE_LIMITED`, `SERVER_ERROR`, `UPSTREAM_ERROR`) trigger failover, whereas client errors (`BAD_REQUEST`, `AUTH_ERROR`) fail immediately without failover.
* Circuit accounting guarantees exactly 1 circuit failure event is recorded per exhausted provider rather than inflating failure counts across retry attempts.
* Integrated `FailoverManager` into `POST /v1/chat/completions` in `app/api/routes_chat.py`.
* Added comprehensive unit and integration test suite in `tests/test_failover.py` (69/69 tests passing across entire test suite).
* Verified live end-to-end inference against local Ollama `qwen2.5:3b` without requiring paid external APIs.

---

# 40. Phase 09 — Redis Operational State Status

Phase 09 completed successfully.

Key deliverables implemented:
* Created async Redis connection manager (`app/storage/redis.py`) managing connection pooling, bounded health checks, and lifecycle shutdown handlers using `redis.asyncio`.
* Defined `CircuitStateStorage` protocol (`app/storage/circuit_storage.py`) providing clean storage abstraction with `RedisCircuitStorage` and `InMemoryCircuitStorage` implementations.
* Implemented distributed, atomic state machine transitions in Redis using server-side Lua scripts (`acquire_permission`, `record_success`, `record_failure`, `release_probe`), guaranteeing multi-worker concurrency safety for `HALF_OPEN` single-probe execution (`CIRCUIT_HALF_OPEN_MAX_PROBES=1`).
* Designed deliberate key schema `llm_gateway:circuit:{provider_id}` storing hash fields (`state`, `consecutive_failures`, `opened_at`, `last_failure_at`, `active_probes`) with auto-recovering TTLs for open circuits (`max(cooldown * 3, 300)`).
* Refactored `CircuitBreakerManager` (`app/reliability/circuit_breaker.py`) to delegate to the storage abstraction with automatic, transparent fallback to local `InMemoryCircuitStorage` on Redis connectivity or timeout errors without leaking internal errors to clients or interrupting LLM completions.
* Updated `GET /ready` in `app/api/routes_health.py` to report Redis connection status (`ready` / `degraded`) with graceful in-memory fallback transparency.
* Added comprehensive unit test suite (`tests/test_circuit_breaker_redis.py`) for storage abstraction, fallback resilience, recovery behavior, probe atomicity, and readiness states without requiring a live Redis server.
* Added live integration test suite (`tests/test_redis_integration.py`) with dynamic availability detection for local Redis daemons (`localhost:6379`).
* Maintained full backwards compatibility across the entire test suite (76 tests collected, 75 passed, 1 skipped when local Redis daemon is offline).
* Verified live end-to-end LLM inference against local Ollama `qwen2.5:3b`.

---

# 41. Phase 10 — Provider Health Tracking Status

Phase 10 completed successfully.

Key deliverables implemented:
* Created `HealthStateStorage` protocol and storage implementations (`RedisHealthStorage`, `InMemoryHealthStorage`) in `app/storage/health_storage.py`.
* Implemented atomic multi-worker Lua script `LUA_RECORD_HEALTH` handling:
  1. Increment `total_requests`.
  2. Increment either `total_successes` or `total_failures`.
  3. Update `last_success_at` or `last_failure_at` with epoch timestamp (`time.time()`).
  4. Update `last_error_category` on failure.
  5. Update `last_latency_ms`.
  6. `LPUSH` latency sample to list `llm_gateway:health:{provider_id}:latencies`.
  7. `LTRIM` to rolling window size (`LATENCY_WINDOW_SIZE = 50`).
  8. Refresh TTL on both Redis keys (`HEALTH_TTL_SECONDS = 604800` / 7 days).
* Created `HealthTracker` service in `app/reliability/health_tracker.py` with dynamic nearest-rank percentile computation ($p50, p95, p99$) and safe success rate calculations ($total\_successes / total\_requests$).
* Established strict per-attempt measurement boundary in `RetryManager.execute_with_retry` (`app/reliability/retry.py`) recording every physical provider attempt around `execute_chat_completion` with `time.perf_counter()`.
* Added observational API endpoint `GET /health/providers` in `app/api/routes_health.py` returning `ProviderHealthSnapshot` models for all configured providers without mutating routing or triggering upstream calls.
* Maintained strict separation: Health tracking is MEASUREMENT only, Circuit Breaker is ENFORCEMENT. Health tracking never opens/closes circuits or modifies routing decisions.
* Ensured fail-safe resilience: Redis health write failures fall back to process-local in-memory storage, log structured warnings, and NEVER mask or replace upstream provider completion results or errors.
* Added unit test suite `tests/test_health_tracker.py` (16 tests) and live Redis integration test suite `tests/test_health_integration.py` (6 tests). All 100 tests passing with 0 skips and clean lint/format/mypy gates.




