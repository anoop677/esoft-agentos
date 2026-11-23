import asyncio
import json
import logging
import os
import socket
from uuid import uuid4

import asyncpg

from app.core.config import get_settings
from app.infrastructure.model_gateway import ModelGateway, ModelGatewayError

logger = logging.getLogger("agentos.worker")
LEASE_SECONDS = 300
POLL_SECONDS = 2


class AgentWorker:
    def __init__(self, pool: asyncpg.Pool, gateway: ModelGateway, worker_id: str):
        self.pool = pool
        self.gateway = gateway
        self.worker_id = worker_id

    async def claim(self):
        async with self.pool.acquire() as connection:
            async with connection.transaction():
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
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )
                return await connection.fetchrow(
                    """
                    SELECT r.id, r.status, r.input, r.attempt_count, r.max_attempts,
                           v.manifest, v.model_config
                    FROM public.agent_runs r
                    JOIN public.agent_versions v
                      ON v.tenant_id = r.tenant_id AND v.id = r.agent_version_id
                    WHERE r.tenant_id = $1 AND r.id = $2
                    """,
                    tenant_id,
                    run_id,
                )

    async def mark_started(self, tenant_id: str, run_id):
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )
                await connection.execute(
                    """
                    UPDATE public.agent_runs
                    SET status = 'running', attempt_count = attempt_count + 1,
                        started_at = COALESCE(started_at, now()), updated_at = now()
                    WHERE tenant_id = $1 AND id = $2 AND status IN ('queued', 'running')
                    """,
                    tenant_id,
                    run_id,
                )

    async def complete(self, tenant_id: str, run_id, result: dict):
        usage = result.get("usage") or {}
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )
                await connection.execute(
                    """
                    UPDATE public.agent_runs
                    SET status = 'succeeded', output = $3::jsonb,
                        input_tokens = $4, output_tokens = $5, model_name = $6,
                        updated_at = now(), finished_at = now(),
                        locked_by = NULL, locked_at = NULL
                    WHERE tenant_id = $1 AND id = $2
                    """,
                    tenant_id,
                    run_id,
                    json.dumps(result),
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    result.get("model"),
                )
                await connection.execute(
                    """
                    DELETE FROM public.agent_run_queue
                    WHERE tenant_id = $1 AND run_id = $2 AND locked_by = $3
                    """,
                    tenant_id,
                    run_id,
                    self.worker_id,
                )

    async def fail(self, tenant_id: str, run_id, error: Exception, max_attempts: int):
        message = str(error)[:500]
        async with self.pool.acquire() as connection:
            async with connection.transaction():
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
                    RETURNING status
                    """,
                    tenant_id,
                    run_id,
                    message,
                )
                if row is None or row["status"] == "failed":
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
                        "DELETE FROM public.agent_run_queue WHERE tenant_id = $1 AND run_id = $2",
                        tenant_id,
                        run_id,
                    )
                return True

            await self.mark_started(tenant_id, run_id)
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
            )
            await self.complete(tenant_id, run_id, result)
        except Exception as exc:  # noqa: BLE001 - isolate one failed job from worker loop
            logger.exception("Agent run %s failed", run_id)
            record = await self.load_run(tenant_id, run_id)
            max_attempts = record["max_attempts"] if record else 1
            await self.fail(tenant_id, run_id, exc, max_attempts)
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
