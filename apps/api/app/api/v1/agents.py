import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.auth import Principal, get_current_principal
from app.infrastructure.tenant_db import (
    get_tenant_admin_connection,
    get_tenant_connection,
)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

Slug = Annotated[str, Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]+$")]


class AgentManifest(BaseModel):
    purpose: str = Field(min_length=1, max_length=2000)
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    autonomy_level: Literal["A0", "A1", "A2", "A3"] = "A1"


class ModelSettings(BaseModel):
    provider: Literal["openai", "anthropic", "gemini", "local"]
    model: str = Field(min_length=1, max_length=160)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=2048, ge=1, le=8192)


class ToolPermission(BaseModel):
    tool_id: str = Field(min_length=1, max_length=160)
    actions: list[str] = Field(min_length=1, max_length=50)
    approval_required: bool = True
    max_calls_per_run: int = Field(default=10, ge=1, le=100)

    @field_validator("actions")
    @classmethod
    def actions_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Tool actions must be unique")
        return value


class AgentConfiguration(BaseModel):
    manifest: AgentManifest
    model_settings: ModelSettings
    tool_permissions: list[ToolPermission] = Field(default_factory=list, max_length=100)


class AgentCreate(AgentConfiguration):
    slug: Slug
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)


class AgentVersionCreate(AgentConfiguration):
    pass


class AgentStatusUpdate(BaseModel):
    status: Literal["draft", "active", "disabled"]


class AgentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    slug: str
    name: str
    description: str
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class AgentVersionSummary(BaseModel):
    id: UUID
    version: int
    manifest: AgentManifest
    model_settings: ModelSettings
    tool_permissions: list[ToolPermission]
    created_by: str
    created_at: datetime


class AgentDetail(AgentSummary):
    latest_version: AgentVersionSummary | None


def _version_payload(configuration: AgentConfiguration) -> tuple[str, str, str]:
    return (
        json.dumps(configuration.manifest.model_dump(mode="json")),
        json.dumps(configuration.model_settings.model_dump(mode="json")),
        json.dumps([item.model_dump(mode="json") for item in configuration.tool_permissions]),
    )


def _version_from_row(row: asyncpg.Record | dict) -> AgentVersionSummary:
    return AgentVersionSummary(
        id=row["id"],
        version=row["version"],
        manifest=row["manifest"],
        model_settings=row["model_config"],
        tool_permissions=row["tool_permissions"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


async def _get_agent_detail(
    connection: asyncpg.Connection,
    tenant_id: str,
    agent_id: UUID,
) -> AgentDetail:
    row = await connection.fetchrow(
        """
        SELECT a.id, a.tenant_id, a.slug, a.name, a.description, a.status,
               a.created_by, a.created_at, a.updated_at,
               v.id AS version_id, v.version, v.manifest, v.model_config,
               v.tool_permissions, v.created_by AS version_created_by,
               v.created_at AS version_created_at
        FROM public.agent_definitions a
        LEFT JOIN LATERAL (
            SELECT id, version, manifest, model_config, tool_permissions,
                   created_by, created_at
            FROM public.agent_versions
            WHERE tenant_id = a.tenant_id AND agent_id = a.id
            ORDER BY version DESC
            LIMIT 1
        ) v ON TRUE
        WHERE a.tenant_id = $1 AND a.id = $2
        """,
        tenant_id,
        agent_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    latest = None
    if row["version_id"] is not None:
        latest = AgentVersionSummary(
            id=row["version_id"],
            version=row["version"],
            manifest=row["manifest"],
            model_settings=row["model_config"],
            tool_permissions=row["tool_permissions"],
            created_by=row["version_created_by"],
            created_at=row["version_created_at"],
        )
    return AgentDetail(
        id=row["id"],
        tenant_id=row["tenant_id"],
        slug=row["slug"],
        name=row["name"],
        description=row["description"],
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        latest_version=latest,
    )


@router.get("", response_model=list[AgentSummary])
async def list_agents(
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    agent_status: Literal["draft", "active", "disabled"] | None = Query(
        default=None, alias="status"
    ),
) -> list[AgentSummary]:
    rows = await connection.fetch(
        """
        SELECT id, tenant_id, slug, name, description, status,
               created_by, created_at, updated_at
        FROM public.agent_definitions
        WHERE tenant_id = $1 AND ($2::text IS NULL OR status = $2)
        ORDER BY updated_at DESC, id
        LIMIT $3 OFFSET $4
        """,
        principal.tenant_id,
        agent_status,
        limit,
        offset,
    )
    return [AgentSummary.model_validate(dict(row)) for row in rows]


@router.post("", response_model=AgentDetail, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentDetail:
    manifest, model_config, tool_permissions = _version_payload(payload)
    try:
        async with connection.transaction():
            agent_id = await connection.fetchval(
                """
                INSERT INTO public.agent_definitions
                    (tenant_id, slug, name, description, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                principal.tenant_id,
                payload.slug,
                payload.name,
                payload.description,
                principal.subject,
            )
            await connection.execute(
                """
                INSERT INTO public.agent_versions
                    (tenant_id, agent_id, version, manifest, model_config,
                     tool_permissions, created_by)
                VALUES ($1, $2, 1, $3::jsonb, $4::jsonb, $5::jsonb, $6)
                """,
                principal.tenant_id,
                agent_id,
                manifest,
                model_config,
                tool_permissions,
                principal.subject,
            )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(
            status_code=409,
            detail="An agent with this slug already exists in the tenant",
        ) from exc
    return await _get_agent_detail(connection, principal.tenant_id, agent_id)


@router.get("/{agent_id}", response_model=AgentDetail)
async def get_agent(
    agent_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentDetail:
    return await _get_agent_detail(connection, principal.tenant_id, agent_id)


@router.post("/{agent_id}/versions", response_model=AgentVersionSummary,
             status_code=status.HTTP_201_CREATED)
async def create_agent_version(
    agent_id: UUID,
    payload: AgentVersionCreate,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentVersionSummary:
    manifest, model_config, tool_permissions = _version_payload(payload)
    async with connection.transaction():
        agent = await connection.fetchrow(
            """
            SELECT id, status
            FROM public.agent_definitions
            WHERE tenant_id = $1 AND id = $2
            FOR UPDATE
            """,
            principal.tenant_id,
            agent_id,
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        version = await connection.fetchval(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM public.agent_versions
            WHERE tenant_id = $1 AND agent_id = $2
            """,
            principal.tenant_id,
            agent_id,
        )
        row = await connection.fetchrow(
            """
            INSERT INTO public.agent_versions
                (tenant_id, agent_id, version, manifest, model_config,
                 tool_permissions, created_by)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7)
            RETURNING id, version, manifest, model_config, tool_permissions,
                      created_by, created_at
            """,
            principal.tenant_id,
            agent_id,
            version,
            manifest,
            model_config,
            tool_permissions,
            principal.subject,
        )
    return _version_from_row(row)


@router.patch("/{agent_id}/status", response_model=AgentSummary)
async def update_agent_status(
    agent_id: UUID,
    payload: AgentStatusUpdate,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentSummary:
    row = await connection.fetchrow(
        """
        UPDATE public.agent_definitions
        SET status = $3, updated_at = now()
        WHERE tenant_id = $1 AND id = $2
        RETURNING id, tenant_id, slug, name, description, status,
                  created_by, created_at, updated_at
        """,
        principal.tenant_id,
        agent_id,
        payload.status,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentSummary.model_validate(dict(row))
