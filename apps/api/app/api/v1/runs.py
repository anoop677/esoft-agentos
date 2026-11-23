import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.core.auth import Principal, get_current_principal
from app.infrastructure.tenant_db import get_tenant_connection

router = APIRouter(prefix="/api/v1", tags=["agent-runs"])


class AgentRunCreate(BaseModel):
    input: dict = Field(default_factory=dict, max_length=100)

    @field_validator("input")
    @classmethod
    def input_must_be_bounded_json(cls, value: dict) -> dict:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 65536:
            raise ValueError("Run input must not exceed 64 KiB")
        return value


class AgentRunSummary(BaseModel):
    id: UUID
    agent_id: UUID
    agent_version_id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    input: dict
    output: dict | None
    error_code: str | None
    error_message: str | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _run_from_row(row: asyncpg.Record | dict) -> AgentRunSummary:
    values = dict(row)
    for field in ("input", "output"):
        if isinstance(values.get(field), str):
            values[field] = json.loads(values[field])
    return AgentRunSummary.model_validate(values)


@router.post(
    "/agents/{agent_id}/runs",
    response_model=AgentRunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_agent_run(
    agent_id: UUID,
    payload: AgentRunCreate,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentRunSummary:
    agent = await connection.fetchrow(
        """
        SELECT a.id, a.status, v.id AS agent_version_id
        FROM public.agent_definitions a
        JOIN LATERAL (
            SELECT id
            FROM public.agent_versions
            WHERE tenant_id = a.tenant_id AND agent_id = a.id
            ORDER BY version DESC
            LIMIT 1
        ) v ON TRUE
        WHERE a.tenant_id = $1 AND a.id = $2
        """,
        principal.tenant_id,
        agent_id,
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only active agents can be executed",
        )

    row = await connection.fetchrow(
        """
        INSERT INTO public.agent_runs
            (tenant_id, agent_id, agent_version_id, requested_by, input)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        RETURNING id, agent_id, agent_version_id, status, input, output,
                  error_code, error_message, attempt_count, created_at,
                  updated_at, started_at, finished_at
        """,
        principal.tenant_id,
        agent_id,
        agent["agent_version_id"],
        principal.subject,
        json.dumps(payload.input),
    )
    await connection.execute(
        """
        INSERT INTO public.agent_run_queue (tenant_id, run_id)
        VALUES ($1, $2)
        """,
        principal.tenant_id,
        row["id"],
    )
    return _run_from_row(row)


@router.get("/runs/{run_id}", response_model=AgentRunSummary)
async def get_agent_run(
    run_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AgentRunSummary:
    row = await connection.fetchrow(
        """
        SELECT id, agent_id, agent_version_id, status, input, output,
               error_code, error_message, attempt_count, created_at,
               updated_at, started_at, finished_at
        FROM public.agent_runs
        WHERE tenant_id = $1 AND id = $2
        """,
        principal.tenant_id,
        run_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return _run_from_row(row)


@router.get("/agents/{agent_id}/runs", response_model=list[AgentRunSummary])
async def list_agent_runs(
    agent_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[AgentRunSummary]:
    rows = await connection.fetch(
        """
        SELECT id, agent_id, agent_version_id, status, input, output,
               error_code, error_message, attempt_count, created_at,
               updated_at, started_at, finished_at
        FROM public.agent_runs
        WHERE tenant_id = $1 AND agent_id = $2
        ORDER BY created_at DESC, id
        LIMIT $3 OFFSET $4
        """,
        principal.tenant_id,
        agent_id,
        limit,
        offset,
    )
    return [_run_from_row(row) for row in rows]
