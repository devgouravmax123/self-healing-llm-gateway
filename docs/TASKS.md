\# Self-Healing LLM Gateway — Implementation Tasks



\## 1. Purpose



This document converts the project requirements and design into an incremental implementation plan.



The project must be built in small, verifiable phases.



Each phase should:



1\. Have a clear objective.

2\. Implement a limited scope.

3\. Include appropriate tests.

4\. Be manually verified where applicable.

5\. Update documentation if behavior changes.

6\. Leave the project in a working state.



Do not skip directly to later phases.



\---



\# 2. Task Status Legend



Use the following status values:



```text

\[ ] Not started

\[-] In progress

\[x] Completed

\[!] Blocked

```



\---



\# 3. Phase Overview



```text

Phase 01 → Project foundation

Phase 02 → FastAPI API foundation

Phase 03 → LLM provider integration

Phase 04 → Provider registry and routing

Phase 05 → Error classification

Phase 06 → Timeout and retry

Phase 07 → Circuit breaker

Phase 08 → Failover

Phase 09 → Redis operational state

Phase 10 → Health tracking

Phase 11 → PostgreSQL persistence

Phase 12 → Usage and cost tracking

Phase 13 → Authentication and rate limiting

Phase 14 → Observability

Phase 15 → Chaos testing

Phase 16 → Automated testing

Phase 17 → Load testing

Phase 18 → Docker and Nginx

Phase 19 → CI/CD and code quality

Phase 20 → End-to-end reliability demonstration

Phase 21 → Documentation and final polish

```



\---



\# 4. Phase 01 — Project Foundation



\## Objective



Create a clean Python project with reproducible development tooling.



\## Tasks



\* \[x] Create project directory structure.

\* \[x] Use Python 3.12.

\* \[x] Initialize project with `uv`.

\* \[x] Create virtual environment.

\* \[x] Create `pyproject.toml`.

\* \[x] Configure Ruff.

\* \[x] Configure mypy.

\* \[x] Configure pytest.

\* \[x] Configure pytest-asyncio.

\* \[x] Configure pre-commit.

\* \[x] Create `.gitignore`.

\* \[x] Create `.env.example`.

\* \[x] Create basic `README.md`.

\* \[x] Initialize Git repository if not already initialized.



\## Verification



Run:



```text

python --version

uv --version

pytest

ruff check .

mypy .

```



Expected result:



\* Environment works.

\* Tools execute.

\* No unexpected project errors.



\---



\# 5. Phase 02 — FastAPI API Foundation



\## Objective



Create the basic gateway API.



\## Tasks



\* \[x] Create `app/main.py`.

\* \[x] Create application factory or clear application initialization.

\* \[x] Add `GET /health`.

\* \[x] Add `GET /ready`.

\* \[x] Add `POST /v1/chat/completions`.

\* \[x] Create Pydantic request models.

\* \[x] Create Pydantic response models.

\* \[x] Add request validation.

\* \[x] Add basic error handling.

\* \[x] Add request ID generation/propagation.

\* \[x] Add API documentation through FastAPI.



\## Verification



Test:



```text

GET /health

GET /ready

POST /v1/chat/completions

```



Verify:



\* Valid requests are accepted.

\* Invalid requests are rejected.

\* `/docs` works.

\* Request IDs are generated.



\---



\# 6. Phase 03 — LLM Provider Integration



\## Objective



Connect the gateway to an actual local LLM.



\## Tasks



\* \[ ] Install LiteLLM.

\* \[ ] Install/configure Ollama.

\* \[ ] Run at least one local model.

\* \[ ] Create provider client abstraction.

\* \[ ] Implement a basic LiteLLM completion call.

\* \[ ] Normalize provider responses.

\* \[ ] Extract token usage when available.

\* \[ ] Measure provider latency.

\* \[ ] Handle provider exceptions.



\## Verification



Flow:



```text

Client

&#x20; ↓

FastAPI

&#x20; ↓

LiteLLM

&#x20; ↓

Ollama

&#x20; ↓

LLM response

&#x20; ↓

Client

```



