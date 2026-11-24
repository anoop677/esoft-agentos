"""Policy-controlled execution endpoints for the fixed ERPNext read-only tool set."""
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.auth import Principal, get_current_principal
from app.core.config import get_settings
from app.infrastructure.erpnext_client import ERPNextConnectorError
from app.infrastructure.policy import evaluate_tool_proposal, sanitize_arguments
from app.infrastructure.tool_execution import (
    ToolExecutionError,
    prepare_erpnext_tool,
    validate_tool_arguments,
)

router = APIRouter(prefix="/api/v1", tags=["tool-execution"])
logger = logging.getLogger("agentos.tool_execution")


class ToolPolicyDenied(RuntimeError):
    """A deterministic policy rejection that must be audited after rollback."""


class ToolExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=32)
    arguments: dict = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def arguments_are_bounded(cls, value: dict) -> dict:
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 16384:
            raise ValueError("Tool arguments must not exceed 16 KiB")
        return value

    @model_validator(mode="after")
    def validate_registered_action(self):
        try:
            validate_tool_arguments(self.tool_id, self.action, self.arguments)
        except ToolExecutionError as exc:
            raise ValueError(str(exc)) from exc
        return self


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


async def _set_tenant(connection, principal: Principal) -> str:
    await connection.execute(
        "SELECT set_config('app.tenant_id', $1, true)", principal.tenant_id
    )
    active = await connection.fetchval(
        "SELECT 1 FROM public.tenants WHERE id = $1 AND status = 'active'",
        principal.tenant_id,
    )
    if not active:
        raise HTTPException(status_code=403, detail="Tenant is unavailable")
    membership = await connection.fetchrow(
        """SELECT role FROM public.tenant_memberships
           WHERE tenant_id = $1 AND subject_id = $2 AND status = 'active'""",
        principal.tenant_id, principal.subject,
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Active tenant membership required")
    return membership["role"]


async def _event(connection, principal, run_id, event_type, details):
    await connection.execute(
        """INSERT INTO public.agent_run_events
           (tenant_id, run_id, event_type, actor, details)
           VALUES ($1, $2, $3, $4, $5::jsonb)""",
        principal.tenant_id, run_id, event_type, principal.subject,
        json.dumps(details),
    )


async def _policy_context(
    connection, principal, run_id, tool_id, action,
    *, exclude_execution_id: str | None = None, allow_completed: bool = False,
):
    row = await connection.fetchrow(
        """
        SELECT r.id, r.status, a.status AS agent_status,
               v.manifest, v.tool_permissions,
               COALESCE(c.agents_enabled, TRUE) AS agents_enabled,
               t.enabled AS tool_enabled, t.risk_tier
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
        principal.tenant_id, run_id, tool_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if row["status"] not in {"queued", "running"} and not (
        allow_completed and row["status"] == "succeeded"
    ):
        raise HTTPException(status_code=409, detail="Tool execution requires an active or successfully completed run")

    permissions = _json(row["tool_permissions"])
    manifest = _json(row["manifest"])
    calls_used = await connection.fetchval(
        """
        SELECT count(*) FROM public.agent_run_events
        WHERE tenant_id = $1 AND run_id = $2
          AND event_type IN (
            'tool_proposal_authorized', 'tool_execution_started'
          )
          AND details->>'tool_id' = $3
          AND ($4::text IS NULL OR details->>'execution_id' <> $4)
        """,
        principal.tenant_id, run_id, tool_id, exclude_execution_id,
    )
    decision = evaluate_tool_proposal(
        agent_status=row["agent_status"],
        autonomy_level=manifest.get("autonomy_level", "A1"),
        tool_permissions=permissions,
        tool_id=tool_id,
        action=action,
        risk_tier=row["risk_tier"] or "critical",
        calls_used=calls_used,
        catalog_enabled=bool(row["tool_enabled"]),
        agents_enabled=row["agents_enabled"],
    )
    if not decision.allowed:
        raise ToolPolicyDenied(decision.reason)
    return decision


async def _reserve_and_call(
    request: Request,
    principal: Principal,
    *,
    run_id: UUID,
    approval_id: UUID | None,
    tool_id: str,
    action: str,
    arguments: dict,
    idempotency_key: str,
):
    settings = get_settings()
    if not settings.tool_execution_enabled:
        raise HTTPException(
            status_code=503,
            detail="Tool execution is disabled by platform configuration",
        )
    try:
        connector_id, _, _ = validate_tool_arguments(tool_id, action, arguments)
    except ToolExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    pool = request.app.state.db_pool
    execution_id = None
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                role = await _set_tenant(connection, principal)
                if approval_id is not None and role not in {"owner", "admin"}:
                    raise HTTPException(status_code=403, detail="Tenant administrator role required")

                existing = await connection.fetchrow(
                    """
                    SELECT id, run_id, approval_id, connector_id, tool_id, action,
                           arguments, status, output
                    FROM public.agent_tool_executions
                    WHERE tenant_id = $1 AND idempotency_key = $2
                    FOR UPDATE
                    """,
                    principal.tenant_id, idempotency_key,
                )
                if existing is not None and (
                    existing["run_id"] != run_id
                    or existing["approval_id"] != approval_id
                    or existing["connector_id"] != connector_id
                    or existing["tool_id"] != tool_id
                    or existing["action"] != action
                    or _json(existing["arguments"]) != sanitize_arguments(arguments)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency-Key was already used for a different tool request",
                    )

                connector = await connection.fetchrow(
                    """
                    SELECT connector_type, status
                    FROM public.agent_connectors
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    principal.tenant_id, connector_id,
                )
                if connector is None:
                    raise HTTPException(status_code=404, detail="Connector not found in this tenant")
                if connector["connector_type"] != "erpnext" or connector["status"] != "active":
                    raise HTTPException(status_code=409, detail="ERPNext connector must be active")

                decision = await _policy_context(
                    connection, principal, run_id, tool_id, action,
                    exclude_execution_id=str(existing["id"]) if existing else None,
                    allow_completed=approval_id is not None or existing is not None,
                )
                if decision.approval_required and approval_id is None:
                    safe_arguments = sanitize_arguments(arguments)
                    approval = await connection.fetchrow(
                        """
                        SELECT id, status, expires_at
                        FROM public.agent_approvals
                        WHERE tenant_id = $1 AND run_id = $2 AND tool_id = $3
                          AND action = $4 AND status = 'pending'
                          AND arguments = $5::jsonb
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        principal.tenant_id, run_id, tool_id, action,
                        json.dumps(safe_arguments),
                    )
                    if approval is None:
                        approval = await connection.fetchrow(
                            """
                            INSERT INTO public.agent_approvals
                                (tenant_id, run_id, tool_id, action, arguments, requested_by)
                            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                            RETURNING id, status, expires_at
                            """,
                            principal.tenant_id, run_id, tool_id, action,
                            json.dumps(safe_arguments), principal.subject,
                        )
                    await _event(connection, principal, run_id, "approval_requested", {
                        "approval_id": str(approval["id"]), "tool_id": tool_id,
                        "action": action, "source": "tool_execution",
                    })
                    return {
                        "status": "approval_pending",
                        "approval_id": str(approval["id"]),
                        "expires_at": approval["expires_at"].isoformat(),
                    }

                if approval_id is not None:
                    approval = await connection.fetchrow(
                        """
                        SELECT id, status, tool_id, action, arguments, expires_at
                        FROM public.agent_approvals
                        WHERE tenant_id = $1 AND run_id = $2 AND id = $3
                        FOR UPDATE
                        """,
                        principal.tenant_id, run_id, approval_id,
                    )
                    if approval is None:
                        raise HTTPException(status_code=404, detail="Approval not found")
                    if approval["status"] != "approved":
                        raise HTTPException(status_code=409, detail="Approval is not approved")
                    if approval["expires_at"] <= await connection.fetchval("SELECT now()"):
                        raise HTTPException(status_code=409, detail="Approval has expired")
                    if approval["tool_id"] != tool_id or approval["action"] != action:
                        raise HTTPException(status_code=409, detail="Approval does not match this tool action")
                    stored_arguments = _json(approval["arguments"])
                    if stored_arguments != sanitize_arguments(arguments):
                        raise HTTPException(status_code=409, detail="Execution arguments differ from approved arguments")

                if existing is not None:
                    if existing["status"] == "succeeded":
                        return {
                            "status": "succeeded", "execution_id": str(existing["id"]),
                            "data": _json(existing["output"]).get("data", []),
                            "idempotent_replay": True,
                        }
                    raise HTTPException(
                        status_code=409,
                        detail="This idempotency key has already been attempted; execution will not be repeated",
                    )

                execution_id = await connection.fetchval(
                    """
                    INSERT INTO public.agent_tool_executions
                        (tenant_id, run_id, approval_id, connector_id, tool_id, action,
                         idempotency_key, arguments)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                    RETURNING id
                    """,
                    principal.tenant_id, run_id, approval_id, connector_id, tool_id,
                    action, idempotency_key, json.dumps(sanitize_arguments(arguments)),
                )
                await _event(connection, principal, run_id, "tool_execution_started", {
                    "execution_id": str(execution_id), "approval_id": str(approval_id) if approval_id else None,
                    "connector_id": str(connector_id), "tool_id": tool_id, "action": action,
                })

    except ToolPolicyDenied as exc:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await _set_tenant(connection, principal)
                await _event(connection, principal, run_id, "tool_execution_denied", {
                    "tool_id": tool_id, "action": action, "reason": str(exc),
                })
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # Network I/O occurs after the reservation transaction commits and releases its DB connection.
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", principal.tenant_id
                )
                client, limit, start, method_name = await prepare_erpnext_tool(
                    connection, settings=settings, tenant_id=principal.tenant_id,
                    tool_id=tool_id, action=action, arguments=arguments,
                )
        async with client:
            try:
                data = await getattr(client, method_name)(limit=limit, start=start)
            except ERPNextConnectorError as exc:
                raise ToolExecutionError(str(exc)) from exc
            except Exception as exc:
                raise ToolExecutionError("ERPNext read request failed") from exc
        output = {"data": data}
    except ToolExecutionError as exc:
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", principal.tenant_id
                )
                await connection.execute(
                    """UPDATE public.agent_tool_executions
                       SET status = 'failed', error_code = 'CONNECTOR_EXECUTION_FAILED',
                           completed_at = now()
                       WHERE tenant_id = $1 AND id = $2""",
                    principal.tenant_id, execution_id,
                )
                await _event(connection, principal, run_id, "tool_execution_failed", {
                    "execution_id": str(execution_id), "tool_id": tool_id,
                    "error": str(exc)[:300],
                })
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected tool execution failure; execution_id=%s", execution_id)
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", principal.tenant_id
                )
                await connection.execute(
                    """UPDATE public.agent_tool_executions
                       SET status = 'failed', error_code = 'INTERNAL_EXECUTION_ERROR',
                           completed_at = now()
                       WHERE tenant_id = $1 AND id = $2""",
                    principal.tenant_id, execution_id,
                )
                await _event(connection, principal, run_id, "tool_execution_failed", {
                    "execution_id": str(execution_id), "tool_id": tool_id,
                    "error": "Unexpected execution failure",
                })
        raise HTTPException(status_code=502, detail="Tool execution failed") from exc

    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)", principal.tenant_id
            )
            await connection.execute(
                """UPDATE public.agent_tool_executions
                   SET status = 'succeeded', output = $3::jsonb, completed_at = now()
                   WHERE tenant_id = $1 AND id = $2""",
                principal.tenant_id, execution_id, json.dumps(output),
            )
            await _event(connection, principal, run_id, "tool_execution_succeeded", {
                "execution_id": str(execution_id), "tool_id": tool_id,
                "record_count": len(data),
            })
    return {
        "status": "succeeded", "execution_id": str(execution_id),
        "data": data, "idempotent_replay": False,
    }


@router.post("/runs/{run_id}/tool-executions", status_code=status.HTTP_200_OK)
async def execute_tool_for_run(
    run_id: UUID,
    payload: ToolExecutionRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=16, max_length=128),
):
    return await _reserve_and_call(
        request, principal, run_id=run_id, approval_id=None,
        tool_id=payload.tool_id, action=payload.action,
        arguments=payload.arguments, idempotency_key=idempotency_key,
    )


@router.post("/approvals/{approval_id}/execute", status_code=status.HTTP_200_OK)
async def execute_approved_tool(
    approval_id: UUID,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    settings = get_settings()
    if not settings.tool_execution_enabled:
        raise HTTPException(status_code=503, detail="Tool execution is disabled by platform configuration")
    pool = request.app.state.db_pool
    async with pool.acquire() as connection:
        async with connection.transaction():
            role = await _set_tenant(connection, principal)
            if role not in {"owner", "admin"}:
                raise HTTPException(status_code=403, detail="Tenant administrator role required")
            approval = await connection.fetchrow(
                """SELECT run_id, tool_id, action, arguments
                   FROM public.agent_approvals
                   WHERE tenant_id = $1 AND id = $2""",
                principal.tenant_id, approval_id,
            )
            if approval is None:
                raise HTTPException(status_code=404, detail="Approval not found")
            arguments = _json(approval["arguments"])
            run_id = approval["run_id"]
            tool_id = approval["tool_id"]
            action = approval["action"]
    return await _reserve_and_call(
        request, principal, run_id=run_id, approval_id=approval_id,
        tool_id=tool_id, action=action, arguments=arguments,
        idempotency_key=f"approval:{approval_id}",
    )
