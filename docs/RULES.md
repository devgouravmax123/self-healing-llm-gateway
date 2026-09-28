\# Self-Healing LLM Gateway — Engineering Rules



\## 1. Purpose



This document defines the mandatory engineering rules for the Self-Healing LLM Gateway.



These rules apply to:



\* Human developers

\* Coding agents

\* AI assistants

\* Automated tooling

\* Future contributors



The purpose is to keep the project:



\* Reliable

\* Understandable

\* Testable

\* Secure

\* Observable

\* Maintainable

\* Cost-free for the mandatory local setup



`PRD.md`, `ARCHITECTURE.md`, `DESIGN.md`, and `RULES.md` together form the primary engineering contract of the project.



\---



\# 2. Source of Truth



Before making significant implementation changes, the developer or coding agent must inspect:



```text

docs/PRD.md

docs/ARCHITECTURE.md

docs/DESIGN.md

docs/RULES.md

```



The documentation is the source of truth.



Code must not silently redefine documented requirements.



\---



\# 3. Do Not Change the Architecture Silently



A coding agent must not independently replace or remove major technologies.



Current intended stack:



```text

Python 3.12

FastAPI

Pydantic v2

httpx

LiteLLM

Ollama

Redis

PostgreSQL

SQLAlchemy 2.x

Alembic

Prometheus

Grafana

OpenTelemetry

pytest

pytest-asyncio

k6

Docker

Docker Compose

Nginx

GitHub Actions

Ruff

mypy

pre-commit

```



Examples of changes that require an explicit architectural decision:



\* Replacing Redis

\* Replacing PostgreSQL

\* Replacing LiteLLM

\* Replacing FastAPI

\* Introducing Kubernetes

\* Introducing Kafka

\* Introducing another message broker

\* Introducing a new database

\* Converting the application into microservices

\* Removing observability components



If a change is genuinely necessary, document it in:



```text

docs/DECISIONS.md

```



before treating it as the new architecture.



\---



\# 4. No Unnecessary Complexity



The project is intended to demonstrate engineering ability, not the number of technologies used.



Do not introduce infrastructure merely because it is commonly used in large companies.



Do not add:



```text

Kubernetes

Kafka

RabbitMQ

Service mesh

Elasticsearch

Cassandra

Celery

Multiple microservices

```



unless a real project requirement demonstrates that the technology is necessary.



A simpler correct system is preferred over a complex system that is difficult to understand and maintain.



\---



\# 5. Local-First Requirement



The complete core project must be runnable locally.



The mandatory implementation must not depend on:



\* Paid cloud APIs

\* Paid databases

\* Paid monitoring services

\* Paid hosting

\* Paid LLM providers



The primary LLM demonstration should use Ollama.



External providers may be supported later as optional integrations.



\---



\# 6. No Mandatory Paid API



The project must remain usable at ₹0 for the core functionality.



If an external API is added:



\* It must be optional.

\* Local Ollama must remain available.

\* The project must document how to run without the external API.



Never design the core demo around a paid API.



\---



\# 7. Python Version



The project should target:



```text

Python 3.12

```



Do not change the Python target without documenting the reason.



Python 3.14 may be used experimentally, but the project's supported environment should remain Python 3.12 unless deliberately changed.



\---



\# 8. Dependency Rules



Every dependency must have a clear purpose.



Before adding a package, ask:



1\. What problem does it solve?

2\. Can the existing stack solve the problem?

3\. Does it increase operational complexity?

4\. Does it introduce licensing or security concerns?

5\. Is it actually required?



Do not add libraries simply because they are popular.



Dependencies must be declared in:



```text

pyproject.toml

```



\---



\# 9. Package Manager



The project uses:



```text

uv

```



Dependency installation and environment management should use `uv`.



Do not mix multiple package-management workflows unnecessarily.



\---



\# 10. Configuration Rules



Configuration must be centralized.



Use environment variables and a settings module.



Do not scatter:



```python

os.getenv(...)

```



throughout the application.



Application configuration should have one clear source.



\---



\# 11. Secrets



Never commit secrets to Git.



This includes:



```text

API keys

Passwords

Database passwords

Access tokens

Private keys

Authentication secrets

Provider credentials

```



Use:



```text

.env

```



for local secrets.



Commit:



```text

.env.example

```



with safe placeholder values.



Never put real secrets into documentation, tests, screenshots, logs, or examples.



\---



\# 12. Logging Rules



Use structured JSON logging.



Logs should contain useful operational context such as:



```text

request\_id

tenant\_id

feature

provider

model

event

error\_type

latency

```



Do not log sensitive data unnecessarily.



In particular, do not automatically log:



\* Full prompts

\* Full model responses

\* API keys

\* Passwords

\* Authorization headers

\* Database credentials



\---



\# 13. Request ID Rule



Every request must have a request ID.



The request ID should be:



\* Unique

\* Available throughout the request lifecycle

\* Included in relevant logs

\* Included in traces

\* Associated with usage records

\* Associated with provider attempts



Example:



```text

req\_8f72c91

```



A request ID must never contain secrets or sensitive user information.



\---



\# 14. Tenant Context Rule



Requests should carry tenant context where authentication is enabled.



Important metadata:



```text

tenant\_id

feature

request\_id

```



These values should remain consistent throughout the request lifecycle.



Tenant data must not accidentally leak between requests.



\---



\# 15. API Compatibility



The main LLM endpoint should follow an OpenAI-compatible request/response shape wherever practical.



Primary endpoint:



```text

POST /v1/chat/completions

```



Do not introduce a completely custom request format unless there is a documented reason.



\---



\# 16. Business Logic Must Not Live in Routes



FastAPI route functions should remain thin.



Routes should primarily:



```text

Receive request

&#x20;   ↓

Validate request

&#x20;   ↓

Call application/service layer

&#x20;   ↓

Return response

```



Do not put complex:



\* Routing logic

\* Retry logic

\* Circuit-breaker logic

\* Cost calculation

\* Provider selection



directly inside route functions.



\---



\# 17. Separation of Responsibilities



Each component must have a clear responsibility.



Examples:



```text

Router

→ Selects provider



Circuit breaker

→ Controls provider availability



Retry manager

→ Controls retries



Error classifier

→ Normalizes failures



Provider client

→ Executes provider calls



Health tracker

→ Tracks provider health



Usage tracker

→ Tracks tokens and cost

```



Do not duplicate the same logic across multiple modules.



\---



\# 18. Provider Abstraction



Provider-specific implementation details should be isolated.



The main gateway should not contain provider-specific logic such as:



```text

if provider == "ollama":

&#x20;   ...

elif provider == "some\_other\_provider":

&#x20;   ...

```



throughout the application.



Provider interaction should go through the provider abstraction/LiteLLM layer.



\---



\# 19. Circuit Breaker Rule



Every provider target must have an independent circuit.



Supported states:



```text

CLOSED

OPEN

HALF\_OPEN

```



The implementation must not reduce the circuit breaker to simple:



```text

if failed:

&#x20;   use another provider

```



The recovery path must be implemented:



```text

OPEN

&#x20;↓

cooldown

&#x20;↓

HALF\_OPEN

&#x20;↓

probe

&#x20;↓

CLOSED

```



\---



\# 20. OPEN Circuit Rule



When a circuit is `OPEN`:



Normal traffic must not be sent to that provider.



The router should skip the provider and select another eligible target.



Exceptions are only allowed for controlled half-open probes.



\---



\# 21. HALF-OPEN Rule



HALF-OPEN is a controlled recovery state.



The initial implementation should allow:



```text

one probe at a time

```



A successful probe:



```text

HALF\_OPEN → CLOSED

```



A failed probe:



```text

HALF\_OPEN → OPEN

```



The system must prevent multiple gateway instances from flooding a recovering provider with probes.