The gateway must successfully return a real model response.



\---



\# 7. Phase 04 — Provider Registry and Routing



\## Objective



Support multiple provider/model targets.



\## Tasks



\* \[ ] Create `ProviderTarget`.

\* \[ ] Create provider registry.

\* \[ ] Configure multiple local provider/model targets.

\* \[ ] Implement provider enable/disable state.

\* \[ ] Implement provider priority.

\* \[ ] Implement candidate selection.

\* \[ ] Create routing policy.

\* \[ ] Ensure routing logic is independently testable.



\## Verification



Example:



```text

Provider A → priority 1

Provider B → priority 2

Provider C → priority 3

```



The router should select the first eligible provider.



\---



\# 8. Phase 05 — Error Classification



\## Objective



Convert provider-specific errors into normalized gateway errors.



\## Tasks



\* \[x] Create internal gateway error model.

\* \[x] Create error classifier.

\* \[x] Implement classification for:



&#x20; \* \[x] TIMEOUT

&#x20; \* \[x] NETWORK\_ERROR

&#x20; \* \[x] SERVER\_ERROR

&#x20; \* \[x] RATE\_LIMIT

&#x20; \* \[x] AUTH\_ERROR

&#x20; \* \[x] BAD\_REQUEST

&#x20; \* \[x] CONTENT\_FILTER

&#x20; \* \[x] UNKNOWN

\* \[x] Add retryable/non-retryable property.

\* \[x] Preserve useful provider error information.

\* \[x] Add unit tests.



\## Verification



Given different simulated exceptions, the classifier should return the correct normalized error type.



\---



\# 9. Phase 06 — Timeout and Retry



\## Objective



Prevent slow or temporary provider failures from unnecessarily failing requests.



\## Tasks



\* \[x] Add configurable provider timeout.

\* \[x] Convert timeout exceptions to `TIMEOUT`.

\* \[x] Implement retry manager.

\* \[x] Define retryable errors.

\* \[x] Add maximum retry count.

\* \[x] Implement exponential backoff.

\* \[x] Add jitter.

\* \[x] Add maximum backoff.

\* \[x] Track retry attempts.

\* \[x] Prevent infinite retries.



\## Verification



Test:



```text

Provider fails

&#x20;   ↓

Retry

&#x20;   ↓

Provider succeeds

&#x20;   ↓

Request succeeds

```



Also test:



```text

Provider keeps failing

&#x20;   ↓

Maximum retries reached

&#x20;   ↓

Retry stops

```



\---



\# 10. Phase 07 — Circuit Breaker



\## Objective



Prevent repeated requests to an unhealthy provider.



\## Tasks



\* \[x] Implement circuit breaker abstraction.

\* \[x] Implement `CLOSED`.

\* \[x] Implement `OPEN`.

\* \[x] Implement `HALF\_OPEN`.

\* \[x] Add configurable failure threshold.

\* \[x] Add configurable cooldown period.

\* \[x] Implement one-at-a-time half-open probe.

\* \[x] Implement successful recovery.

\* \[x] Implement failed recovery.

\* \[x] Add circuit transition events.

\* \[x] Add unit tests for every transition.



\## Required state transitions



```text

CLOSED

&#x20; ↓ repeated qualifying failures

OPEN

&#x20; ↓ cooldown

HALF\_OPEN

&#x20; ↓ successful probe

CLOSED

```



Failure during probe:



```text

HALF\_OPEN

&#x20; ↓ failed probe

OPEN

```



\## Verification



Demonstrate the complete state lifecycle.



\---



\# 11. Phase 08 — Failover



\## Objective



Automatically switch to another eligible provider when a provider cannot serve a request.



\## Tasks



\* \[x] Create failover manager.

\* \[x] Track provider attempts per request.

\* \[x] Skip OPEN circuits.

\* \[x] Skip already-attempted providers where appropriate.

\* \[x] Apply retry policy.

