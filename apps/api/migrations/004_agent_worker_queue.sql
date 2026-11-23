-- Cross-tenant queue contains routing metadata only. Run inputs, outputs,
-- and agent configuration remain protected by agent_runs RLS.
CREATE TABLE IF NOT EXISTS public.agent_run_queue (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    run_id UUID NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running')),
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, run_id)
        REFERENCES public.agent_runs (tenant_id, id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_agent_run_queue_claim
    ON public.agent_run_queue (status, created_at, id);

-- The queue table exposes only tenant/run identifiers and lease metadata.
-- It deliberately has no input, output, prompt, or agent configuration columns.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.agent_run_queue TO agentos_app;
GRANT USAGE, SELECT ON SEQUENCE public.agent_run_queue_id_seq TO agentos_app;

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS input_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
    ADD COLUMN IF NOT EXISTS model_name TEXT;
