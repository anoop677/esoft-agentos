-- Tighten grants for databases that applied the first draft of migration 001.
REVOKE INSERT, UPDATE, DELETE
    ON public.tenants, public.tenant_memberships
    FROM agentos_app;
REVOKE UPDATE, DELETE
    ON public.agent_versions
    FROM agentos_app;

GRANT SELECT ON public.tenants, public.tenant_memberships TO agentos_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.agent_definitions TO agentos_app;
GRANT SELECT, INSERT ON public.agent_versions TO agentos_app;