\---



\# 22. No In-Memory Shared Circuit State



Circuit state must not exist only inside Python memory.



Do not rely on:



```python

circuit\_state = {}

```



as the authoritative state.



Shared operational state must use Redis.



In-memory caching may be used only as an optimization and must never become the source of truth for distributed decisions.



\---



\# 23. Retry Rules



Retries must always be bounded.



Never retry indefinitely.



Every retry policy must define:



```text

maximum retries

timeout

backoff

jitter

retryable errors

```



\---



\# 24. Do Not Retry Everything



Retry only errors that are potentially transient.



Generally retryable:



```text

TIMEOUT

NETWORK\_ERROR

SERVER\_ERROR

```



Potentially retryable depending on policy:



```text

RATE\_LIMIT

```



Generally not retryable:



```text

AUTH\_ERROR

BAD\_REQUEST

CONTENT\_FILTER

```



Retry behavior must be explicit.



\---



\# 25. Exponential Backoff



Retries must use controlled backoff.



Do not immediately retry in a tight loop.



Use exponential backoff with bounded jitter.



The maximum delay must be configurable.



\---



\# 26. Maximum Total Attempts



The system must have a global attempt limit.



This includes:



```text

Retries

\+

Failover attempts

```



The gateway must never enter an infinite retry/failover loop.



\---



\# 27. Failover Rules



Failover should happen only when another eligible provider exists.



The gateway should consider:



\* Circuit state

\* Provider availability

\* Request constraints

\* Attempt history

\* Maximum attempt count



Every failover must be observable.



\---



\# 28. Failover Loop Prevention



A request must maintain provider attempt history.



Example:



```text

Provider A → attempted

Provider B → attempted

Provider C → attempted

```



The system should not repeatedly select the same failed provider during the same request unless explicitly designed to do so.



\---



\# 29. Error Taxonomy



Provider errors must be normalized into internal categories:



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



Business logic should operate primarily on normalized errors rather than provider-specific exception formats.



\---



\# 30. Do Not Hide Errors



Errors must not be silently swallowed.



Bad:



```python

try:

&#x20;   ...

except Exception:

&#x20;   pass

```



Every caught exception must either:



\* Be handled intentionally,

\* Be converted to a known internal error,

\* Be logged appropriately,

\* Or be re-raised.



\---



\# 31. Exception Handling



Do not use broad exception handling without a reason.



Prefer:



```python

except SpecificProviderError:

```



over:



```python

except Exception:

```



If a broad exception handler is required at a boundary, it must preserve enough context for debugging.



\---



\# 32. Timeout Rule



Every provider call must have an explicit timeout.



Never allow provider requests to wait indefinitely.



Timeout values must be configurable.



A timeout must update:



\* Provider health

\* Error metrics

\* Logs

\* Trace

\* Circuit state where applicable



\---



\# 33. Health Metrics



Provider health should include:



```text

Total requests

Successful requests

Failed requests

Success rate

Failure rate

p50 latency

p95 latency

p99 latency

Error categories

Last success

Last failure

Circuit state

Failover count

```



Health calculations must be based on actual observed data.



\---



\# 34. No Fake Metrics



Never generate fake:



```text

latency

success rate

failure rate

token usage

cost

RPS

availability

```



Metrics must come from actual system behavior.



\---



\# 35. No Fake Performance Claims



Never write claims such as:



```text

99.99% availability

1000 RPS

50ms latency

99% failover success

```



unless the corresponding measurement has actually been performed.



Performance claims must include test conditions.



\---



\# 36. Percentile Rules



When reporting:



```text

p50

p95

p99

```



the measurement window and test conditions should be known.



Do not present a percentile from a tiny or meaningless sample as a production-quality performance claim.



\---



\# 37. Redis Rules



Redis should be used for:



```text

Circuit state

Rate-limit state

Rolling operational counters

Temporary coordination state

```



Do not use Redis as the primary durable store for historical business records.



\---