\* \[x] Apply total attempt limit.

\* \[x] Select next eligible provider.

\* \[x] Record failover events.

\* \[x] Return successful response from replacement provider.



\## Verification



Demonstrate:



```text

Provider A

&#x20;  ↓

Failure

&#x20;  ↓

Provider B

&#x20;  ↓

Success

```



The client should receive the successful response.



\---



\# 12. Phase 09 — Redis Operational State



\## Objective



Move shared operational state from process memory to Redis.



## Tasks

* [x] Add Redis dependency.
* [x] Create Redis connection module.
* [x] Add configuration for Redis URL.
* [x] Store circuit state in Redis.
* [x] Store failure counters in Redis.
* [ ] Store rate-limit counters in Redis. (Scheduled for Phase 13)
* [x] Implement Redis key conventions (`llm_gateway:circuit:{provider_id}`).
* [x] Add TTLs where appropriate.
* [x] Handle Redis connection failures with graceful in-memory fallback.
* [x] Consider concurrency/atomicity for state changes (atomic Lua scripts).
* [x] Add Redis unit and integration tests.



\## Initial key patterns



```text

circuit:{provider\_id}

health:{provider\_id}

ratelimit:{tenant\_id}

```



\## Verification



Restart the gateway and verify that appropriate operational state remains available from Redis.



\---



\# 13. Phase 10 — Provider Health Tracking



\## Objective



Create measurable provider health information.



\## Tasks



\* \[x] Create health tracker.

\* \[x] Track total requests.

\* \[x] Track successful requests.

\* \[x] Track failed requests.

\* \[x] Track error categories.

\* \[x] Track latency.

\* \[x] Calculate success rate.

\* \[x] Calculate failure rate.

\* \[x] Calculate p50 latency.

\* \[x] Calculate p95 latency.

\* \[x] Calculate p99 latency.

\* \[x] Track last success.

\* \[x] Track last failure.

\* \[x] Track failover count.

\* \[x] Define rolling health window.



\## Verification



Generate successful and failed requests and verify health statistics change accordingly.



\---



\# 14. Phase 11 — PostgreSQL Persistence



\## Objective



Add durable storage for historical information.



\## Tasks



\* \[ ] Add PostgreSQL.

\* \[ ] Add SQLAlchemy 2.x.

\* \[ ] Add async database support.

\* \[ ] Configure Alembic.

\* \[ ] Create database models.

\* \[ ] Create tenant table.

\* \[ ] Create request table.

\* \[ ] Create usage table.

\* \[ ] Create provider event table.

\* \[ ] Create audit/event history where needed.

\* \[ ] Create initial migration.

\* \[ ] Add repository/data-access layer.

\* \[ ] Add database integration tests.



\## Verification



Create records, restart the application, and verify durable records remain available.



\---



\# 15. Phase 12 — Usage and Cost Tracking



\## Objective



Track token usage and estimated cost.



\## Tasks



\* \[ ] Create usage tracker.

\* \[ ] Extract input tokens.

\* \[ ] Extract output tokens.

\* \[ ] Calculate total tokens.

\* \[ ] Create cost calculator.

\* \[ ] Make pricing configuration-driven.

\* \[ ] Support local Ollama cost representation.

\* \[ ] Record usage by tenant.

\* \[ ] Record usage by feature.

\* \[ ] Record usage by provider.

\* \[ ] Record usage by model.

\* \[ ] Store durable usage records.

\* \[ ] Add usage metrics.



\## Important rule



Never invent token usage or pricing.



If data is unavailable:



```text

null / unknown

```



should be preferred over fabricated values.



\---



\# 16. Phase 13 — Authentication and Rate Limiting



\## Objective



Introduce tenant-aware access control and traffic control.



\## Tasks



\### Authentication



\* \[ ] Define API-key format.

\* \[ ] Implement authentication middleware/dependency.

\* \[ ] Resolve authenticated tenant.

\* \[ ] Protect admin endpoints.

