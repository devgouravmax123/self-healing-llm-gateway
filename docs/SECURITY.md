\# Security



\## 1. Purpose



This document defines the security requirements and practices for the Self-Healing LLM Gateway.



Security must be considered during design, implementation, testing, and deployment.



The goal is to prevent:



\* Unauthorized access

\* Secret leakage

\* Tenant data exposure

\* Abuse of gateway resources

\* Unauthorized provider usage

\* Accidental exposure of administrative functionality

\* Unsafe chaos/fault-injection usage

\* Injection and malformed-input attacks

\* Sensitive information appearing in logs

\* Unauthorized access to Redis or PostgreSQL



Security should be implemented without adding unnecessary complexity to the first version.



\---



\# 2. Security Principles



The project follows these principles:



1\. \*\*Secure by default\*\*

2\. \*\*Least privilege\*\*

3\. \*\*Never trust client input\*\*

4\. \*\*Never commit secrets\*\*

5\. \*\*Protect administrative functionality\*\*

6\. \*\*Separate tenants\*\*

7\. \*\*Avoid unnecessary sensitive logging\*\*

8\. \*\*Fail safely\*\*

9\. \*\*Validate security-sensitive configuration\*\*

10\. \*\*Keep security controls observable\*\*

11\. \*\*Do not introduce unnecessary infrastructure\*\*

12\. \*\*Document significant security decisions\*\*



\---



\# 3. Threat Model



The gateway sits between applications and LLM providers.



The basic security boundary is:



```text

Untrusted Client

&#x20;      ↓

&#x20;   Nginx

&#x20;      ↓

FastAPI Gateway

&#x20;      ↓

Internal Services

&#x20;      ↓

Redis / PostgreSQL / LLM Providers

```



The client must not automatically be trusted.



Potential attackers or unsafe actors include:



\* Unauthenticated users

\* Users with stolen API keys

\* Malicious tenants

\* Compromised client applications

\* Attackers sending malformed requests

\* Attackers attempting to abuse rate limits

\* Attackers attempting to access admin endpoints

\* Attackers attempting to trigger chaos functionality

\* Attackers attempting to extract secrets through logs or responses



\---



\# 4. Authentication



The gateway should require authentication for protected API endpoints.



The initial design should support API-key-based authentication.



Conceptually:



```text

Client

&#x20;  ↓

API Key

&#x20;  ↓

Gateway

&#x20;  ↓

Validate key

&#x20;  ↓

Identify tenant

&#x20;  ↓

Process request

```



Unauthenticated requests should not be allowed to access protected gateway functionality.



Authentication failures should return an appropriate HTTP error without revealing sensitive information.



\---



\# 5. API Key Security



API keys are sensitive credentials.



Rules:



\* Never hard-code API keys.

\* Never commit API keys to Git.

\* Never place real API keys in documentation.

\* Never print API keys in logs.

\* Never return API keys in API responses.

\* Store only what is necessary.

\* Prefer storing a secure hash of gateway API keys when practical.

\* Support key rotation.

\* Revoke compromised keys.

\* Use environment variables or secure secret storage for provider credentials.



Example:



```text

SAFE:



Authorization: Bearer <client-api-key>



UNSAFE:



logger.info("API key = %s", api\_key)

```



\---



\# 6. Provider Secrets



External provider credentials must never be stored directly in source code.



Examples include:



```text

OPENAI\_API\_KEY

ANTHROPIC\_API\_KEY

GEMINI\_API\_KEY

```



if external providers are added.



Use environment variables or an appropriate secret-management mechanism.



Example:



```text

OPENAI\_API\_KEY=<secret>

```



The actual value must never be committed.



The repository should contain:



```text

.env.example

```



but not the real:



```text

.env

```



when it contains secrets.



\---



\# 7. Git Security



Before committing code:



\* Check for secrets.

\* Check for API keys.

\* Check for passwords.

\* Check for database credentials.

\* Check for private certificates.

\* Check for tokens.

\* Check for accidentally committed `.env` files.



The `.gitignore` should include sensitive local files such as:



```text

.env

.env.\*

!.env.example

```



