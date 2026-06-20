-- ============================================================================
--  Read-only PostgreSQL role for the dashboard.
--
--  The dashboard MUST NEVER write to the bot's database. This role enforces
--  that at the database level (defense in depth) — even if application code has
--  a bug, the connection physically cannot INSERT/UPDATE/DELETE.
--
--  Run this as the database OWNER (the same role the bot uses, e.g. the role
--  that created the tables), connected to the bot's database:
--
--      psql -U <bot_owner> -d <bot_db> -f 01_dashboard_readonly_role.sql
--
--  The ALTER DEFAULT PRIVILEGES line only affects tables created by the role
--  that RUNS it, so it must be run as the bot owner for future bot tables to be
--  automatically readable.
-- ============================================================================

-- 1) Create the login role. Replace the password before running.
--    No SUPERUSER, no CREATEDB, no CREATEROLE — a pure read-only login.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_ro') THEN
        CREATE ROLE dashboard_ro LOGIN PASSWORD 'CHANGE_ME_TO_A_STRONG_PASSWORD';
    END IF;
END
$$;

-- 2) Allow connecting and reading the public schema.
--    (Adjust the database/schema names if yours differ.)
GRANT CONNECT ON DATABASE predicted TO dashboard_ro;
GRANT USAGE  ON SCHEMA public        TO dashboard_ro;

-- 3) Read existing tables...
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_ro;

-- 4) ...and any tables the bot owner creates in the future.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dashboard_ro;

-- 5) Belt and braces: explicitly revoke every write privilege.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM dashboard_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM dashboard_ro;

-- 6) Deny schema creation so the role can never add objects.
REVOKE CREATE ON SCHEMA public FROM dashboard_ro;

-- Verify with:
--   SELECT has_table_privilege('dashboard_ro','trades','SELECT');  -- t
--   SELECT has_table_privilege('dashboard_ro','trades','INSERT');  -- f
