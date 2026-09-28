\# Product Requirements Document (PRD)



\## 1. Product Name



\*\*Self-Healing LLM Gateway\*\*



\---



\## 2. Product Summary



The Self-Healing LLM Gateway is a local-first, OpenAI-compatible gateway that sits between applications and Large Language Model (LLM) providers.



Its primary purpose is to make LLM applications more \*\*reliable, observable, fault-tolerant, and cost-aware\*\*.



Instead of an application communicating directly with a single LLM provider:



```text

Application

&#x20;   ↓

LLM Provider

```



the application communicates with our gateway:



```text

Application

&#x20;   ↓

Self-Healing LLM Gateway

&#x20;   ↓

Multiple LLM Providers / Models

```



The gateway monitors provider health and automatically reacts to failures.



When a provider becomes unhealthy, the gateway should:



1\. Detect the failure.

2\. Prevent unnecessary traffic from continuing to reach the unhealthy provider.

3\. Open the provider's circuit breaker when configured thresholds are exceeded.

4\. Route requests to another healthy provider/model.

5\. Periodically test the failed provider.

6\. Automatically return the provider to normal service when it recovers.



The entire project must be capable of running locally with \*\*zero mandatory paid services or API costs\*\*.



\---



\# 3. Problem Statement



Modern applications increasingly depend on LLM APIs for chat, summarization, coding assistance, classification, and other AI functionality.



Depending directly on a single provider introduces several reliability problems:



\* Provider outages

\* Temporary server errors

\* Rate limiting

\* Network failures

\* High latency

\* Provider-specific failures

\* Model unavailability

\* API timeouts

\* Sudden degradation in response latency



A simple application may behave like this:



```text

Application

&#x20;   ↓

Provider A

&#x20;   ↓

Provider A fails

&#x20;   ↓

Application fails

```



The gateway should instead provide resilience:



```text

Application

&#x20;   ↓

Gateway

&#x20;   ↓

Provider A ── failure ──X

&#x20;   ↓

Provider B ── success ──→ Response

```



The gateway should also avoid repeatedly sending requests to a known unhealthy provider.



Therefore, the system needs health monitoring, timeout handling, controlled retries, circuit breaking, failover, observability, and shared state.



\---



\# 4. Product Goals



\## 4.1 Primary Goals



The system must:



\* Provide an OpenAI-compatible API interface.

\* Accept LLM requests from client applications.

\* Route requests through a centralized gateway.

\* Support multiple LLM providers/models through LiteLLM.

\* Support local LLM inference using Ollama.

\* Detect provider failures.

\* Classify provider errors.

\* Track provider health.

\* Implement configurable circuit breakers.

\* Support CLOSED, OPEN, and HALF-OPEN circuit states.

\* Automatically fail over to healthy providers/models.

\* Use timeouts for upstream requests.

\* Implement controlled retries for retryable failures.

\* Use exponential backoff with jitter.

\* Maintain important operational state in Redis.

\* Store durable usage/cost information in PostgreSQL.

\* Expose metrics for Prometheus.

\* Provide operational dashboards through Grafana.

\* Support distributed tracing through OpenTelemetry.

\* Provide structured logs.

\* Provide reproducible fault/chaos injection.

\* Support load testing using k6.

\* Provide automated tests.

\* Run locally using Docker Compose.

\* Remain usable without mandatory paid cloud services.



\---



\# 5. Secondary Goals



The system should also support:



\* Multi-tenant request attribution.

\* Feature-level usage attribution.

\* Request IDs/correlation IDs.

\* Per-tenant rate limits.

\* Per-tenant usage tracking.

\* Token usage tracking.

\* Cost calculation.

\* Provider/model-level latency tracking.

\* Provider/model-level error rates.

\* Configurable routing preferences.

\* Graceful handling when all providers are unhealthy.



These features should be introduced incrementally after the core gateway is stable.



\---



\# 6. Non-Goals



The first production-style version will NOT attempt to become:



\* A general-purpose Kubernetes platform.

\* A complete cloud infrastructure platform.

\* A model training system.

\* A model hosting platform.

\* An LLM evaluation platform.

\* A full AI agent framework.

\* A replacement for LiteLLM.

\* A replacement for Prometheus or Grafana.

\* A large-scale distributed system requiring dozens of microservices.



The project should prioritize \*\*correctness, reliability, observability, and explainability\*\* over unnecessary complexity.



\---



\# 7. Target Users



\## 7.1 Application Developer



A developer should be able to point an existing OpenAI-compatible client at the gateway instead of directly calling a provider.



Example:



```text

Application

&#x20;   ↓

http://localhost:<gateway-port>/v1/chat/completions

&#x20;   ↓

Gateway

&#x20;   ↓

LLM Provider

```



