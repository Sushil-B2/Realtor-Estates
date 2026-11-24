-- =====================================================================
-- Migration 003 — make the RLS policy testable.
--
-- `re_app` is NOLOGIN, so nobody can connect as it directly. Without a
-- membership grant, not even the database owner can `set role re_app`, which
-- means the isolation policy in 002 could never be exercised — an untested
-- policy is not a control, it is a comment.
--
-- Granting `re_app` TO the owner does not widen anything: the owner already
-- bypasses RLS and owns every object. It only allows a test (or an operator) to
-- drop to the restricted role and verify what a non-owner service would see.
-- =====================================================================

do $$
begin
  if exists (select 1 from pg_roles where rolname = 're_app') then
    execute format('grant re_app to %I', current_user);
  end if;
end
$$;
