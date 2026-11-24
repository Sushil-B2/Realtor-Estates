-- =====================================================================
-- Migration 002 — row-level security (opt-in; run with `--rls`).
--
-- The application connects as the database owner (Neon's default role), which
-- BYPASSES RLS by design. This policy protects the *other* path: any future
-- service, analytics job or read-replica user that connects with the
-- `re_app` role can only ever see one tenant's rows.
-- =====================================================================

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 're_app') then
    create role re_app nologin;
  end if;
end
$$;

grant usage on schema public to re_app;
grant select, insert, update, delete on re_documents to re_app;
grant select, insert on re_audit to re_app;

alter table re_documents enable row level security;

drop policy if exists re_documents_tenant_isolation on re_documents;
create policy re_documents_tenant_isolation on re_documents
  for all
  to re_app
  using (tenant_id = current_setting('app.tenant_id', true))
  with check (tenant_id = current_setting('app.tenant_id', true));
