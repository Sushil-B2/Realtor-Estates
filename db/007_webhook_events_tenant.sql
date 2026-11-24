-- 007_webhook_events_tenant.sql
--
-- Tenant attribution for verified payment webhooks.
--
-- A payments provider has no idea which workspace an event belongs to: the
-- workspace travels in the order's `notes` (set when the order is created). The
-- function copies it here so the app can filter by tenant_id instead of parsing
-- JSON on every read — and so a route can never accidentally list another
-- workspace's payments because it forgot to look inside `payload`.
--
-- Separate from 006 on purpose: 006 had already been applied, and rewriting an
-- applied migration would fail this repo's checksum gate (rightly — a migration
-- is history, not a draft).

alter table re_webhook_events add column if not exists tenant_id text;

create index if not exists re_webhook_events_tenant_idx
  on re_webhook_events (tenant_id, received_at desc);
