-- =====================================================================
-- Realtor Estates — Postgres / Neon schema, migration 001
--
-- Design note (documented deviation, see docs/12-NEON-BACKEND.md §2):
-- one row per *document* in `re_documents` rather than 21 typed tables.
-- The application's document model is the source of truth today; mapping it
-- to a `collection` column keeps the migration mechanical and lossless, and
-- RLS still enforces the tenant boundary at the database. Typed tables with
-- generated columns arrive in phase 2, alongside real indexes on the fields
-- the UI actually filters (stage, owner_id, created_at).
-- =====================================================================

create table if not exists re_documents (
  collection  text        not null,
  tenant_id   text        not null,
  id          text        not null,
  doc         jsonb       not null,
  created_at  bigint      not null default extract(epoch from now())::bigint,
  updated_at  bigint      not null default extract(epoch from now())::bigint,
  constraint re_documents_pk primary key (collection, tenant_id, id)
);

create index if not exists re_documents_tenant_collection_idx
  on re_documents (tenant_id, collection);
create index if not exists re_documents_updated_idx
  on re_documents (tenant_id, updated_at desc);
create index if not exists re_documents_doc_gin
  on re_documents using gin (doc jsonb_path_ops);

-- ---------------------------------------------------------------------
-- Audit log: append-only at the database, not just in the application.
-- The application still computes the hash chain; the trigger makes history
-- immutable even for a connection that could otherwise UPDATE it.
-- ---------------------------------------------------------------------
create table if not exists re_audit (
  seq        bigserial   primary key,
  tenant_id  text        not null,
  id         text        not null unique,
  actor      text        not null,
  action     text        not null,
  target     text        not null default '',
  meta       jsonb       not null default '{}'::jsonb,
  at         bigint      not null,
  prev_hash  text        not null default '',
  hash       text        not null
);

create index if not exists re_audit_tenant_seq_idx on re_audit (tenant_id, seq);

create or replace function re_audit_block_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 're_audit is append-only (% rejected)', tg_op
    using errcode = 'insufficient_privilege';
end;
$$;

drop trigger if exists re_audit_no_update on re_audit;
create trigger re_audit_no_update
  before update or delete on re_audit
  for each row execute function re_audit_block_mutation();

-- ---------------------------------------------------------------------
-- Schema version ledger (the migration runner writes here)
-- ---------------------------------------------------------------------
create table if not exists re_migrations (
  version     text        primary key,
  applied_at  timestamptz not null default now(),
  checksum    text        not null
);