and local development artifacts where appropriate.



If a secret is accidentally committed, removing it from the latest commit is not sufficient.



The credential itself should be considered compromised and rotated/revoked.



\---



\# 8. Input Validation



All external input must be validated.



FastAPI and Pydantic should be used to validate request structure.



Validation should cover:



\* Required fields

\* Data types

\* String lengths

\* Allowed values

\* Message structure

\* Model identifiers

\* Tenant identifiers

\* Optional configuration values



The gateway should reject malformed requests before they reach internal provider logic.



Example:



```text

Invalid request

&#x20;     ↓

Pydantic validation

&#x20;     ↓

Reject request

&#x20;     ↓

Do not call provider

```



\---



\# 9. Prompt and Message Handling



LLM request content should be treated as untrusted user input.



The gateway should not assume that prompts are safe.



However, the gateway's primary responsibility is infrastructure reliability rather than content moderation.



Therefore:



\* Do not unnecessarily modify user prompts.

\* Do not execute prompt content as code.

\* Do not interpret user-controlled strings as SQL.

\* Do not construct shell commands from prompts.

\* Do not expose internal system information through error messages.



Provider-specific content safety remains the responsibility of the provider/application layer unless explicitly implemented as a gateway feature.



\---



\# 10. Tenant Isolation



Requests should be associated with a tenant.



Important identifiers:



```text

tenant\_id

request\_id

feature

```



A tenant must not be able to access another tenant's:



\* Usage data

\* Cost data

\* API credentials

\* Request history

\* Rate-limit state

\* Administrative information



Database queries and Redis keys must include the appropriate tenant scope where required.



Example:



```text

usage:{tenant\_id}:{request\_id}

```



rather than storing tenant-sensitive information under a shared unscoped key.



\---



\# 11. Rate Limiting



Rate limiting is both a reliability and security control.



The gateway should use Redis-backed rate limiting.



Potential limits include:



```text

Requests per minute

Requests per hour

Tenant limits

API-key limits

```



Rate limiting helps prevent:



\* Accidental traffic spikes

\* Resource exhaustion

\* Abusive clients

\* Excessive provider usage

\* Excessive infrastructure costs



Rate-limit failures should not expose internal Redis details.



\---



\# 12. Resource Exhaustion



The gateway should protect itself against excessively expensive requests.



Controls may include:



\* Maximum request body size

\* Maximum number of messages

\* Maximum message length

\* Maximum configured output tokens

\* Request timeout

\* Provider timeout

\* Maximum retry count

\* Rate limits

\* Connection limits



The gateway must not allow a client to cause unlimited retries or unlimited provider requests.



\---



\# 13. Retry Security



Retries can unintentionally amplify traffic.



Therefore:



\* Retries must be bounded.

\* Only eligible errors should be retried.

\* Exponential backoff should be used.

\* Jitter should be added where appropriate.

\* Retry counts should be observable.

\* A failed provider must not cause an uncontrolled retry loop.



Unsafe behavior:



```text

Failure

&#x20;↓

Retry

&#x20;↓

Failure

&#x20;↓

Retry

&#x20;↓

Failure

&#x20;↓

Retry forever

```



Expected behavior:



```text

Failure

&#x20;↓

Bounded retry

&#x20;↓

Failure

&#x20;↓

Failover / return error

```



\---



\# 14. Circuit Breaker Security



Circuit breakers help prevent a failing provider from consuming excessive gateway resources.



When a provider is unhealthy:



```text

OPEN

```



normal traffic should stop being sent to that provider.



Half-open recovery testing must also be bounded.



The gateway should not allow a large number of requests to bypass the circuit simply because the provider is being tested.



\---



\# 15. Chaos Testing Security



Chaos/fault injection is potentially dangerous.



It must never be an unrestricted public feature.



Chaos functionality should be:



\* Disabled by default.

\* Available only in development/testing environments.

\* Protected by authentication/authorization.

\* Clearly separated from normal client endpoints.

\* Restricted to explicitly configured providers.

\* Logged.

\* Observable through metrics/traces.

\* Impossible to accidentally trigger through normal user requests.



