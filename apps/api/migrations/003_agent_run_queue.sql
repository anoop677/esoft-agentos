-- Durable, tenant-scoped execution records. Execution is queued separately
-- from request handling so API retries cannot silently duplicate model work.
CREATE TABLE IF NOT EXISTS public.agent_runs (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    agent_id UUID NOT NULL,
    agent_version_id UUID NOT NULL,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    input JSONB NOT NULL DEFAULT '{}'::jsonb,
    output JSONB,
    error_code TEXT,
    error_message TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL DEFAULT 2 CHECK (max_attempts BETWEEN 1 AND 5),
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, agent_id)
        REFERENCES public.agent_definitions (tenant_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, agent_version_id)
        REFERENCES public.agent_versions (tenant_id, id)
        ON DELETE RESTRICT,
    CHECK (jsonb_typeof(input) = 'object'),
    CHECK (output IS NULL OR jsonb_typeof(output) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_agent_runs_tenant_created
    ON public.agent_runs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_runs_queue
    ON public.agent_runs (status, created_at)
    WHERE status = 'queued';

ALTER TABLE public.agent_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.agent_runs;
CREATE POLICY tenant_isolation ON public.agent_runs
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

GRANT SELECT, INSERT, UPDATE ON public.agent_runs TO agentos_app;