The application should not need to understand the gateway's internal routing logic.



\---



\## 7.2 Infrastructure / Platform Engineer



The gateway should provide enough operational information for an engineer to answer questions such as:



\* Which provider is currently unhealthy?

\* What is its error rate?

\* What is its p95 latency?

\* How many requests failed over?

\* Which circuit breakers are open?

\* How long has a provider been unhealthy?

\* How much traffic is each provider receiving?

\* Which tenant generated the most usage?

\* What happened to a particular request?



\---



\## 7.3 Project Reviewer / Interviewer



A reviewer should be able to reproduce a provider failure and observe the gateway responding automatically.



The demonstration should show:



```text

Healthy

&#x20;  ↓

Provider failure

&#x20;  ↓

Health degradation

&#x20;  ↓

Circuit breaker opens

&#x20;  ↓

Traffic fails over

&#x20;  ↓

Provider recovery

&#x20;  ↓

HALF-OPEN probe

&#x20;  ↓

Circuit closes

&#x20;  ↓

Normal routing resumes

```



\---



\# 8. Core User Flow



\## 8.1 Normal Request



```text

Client

&#x20; ↓

Gateway

&#x20; ↓

Validate request

&#x20; ↓

Determine routing policy

&#x20; ↓

Select healthy provider/model

&#x20; ↓

LiteLLM

&#x20; ↓

Provider

&#x20; ↓

Response

&#x20; ↓

Client

```



\---



\# 9. Failure User Flow



When a provider fails:



```text

Client

&#x20; ↓

Gateway

&#x20; ↓

Provider A

&#x20; ↓

Failure

&#x20; ↓

Classify error

&#x20; ↓

Update health state

&#x20; ↓

Determine whether retry is appropriate

&#x20; ↓

If provider remains unhealthy:

Circuit breaker → OPEN

&#x20; ↓

Select Provider B

&#x20; ↓

Response

```



\---



\# 10. Circuit Breaker Requirements



Each provider/model route must have a circuit breaker.



The circuit breaker has three states:



\### CLOSED



Normal operation.



Requests are allowed.



```text

Gateway → Provider A

```



\### OPEN



The provider is considered unhealthy.



Normal requests must not be sent to the provider.



```text

Gateway ──X──> Provider A

```



Requests should be routed to another eligible provider/model when possible.



\### HALF-OPEN



After a configurable cooldown period, the gateway should allow a controlled recovery probe.



If the probe succeeds:



```text

HALF-OPEN → CLOSED

```



If the probe fails:



```text

HALF-OPEN → OPEN

```



The gateway must prevent an unlimited number of normal requests from flooding a provider during HALF-OPEN.



\---



\# 11. Provider Health Requirements



The gateway should maintain provider/model health information.



The health system should track at minimum:



\* Total requests

\* Successful requests

\* Failed requests

\* Error categories

\* Success rate

\* Failure rate

\* Request latency

\* p50 latency

\* p95 latency

\* p99 latency

\* Circuit state

\* Last failure time

\* Last successful request

\* Failover count



Health calculations should use configurable rolling/sliding windows where appropriate.



Example:



```text

Provider A



Requests:       100

Successes:       92

Failures:         8

Success rate:    92%

p95 latency:   2.4 sec

Circuit:       CLOSED

```



\---



\# 12. Error Classification



Errors should be classified into meaningful categories.



Initial categories:



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



The gateway must not treat every error identically.



For example:



\* A timeout may be retryable.

\* A temporary server error may be retryable.

\* A rate limit may require backoff or failover.

\* An authentication error generally should not trigger blind retries.

\* A malformed request should normally be returned to the client instead of failing over.



The exact retry/failover policy will be defined in `DESIGN.md`.



\---



\# 13. Retry Requirements



Retries must be controlled.



The gateway must:



\* Have a configurable maximum retry count.

\* Apply request timeouts.

\* Retry only eligible error categories.

\* Use exponential backoff.

\* Add jitter.

\* Avoid retry storms.

\* Record retry attempts in logs and metrics.



Example:



```text

Attempt 1

&#x20;  ↓

failure

&#x20;  ↓

backoff + jitter

&#x20;  ↓

Attempt 2

&#x20;  ↓

failure

&#x20;  ↓

backoff + jitter

&#x20;  ↓

Failover / final failure

```



Retries must not continue indefinitely.



\---



\# 14. Failover Requirements



When the preferred provider cannot serve a request, the gateway should select another eligible provider/model.



Example:



```text

Preference:



1\. Provider A

2\. Provider B

3\. Provider C

```



If:



```text

Provider A → OPEN

Provider B → CLOSED

Provider C → CLOSED

```