Example conceptual endpoint:



```text

/admin/chaos/provider/{provider}/failure

```



This should not be publicly accessible in a production deployment.



\---



\# 16. Administrative Endpoints



Administrative functionality must be separated from normal user functionality.



Examples:



```text

/admin/health

/admin/providers

/admin/circuits

/admin/chaos

/admin/config

```



Administrative endpoints may expose sensitive information.



Therefore they should require stronger access controls than normal LLM requests.



Do not expose internal provider details unnecessarily.



\---



\# 17. Error Responses



Error responses should provide enough information for clients to understand the failure without exposing internal implementation details.



Avoid returning:



```text

Database connection string

Redis connection details

API keys

Stack traces

Internal filesystem paths

Environment variables

Provider credentials

Internal network addresses

```



For example, avoid exposing raw exceptions directly:



```python

return {"error": str(exception)}

```



Instead, return a controlled error response.



Detailed information should remain in appropriately secured server-side logs.



\---



\# 18. Logging Security



Structured JSON logging is required.



However, logs must not become a source of sensitive-data leakage.



Avoid logging:



\* API keys

\* Provider credentials

\* Passwords

\* Authorization headers

\* Session tokens

\* Full sensitive prompts unnecessarily

\* Full sensitive model responses unnecessarily

\* Database credentials



Useful metadata includes:



```text

request\_id

tenant\_id

provider

model

status

latency

error\_type

retry\_count

failover\_count

circuit\_state

```



The project should prefer metadata over storing complete user content.



\---



\# 19. Request IDs



Every request should receive or propagate a request ID.



Example:



```text

X-Request-ID: abc123

```



The request ID should appear in:



\* Logs

\* Metrics where appropriate

\* Traces

\* Error correlation

\* Audit records



Request IDs must not themselves contain secrets or sensitive user information.



\---



\# 20. Redis Security



Redis contains operational state and potentially tenant-sensitive information.



Redis should:



\* Not be exposed directly to the public internet.

\* Be accessible only to trusted application services.

\* Use authentication where appropriate.

\* Use network isolation.

\* Avoid storing secrets unnecessarily.

\* Use controlled key namespaces.



Example:



```text

gateway:health:{provider}

gateway:circuit:{provider}

gateway:ratelimit:{tenant}

```



Redis should not become an unauthenticated public service.



\---



\# 21. PostgreSQL Security



PostgreSQL contains durable application information.



Security requirements include:



\* Use strong database credentials.

\* Do not commit credentials.

\* Restrict database network access.

\* Use a dedicated application database user.

\* Grant only required permissions.

\* Use parameterized queries/ORM mechanisms.

\* Maintain tenant isolation.

\* Avoid exposing PostgreSQL directly to clients.



SQLAlchemy should be used safely rather than constructing SQL queries by concatenating user-controlled strings.



\---



\# 22. SQL Injection Prevention



User input must never be directly concatenated into SQL.



Unsafe:



```text

"SELECT \* FROM users WHERE id = '" + user\_input + "'"

```



Use SQLAlchemy's parameterized query mechanisms instead.



The database layer should treat all user-controlled values as data rather than executable SQL.



\---



\# 23. Command Injection Prevention



The gateway should avoid executing shell commands using user-controlled values.



Especially:



\* Model names

\* Provider names

\* Tenant IDs

\* Request content

\* File paths

\* Configuration values



Do not pass user-controlled strings directly into shell commands.



\---



\# 24. SSRF Considerations



The gateway may communicate with provider endpoints.



Provider URLs and network destinations should therefore be controlled through trusted configuration rather than arbitrary client input.



A client should not be able to submit:



```text

provider\_url = arbitrary\_internal\_address

```



and make the gateway request internal infrastructure.



Provider configuration should come from trusted server-side configuration.



\---



\# 25. Network Security



The intended deployment is:



```text

Internet / Client

&#x20;      ↓

&#x20;    Nginx

&#x20;      ↓

&#x20;  FastAPI

&#x20;      ↓

&#x20;┌─────┼─────────────┐

&#x20;↓     ↓             ↓

Redis PostgreSQL   Providers

```



