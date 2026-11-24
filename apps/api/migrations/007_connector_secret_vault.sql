-- Encrypted connector credentials. Plaintext secrets never enter connector configuration.
ALTER TABLE public.agent_connectors
    ADD CONSTRAINT uq_agent_connectors_tenant_id UNIQUE (tenant_id, id);

CREATE TABLE IF NOT EXISTS public.agent_connector_secrets (
    tenant_id TEXT NOT NULL,
    connector_id UUID NOT NULL,
    ciphertext BYTEA NOT NULL,
    nonce BYTEA NOT NULL CHECK (octet_length(nonce) = 12),
    key_version TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, connector_id),
    FOREIGN KEY (tenant_id, connector_id)
        REFERENCES public.agent_connectors (tenant_id, id) ON DELETE CASCADE
);

ALTER TABLE public.agent_connector_secrets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_connector_secrets FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_connector_secrets_tenant_policy
    ON public.agent_connector_secrets
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

GRANT SELECT, INSERT, UPDATE, DELETE ON public.agent_connector_secrets TO agentos_app;