\* \[ ] Avoid storing plaintext secrets where possible.

\* \[ ] Add authentication tests.



\### Rate Limiting



\* \[ ] Choose initial Redis-backed algorithm.

\* \[ ] Implement tenant rate limits.

\* \[ ] Return controlled HTTP 429 responses.

\* \[ ] Record rate-limit events.

\* \[ ] Add tests.

\* \[ ] Test concurrent requests.



\## Verification



Verify that:



```text

Allowed requests → continue

Exceeded limit → HTTP 429

```



\---



\# 17. Phase 14 — Observability



\## Objective



Make system behavior measurable and debuggable.



\---



\## 17.1 Structured Logging



Tasks:



\* \[ ] Create structured JSON logger.

\* \[ ] Add request ID.

\* \[ ] Add tenant ID.

\* \[ ] Add feature.

\* \[ ] Add provider.

\* \[ ] Add model.

\* \[ ] Add event type.

\* \[ ] Add error type.

\* \[ ] Add latency.

\* \[ ] Add attempt number.

\* \[ ] Avoid sensitive data.



\---



\## 17.2 Prometheus



Tasks:



\* \[ ] Add Prometheus instrumentation.

\* \[ ] Add request counters.

\* \[ ] Add request duration.

\* \[ ] Add provider request counters.

\* \[ ] Add provider errors.

\* \[ ] Add retries.

\* \[ ] Add failovers.

\* \[ ] Add circuit transitions.

\* \[ ] Add token usage.

\* \[ ] Add estimated cost.

\* \[ ] Expose metrics endpoint.



\---



\## 17.3 Grafana



Tasks:



\* \[ ] Add Grafana.

\* \[ ] Configure Prometheus data source.

\* \[ ] Create request dashboard.

\* \[ ] Create latency dashboard.

\* \[ ] Create provider health dashboard.

\* \[ ] Create reliability dashboard.

\* \[ ] Create usage/cost dashboard.

\* \[ ] Visualize circuit states.



\---



\## 17.4 OpenTelemetry



Tasks:



\* \[ ] Add OpenTelemetry.

\* \[ ] Create request root span.

\* \[ ] Trace routing.

\* \[ ] Trace provider attempts.

\* \[ ] Trace retries.

\* \[ ] Trace failovers.

\* \[ ] Trace usage recording.

\* \[ ] Correlate traces with request IDs.

\* \[ ] Avoid sensitive prompt/response content.



\---



\# 18. Phase 15 — Chaos Testing



\## Objective



Provide controlled failure injection to demonstrate self-healing behavior.



\## Tasks



\* \[ ] Create fault injection module.

\* \[ ] Support simulated timeout.

\* \[ ] Support simulated 500 error.

\* \[ ] Support simulated 429 error.

\* \[ ] Support artificial latency.

\* \[ ] Support network-like failure.

\* \[ ] Restrict chaos functionality to development/testing.

\* \[ ] Keep chaos disabled by default.

\* \[ ] Protect chaos controls.

\* \[ ] Record chaos events.

\* \[ ] Add automated chaos tests.



\## Verification



Run:



```text

Inject failure

&#x20;   ↓

Provider fails

&#x20;   ↓

Error classification

&#x20;   ↓

Retry

&#x20;   ↓

Circuit behavior

&#x20;   ↓

Failover

&#x20;   ↓

Successful replacement provider

```



\---



\# 19. Phase 16 — Automated Testing



\## Objective



Build a reliable automated test suite.



\## Unit tests



\* \[ ] Request models.

\* \[ ] Error classifier.

\* \[ ] Retry manager.

\* \[ ] Backoff.

\* \[ ] Circuit breaker.

\* \[ ] Router.

\* \[ ] Failover manager.

\* \[ ] Health calculations.

\* \[ ] Cost calculator.

\* \[ ] Rate limiter.



\## Integration tests



\* \[ ] FastAPI + Redis.

\* \[ ] FastAPI + PostgreSQL.

\* \[ ] Gateway + LiteLLM.