the request should be routed to Provider B.



The gateway should record the failover event.



Example:



```text

request\_id=req\_123

original\_provider=provider\_a

fallback\_provider=provider\_b

reason=TIMEOUT

```



\---



\# 15. Local-First Requirement



The project must be fully demonstrable without mandatory paid LLM APIs.



Primary development path:



```text

FastAPI

&#x20;  ↓

LiteLLM

&#x20;  ↓

Ollama

&#x20;  ↓

Local open-source model

```



The architecture should remain provider-agnostic so optional external providers can be added later.



No feature should require a paid API for the core project demonstration.



\---



\# 16. State Management Requirements



Redis will be used for rapidly changing shared gateway state, including appropriate:



\* Provider health information

\* Circuit breaker state

\* Rolling-window counters

\* Rate-limit state

\* Temporary coordination data



PostgreSQL will be used for durable information, including appropriate:



\* Tenants

\* Request/usage records

\* Token usage

\* Cost records

\* Audit information

\* Historical operational data where required



The system must clearly distinguish temporary operational state from durable application data.



\---



\# 17. Observability Requirements



The gateway must provide three major observability signals:



\### Logs



Answer:



> What happened?



Logs should include fields such as:



```text

timestamp

level

request\_id

tenant\_id

provider

model

event

latency

error\_type

circuit\_state

```



\### Metrics



Answer:



> How often/how much did it happen?



Metrics should include:



\* Request rate

\* Success rate

\* Error rate

\* Provider latency

\* p50/p95/p99 latency

\* Retry count

\* Failover count

\* Circuit state

\* Provider availability

\* Queue depth where applicable

\* Token usage

\* Cost



\### Traces



Answer:



> Where did this request spend its time?



OpenTelemetry should provide request-level tracing and correlation.



\---



\# 18. Cost Tracking Requirements



The gateway should attribute LLM usage to:



```text

tenant

feature

request

provider

model

```



Where token information and pricing information are available, the gateway should calculate estimated cost.



Example:



```text

Tenant:       tenant\_123

Feature:      summarization

Provider:     provider\_a

Model:        model\_x

Input tokens: 1200

Output tokens: 500

Estimated cost: ...

```



Cost figures must be clearly identified as estimates when provider pricing or local-model economics make exact billing unavailable.



\---



\# 19. Multi-Tenancy Requirements



Requests should support metadata such as:



```text

tenant\_id

feature

request\_id

```



Example:



```json

{

&#x20; "tenant\_id": "tenant\_123",

&#x20; "feature": "chat",

&#x20; "request\_id": "req\_abc123"

}

```



The system should use this metadata for:



\* Usage attribution

\* Cost attribution

\* Rate limiting

\* Observability

\* Debugging



Tenant isolation must be considered when storing and exposing data.



\---



\# 20. Rate Limiting



The gateway should support configurable rate limiting.



Initial implementation may use Redis.



The design should allow limits to be applied by:



\* Tenant

\* API key

\* Request class



Rate limiting must return an appropriate HTTP response when a limit is exceeded.



\---



\# 21. Chaos / Fault Injection



The project must include a controlled mechanism for demonstrating failure behavior.



It should be possible to simulate conditions such as:



```text

Provider unavailable

Provider timeout

Provider 429

Provider 500

Artificial latency

High failure rate

```



Fault injection must be:



\* Explicitly enabled.

\* Clearly identifiable.

\* Restricted to development/testing environments.

\* Disabled by default.

\* Impossible to accidentally expose as a production endpoint without configuration.



\---



\# 22. Load Testing



The system should be load tested using k6.



Tests should measure:



\* Requests per second

\* Latency

\* Error rate

\* Failover behavior

\* Gateway stability

\* Behavior under provider degradation



The project should report measured results rather than claiming arbitrary performance numbers.



\---



\# 23. Testing Requirements



The project should contain:



\### Unit tests



Test individual components:



\* Error classifier

\* Retry policy

\* Backoff calculation

\* Circuit breaker

\* Health calculations

\* Routing decisions

\* Cost calculation



\### Integration tests



Test:



```text

FastAPI

&#x20;  ↓

Gateway

&#x20;  ↓

Redis

&#x20;  ↓

Provider abstraction

```



\### Failure tests



Test scenarios such as:



```text

Provider timeout

Provider 429

Provider 500

Provider recovery

Circuit opening

Circuit half-open

Circuit closing

Failover

```



\### Load tests



Use k6 for traffic simulation.



\---



\# 24. Deployment Requirements



The complete development environment should run locally using Docker Compose.



The local environment should eventually include:



```text

Gateway

Redis

PostgreSQL

Prometheus

Grafana

Ollama

```



