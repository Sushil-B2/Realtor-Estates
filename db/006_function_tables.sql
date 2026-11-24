-- 006_function_tables.sql
--
-- Tables owned by the two Neon Functions added alongside the photo pipeline:
--
--   re_compliance_runs  — one row per nightly run of `compliance-sweep`
--   re_webhook_events   — verified payment webhook deliveries (`payments-webhook`)
--
-- Same ownership rule as db/005: the function is the only writer, the app role
-- reads. `re_app` therefore gets SELECT and nothing else — it cannot forge a
-- "clean sweep" record or fabricate a payment event, which is the whole point of
-- keeping the write path out of the web tier.

create table if not exists re_compliance_runs (
  id             text primary key,
  trigger_name   text not null,
  scheduled_at   text,
  started_at     bigint not null,
  finished_at    bigint not null,
  status         text not null,
  retention      jsonb not null,
  audit          jsonb not null,
  media          jsonb not null,
  error          text
);

create index if not exists re_compliance_runs_started_idx
  on re_compliance_runs (started_at desc);

create table if not exists re_webhook_events (
  id              text primary key,
  provider        text not null,
  event_id        text,
  event_type      text,
  status          text not null,
  received_at     bigint not null,
  payment_id      text,
  amount          bigint,
  currency        text,
  key_fingerprint text not null,
  payload         jsonb
);

-- Dedupe lives in the database: two concurrent deliveries of the same event
-- cannot both insert, so a provider retry is a no-op rather than a double count.
create unique index if not exists re_webhook_events_dedupe_idx
  on re_webhook_events (provider, event_id)
  where event_id is not null;

create index if not exists re_webhook_events_received_idx
  on re_webhook_events (received_at desc);

do $$
begin
  if exists (select 1 from pg_roles where rolname = 're_app') then
    execute 'grant usage on schema public to re_app';
    execute 'grant select on re_compliance_runs to re_app';
    execute 'grant select on re_webhook_events to re_app';
  else
    raise notice 'role re_app not present; skipping grants (see db/004_runtime_role.sql)';
  end if;
end $$;
