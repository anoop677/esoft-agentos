import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.core.auth import Principal, get_current_principal
from app.infrastructure.policy import evaluate_tool_proposal, sanitize_arguments
from app.infrastructure.tenant_db import get_tenant_admin_connection, get_tenant_connection

router = APIRouter(prefix="/api/v1", tags=["security-approvals"])


class ToolProposal(BaseModel):
    tool_id: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def arguments_are_bounded(cls, value: dict) -> dict:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 16384:
            raise ValueError("Tool arguments must not exceed 16 KiB")
        return value


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = Field(default="", max_length=1000)


class ApprovalSummary(BaseModel):
    id: UUID
    run_id: UUID
    tool_id: str
    action: str
    arguments: dict
    status: str
    requested_by: str
    decided_by: str | None
    decision_comment: str | None
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime


class TenantControlsUpdate(BaseModel):
    agents_enabled: bool


def _decode_json(value):
    return json.loads(value) if isinstance(value, str) else value


def _approval(row: asyncpg.Record | dict) -> ApprovalSummary:
    values = dict(row)
    values["arguments"] = _decode_json(values["arguments"])
    return ApprovalSummary.model_validate(values)


async def _event(
    connection: asyncpg.Connection,
    tenant_id: str,
    run_id: UUID,
    event_type: str,
    actor: str,
    details: dict,
) -> None:
    await connection.execute(
        """
        INSERT INTO public.agent_run_events (tenant_id, run_id, event_type, actor, details)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        tenant_id,
        run_id,
        event_type,
        actor,
        json.dumps(details),
    )


@router.post(
    "/runs/{run_id}/tool-proposals",
    status_code=status.HTTP_202_ACCEPTED,
)
async def propose_tool_action(
    run_id: UUID,
    payload: ToolProposal,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    async with connection.transaction():
        run = await connection.fetchrow(
            """
            SELECT r.id, r.agent_id, r.status, a.status AS agent_status,
                   v.manifest, v.tool_permissions,
                   COALESCE(c.agents_enabled, TRUE) AS agents_enabled,
                   t.enabled AS tool_enabled, t.risk_tier AS tool_risk_tier
            FROM public.agent_runs r
            JOIN public.agent_definitions a
              ON a.tenant_id = r.tenant_id AND a.id = r.agent_id
            JOIN public.agent_versions v
              ON v.tenant_id = r.tenant_id AND v.id = r.agent_version_id
            LEFT JOIN public.tenant_agentos_controls c ON c.tenant_id = r.tenant_id
            LEFT JOIN public.agent_tool_catalog t ON t.tool_id = $3
            WHERE r.tenant_id = $1 AND r.id = $2
            FOR UPDATE OF r
            """,
            principal.tenant_id,
            run_id,
            payload.tool_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found")
        if run["status"] not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="Tool proposals require an active run")

        manifest = _decode_json(run["manifest"])
        permissions = _decode_json(run["tool_permissions"])
        decision = evaluate_tool_proposal(
            agent_status=run["agent_status"],
            autonomy_level=manifest.get("autonomy_level", "A1"),
            tool_permissions=permissions,
            tool_id=payload.tool_id,
            action=payload.action,
            risk_tier=run["tool_risk_tier"] or "critical",
            catalog_enabled=bool(run["tool_enabled"]),
            agents_enabled=run["agents_enabled"],
        )
        if not decision.allowed:
            raise HTTPException(status_code=403, detail=decision.reason)

        if not decision.approval_required:
            await _event(
                connection, principal.tenant_id, run_id, "tool_proposal_authorized",
                principal.subject, {"tool_id": payload.tool_id, "action": payload.action,
                                    "execution": "not_implemented"},
            )
            return {
                "status": "authorized_not_executed",
                "approval_required": False,
                "message": "Policy allows this action, but tool execution is not enabled in this release.",
            }

        row = await connection.fetchrow(
            """
            INSERT INTO public.agent_approvals
                (tenant_id, run_id, tool_id, action, arguments, requested_by)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            RETURNING id, run_id, tool_id, action, arguments, status, requested_by,
                      decided_by, decision_comment, expires_at, decided_at, created_at
            """,
            principal.tenant_id,
            run_id,
            payload.tool_id,
            payload.action,
            json.dumps(sanitize_arguments(payload.arguments)),
            principal.subject,
        )
        await _event(
            connection, principal.tenant_id, run_id, "approval_requested",
            principal.subject, {"approval_id": str(row["id"]), "tool_id": payload.tool_id,
                                "action": payload.action},
        )
    return {"status": "approval_pending", "approval": _approval(row)}


@router.get("/approvals", response_model=list[ApprovalSummary])
async def list_approvals(
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    approval_status: Literal["pending", "approved", "rejected", "expired", "cancelled"] | None = Query(
        default="pending", alias="status"
    ),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ApprovalSummary]:
    await connection.execute(
        """
        UPDATE public.agent_approvals
        SET status = 'expired'
        WHERE tenant_id = $1 AND status = 'pending' AND expires_at <= now()
        """,
        principal.tenant_id,
    )
    rows = await connection.fetch(
        """
        SELECT id, run_id, tool_id, action, arguments, status, requested_by,
               decided_by, decision_comment, expires_at, decided_at, created_at
        FROM public.agent_approvals
        WHERE tenant_id = $1 AND ($2::text IS NULL OR status = $2)
        ORDER BY created_at DESC, id
        LIMIT $3 OFFSET $4
        """,
        principal.tenant_id, approval_status, limit, offset,
    )
    return [_approval(row) for row in rows]


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalSummary)
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecision,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalSummary:
    async with connection.transaction():
        current = await connection.fetchrow(
            """
            SELECT id, run_id, status, expires_at
            FROM public.agent_approvals
            WHERE tenant_id = $1 AND id = $2
            FOR UPDATE
            """,
            principal.tenant_id, approval_id,
        )
        if current is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        if current["status"] != "pending":
            raise HTTPException(status_code=409, detail=f"Approval is already {current['status']}")
        if current["expires_at"] <= datetime.now(current["expires_at"].tzinfo):
            raise HTTPException(status_code=409, detail="Approval has expired")

        row = await connection.fetchrow(
            """
            UPDATE public.agent_approvals
            SET status = $3, decided_by = $4, decision_comment = $5, decided_at = now()
            WHERE tenant_id = $1 AND id = $2 AND status = 'pending'
            RETURNING id, run_id, tool_id, action, arguments, status, requested_by,
                      decided_by, decision_comment, expires_at, decided_at, created_at
            """,
            principal.tenant_id, approval_id, payload.decision, principal.subject, payload.comment,
        )
        await _event(
            connection, principal.tenant_id, current["run_id"],
            "approval_" + payload.decision, principal.subject,
            {"approval_id": str(approval_id), "comment": payload.comment},
        )
    return _approval(row)


@router.get("/runs/{run_id}/events")
async def list_run_events(
    run_id: UUID,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    rows = await connection.fetch(
        """
        SELECT id, event_type, actor, details, created_at
        FROM public.agent_run_events
        WHERE tenant_id = $1 AND run_id = $2
        ORDER BY id
        LIMIT $3
        """,
        principal.tenant_id, run_id, limit,
    )
    return [
        {"id": row["id"], "event_type": row["event_type"], "actor": row["actor"],
         "details": _decode_json(row["details"]), "created_at": row["created_at"]}
        for row in rows
    ]


@router.get("/security/tenant-controls")
async def get_tenant_controls(
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    row = await connection.fetchrow(
        "SELECT agents_enabled, updated_by, updated_at FROM public.tenant_agentos_controls WHERE tenant_id = $1",
        principal.tenant_id,
    )
    if row is None:
        return {"agents_enabled": True, "updated_by": None, "updated_at": None}
    return dict(row)


@router.patch("/security/tenant-controls")
async def update_tenant_controls(
    payload: TenantControlsUpdate,
    connection: Annotated[asyncpg.Connection, Depends(get_tenant_admin_connection)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict:
    row = await connection.fetchrow(
        """
        INSERT INTO public.tenant_agentos_controls (tenant_id, agents_enabled, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (tenant_id) DO UPDATE
        SET agents_enabled = EXCLUDED.agents_enabled,
            updated_by = EXCLUDED.updated_by, updated_at = now()
        RETURNING agents_enabled, updated_by, updated_at
        """,
        principal.tenant_id, payload.agents_enabled, principal.subject,
    )
    return dict(row)
