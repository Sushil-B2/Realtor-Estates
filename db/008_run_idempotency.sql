-- 008_run_idempotency.sql
--
-- Make "one run per occurrence" true at the database, not just in one process.
--
-- `Runtime.plan` already refuses a run whose `idempotency_key` exists, but that
-- check reads the process's working set. Two app instances (or two ticks racing
-- each other, or a worker plus a manual tick) each hold their own copy, so both
-- can pass the check and both can send. The gate that actually holds is a unique
-- index: the loser of the race gets a constraint violation, which the runtime
-- treats as "somebody else already claimed this occurrence" and skips.
--
-- Scoped to the `runs` collection and partial on the key being present, so it
-- cannot affect any other document type, and a run written by an older build
-- (no key) is still insertable.

create unique index if not exists re_runs_idempotency_idx
  on re_documents (tenant_id, (doc ->> 'idempotency_key'))
  where collection = 'runs' and doc ? 'idempotency_key';