Internal services should not be unnecessarily exposed to the public network.



In Docker Compose:



\* Redis should remain internal.

\* PostgreSQL should remain internal.

\* Internal service ports should not be published unless necessary.

\* Only required public ports should be exposed.



\---



\# 26. Nginx Security



Nginx acts as the public-facing reverse proxy.



It may provide:



\* Request size limits

\* Connection handling

\* Basic request filtering

\* TLS termination in appropriate deployments

\* Access logging

\* Routing to FastAPI



The gateway should still perform application-level validation and authorization.



Nginx is not a replacement for application security.



\---



\# 27. TLS / HTTPS



Local development may use plain HTTP.



Production deployments should use HTTPS.



Sensitive credentials and API keys should not be transmitted over unencrypted public connections.



The project documentation should clearly distinguish:



```text

Local development

```



from:



```text

Production deployment

```



\---



\# 28. Dependency Security



Third-party dependencies introduce supply-chain risk.



The project should:



\* Keep dependencies reasonably minimal.

\* Pin or constrain versions appropriately.

\* Regularly update dependencies.

\* Review major dependency changes.

\* Avoid unnecessary packages.

\* Run automated dependency/security checks where practical.



A dependency should not be added merely because it makes a small task slightly easier.



\---



\# 29. Container Security



Docker containers should follow reasonable security practices.



Rules:



\* Use official/minimal base images where practical.

\* Avoid running services with unnecessary privileges.

\* Do not bake secrets into images.

\* Do not copy `.env` files containing secrets into images.

\* Keep exposed ports minimal.

\* Use separate containers for major infrastructure components.

\* Keep images reasonably up to date.



\---



\# 30. Configuration Security



Configuration should be separated from source code.



Examples:



```text

DATABASE\_URL

REDIS\_URL

PROVIDER\_API\_KEY

LOG\_LEVEL

RATE\_LIMIT

CIRCUIT\_BREAKER\_THRESHOLD

```



Development configuration should use `.env`.



Production environments should use appropriate secret/configuration management.



Never rely on source-code constants for secrets.



\---



\# 31. Security Headers



Where appropriate, the reverse proxy or application should configure standard HTTP security headers.



Examples may include:



```text

X-Content-Type-Options

X-Frame-Options

Content-Security-Policy

Strict-Transport-Security

```



The exact headers should depend on how the API is deployed.



Do not add headers blindly without understanding their effect on the API and documentation interface.



\---



\# 32. Authentication vs Authorization



These are different concepts.



\### Authentication



Answers:



> Who are you?



Example:



```text

API key → tenant identification

```



\### Authorization



Answers:



> What are you allowed to do?



Example:



```text

Normal tenant

&#x20;   → Can send LLM requests



Admin

&#x20;   → Can access provider/circuit information



Chaos tester

&#x20;   → Can trigger fault injection

```



The gateway must not assume that successfully authenticating a client means the client can perform every action.



\---



\# 33. Auditability



Security-sensitive actions should be auditable.



Examples:



\* API key creation

\* API key revocation

\* Administrative configuration changes

\* Provider configuration changes

\* Chaos/fault injection

\* Rate-limit configuration changes



Audit records should include useful metadata such as:



```text

timestamp

actor

tenant

action

request\_id

target

result

```



Sensitive credentials should never be stored in audit logs.



\---



\# 34. Security Testing



Security should be tested as part of the project.



Tests should include:



\### Authentication



```text

Missing API key

Invalid API key

Valid API key

```



\### Authorization



```text

Tenant accessing another tenant's data

Normal user accessing admin endpoint

Unauthorized chaos request

```



\### Input validation



```text

Malformed request

Oversized request

Invalid model

Invalid tenant

Invalid configuration

```



\### Rate limiting



```text

Requests below limit

Requests above limit

Multiple requests from same tenant

```



\### Secret protection



Verify that secrets do not appear in:



```text

Logs

Responses

Repository

Docker image

Error messages

```



\---



\# 35. Security and Observability



Security events should be observable without exposing secrets.



Useful security-related metrics include:



