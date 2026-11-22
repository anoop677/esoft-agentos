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

The readiness endpoint checks PostgreSQL and Redis. The liveness endpoint only checks that the API process is responding.

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

The first migration creates:

- Tenant directory and tenant membership tables.
- Tenant-scoped agent definitions and immutable agent version records.
- PostgreSQL row-level security (RLS) policies on all four tenant-owned tables.
- Forced RLS, including for the table owner, so a normal application database role cannot bypass policies.

Every tenant-scoped query must use `get_tenant_connection` from `app/infrastructure/tenant_db.py`. It opens a transaction and sets `app.tenant_id` from the verified principal using transaction-local configuration. The value is cleared automatically when the transaction ends and the pooled connection is returned.

Do not run the application as a PostgreSQL superuser or a role with `BYPASSRLS`. Tenant provisioning must be performed by a trusted platform provisioning process; do not let a client choose an arbitrary tenant ID and create its own tenant.

## Run tests locally

From `apps/api`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

The PostgreSQL RLS integration test runs when `TEST_DATABASE_URL` or `DATABASE_URL` is set and the schema migration has been applied. It verifies that tenant A cannot read, update, or insert records belonging to tenant B. The GitHub Actions workflow starts PostgreSQL, applies migrations, and runs this integration test on every feature-branch push and pull request.

## Configuration

Configuration is supplied through environment variables. See `app/core/config.py` for the current settings. Do not commit real credentials or production secrets.
