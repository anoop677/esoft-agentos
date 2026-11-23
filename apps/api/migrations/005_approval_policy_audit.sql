-- Approval, tool-policy and audit foundation. No tool executor is enabled by this migration.
CREATE TABLE IF NOT EXISTS public.agent_tool_catalog (
    tool_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    risk_tier TEXT NOT NULL CHECK (risk_tier IN ('low', 'medium', 'high', 'critical')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.agent_tool_catalog (tool_id, display_name, risk_tier, description)
VALUES ('inventory.lookup', 'Inventory lookup', 'low', 'Read-only inventory lookup adapter placeholder')
ON CONFLICT (tool_id) DO NOTHING;

GRANT SELECT ON public.agent_tool_catalog TO agentos_app;

CREATE TABLE IF NOT EXISTS public.agent_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    run_id UUID NOT NULL,
    tool_id TEXT NOT NULL REFERENCES public.agent_tool_catalog(tool_id),
    action TEXT NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled')),
    requested_by TEXT NOT NULL,
    decided_by TEXT,
    decision_comment TEXT,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 minutes'),
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES public.agent_runs (tenant_id, id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(arguments) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_agent_approvals_inbox
    ON public.agent_approvals (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_approvals_run
    ON public.agent_approvals (tenant_id, run_id, created_at);

ALTER TABLE public.agent_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_approvals_tenant_policy ON public.agent_approvals
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

GRANT SELECT, INSERT, UPDATE ON public.agent_approvals TO agentos_app;

CREATE TABLE IF NOT EXISTS public.agent_run_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES public.agent_runs (tenant_id, id) ON DELETE CASCADE,
    CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_agent_run_events_timeline
    ON public.agent_run_events (tenant_id, run_id, id);

ALTER TABLE public.agent_run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_run_events FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_run_events_tenant_policy ON public.agent_run_events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE OR REPLACE FUNCTION public.reject_agent_run_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Permit referential cleanup cascades; direct row mutation remains forbidden.
    IF pg_trigger_depth() = 1 THEN
        RAISE EXCEPTION 'agent_run_events is append-only';
    END IF;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS agent_run_events_no_update ON public.agent_run_events;
CREATE TRIGGER agent_run_events_no_update
    BEFORE UPDATE OR DELETE ON public.agent_run_events
    FOR EACH ROW EXECUTE FUNCTION public.reject_agent_run_event_mutation();

GRANT SELECT, INSERT ON public.agent_run_events TO agentos_app;
GRANT USAGE, SELECT ON SEQUENCE public.agent_run_events_id_seq TO agentos_app;

CREATE TABLE IF NOT EXISTS public.tenant_agentos_controls (
    tenant_id TEXT PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
    agents_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.tenant_agentos_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_agentos_controls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_agentos_controls_tenant_policy ON public.tenant_agentos_controls
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
GRANT SELECT, INSERT, UPDATE ON public.tenant_agentos_controls TO agentos_app;
