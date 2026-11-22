-- Tenant-owned records are protected by PostgreSQL row-level security.
-- The application role must not be a superuser or have BYPASSRLS.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.tenants (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 128),
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.tenant_memberships (
    tenant_id TEXT NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    subject_id TEXT NOT NULL CHECK (length(subject_id) BETWEEN 1 AND 255),
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('invited', 'active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject_id)
);

CREATE INDEX IF NOT EXISTS ix_tenant_memberships_subject
    ON public.tenant_memberships (subject_id);

CREATE TABLE IF NOT EXISTS public.agent_definitions (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    slug TEXT NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 160),
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'disabled')),
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS ix_agent_definitions_tenant_status
    ON public.agent_definitions (tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.agent_versions (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    agent_id UUID NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    tool_permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    UNIQUE (tenant_id, id),
    UNIQUE (tenant_id, agent_id, version),
    FOREIGN KEY (tenant_id, agent_id)
        REFERENCES public.agent_definitions (tenant_id, id)
        ON DELETE CASCADE,
    CHECK (jsonb_typeof(manifest) = 'object'),
    CHECK (jsonb_typeof(model_config) = 'object'),
    CHECK (jsonb_typeof(tool_permissions) = 'array')
);

CREATE INDEX IF NOT EXISTS ix_agent_versions_tenant_agent
    ON public.agent_versions (tenant_id, agent_id, version DESC);

ALTER TABLE public.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenants FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.tenants;
CREATE POLICY tenant_isolation ON public.tenants
    USING (id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE public.tenant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_memberships FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.tenant_memberships;
CREATE POLICY tenant_isolation ON public.tenant_memberships
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE public.agent_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_definitions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.agent_definitions;
CREATE POLICY tenant_isolation ON public.agent_definitions
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

ALTER TABLE public.agent_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.agent_versions;
CREATE POLICY tenant_isolation ON public.agent_versions
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''));

COMMENT ON TABLE public.tenants IS 'Tenant directory. Provisioning is restricted to a trusted platform workflow.';
COMMENT ON TABLE public.tenant_memberships IS 'Maps identity-provider subjects to tenant roles.';
COMMENT ON TABLE public.agent_definitions IS 'Tenant-scoped logical agent records.';
COMMENT ON TABLE public.agent_versions IS 'Immutable, tenant-scoped agent configuration versions.';