\# 38. Redis Concurrency



Shared Redis state must account for concurrent access.



Operations such as:



```text

Increment failure count

Change circuit state

Acquire half-open probe

Update rate limit

```



must be designed with concurrency in mind.



Use appropriate atomic Redis mechanisms when required.



\---



\# 39. PostgreSQL Rules



PostgreSQL should store durable records such as:



```text

Tenants

Requests

Usage

Cost

Provider events

Audit history

```



Database schemas must use migrations.



Do not manually modify production schemas without corresponding migration files.



\---



\# 40. SQLAlchemy Rules



Use SQLAlchemy for database access.



Database queries should not be scattered randomly throughout route functions.



Use repository/data-access boundaries where appropriate.



\---



\# 41. Database Migration Rules



Use Alembic.



Every schema change must have an associated migration.



Do not treat:



```text

DROP DATABASE

```



or destructive schema recreation as a normal development workflow.



\---



\# 42. Cost Tracking Rules



Cost calculations must be explicit.



Never invent pricing.



If pricing is unavailable:



```text

estimated\_cost = null

```



is preferable to a fabricated value.



For local Ollama inference, monetary cost can be represented as zero for the configured local inference model, while token usage remains tracked when available.



\---



\# 43. Token Tracking



When provider responses expose token usage, record:



```text

input\_tokens

output\_tokens

total\_tokens

```



If token usage is unavailable, do not fabricate it.



\---



\# 44. Prometheus Rules



Prometheus should contain metrics, not arbitrary application data.



Avoid high-cardinality labels.



Do not use:



```text

request\_id

full user ID

raw prompt

```



as Prometheus labels.



These values can create an excessive number of time series.



\---



\# 45. Metrics Naming



Metrics should have consistent names and units.



For example:



```text

\*\_total

```



for counters.



Durations should use a consistent unit such as seconds.



\---



\# 46. Grafana Rules



Grafana dashboards must visualize actual Prometheus data.



Do not create dashboards containing manually fabricated values.



Dashboards should make these areas visible:



```text

Traffic

Latency

Errors

Provider health

Circuit states

Retries

Failovers

Usage

Cost

```



\---



\# 47. OpenTelemetry Rules



Tracing should allow a request to be followed across:



```text

Gateway

&#x20;→ routing

&#x20;→ provider attempt

&#x20;→ retry/failover

&#x20;→ usage recording

```



Traces must not automatically contain sensitive prompt or response content.



\---



\# 48. Observability Correlation



Logs, metrics, and traces should share useful correlation identifiers.



At minimum:



```text

request\_id

```



should be available to logs and traces.



Provider/model information should be available where relevant.



\---



\# 49. Health Endpoint Rules



`/health` should answer whether the application process is alive.



`/ready` should answer whether the gateway is ready to perform its intended role.



Do not make readiness depend on every individual provider being healthy.



One provider being down should not automatically mean the gateway process itself is unhealthy.



\---



\# 50. Chaos Testing Rules



Chaos testing is a development/testing capability.



It must be:



```text

Disabled by default

Explicitly activated

Environment-restricted

Protected

Observable

```



Possible faults:



```text

TIMEOUT

SERVER\_ERROR

RATE\_LIMIT

LATENCY

NETWORK\_ERROR

```



\---



\# 51. Chaos Must Never Be Unrestricted



Never expose an unrestricted chaos endpoint to the public internet.



Chaos functionality must have additional protection.



Production deployments should disable it unless explicitly required for controlled testing.



\---



\# 52. Testing Rules



Every major reliability feature must have tests.



At minimum:



```text

Routing

Retry

Timeout

Circuit breaker

Failover

Error classification

Health tracking

Cost calculation

Rate limiting

```



\---



\# 53. Test Isolation



Unit tests should not require:



```text

Real Ollama

Real external LLM API

Production Redis

Production PostgreSQL

```



unless they are explicitly integration tests.



Use mocks, fakes, or test containers where appropriate.



