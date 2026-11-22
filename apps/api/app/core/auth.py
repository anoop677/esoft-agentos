import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWTError

from app.core.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity and tenant scope."""

    subject: str
    tenant_id: str
    roles: frozenset[str]


@lru_cache(maxsize=8)
def _jwks_client(url: str, cache_seconds: int) -> PyJWKClient:
    return PyJWKClient(url, cache_jwk_set=True, lifespan=cache_seconds)


def verify_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a Keycloak-signed access token and return its validated claims."""
    if not settings.keycloak_issuer or not settings.keycloak_jwks_url:
        raise RuntimeError("Keycloak issuer and JWKS URL must be configured when auth is enabled")

    client = _jwks_client(
        settings.keycloak_jwks_url,
        settings.keycloak_jwks_cache_seconds,
    )
    signing_key = client.get_signing_key_from_jwt(token).key

    return jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=settings.keycloak_audience,
        issuer=settings.keycloak_issuer,
        options={"require": ["exp", "iat", "sub"]},
    )


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    dev_tenant_id: str | None = Header(default=None, alias="X-Dev-Tenant-ID"),
) -> Principal:
    """Resolve an authenticated principal; dev header mode is local-only."""
    settings = get_settings()

    if not settings.auth_enabled:
        if settings.environment.lower() not in {"development", "local", "test"}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication must be enabled outside local development",
            )
        if not dev_tenant_id or not dev_tenant_id.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X-Dev-Tenant-ID is required in local development mode",
            )
        return Principal(
            subject="local-dev-user",
            tenant_id=dev_tenant_id.strip(),
            roles=frozenset({"developer"}),
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer access token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = await asyncio.to_thread(
            verify_access_token,
            credentials.credentials,
            settings,
        )
    except PyJWKClientConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider is temporarily unavailable",
        ) from exc
    except (PyJWKClientError, PyJWTError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider configuration is incomplete",
        ) from exc

    subject = claims.get("sub")
    tenant_id = claims.get(settings.tenant_claim)
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=401, detail="Token subject is missing")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise HTTPException(status_code=403, detail="Token does not contain a valid tenant claim")

    realm_access = claims.get("realm_access", {})
    role_values = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
    roles = frozenset(role for role in role_values if isinstance(role, str))

    return Principal(subject=subject, tenant_id=tenant_id.strip(), roles=roles)