\* \[ ] Gateway + Ollama.

\* \[ ] Full request lifecycle.



\## Failure tests



\* \[ ] Timeout.

\* \[ ] Network failure.

\* \[ ] 429.

\* \[ ] 500.

\* \[ ] Repeated failures.

\* \[ ] Circuit opening.

\* \[ ] Half-open probe.

\* \[ ] Recovery.

\* \[ ] Failover.

\* \[ ] All providers unavailable.



\## Quality gates



\* \[ ] pytest passes.

\* \[ ] Ruff passes.

\* \[ ] mypy passes.



\---



\# 20. Phase 17 — Load Testing



\## Objective



Measure gateway behavior under controlled load.



\## Tasks



\* \[ ] Install/configure k6.

\* \[ ] Create baseline load test.

\* \[ ] Test healthy provider.

\* \[ ] Test multiple providers.

\* \[ ] Test provider failure under load.

\* \[ ] Measure RPS.

\* \[ ] Measure p50.

\* \[ ] Measure p95.

\* \[ ] Measure p99.

\* \[ ] Measure error rate.

\* \[ ] Measure retry rate.

\* \[ ] Measure failover rate.

\* \[ ] Measure gateway overhead.

\* \[ ] Record test environment.



\## Important



Do not write performance claims before running the tests.



\---



\# 21. Phase 18 — Docker and Nginx



\## Objective



Make the entire local system reproducible.



\## Tasks



\* \[ ] Create Dockerfile.

\* \[ ] Create Docker Compose configuration.

\* \[ ] Containerize gateway.

\* \[ ] Add Redis service.

\* \[ ] Add PostgreSQL service.

\* \[ ] Add Ollama service.

\* \[ ] Add Prometheus service.

\* \[ ] Add Grafana service.

\* \[ ] Add Nginx service.

\* \[ ] Configure service networking.

\* \[ ] Configure health checks.

\* \[ ] Configure persistent volumes where appropriate.

\* \[ ] Configure environment variables.

\* \[ ] Verify clean startup.



\## Target local stack



```text

nginx

gateway

redis

postgres

ollama

prometheus

grafana

```



\---



\# 22. Phase 19 — CI/CD and Code Quality



\## Objective



Automate basic project quality checks.



\## Tasks



\* \[ ] Create GitHub Actions workflow.

\* \[ ] Install dependencies.

\* \[ ] Run Ruff.

\* \[ ] Run mypy.

\* \[ ] Run pytest.

\* \[ ] Add test coverage reporting if useful.

\* \[ ] Configure pre-commit.

\* \[ ] Ensure secrets are not committed.

\* \[ ] Verify CI from a clean environment.



\---



\# 23. Phase 20 — End-to-End Reliability Demonstration



\## Objective



Demonstrate the project's central capability.



The final demonstration should show:



\### Scenario A — Healthy request



```text

Client

&#x20;↓

Gateway

&#x20;↓

Provider A

&#x20;↓

Success

```



\---



\### Scenario B — Provider failure



```text

Client

&#x20;↓

Gateway

&#x20;↓

Provider A

&#x20;↓

Timeout

&#x20;↓

Retry

&#x20;↓

Failure

&#x20;↓

Failover

&#x20;↓

Provider B

&#x20;↓

Success

```



\---



\### Scenario C — Circuit opens



Repeated provider failures:



```text

Provider A

&#x20;↓

Failure

&#x20;↓

Failure

&#x20;↓

Failure

&#x20;↓

Failure

&#x20;↓

Threshold reached

&#x20;↓

Circuit OPEN

```



New requests should avoid Provider A.



\---



\### Scenario D — Provider recovery



```text

Circuit OPEN

&#x20;    ↓

Cooldown

&#x20;    ↓

HALF\_OPEN

&#x20;    ↓

Probe

&#x20;    ↓

Success

&#x20;    ↓

CLOSED

```



Normal traffic can resume.



\---



\### Scenario E — All providers unavailable



