-- 005_property_media.sql
--
-- Listing media produced by the `property-photos` Neon Function
-- (functions/property-photos.ts, trigger `on-property-photo`).
--
-- Ownership, deliberately split:
--   * the function    — inserts/updates rows (a delivery is the only writer)
--   * the app (re_app) — SELECT only, so a request can list variants without
--                        also being able to fabricate media metadata
--
-- The function creates this table on first use as well, so a branch that has
-- never run migrations still works; `create table if not exists` here keeps the
-- schema in one reviewable place. Neither path enables update/delete for the
-- app role, and there is no tenant column trickery: `tenant_id` is resolved by
-- the function from the property document and the app always filters on it.

create table if not exists re_property_media (
  id           text primary key,
  property_id  text not null,
  tenant_id    text,
  bucket       text not null,
  source_key   text not null,
  variant      text not null,
  object_key   text not null,
  format       text not null,
  width        integer not null,
  height       integer not null,
  bytes        bigint  not null,
  checksum     text    not null,
  created_at   bigint  not null,
  updated_at   bigint  not null
);

create unique index if not exists re_property_media_source_variant_idx
  on re_property_media (source_key, variant);

create index if not exists re_property_media_property_idx
  on re_property_media (property_id);

-- Runtime role: read-only on media.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 're_app') then
    execute 'grant usage on schema public to re_app';
    execute 'grant select on re_property_media to re_app';
  else
    raise notice 'role re_app not present; skipping grants (see db/004_runtime_role.sql)';
  end if;
end $$;
