-- Persist the OpenAI response/function-call correlation needed to resume a run
-- after a human-approved tool has completed.
ALTER TABLE public.agent_approvals
    ADD COLUMN IF NOT EXISTS model_response_id TEXT,
    ADD COLUMN IF NOT EXISTS model_call_id TEXT;

ALTER TABLE public.agent_approvals
    ADD CONSTRAINT agent_approvals_model_call_pair
    CHECK (
        (model_response_id IS NULL AND model_call_id IS NULL)
        OR
        (model_response_id IS NOT NULL AND model_call_id IS NOT NULL)
    );

ALTER TABLE public.agent_runs
    ADD COLUMN IF NOT EXISTS tool_context JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS tool_round_count INTEGER NOT NULL DEFAULT 0
        CHECK (tool_round_count >= 0);

ALTER TABLE public.agent_runs
    ADD CONSTRAINT agent_runs_tool_context_array
    CHECK (jsonb_typeof(tool_context) = 'array');
