-- Read-only ERPNext tool execution ledger and catalog entries.
-- Execution remains disabled until TOOL_EXECUTION_ENABLED is explicitly enabled.
INSERT INTO public.agent_tool_catalog (tool_id, display_name, risk_tier, description)
VALUES
    ('erpnext.items.list', 'ERPNext: List items', 'low', 'List a bounded page of ERPNext items'),
    ('erpnext.warehouses.list', 'ERPNext: List warehouses', 'low', 'List a bounded page of ERPNext warehouses'),
    ('erpnext.stock_bins.list', 'ERPNext: List stock bins', 'low', 'List a bounded page of ERPNext stock bins'),
    ('erpnext.suppliers.list', 'ERPNext: List suppliers', 'low', 'List a bounded page of ERPNext suppliers')
ON CONFLICT (tool_id) DO NOTHING;

ALTER TABLE public.agent_approvals
    ADD CONSTRAINT uq_agent_approvals_tenant_id UNIQUE (tenant_id, id);

CREATE TABLE IF NOT EXISTS public.agent_tool_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    run_id UUID NOT NULL,
    approval_id UUID,
    connector_id UUID NOT NULL,
    tool_id TEXT NOT NULL REFERENCES public.agent_tool_catalog(tool_id),
    action TEXT NOT NULL CHECK (action = 'list'),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 16 AND 128),
    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'succeeded', 'failed')),
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    output JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES public.agent_runs (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, approval_id)
        REFERENCES public.agent_approvals (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, connector_id)
        REFERENCES public.agent_connectors (tenant_id, id) ON DELETE CASCADE,
    UNIQUE (tenant_id, idempotency_key),
    CHECK (jsonb_typeof(arguments) = 'object'),
    CHECK (output IS NULL OR jsonb_typeof(output) IN ('object', 'array'))
);

CREATE INDEX IF NOT EXISTS ix_agent_tool_executions_run
    ON public.agent_tool_executions (tenant_id, run_id, created_at DESC);

ALTER TABLE public.agent_tool_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_tool_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_tool_executions_tenant_policy
    ON public.agent_tool_executions
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

GRANT SELECT, INSERT, UPDATE ON public.agent_tool_executions TO agentos_app;
