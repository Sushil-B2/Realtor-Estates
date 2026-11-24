-- =====================================================================
-- Migration 004 — the runtime role is *subject* to the tenant policy.
--
-- Migration 002 created the `re_app` role and a policy, and left RLS
-- unforced. The application connected as `neondb_owner` with
-- rolbypassrls = true, so for the connection that actually serves HTTP the
-- policy was decorative: every `select` returned every tenant (SEN-003).
--
-- This migration closes that gap:
--   * `re_app` may never hold SUPERUSER or BYPASSRLS;
--   * RLS is FORCED on both tenant tables, so even the table owner is
--     filtered — only a role with BYPASSRLS escapes, and that role is no
--     longer the one the web tier uses;
--   * the app sets `app.tenant_id` per transaction (see PgBackend._set_tenant),
--     so a query that forgets `where tenant_id = ...` still returns nothing.
--
-- The privileged paths that genuinely must cross tenants (schema migrations,
-- boot-time hydration of the working set) use a separate connection from
-- RE_ADMIN_DATABASE_URL and are named as such in the code.
-- =====================================================================

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 're_app') then
    create role re_app nologin;
  end if;
end
$$;

-- An app role that can bypass the policy it is subject to is not a control.
-- (Neon's owner role is not SUPERUSER, so it can set every attribute here
-- except NOSUPERUSER/NOBYPASSRLS on an existing role; the provisioner recreates
-- the role with those attributes instead of altering them.)
do $$
begin
  begin
    alter role re_app nocreatedb nocreaterole;
  exception when insufficient_privilege then
    null;
  end;
  begin
    alter role re_app nobypassrls nosuperuser;
  exception when insufficient_privilege then
    raise notice 're_app: NOSUPERUSER/NOBYPASSRLS not settable by this role — verify in pg_roles';
  end;
end
$$;

-- Membership for the isolation probe: `set role re_app` from an owner session.
-- This widens nothing (the owner already bypasses RLS); it lets a test drop to
-- the restricted role and prove what a non-owner connection would see.
do $$
begin
  execute format('grant re_app to %I', current_user);
exception when duplicate_object or insufficient_privilege then
  null;
end
$$;

grant usage on schema public to re_app;
grant select, insert, update, delete on re_documents to re_app;
grant select, insert on re_audit to re_app;          -- append-only: no update, no delete
grant usage, select on sequence re_audit_seq_seq to re_app;
grant select on re_migrations to re_app;

-- documents ------------------------------------------------------------
alter table re_documents enable row level security;
alter table re_documents force row level security;

drop policy if exists re_documents_tenant_isolation on re_documents;
create policy re_documents_tenant_isolation on re_documents
  for all
  to re_app
  using (tenant_id = current_setting('app.tenant_id', true))
  with check (tenant_id = current_setting('app.tenant_id', true));

-- audit ---------------------------------------------------------------
alter table re_audit enable row level security;
alter table re_audit force row level security;

drop policy if exists re_audit_tenant_isolation on re_audit;
create policy re_audit_tenant_isolation on re_audit
  for all
  to re_app
  using (tenant_id = current_setting('app.tenant_id', true))
  with check (tenant_id = current_setting('app.tenant_id', true));