\---



\# 54. Failure Testing Is Mandatory



The project is specifically a self-healing gateway.



Therefore, testing only successful requests is insufficient.



The test suite must demonstrate:



```text

Provider timeout

Provider 5xx

Provider rate limit

Repeated provider failures

Circuit opening

Failover

Provider recovery

Half-open probe

Circuit closing

```



\---



\# 55. Regression Rule



Every discovered bug that affects behavior should result in a regression test when practical.



The goal is:



```text

Bug found

&#x20;  ↓

Fix

&#x20;  ↓

Test added

&#x20;  ↓

Bug should not return

```



\---



\# 56. API Testing



The main endpoint should be tested for:



\* Valid requests

\* Invalid requests

\* Missing fields

\* Invalid message formats

\* Authentication failures

\* Provider failures

\* Failover

\* Final successful responses

\* Controlled error responses



\---



\# 57. Load Testing Rules



k6 will be used for load testing.



Load tests must document:



```text

Virtual users

Duration

Request rate

Payload

Provider configuration

Machine/environment

Observed metrics

```



Do not compare results from different environments without noting the difference.



\---



\# 58. Docker Rules



Docker should be used to make the local environment reproducible.



Docker images should be kept reasonably small.



Do not run unnecessary services.



The default Compose environment should include only required project services.



\---



\# 59. Docker Compose Rules



The local stack should eventually include:



```text

nginx

gateway

redis

postgres

prometheus

grafana

ollama

```



Each service should have a clear purpose.



\---



\# 60. Nginx Rules



Nginx should handle:



```text

Reverse proxy

Basic proxy configuration

```



Nginx should not contain:



```text

Circuit breaker logic

Provider routing

Retry policy

Cost calculations

```



Those belong to the gateway.



\---



\# 61. Code Quality



Use:



```text

Ruff

mypy

pytest

pre-commit

```



Code should be:



\* Readable

\* Typed where practical

\* Modular

\* Testable

\* Explicit



Avoid clever code when simple code is easier to understand.



\---



\# 62. Type Checking



New application code should use type hints.



Avoid unnecessary:



```python

Any

```



when a meaningful type can be defined.



Type-checking errors should not be ignored without a documented reason.



\---



\# 63. Formatting and Linting



Code must pass the configured Ruff checks.



Do not disable lint rules globally merely to make the project pass.



If a rule is genuinely inappropriate, disable it narrowly and document why.



\---



\# 64. Comments



Comments should explain:



```text

Why

```



rather than simply:



```text

What

```



Bad:



```python

\# Increment count

count += 1

```



Useful:



```python

\# Store the failure count in Redis so multiple gateway instances

\# observe the same circuit-breaker state.

```



\---



\# 65. Documentation Rules



If implementation behavior changes significantly, update the relevant documentation.



Examples:



```text

Architecture change → ARCHITECTURE.md

Implementation design → DESIGN.md

Constraint change → RULES.md

Task progress → TASKS.md

Architectural decision → DECISIONS.md

Security change → SECURITY.md

```



Documentation should not intentionally describe behavior that the code does not implement.



\---



\# 66. Coding Agent Rules



When using Antigravity or another coding agent:



1\. Read the relevant documentation before editing.

2\. Work on one logical task at a time.

3\. Do not rewrite unrelated files.

4\. Do not change the stack without approval.

5\. Do not introduce unnecessary dependencies.

6\. Run tests after meaningful changes.

7\. Report failures instead of hiding them.

8\. Do not fabricate successful test results.

9\. Do not fabricate performance results.

10\. Update documentation when behavior changes.

11\. Keep changes reviewable.

12\. Explain architectural changes before implementing them.



\---



\# 67. Small-Change Rule



Prefer small incremental changes.



Bad approach:



```text

Build entire gateway

\+

Redis

\+

PostgreSQL

\+

Grafana

\+

Tracing

\+

Chaos

\+

Tests

```



in one operation.



Preferred approach:



