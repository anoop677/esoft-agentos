# AgentOS API

FastAPI service for eSoft AgentOS.

## Requirements

- Docker Engine and Docker Compose plugin
- Or Python 3.12+ for running the API outside Docker

## Run with Docker Compose

From the repository root:

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api python scripts/apply_migrations.py
```

API: http://localhost:8000  
OpenAPI docs: http://localhost:8000/docs  
Liveness: http://localhost:8000/health/live  
Readiness: http://localhost:8000/health/ready  
Current principal: http://localhost:8000/api/v1/me

The Compose startup runs a one-shot database bootstrap service before the API. It creates or updates the restricted `agentos_app` role without deleting an existing database volume.

## Identity and tenant context

### Local identity mode

For local development only, authentication is disabled by default. The identity endpoint requires an explicit tenant header:

```bash
curl -H "X-Dev-Tenant-ID: demo" http://localhost:8000/api/v1/me
```

This development header is rejected outside `development`, `local`, or `test` environments. Do not use it as a production authentication mechanism.

### Keycloak configuration

Set these environment variables when connecting a Keycloak realm:

- `AUTH_ENABLED=true`
- `KEYCLOAK_ISSUER=https://<keycloak-host>/realms/<realm>`
- `KEYCLOAK_JWKS_URL=https://<keycloak-host>/realms/<realm>/protocol/openid-connect/certs`
- `KEYCLOAK_AUDIENCE=agentos-api`

The Keycloak access token must include a tenant claim named `tenant_id` by default. The claim name can be changed using `TENANT_CLAIM`. The API validates the token signature, issuer, audience, expiry, and subject using the realm's JWKS. Configure a dedicated API audience mapper in Keycloak rather than relying on an unverified client-side value.

## Database migrations and tenant isolation

SQL migrations live in `migrations/` and are tracked in `public.schema_migrations`. Apply pending migrations with:

```bash
docker compose exec api python scripts/apply_migrations.py
```

The migrations create:

- Tenant directory and tenant membership tables.
- Tenant-scoped agent definitions and immutable agent version records.
- Durable agent run records pinned to an immutable agent version.
- PostgreSQL row-level security (RLS) policies on tenant-owned tables.
- Forced RLS, including for the table owner.

Database identities are deliberately separated:

- `agentos` is the local database administrator used only by the migration runner.
- `agentos_app` is the API runtime role. It is `NOSUPERUSER`, `NOBYPASSRLS`, and does not own tenant tables.
- The migration grants the runtime role only the required table operations.

Every tenant-scoped query must use `get_tenant_connection` from `app/infrastructure/tenant_db.py`. It opens a transaction, sets `app.tenant_id` from the verified principal using transaction-local configuration, and confirms the tenant and caller membership are active. The setting is cleared when the transaction ends and the pooled connection is returned.

Do not run the application as a PostgreSQL superuser or a role with `BYPASSRLS`. Tenant provisioning must be performed by a trusted platform provisioning process; do not let a client choose an arbitrary tenant ID and create its own tenant.

## Agent Registry and execution queue

Agent definitions are managed through `/api/v1/agents`. Configuration writes require an active tenant membership with the database role `owner` or `admin`; reads are tenant-scoped. Each agent starts in `draft` and stores an immutable version 1 configuration.

Execution requests are accepted only for agents in `active` status. `POST /api/v1/agents/{agent_id}/runs` validates a bounded JSON input payload and creates a durable `queued` run pinned to the latest immutable agent version. The response is HTTP 202. Use `GET /api/v1/runs/{run_id}` to inspect its state or `GET /api/v1/agents/{agent_id}/runs` to list runs. Run creation does not execute model calls or tools yet; the worker and model gateway are the next runtime milestone. No tool permission is bypassed and no external action is performed by these endpoints.

## Background worker

Docker Compose starts a separate `worker` process alongside the API. It claims jobs with PostgreSQL `FOR UPDATE SKIP LOCKED`, uses a five-minute lease to recover abandoned work, pins execution to the agent version selected when the run was queued, and records output, model name, token usage, and bounded failure details. Failed model calls are retried up to the run's configured attempt limit. Set `OPENAI_API_KEY` in `.env` to enable model execution. The worker uses the restricted `agentos_app` database role; it does not use migration/admin credentials.

The first runtime milestone intentionally has no tool-calling capability. It is a text-only model execution path. Never grant tools or production credentials to an agent until the approval/policy engine and audited tool adapters are in place.

## Run tests locally

From `apps/api`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

The PostgreSQL RLS integration test runs when `TEST_DATABASE_URL` or `DATABASE_URL` is set and the schema migration has been applied. It verifies that tenant A cannot read or update records belonging to tenant B and cannot insert rows for another tenant. GitHub Actions runs these tests using the restricted application role, not the database administrator.

## Configuration

Configuration is supplied through environment variables. See `app/core/config.py` for the current settings. Do not commit real credentials or production secrets.