```text

Provider A → failure

Provider B → failure

Provider C → failure

&#x20;       ↓

No eligible provider

&#x20;       ↓

Controlled gateway error

```



No infinite retries or loops should occur.



\---



\# 24. Phase 21 — Documentation and Final Polish



\## Objective



Make the project understandable to another engineer.



\## Tasks



\* \[ ] Complete README.

\* \[ ] Explain architecture.

\* \[ ] Explain local setup.

\* \[ ] Explain Docker Compose.

\* \[ ] Explain configuration.

\* \[ ] Explain API usage.

\* \[ ] Explain circuit breaker.

\* \[ ] Explain retry/failover.

\* \[ ] Explain observability.

\* \[ ] Explain chaos testing.

\* \[ ] Explain load testing.

\* \[ ] Add architecture diagram.

\* \[ ] Add example requests.

\* \[ ] Add example responses.

\* \[ ] Add example failure scenario.

\* \[ ] Add Grafana screenshots if useful.

\* \[ ] Document actual benchmark results.

\* \[ ] Document limitations.

\* \[ ] Review security documentation.

\* \[ ] Review `DECISIONS.md`.

\* \[ ] Remove unused code/dependencies.

\* \[ ] Run complete test suite.

\* \[ ] Perform clean local setup from repository.



\---



\# 25. Cross-Phase Requirements



The following requirements apply throughout the project.



\## Testing



Every major feature must have appropriate tests.



\## Observability



Important reliability behavior should be observable.



\## Security



Secrets and sensitive information must be protected.



\## Documentation



Important behavioral or architectural changes must be documented.



\## Reproducibility



The project should remain runnable from a clean environment.



\## Simplicity



Do not introduce infrastructure without a demonstrated need.



\---



\# 26. Suggested Milestones



\## Milestone 1 — Basic Gateway



Complete:



```text

Phase 01

Phase 02

Phase 03

```



Result:



```text

Client → FastAPI → LiteLLM → Ollama

```



A real LLM response works.



\---



\## Milestone 2 — Reliability Core



Complete:



```text

Phase 04

Phase 05

Phase 06

Phase 07

Phase 08

```



Result:



```text

Routing

\+

Error handling

\+

Retry

\+

Timeout

\+

Circuit breaker

\+

Failover

```



This is the core of the project.



\---



\## Milestone 3 — Shared State and Persistence



Complete:



```text

Phase 09

Phase 10

Phase 11

Phase 12

```



Result:



```text

Redis

\+

Health tracking

\+

PostgreSQL

\+

Usage/cost

```



\---



\## Milestone 4 — Production-Like Controls



Complete:



```text

Phase 13

Phase 14

```



Result:



```text

Authentication

\+

Rate limiting

\+

Logs

\+

Metrics

\+

Dashboards

\+

Tracing

```



\---



\## Milestone 5 — Validation



Complete:



```text

Phase 15

Phase 16

Phase 17

```



Result:



```text

Chaos testing

\+

Automated testing

\+

Load testing

```



\---



\## Milestone 6 — Deployment



Complete:



```text

Phase 18

Phase 19

```



Result:



```text

Docker Compose

\+

Nginx

\+

CI

```



\---



\## Milestone 7 — Final Demonstration



Complete:



```text

Phase 20

Phase 21

```



Result:



```text

A reproducible, observable,

self-healing LLM gateway

with documented failure recovery.

```



\---



\# 27. Current Starting Point



Initial project state:



