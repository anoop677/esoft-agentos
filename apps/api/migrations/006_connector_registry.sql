-- Tenant-scoped connector registry. Credentials are deliberately not stored here.
CREATE TABLE IF NOT EXISTS public.agent_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    connector_key TEXT NOT NULL,
    connector_type TEXT NOT NULL CHECK (connector_type IN ('erpnext')),
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'disabled')),
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, connector_key),
    CHECK (jsonb_typeof(configuration) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_agent_connectors_tenant_status
    ON public.agent_connectors (tenant_id, status, created_at DESC);

ALTER TABLE public.agent_connectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_connectors FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_connectors_tenant_policy ON public.agent_connectors
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

GRANT SELECT, INSERT, UPDATE ON public.agent_connectors TO agentos_app;