```text

authentication\_failures\_total

authorization\_failures\_total

rate\_limit\_rejections\_total

chaos\_requests\_total

invalid\_requests\_total

```



Logs should allow investigation using:



```text

request\_id

tenant\_id

timestamp

event\_type

result

```



\---



\# 36. Development vs Production



The project has different security expectations depending on the environment.



\## Development



Local development may use:



\* HTTP

\* Localhost services

\* Development API keys

\* Local Ollama

\* Simplified authentication

\* Docker Compose



But dangerous functionality should still be protected.



\## Production-like Deployment



A production-like deployment should additionally consider:



\* HTTPS

\* Strong authentication

\* Strong authorization

\* Secret management

\* Restricted network access

\* Secure database credentials

\* Redis authentication

\* Hardened containers

\* Restricted admin endpoints

\* Monitoring and alerting

\* Dependency/security scanning



The local development setup must not be described as production-hardened.



\---



\# 37. Failure-Safe Security Behavior



Security controls should fail safely.



Examples:



```text

Authentication service unavailable

&#x20;       ↓

Do not automatically allow access

```



```text

Redis unavailable

&#x20;       ↓

Do not silently disable security-sensitive rate limits

```



```text

Invalid authentication

&#x20;       ↓

Reject request

```



The exact fallback behavior should be documented and tested for each security-sensitive dependency.



\---



\# 38. Security Non-Goals



The first version does not attempt to implement:



\* Full enterprise identity management

\* OAuth provider ecosystem

\* Complex RBAC systems

\* Hardware security modules

\* Full SIEM platform

\* Advanced DDoS protection

\* Kubernetes security policies

\* Enterprise secret-management infrastructure



These may be added later if a real requirement appears.



The project should not become unnecessarily complex merely to claim additional security features.



\---



\# 39. Security Incident Response



If a credential is exposed:



```text

1\. Revoke/rotate the credential.

2\. Remove the secret from the repository.

3\. Check whether it appeared in logs or artifacts.

4\. Identify potentially affected systems.

5\. Replace the credential in configuration.

6\. Review how the exposure occurred.

7\. Add a preventive control if appropriate.

```



Removing the visible secret from Git is not enough if the actual credential remains valid.



\---



\# 40. Security Checklist



Before considering the project complete:



```text

\[ ] No secrets committed to Git

\[ ] .env excluded from Git

\[ ] .env.example contains no real secrets

\[ ] API authentication implemented

\[ ] Authorization implemented for protected endpoints

\[ ] Tenant isolation verified

\[ ] Rate limiting implemented

\[ ] Request size limits implemented

\[ ] Retry limits enforced

\[ ] Circuit breaker limits enforced

\[ ] Chaos endpoints protected

\[ ] Admin endpoints protected

\[ ] Sensitive information excluded from logs

\[ ] Error responses do not expose internals

\[ ] Redis not publicly exposed

\[ ] PostgreSQL not publicly exposed

\[ ] SQL injection protections verified

\[ ] Command injection risks reviewed

\[ ] Provider URLs controlled by trusted configuration

\[ ] Docker secrets not baked into images

\[ ] Dependency security reviewed

\[ ] Security tests passing

\[ ] Production vs development security documented

```



\---



\# 41. Security Decision Rule



When adding a new feature, ask:



```text

What can an attacker control?

&#x20;       ↓

What can that input access?

&#x20;       ↓

What resources can it consume?

&#x20;       ↓

Can it cross tenant boundaries?

&#x20;       ↓

Can it expose secrets?

&#x20;       ↓

Can it bypass reliability controls?

&#x20;       ↓

Can it reach administrative functionality?

```



Security should be considered before implementation rather than after the feature is complete.



\---



\# 42. Final Security Principle



The Self-Healing LLM Gateway should be:



```text

Authenticated

Authorized

Validated

Tenant-aware

Rate-limited

Secret-safe

Observable

Failure-safe

```



while remaining understandable and practical for a local-first infrastructure project.



Security should improve the reliability and trustworthiness of the gateway without turning the project into an unnecessarily large enterprise security platform.



