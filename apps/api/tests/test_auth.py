from typing import Annotated

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import auth
from app.core.auth import Principal, get_current_principal
from app.core.config import Settings

test_app = FastAPI()


@test_app.get("/private")
async def private_endpoint(
    principal: Annotated[Principal, Depends(get_current_principal)],
):
    return {
        "subject": principal.subject,
        "tenant_id": principal.tenant_id,
        "roles": sorted(principal.roles),
    }


async def request_private(headers: dict[str, str] | None = None):
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as client:
        return await client.get("/private", headers=headers or {})


async def test_local_development_requires_explicit_tenant(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(environment="development", auth_enabled=False),
    )

    response = await request_private()

    assert response.status_code == 401
    assert response.json()["detail"] == "X-Dev-Tenant-ID is required in local development mode"


async def test_local_development_resolves_tenant(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(environment="development", auth_enabled=False),
    )

    response = await request_private({"X-Dev-Tenant-ID": "tenant-demo"})

    assert response.status_code == 200
    assert response.json() == {
        "subject": "local-dev-user",
        "tenant_id": "tenant-demo",
        "roles": ["developer"],
    }


async def test_keycloak_claims_resolve_tenant_and_roles(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(
            environment="production",
            auth_enabled=True,
            keycloak_issuer="https://identity.example.test/realms/agentos",
            keycloak_jwks_url="https://identity.example.test/realms/agentos/protocol/openid-connect/certs",
        ),
    )
    monkeypatch.setattr(
        auth,
        "verify_access_token",
        lambda token, settings: {
            "sub": "user-123",
            "tenant_id": "tenant-acme",
            "realm_access": {"roles": ["agent-admin", "workflow-user"]},
        },
    )

    response = await request_private({"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    assert response.json() == {
        "subject": "user-123",
        "tenant_id": "tenant-acme",
        "roles": ["agent-admin", "workflow-user"],
    }


async def test_keycloak_token_without_tenant_is_rejected(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(
            environment="production",
            auth_enabled=True,
            keycloak_issuer="https://identity.example.test/realms/agentos",
            keycloak_jwks_url="https://identity.example.test/realms/agentos/protocol/openid-connect/certs",
        ),
    )
    monkeypatch.setattr(
        auth,
        "verify_access_token",
        lambda token, settings: {"sub": "user-123"},
    )

    response = await request_private({"Authorization": "Bearer test-token"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Token does not contain a valid tenant claim"