Additional services should only be introduced when justified.



Kubernetes is not required for the first version.



\---



\# 25. Security Requirements



Security is a first-class requirement.



The system must:



\* Never commit secrets to Git.

\* Use environment variables/secrets for credentials.

\* Protect administrative endpoints.

\* Restrict chaos/fault-injection functionality.

\* Validate incoming requests.

\* Avoid exposing sensitive request data unnecessarily.

\* Apply tenant isolation to stored data.

\* Use secure defaults.

\* Provide a `.env.example` without real credentials.



Detailed requirements will be maintained in `SECURITY.md`.



\---



\# 26. Performance Requirements



Performance targets must be measured rather than invented.



The gateway should minimize its own overhead.



The project should measure:



\* Gateway processing latency

\* Upstream provider latency

\* End-to-end latency

\* Requests per second

\* Error rate under load

\* Redis operation latency where relevant



LLM inference latency must be separated from gateway overhead.



\---



\# 27. Reliability Requirements



The gateway should be resilient to:



\* Individual provider failures

\* Temporary network errors

\* Provider rate limiting

\* Provider timeouts

\* Provider server errors

\* Provider latency degradation

\* Gateway process restarts

\* Redis-backed state recovery where applicable



The system should fail gracefully when no provider is available.



It must not claim zero downtime or perfect availability.



All reliability claims must be based on reproducible measurements.



\---



\# 28. Success Criteria



The project will be considered successful when the following complete flow can be demonstrated locally:



\### Stage 1 — Normal operation



```text

Client

&#x20; ↓

Gateway

&#x20; ↓

Provider A

&#x20; ↓

Successful response

```



\### Stage 2 — Provider failure



```text

Provider A

&#x20;   ↓

Injected failure

&#x20;   ↓

Health degradation

&#x20;   ↓

Circuit breaker OPEN

```



\### Stage 3 — Automatic failover



```text

Client

&#x20; ↓

Gateway

&#x20; ↓

Provider A ❌

&#x20; ↓

Provider B ✅

&#x20; ↓

Successful response

```



\### Stage 4 — Provider recovery



```text

Provider A

&#x20;   ↓

Recovery

&#x20;   ↓

HALF-OPEN probe

&#x20;   ↓

Success

&#x20;   ↓

CLOSED

```



\### Stage 5 — Observability



Grafana should show evidence of:



\* Provider failures

\* Circuit state changes

\* Failover events

\* Latency

\* Request rates

\* Error rates



\### Stage 6 — Reproducibility



A new developer should be able to clone the repository, follow the README, start the local environment, and reproduce the main demonstration without requiring paid services.



\---



\# 29. Project Principles



The project will follow these principles:



\### Reliability over complexity



Every component must solve a real problem.



\### Measurable engineering



Performance and availability claims must come from measurements.



\### Local-first development



The core project must work without mandatory paid APIs.



\### Explicit failure handling



Failures must be classified and handled deliberately.



\### Observable by default



Important system behavior should be visible through logs, metrics, or traces.



\### Test before claiming



A feature is not considered complete merely because the code exists.



\### Understandable architecture



The project should remain explainable by one developer.



\### Incremental development



Features should be implemented in small, testable phases.



\---



\# 30. Definition of Done



A feature is considered complete only when:



\* Implementation exists.

\* Relevant tests exist.

\* Failure cases have been considered.

\* Logging/metrics are added where appropriate.

\* Documentation is updated.

\* The feature works in the local environment.

\* No secrets are committed.

\* Existing functionality still works.



\---



\# 31. Initial Technology Direction



The initial technology direction is:



```text

Language:             Python 3.12

Package management:  uv

API:                  FastAPI

Validation:           Pydantic v2

HTTP client:          httpx

LLM abstraction:      LiteLLM

Local inference:      Ollama

State:                Redis

Database:             PostgreSQL

ORM:                  SQLAlchemy 2.x

Migrations:           Alembic

Metrics:              Prometheus

Dashboards:           Grafana

Tracing:              OpenTelemetry

Testing:              pytest

Async testing:        pytest-asyncio

Load testing:         k6

Containers:           Docker

Local orchestration:  Docker Compose

Reverse proxy:        Nginx

CI/CD:                GitHub Actions

Linting:              Ruff

Type checking:        mypy

```



Technology choices may be changed only when there is a clear engineering reason. Such changes should be documented in `DECISIONS.md`.



\---



\# 32. Project Constraint



\*\*The project must remain capable of being developed and demonstrated at ₹0 mandatory cost.\*\*



Open-source software and locally running infrastructure should be preferred.



External paid services may be supported as optional integrations but must not become dependencies for the core project or its primary demonstration.



