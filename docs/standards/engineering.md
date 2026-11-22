# Engineering Standards

## General
- Prefer small, cohesive modules and explicit boundaries.
- Keep domain logic independent of web frameworks, model providers, and cloud vendors.
- Use type hints and validated request/response schemas at service boundaries.
- Fail closed when tenant context, authorization, or required business data is missing.
- Do not commit credentials, tokens, private keys, customer data, or production dumps.

## Agent and tool contracts
Every agent/tool must define:
- Stable identifier and semantic version.
- Purpose, supported actions, and explicit out-of-scope behavior.
- Typed input and output schemas.
- Required permissions and resource scopes.
- Timeout, retry, and idempotency behavior.
- Execution budget and cancellation behavior.
- Structured error taxonomy.
- Unit and integration tests.

Treat model-generated content, retrieved documents, and tool output as untrusted data. Validate all values before use.

## Security
- Enforce tenant context on every business-data access.
- Use least-privilege service identities and connector credentials.
- Never let an agent execute arbitrary SQL, shell commands, or unrestricted network requests.
- Recheck policy and relevant business state immediately before consequential writes.
- Require human approval for sensitive actions in the MVP.
- Redact secrets and minimize sensitive data in logs.
- Provide an operational kill switch for agent/workflow execution.

## Reliability
- Mutating operations must be idempotent or protected against duplicate execution.
- Use bounded retries with backoff; do not retry permanent validation or authorization failures.
- Set explicit timeouts and request budgets.
- Make partial failures visible and recoverable.
- Test duplicate events, concurrent updates, stale data, provider outages, and connector timeouts.

## Quality gates
A feature is not complete until it has:
1. Unit tests for domain logic.
2. Integration tests for persistence and connector boundaries.
3. Authorization and tenant-isolation tests.
4. Failure-path tests.
5. Regression coverage for fixed defects.
6. Relevant documentation and configuration examples.

## Pull requests
- Work on feature branches based on `develop`.
- Keep pull requests focused and explain risk, tests, and rollout/rollback.
- Do not commit directly to `main`.
- Do not merge changes that bypass required checks or approval policies.
