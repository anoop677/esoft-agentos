import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.auth import Principal, get_current_principal
from app.core.config import get_settings
from app.infrastructure.credential_vault import CredentialEncryptionError, encrypt_credentials

from app.infrastructure.connectors import (
    get_connector_adapter,
    list_connector_descriptors,
)
from app.infrastructure.tenant_db import get_tenant_admin_connection, get_tenant_connection

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])


class ERPNextConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_url: AnyHttpUrl
    api_version: str = Field(default="v1", min_length=1, max_length=20)

    @field_validator("site_url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("ERPNext site_url must use HTTPS")
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("ERPNext site_url must not contain credentials, query parameters, or fragments")
        return value


class ConnectorCreate(BaseModel):
    connector_key: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
    connector_type: Literal["erpnext"]
    display_name: str = Field(min_length=1, max_length=160)
    configuration: ERPNextConfiguration


class ConnectorStatusUpdate(BaseModel):
    status: Literal["draft", "active", "disabled"]


class ConnectorSummary(BaseModel):
    id: UUID
    connector_key: str
    connector_type: str
    display_name: str
    status: str
    configuration: dict
    created_by: str
    created_at: datetime
    updated_at: datetime


def _connector(row: asyncpg.Record | dict) -> ConnectorSummary:
    values = dict(row)
    if isinstance(values["configuration"], str):
        values["configuration"] = json.loads(values["configuration"])
    return ConnectorSummary.model_validate(values)


@router.get("/catalog")
async def connector_catalog() -> list[dict]:
    """List supported connector types and their declared capabilities."""
    return [
        {
            "connector_type": item.connector_type,
            "display_name": item.display_name,
            "description": item.description,
            "capabilities": list(item.capabilities),
            "mode": item.mode,
        }
        for item in list_connector_descriptors()
    ]


@router.get("", response_model=list[ConnectorSummary])
async def list_connectors(
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    connector_status: Literal["draft", "active", "disabled"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ConnectorSummary]:
    rows = await connection.fetch(
        """
        SELECT id, connector_key, connector_type, display_name, status,
               configuration, created_by, created_at, updated_at
        FROM public.agent_connectors
        WHERE tenant_id = $1 AND ($2::text IS NULL OR status = $2)
        ORDER BY created_at DESC, id
        LIMIT $3 OFFSET $4
        """,
        principal.tenant_id, connector_status, limit, offset,
    )
    return [_connector(row) for row in rows]


@router.post("", response_model=ConnectorSummary, status_code=status.HTTP_201_CREATED)
async def create_connector(
    payload: ConnectorCreate,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ConnectorSummary:
    adapter = get_connector_adapter(payload.connector_type)
    if adapter is None:
        raise HTTPException(status_code=422, detail="Connector type is not registered")
    configuration = payload.configuration.model_dump(mode="json")
    errors = adapter.validate_configuration(configuration)
    if errors:
        raise HTTPException(status_code=422, detail={"configuration_errors": errors})
    try:
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO public.agent_connectors
                    (tenant_id, connector_key, connector_type, display_name,
                     configuration, created_by)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                RETURNING id, connector_key, connector_type, display_name, status,
                          configuration, created_by, created_at, updated_at
                """,
                principal.tenant_id, payload.connector_key, payload.connector_type,
                payload.display_name, json.dumps(configuration), principal.subject,
            )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Connector key already exists") from exc
    return _connector(row)


@router.get("/{connector_id}", response_model=ConnectorSummary)
async def get_connector(
    connector_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ConnectorSummary:
    row = await connection.fetchrow(
        """
        SELECT id, connector_key, connector_type, display_name, status,
               configuration, created_by, created_at, updated_at
        FROM public.agent_connectors
        WHERE tenant_id = $1 AND id = $2
        """,
        principal.tenant_id, connector_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _connector(row)


@router.patch("/{connector_id}/status", response_model=ConnectorSummary)
async def update_connector_status(
    connector_id: UUID,
    payload: ConnectorStatusUpdate,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ConnectorSummary:
    row = await connection.fetchrow(
        """
        UPDATE public.agent_connectors
        SET status = $3, updated_at = now()
        WHERE tenant_id = $1 AND id = $2
        RETURNING id, connector_key, connector_type, display_name, status,
                  configuration, created_by, created_at, updated_at
        """,
        principal.tenant_id, connector_id, payload.status,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _connector(row)


@router.post("/{connector_id}/validate")
async def validate_connector_configuration(
    connector_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    row = await connection.fetchrow(
        """
        SELECT connector_type, status, configuration
        FROM public.agent_connectors
        WHERE tenant_id = $1 AND id = $2
        """,
        principal.tenant_id, connector_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Connector not found")
    configuration = row["configuration"]
    if isinstance(configuration, str):
        configuration = json.loads(configuration)
    adapter = get_connector_adapter(row["connector_type"])
    if adapter is None:
        return {"valid": False, "mode": "unsupported", "errors": ["No adapter is registered"]}
    errors = adapter.validate_configuration(configuration)
    return {
        "valid": not errors,
        "connector_status": row["status"],
        "mode": adapter.descriptor.mode,
        "network_check": "not_performed",
        "credentials": "not_configured",
        "errors": errors,
        "message": "Configuration validated only; no remote ERPNext request was made.",
    }



class ERPNextCredentialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=512, repr=False)
    api_secret: SecretStr = Field(min_length=1, max_length=512, repr=False)


@router.put("/{connector_id}/credentials", status_code=status.HTTP_200_OK)
async def put_connector_credentials(
    connector_id: UUID,
    payload: ERPNextCredentialsInput,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    settings = get_settings()
    if not settings.connector_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connector credential encryption is not configured",
        )

    exists = await connection.fetchval(
        "SELECT 1 FROM public.agent_connectors WHERE tenant_id = $1 AND id = $2",
        principal.tenant_id, connector_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Connector not found")

    try:
        encrypted = encrypt_credentials(
            {
                "api_key": payload.api_key.get_secret_value(),
                "api_secret": payload.api_secret.get_secret_value(),
            },
            encryption_key=settings.connector_encryption_key,
            key_version=settings.connector_encryption_key_version,
            tenant_id=principal.tenant_id,
            connector_id=str(connector_id),
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Connector credential encryption is not configured correctly",
        ) from exc
    await connection.execute(
        """
        INSERT INTO public.agent_connector_secrets
            (tenant_id, connector_id, ciphertext, nonce, key_version, updated_by)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (tenant_id, connector_id) DO UPDATE
        SET ciphertext = EXCLUDED.ciphertext,
            nonce = EXCLUDED.nonce,
            key_version = EXCLUDED.key_version,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
        """,
        principal.tenant_id, connector_id, encrypted.ciphertext, encrypted.nonce,
        encrypted.key_version, principal.subject,
    )
    return {"configured": True, "message": "Credentials encrypted and stored; secret values are never returned."}


@router.get("/{connector_id}/credentials")
async def get_connector_credentials_status(
    connector_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    row = await connection.fetchrow(
        """
        SELECT s.key_version, s.updated_at, s.updated_by
        FROM public.agent_connector_secrets s
        JOIN public.agent_connectors c
          ON c.tenant_id = s.tenant_id AND c.id = s.connector_id
        WHERE s.tenant_id = $1 AND s.connector_id = $2
        """,
        principal.tenant_id, connector_id,
    )
    if row is None:
        return {"configured": False}
    return {"configured": True, "key_version": row["key_version"], "updated_at": row["updated_at"]}


@router.delete("/{connector_id}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector_credentials(
    connector_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    exists = await connection.fetchval(
        "SELECT 1 FROM public.agent_connectors WHERE tenant_id = $1 AND id = $2",
        principal.tenant_id, connector_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Connector not found")
    await connection.execute(
        "DELETE FROM public.agent_connector_secrets WHERE tenant_id = $1 AND connector_id = $2",
        principal.tenant_id, connector_id,
    )