```text

Feature

&#x20;  ↓

Implement

&#x20;  ↓

Test

&#x20;  ↓

Verify

&#x20;  ↓

Document

&#x20;  ↓

Next feature

```



\---



\# 68. Do Not Overwrite Working Code Without Reason



Before replacing working implementation:



1\. Understand the existing behavior.

2\. Identify the reason for change.

3\. Preserve existing tests where applicable.

4\. Make the smallest reasonable change.



Do not rewrite modules merely for stylistic preference.



\---



\# 69. Backward Compatibility



Changes to the public API should be deliberate.



Do not unexpectedly change:



```text

Endpoint

Request schema

Response schema

Error format

Authentication behavior

```



without documenting the change.



\---



\# 70. Error Response Rules



Gateway errors should be:



\* Predictable

\* Structured

\* Safe

\* Useful to the client



Do not expose:



```text

Stack traces

Database credentials

API keys

Internal secrets

Unnecessary infrastructure details

```



to clients.



\---



\# 71. Security by Default



Default behavior should be secure.



Examples:



```text

Chaos disabled

Secrets externalized

Admin endpoints protected

Sensitive logs avoided

Input validated

Internal services not unnecessarily exposed

```



\---



\# 72. Tenant Isolation



A tenant must only be able to access data belonging to that tenant.



Tenant information must be derived from authenticated context rather than blindly trusting arbitrary client-provided identifiers.



Tenant isolation must be tested.



\---



\# 73. No Sensitive Prompt Storage by Default



The gateway should not persist complete prompts or model responses by default.



If debugging requires storing content temporarily, it must be explicitly configured and documented.



\---



\# 74. Git Rules



Do not commit:



```text

.env

venv/

\_\_pycache\_\_/

.pytest\_cache/

.mypy\_cache/

.ruff\_cache/

large model files

secrets

local database files

generated logs

```



Use `.gitignore`.



\---



\# 75. Commit Quality



Commits should represent logical changes.



Prefer:



```text

feat: add provider registry

feat: implement circuit breaker

test: add circuit breaker failure tests

feat: add Redis circuit state

```



over one huge commit containing unrelated work.



\---



\# 76. Decision Rule



When there are multiple technically valid solutions, do not automatically choose the most complex one.



Evaluate:



```text

Correctness

Reliability

Simplicity

Testability

Maintainability

Operational cost

```



The selected approach should be documented when it is architecturally significant.



\---



\# 77. Decision Documentation



Important architectural decisions belong in:



```text

docs/DECISIONS.md

```



Examples:



```text

Why Redis instead of in-memory state?

Why PostgreSQL?

Why LiteLLM?

Why Ollama?

Why Docker Compose?

Why one gateway service instead of microservices?

Why one half-open probe?

```



\---



\# 78. No Premature Optimization



Do not optimize based on assumptions.



First:



```text

Measure

```



Then:



```text

Identify bottleneck

```



Then:



```text

Optimize

```



Performance optimizations should have measurable justification.



\---



\# 79. Reliability Over Feature Count



The project's main value comes from demonstrating reliable behavior.



A smaller system with correctly implemented:



```text

Timeout

Retry

Circuit breaker

Failover

Recovery

Observability

```



is preferable to a large system with many unfinished features.



\---



\# 80. Definition of Done



A feature is not considered complete merely because the code exists.



A feature is complete when appropriate:



```text

Implementation

\+

Tests

\+

Error handling

\+

Observability

\+

Documentation

\+

Manual verification

```



are completed.



The exact requirements depend on the feature.



\---



\# 81. Final Rule



The most important rule is:



> Do not fake reliability.



The system must demonstrate real behavior.



If a feature has not been implemented, say that it has not been implemented.



If a test has not been run, do not claim it passed.



If a performance measurement has not been performed, do not claim a performance number.



If an architectural decision has not been made, do not silently make it.



The credibility of the project depends on measurable, reproducible engineering rather than impressive-looking claims.



