#!/bin/sh
set -eu

: "${AGENTOS_APP_PASSWORD:?AGENTOS_APP_PASSWORD must be set}"

psql --set=app_password="${AGENTOS_APP_PASSWORD}" --set=ON_ERROR_STOP=1 <<'SQL'
SELECT format(
    'CREATE ROLE agentos_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agentos_app')
\gexec

SELECT format(
    'ALTER ROLE agentos_app WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
    :'app_password'
)
\gexec

GRANT CONNECT ON DATABASE agentos TO agentos_app;
GRANT USAGE ON SCHEMA public TO agentos_app;
SQL
