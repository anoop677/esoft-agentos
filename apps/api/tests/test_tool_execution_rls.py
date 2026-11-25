# ruff: noqa: I001

import os
from uuid import uuid4

import asyncpg
import pytest


APP_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
ADMIN_DATABASE_URL = os.getenv("MIGRATION_DATABASE_URL")


@pytest.mark.asyncio
async def test_tool_execution_ledger_is_tenant_isolated():
    if not APP_DATABASE_URL or not ADMIN_DATABASE_URL:
        pytest.skip("Set DATABASE_URL and MIGRATION_DATABASE_URL to run tool execution RLS tests")

    admin = await asyncpg.connect(ADMIN_DATABASE_URL)
    app = await asyncpg.connect(APP_DATABASE_URL)
    tenant_a = f"tool-exec-a-{uuid4()}"
    tenant_b = f"tool-exec-b-{uuid4()}"

    try:
        async with admin.transaction():
            await admin.execute(
                "INSERT INTO public.tenants (id, display_name) VALUES ($1, $2), ($3, $4)",
                tenant_a, "Tool Execution Tenant A", tenant_b, "Tool Execution Tenant B",
            )
            agent_id = await admin.fetchval(
                """
                INSERT INTO public.agent_definitions
                    (tenant_id, slug, name, status, created_by)
                VALUES ($1, $2, 'Tool Execution Agent', 'active', 'tool-execution-test')
                RETURNING id
                """,
                tenant_a, f"tool-exec-{uuid4().hex[:12]}",
            )
            version_id = await admin.fetchval(
                """
                INSERT INTO public.agent_versions
                    (tenant_id, agent_id, version, manifest, tool_permissions, created_by)
                VALUES ($1, $2, 1, '{"purpose":"test"}'::jsonb,
                        '[{"tool_id":"erpnext.items.list","actions":["list"]}]'::jsonb,
                        'tool-execution-test')
                RETURNING id
                """,
                tenant_a, agent_id,
            )
            run_id = await admin.fetchval(
                """
                INSERT INTO public.agent_runs
                    (tenant_id, agent_id, agent_version_id, requested_by, input,
                     tool_context, tool_round_count)
                VALUES ($1, $2, $3, 'tool-execution-test', '{}'::jsonb,
                        '[{"response_id":"resp_test","call_id":"call_test","output":[]}]'::jsonb,
                        1)
                RETURNING id
                """,
                tenant_a, agent_id, version_id,
            )
            approval_id = await admin.fetchval(
                """
                INSERT INTO public.agent_approvals
                    (tenant_id, run_id, tool_id, action, arguments, requested_by,
                     model_response_id, model_call_id)
                VALUES ($1, $2, 'erpnext.items.list', 'list', '{}'::jsonb,
                        'tool-execution-test', 'resp_test', 'call_test')
                RETURNING id
                """,
                tenant_a, run_id,
            )
            connector_id = await admin.fetchval(
                """
                INSERT INTO public.agent_connectors
                    (tenant_id, connector_key, connector_type, display_name, created_by)
                VALUES ($1, $2, 'erpnext', 'ERPNext test', 'tool-execution-test')
                RETURNING id
                """,
                tenant_a, f"erpnext-{uuid4().hex[:12]}",
            )
            await admin.execute(
                """
                INSERT INTO public.agent_tool_executions
                    (tenant_id, run_id, connector_id, tool_id, action,
                     idempotency_key, arguments, status, output, completed_at)
                VALUES ($1, $2, $3, 'erpnext.items.list', 'list', $4,
                        jsonb_build_object('connector_id', $3::uuid::text),
                        'succeeded', '{"data":[]}'::jsonb, now())
                """,
                tenant_a, run_id, connector_id, f"rls-tool-execution-{uuid4()}",
            )

        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_a)
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_tool_executions"
            ) == 1
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_approvals WHERE id = $1",
                approval_id,
            ) == 1
            assert await app.fetchval(
                "SELECT tool_round_count FROM public.agent_runs WHERE id = $1",
                run_id,
            ) == 1

        async with app.transaction():
            await app.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_b)
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_tool_executions"
            ) == 0
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_approvals WHERE id = $1",
                approval_id,
            ) == 0
            assert await app.fetchval(
                "SELECT count(*) FROM public.agent_runs WHERE id = $1",
                run_id,
            ) == 0

    finally:
        await admin.execute(
            "DELETE FROM public.tenants WHERE id = ANY($1::text[])", [tenant_a, tenant_b]
        )
        await app.close()
        await admin.close()
