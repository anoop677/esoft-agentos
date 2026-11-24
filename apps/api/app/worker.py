import asyncio
import json
import logging
import os
import socket
from uuid import uuid4

import asyncpg

from app.core.config import get_settings
from app.infrastructure.model_gateway import ModelGateway, ModelGatewayError
from app.infrastructure.policy import evaluate_tool_proposal, sanitize_arguments
from app.infrastructure.tool_execution import ToolExecutionError, validate_tool_arguments

logger = logging.getLogger("agentos.worker")
LEASE_SECONDS = 300
POLL_SECONDS = 2


class AgentWorker:
    def __init__(self, pool: asyncpg.Pool, gateway: ModelGateway, worker_id: str):
        self.pool = pool
        self.gateway = gateway
        self.worker_id = worker_id

    async def claim(self):
        async with self.pool.acquire() as connection, connection.transaction():
            return await connection.fetchrow(
                """
                WITH candidate AS (
                    SELECT id
                    FROM public.agent_run_queue
                    WHERE status = 'queued'
                       OR (status = 'running'
                           AND locked_at < now() - ($1::int * interval '1 second'))
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE public.agent_run_queue q
                SET status = 'running',
                    locked_by = $2,
                    locked_at = now(),
                    attempt_count = q.attempt_count + 1
                FROM candidate
                WHERE q.id = candidate.id
                RETURNING q.tenant_id, q.run_id, q.attempt_count
                """,
                LEASE_SECONDS,
                self.worker_id,
            )

    async def load_run(self, tenant_id: str, run_id):
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            return await connection.fetchrow(
                """
                SELECT r.id, r.status, r.input, r.attempt_count, r.max_attempts,
                       v.manifest, v.model_config, a.status AS agent_status,
                       COALESCE(c.agents_enabled, TRUE) AS agents_enabled
                FROM public.agent_runs r
                JOIN public.agent_versions v
                  ON v.tenant_id = r.tenant_id AND v.id = r.agent_version_id
                JOIN public.agent_definitions a
                  ON a.tenant_id = r.tenant_id AND a.id = r.agent_id
                LEFT JOIN public.tenant_agentos_controls c
                  ON c.tenant_id = r.tenant_id
                WHERE r.tenant_id = $1 AND r.id = $2
                """,
                tenant_id,
                run_id,
            )

    async def mark_started(self, tenant_id: str, run_id):
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            started_id = await connection.fetchval(
                """
                UPDATE public.agent_runs
                SET status = 'running', attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, now()), updated_at = now()
                WHERE tenant_id = $1 AND id = $2 AND status IN ('queued', 'running')
                  AND EXISTS (
                      SELECT 1 FROM public.agent_run_queue q
                      WHERE q.tenant_id = $1 AND q.run_id = $2 AND q.locked_by = $3
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM public.agent_definitions a
                      LEFT JOIN public.tenant_agentos_controls c
                        ON c.tenant_id = a.tenant_id
                      WHERE a.tenant_id = $1
                        AND a.id = public.agent_runs.agent_id
                        AND a.status = 'active'
                        AND COALESCE(c.agents_enabled, TRUE) = TRUE
                  )
                RETURNING id
                """,
                tenant_id,
                run_id,
                self.worker_id,
            )
            return started_id is not None

    async def cancel_blocked(self, tenant_id: str, run_id, reason: str):
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await connection.execute(
                """
                UPDATE public.agent_runs
                SET status = 'cancelled', error_code = 'EXECUTION_DISABLED',
                    error_message = $3, updated_at = now(), finished_at = now()
                WHERE tenant_id = $1 AND id = $2
                  AND EXISTS (
                      SELECT 1 FROM public.agent_run_queue q
                      WHERE q.tenant_id = $1 AND q.run_id = $2 AND q.locked_by = $4
                  )
                """,
                tenant_id, run_id, reason[:500], self.worker_id,
            )
            await connection.execute(
                """
                INSERT INTO public.agent_run_events
                    (tenant_id, run_id, event_type, actor, details)
                SELECT $1, $2, 'run_cancelled_by_policy', 'agent-worker',
                       jsonb_build_object('reason', $3)
                WHERE EXISTS (
                    SELECT 1 FROM public.agent_runs
                    WHERE tenant_id = $1 AND id = $2 AND status = 'cancelled'
                )
                """,
                tenant_id, run_id, reason[:500],
            )
            await connection.execute(
                """
                DELETE FROM public.agent_run_queue
                WHERE tenant_id = $1 AND run_id = $2 AND locked_by = $3
                """,
                tenant_id, run_id, self.worker_id,
            )

    async def complete(self, tenant_id: str, run_id, result: dict):
        usage = result.get("usage") or {}
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await connection.fetchrow(
                """
                UPDATE public.agent_runs
                SET status = 'succeeded', output = $3::jsonb,
                    input_tokens = $4, output_tokens = $5, model_name = $6,
                    updated_at = now(), finished_at = now(),
                    locked_by = NULL, locked_at = NULL
                WHERE tenant_id = $1 AND id = $2
                  AND EXISTS (
                      SELECT 1 FROM public.agent_run_queue q
                      WHERE q.tenant_id = $1 AND q.run_id = $2 AND q.locked_by = $7
                  )
                RETURNING id
                """,
                tenant_id,
                run_id,
                json.dumps(result),
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                result.get("model"),
                self.worker_id,
            )
            if not row:
                raise RuntimeError("Agent run lease was lost before completion")
            await connection.execute(
                """
                DELETE FROM public.agent_run_queue
                WHERE tenant_id = $1 AND run_id = $2 AND locked_by = $3
                """,
                tenant_id,
                run_id,
                self.worker_id,
            )

    async def fail(self, tenant_id: str, run_id, error: Exception):
        message = str(error)[:500]
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            row = await connection.fetchrow(
                """
                UPDATE public.agent_runs
                SET status = CASE WHEN attempt_count >= max_attempts
                                  THEN 'failed' ELSE 'queued' END,
                    error_code = 'MODEL_EXECUTION_FAILED',
                    error_message = $3,
                    updated_at = now(),
                    finished_at = CASE WHEN attempt_count >= max_attempts
                                        THEN now() ELSE NULL END,
                    locked_by = NULL, locked_at = NULL
                WHERE tenant_id = $1 AND id = $2
                  AND EXISTS (
                      SELECT 1 FROM public.agent_run_queue q
                      WHERE q.tenant_id = $1 AND q.run_id = $2 AND q.locked_by = $4
                  )
                RETURNING status
                """,
                tenant_id,
                run_id,
                message,
                self.worker_id,
            )
            if row is None:
                return
            if row["status"] == "failed":
                await connection.execute(
                    "DELETE FROM public.agent_run_queue WHERE tenant_id = $1 AND run_id = $2",
                    tenant_id,
                    run_id,
                )
            else:
                await connection.execute(
                    """
                    UPDATE public.agent_run_queue
                    SET status = 'queued', locked_by = NULL, locked_at = NULL
                    WHERE tenant_id = $1 AND run_id = $2 AND locked_by = $3
                    """,
                    tenant_id,
                    run_id,
                    self.worker_id,
                )

    async def record_tool_proposals(self, tenant_id: str, run_id, proposals: list[dict]) -> list[dict]:
        """Validate model proposals and create tenant-scoped human approvals.

        Worker-originated model calls are deliberately never executed inline.
        Even when policy would permit autonomous read access, a human must
        approve the exact arguments before the API execution boundary can run.
        """
        recorded = []
        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            run = await connection.fetchrow(
                """
                SELECT r.status, a.status AS agent_status, v.manifest,
                       v.tool_permissions,
                       COALESCE(c.agents_enabled, TRUE) AS agents_enabled
                FROM public.agent_runs r
                JOIN public.agent_definitions a
                  ON a.tenant_id = r.tenant_id AND a.id = r.agent_id
                JOIN public.agent_versions v
                  ON v.tenant_id = r.tenant_id AND v.id = r.agent_version_id
                LEFT JOIN public.tenant_agentos_controls c ON c.tenant_id = r.tenant_id
                WHERE r.tenant_id = $1 AND r.id = $2
                FOR UPDATE OF r
                """,
                tenant_id, run_id,
            )
            if run is None or run["status"] not in {"running", "queued"}:
                raise RuntimeError("Run is no longer eligible for tool proposals")

            manifest = json.loads(run["manifest"]) if isinstance(run["manifest"], str) else run["manifest"]
            permissions = (
                json.loads(run["tool_permissions"])
                if isinstance(run["tool_permissions"], str)
                else run["tool_permissions"]
            )

            for proposal in proposals[:4]:
                tool_id = proposal.get("tool_id")
                arguments = proposal.get("arguments")
                if not isinstance(arguments, dict):
                    recorded.append({"status": "rejected", "reason": "Arguments must be an object"})
                    continue
                try:
                    validate_tool_arguments(tool_id, "list", arguments)
                except (ToolExecutionError, TypeError):
                    recorded.append({"status": "rejected", "reason": "Tool arguments failed validation"})
                    continue

                catalog = await connection.fetchrow(
                    "SELECT enabled, risk_tier FROM public.agent_tool_catalog WHERE tool_id = $1",
                    tool_id,
                )
                calls_used = await connection.fetchval(
                    """
                    SELECT count(*) FROM public.agent_run_events
                    WHERE tenant_id = $1 AND run_id = $2
                      AND event_type IN ('approval_requested', 'tool_proposal_authorized')
                      AND details->>'tool_id' = $3
                    """,
                    tenant_id, run_id, tool_id,
                )
                decision = evaluate_tool_proposal(
                    agent_status=run["agent_status"],
                    autonomy_level=manifest.get("autonomy_level", "A1"),
                    tool_permissions=permissions,
                    tool_id=tool_id,
                    action="list",
                    risk_tier=catalog["risk_tier"] if catalog else "critical",
                    calls_used=calls_used,
                    catalog_enabled=bool(catalog and catalog["enabled"]),
                    agents_enabled=run["agents_enabled"],
                )
                if not decision.allowed:
                    await connection.execute(
                        """
                        INSERT INTO public.agent_run_events
                            (tenant_id, run_id, event_type, actor, details)
                        VALUES ($1, $2, 'tool_proposal_denied', 'agent-worker', $3::jsonb)
                        """,
                        tenant_id, run_id,
                        json.dumps({"tool_id": tool_id, "reason": decision.reason}),
                    )
                    recorded.append({"tool_id": tool_id, "status": "rejected", "reason": decision.reason})
                    continue

                safe_arguments = sanitize_arguments(arguments)
                existing = await connection.fetchrow(
                    """
                    SELECT id FROM public.agent_approvals
                    WHERE tenant_id = $1 AND run_id = $2 AND tool_id = $3
                      AND action = 'list' AND status = 'pending'
                      AND arguments = $4::jsonb
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    tenant_id, run_id, tool_id, json.dumps(safe_arguments),
                )
                if existing:
                    approval_id = existing["id"]
                else:
                    approval_id = await connection.fetchval(
                        """
                        INSERT INTO public.agent_approvals
                            (tenant_id, run_id, tool_id, action, arguments, requested_by)
                        VALUES ($1, $2, $3, 'list', $4::jsonb, 'agent-worker')
                        RETURNING id
                        """,
                        tenant_id, run_id, tool_id, json.dumps(safe_arguments),
                    )
                    await connection.execute(
                        """
                        INSERT INTO public.agent_run_events
                            (tenant_id, run_id, event_type, actor, details)
                        VALUES ($1, $2, 'approval_requested', 'agent-worker', $3::jsonb)
                        """,
                        tenant_id, run_id,
                        json.dumps({
                            "approval_id": str(approval_id),
                            "tool_id": tool_id,
                            "action": "list",
                            "source": "model_tool_call",
                        }),
                    )
                recorded.append({
                    "tool_id": tool_id,
                    "status": "approval_pending",
                    "approval_id": str(approval_id),
                })
        return recorded

    async def process_one(self) -> bool:
        job = await self.claim()
        if job is None:
            return False

        tenant_id, run_id = job["tenant_id"], job["run_id"]
        try:
            record = await self.load_run(tenant_id, run_id)
            if record is None or record["status"] in {"succeeded", "failed", "cancelled"}:
                async with self.pool.acquire() as connection:
                    await connection.execute(
                        """
                        DELETE FROM public.agent_run_queue
                        WHERE tenant_id = $1 AND run_id = $2 AND locked_by = $3
                        """,
                        tenant_id,
                        run_id,
                        self.worker_id,
                    )
                return True

            if not record["agents_enabled"] or record["agent_status"] != "active":
                await self.cancel_blocked(
                    tenant_id, run_id,
                    "Tenant agent kill switch is active or the agent is disabled",
                )
                return True

            if not await self.mark_started(tenant_id, run_id):
                await self.cancel_blocked(
                    tenant_id, run_id,
                    "Execution was stopped by the tenant or agent security control",
                )
                return True

            manifest = record["manifest"]
            model_config = record["model_config"]
            if isinstance(manifest, str):
                manifest = json.loads(manifest)
            if isinstance(model_config, str):
                model_config = json.loads(model_config)
            if isinstance(record["input"], str):
                input_payload = json.loads(record["input"])
            else:
                input_payload = record["input"]

            # Configured tool permissions are not executed in this milestone.
            if model_config.get("provider") != "openai":
                raise ModelGatewayError("Only the OpenAI provider is enabled in this worker")
            result = await self.gateway.generate(
                provider=model_config["provider"],
                model=model_config["model"],
                instructions=manifest["purpose"],
                input_payload=input_payload,
                max_output_tokens=model_config.get("max_output_tokens", 2048),
                temperature=model_config.get("temperature", 0.2),
                enable_tools=get_settings().tool_execution_enabled,
            )
            proposals = result.pop("tool_calls", [])
            if proposals:
                result["tool_proposals"] = await self.record_tool_proposals(
                    tenant_id, run_id, proposals
                )
            await self.complete(tenant_id, run_id, result)
        except Exception as exc:
            logger.exception("Agent run %s failed", run_id)
            await self.fail(tenant_id, run_id, exc)
        return True

    async def run_forever(self):
        while True:
            processed = await self.process_one()
            if not processed:
                await asyncio.sleep(POLL_SECONDS)


async def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    settings = get_settings()
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=5,
        command_timeout=60,
    )
    worker_id = f"{socket.gethostname()}-{uuid4()}"
    gateway = ModelGateway(openai_api_key=settings.openai_api_key)
    worker = AgentWorker(pool, gateway, worker_id)
    try:
        await worker.run_forever()
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