```text

\[ ] Phase 01 — Project foundation

\[-] Phase 02 — FastAPI API foundation

\[ ] Phase 03 — LLM provider integration

\[ ] Phase 04 — Provider registry and routing

\[ ] Phase 05 — Error classification

\[ ] Phase 06 — Timeout and retry

\[ ] Phase 07 — Circuit breaker

\[ ] Phase 08 — Failover

\[ ] Phase 09 — Redis operational state

\[x] Phase 10 — Provider health tracking

\[ ] Phase 11 — PostgreSQL persistence

\[ ] Phase 12 — Usage and cost tracking

\[ ] Phase 13 — Authentication and rate limiting

\[ ] Phase 14 — Observability

\[ ] Phase 15 — Chaos testing

\[ ] Phase 16 — Automated testing

\[ ] Phase 17 — Load testing

\[ ] Phase 18 — Docker and Nginx

\[ ] Phase 19 — CI/CD and code quality

\[ ] Phase 20 — End-to-end reliability demonstration

\[ ] Phase 21 — Documentation and final polish

```



The status of Phase 02 should be updated based on actual implementation rather than assumed completion.



\---



\# 28. Task Execution Rule



Only work on the current phase unless there is a clear dependency requiring another phase.



For each phase:



```text

Read documentation

&#x20;     ↓

Implement small task

&#x20;     ↓

Run tests

&#x20;     ↓

Verify behavior

&#x20;     ↓

Update task status

&#x20;     ↓

Commit changes

&#x20;     ↓

Move forward

```



Do not mark a task `\[x]` merely because code has been written.



It should be marked complete only after the relevant verification has succeeded.



\---



\# 29. Final Definition of Done



The project is complete when:



\* \[ ] Core API works.

\* \[ ] Multiple provider/model targets work.

\* \[ ] Provider failures are detected.

\* \[ ] Errors are classified.

\* \[ ] Retries are bounded.

\* \[ ] Timeouts are enforced.

\* \[ ] Circuit breaker works.

\* \[ ] HALF\_OPEN recovery works.

\* \[ ] Failover works.

\* \[ ] Redis stores shared operational state.

\* \[ ] PostgreSQL stores durable records.

\* \[ ] Health metrics are measurable.

\* \[ ] Token usage is tracked when available.

\* \[ ] Cost tracking is explicit.

\* \[ ] Authentication works.

\* \[ ] Rate limiting works.

\* \[ ] Structured logging works.

\* \[ ] Prometheus metrics work.

\* \[ ] Grafana dashboards work.

\* \[ ] OpenTelemetry tracing works.

\* \[ ] Chaos testing works safely.

\* \[ ] Unit tests pass.

\* \[ ] Integration tests pass.

\* \[ ] Failure tests pass.

\* \[ ] Load tests have been executed.

\* \[ ] Docker Compose works.

\* \[ ] Nginx works.

\* \[ ] CI works.

\* \[ ] Documentation is complete.

\* \[ ] No secrets are committed.

\* \[ ] No unsupported performance claims exist.

\* \[ ] The core project runs locally without mandatory paid services.



\---



\# 30. Most Important Project Demonstration



The final project should make this sequence easy to demonstrate:



```text

&#x20;                NORMAL

&#x20;                  │

&#x20;                  ▼

&#x20;             Provider A

&#x20;                  │

&#x20;                FAIL

&#x20;                  │

&#x20;                  ▼

&#x20;             RETRY / ERROR

&#x20;              CLASSIFICATION

&#x20;                  │

&#x20;                  ▼

&#x20;            CIRCUIT OPEN

&#x20;                  │

&#x20;                  ▼

&#x20;             FAILOVER

&#x20;                  │

&#x20;                  ▼

&#x20;             Provider B

&#x20;                  │

&#x20;               SUCCESS

&#x20;                  │

&#x20;                  ▼

&#x20;             CLIENT





Later...



&#x20;             Provider A

&#x20;                  ▲

&#x20;                  │

&#x20;             HALF\_OPEN

&#x20;                  ▲

&#x20;                  │

&#x20;               COOLDOWN

&#x20;                  ▲

&#x20;                  │

&#x20;               OPEN

&#x20;                  │

&#x20;             Probe succeeds

&#x20;                  │

&#x20;                  ▼

&#x20;               CLOSED

```



The project should demonstrate not merely that an LLM request works, but that the gateway can \*\*detect failure, protect the failing provider, recover, and continue serving traffic through another provider while making the behavior observable\*\*.



