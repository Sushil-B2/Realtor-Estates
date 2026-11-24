/**
 * Nightly compliance sweep — a Neon Function on a `schedule` trigger.
 *
 * Trigger: `nightly-compliance`, five-field UTC cron (../neon.ts). Runs at
 * 02:00 UTC = 07:30 IST, before the working day, so a finding is on the owner's
 * screen when the office opens.
 *
 * Three jobs, in order of how much they can actually fix:
 *
 *   1. **Orphan media purge (destructive, function-owned data only).** A row in
 *      `re_property_media` whose source object is gone from the bucket is
 *      garbage: the listing photo was deleted and the derived variants survived.
 *      This sweeps those variants out of storage and out of the table. It never
 *      touches `re_documents`.
 *   2. **Retention evidence (read-only).** Counts, per retention class, of rows
 *      past their horizon — call recordings 365 d, chat 540 d, audit 2555 d.
 *      It reports; it does not delete. Documents belong to the app's single
 *      writer (docs/12), and an audit log is append-only by design, so a cron
 *      job must not be the thing that removes them.
 *   3. **Audit chain integrity (read-only).** Every `re_audit` row carries
 *      `prev_hash`. A broken link means the log was edited by something other
 *      than the append path. This does not need to know the hash function — it
 *      compares each row's `prev_hash` with the previous row's `hash` for the
 *      same tenant, which is the property the chain actually promises. Gaps in
 *      the global `seq` are counted but not alarmed on: a bigserial never reuses
 *      a value, so a rolled-back insert leaves a hole that is not tampering.
 *
 * Every run writes one row to `re_compliance_runs`, so "did the sweep run and
 * what did it see" is answerable months later without log access.
 *
 * Authenticated with `parseTriggerDelivery`: a schedule trigger POST carries no
 * JWT and no shared secret, so this is the only check available on that path.
 * Any other caller gets 401 — the function accepts nothing but a real delivery.
 */
import { createHash } from "node:crypto";

import { attachDatabasePool } from "@neon/functions";
import { parseTriggerDelivery } from "@neon/functions/triggers";
import { DeleteObjectCommand, HeadObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { Pool } from "pg";

const BUCKET = process.env.NEON_STORAGE_BUCKET ?? "projectassets";
const MAX_ORPHANS_PER_RUN = 25;   // bounded work: a delivery must fit in 15 min
const MAX_MEDIA_SOURCES = 400;
const AUDIT_SCAN_LIMIT = 20_000;

/** Retention policy, mirroring the app's declared numbers (app/api.py). */
const RETENTION = [
  {
    key: "call_recordings_days",
    days: 365,
    label: "call recordings",
    // No rows exist in this class yet; the query is written so it starts
    // reporting the moment one does.
    sql: `select count(*)::int as n, min((doc->>'at')::bigint) as oldest
            from re_documents
           where collection = 'deliveries'
             and coalesce(doc->>'event', '') like 'call%'
             and (doc->>'at') ~ '^[0-9]{9,}$'`,
  },
  {
    key: "chat_days",
    days: 540,
    label: "chat messages",
    sql: `select count(*)::int as n, min(coalesce(nullif(doc->>'last_at', '')::bigint, (doc->>'created_at')::bigint)) as oldest
            from re_documents
           where collection = 'conversations'
             and (doc->>'created_at') ~ '^[0-9]{9,}$'`,
  },
  {
    key: "audit_days",
    days: 2555,
    label: "audit entries",
    sql: `select count(*)::int as n, min(at) as oldest from re_audit`,
  },
];

const s3 = new S3Client({
  region: process.env.NEON_STORAGE_REGION ?? "ap-southeast-1",
  endpoint: process.env.NEON_STORAGE_ENDPOINT,
  forcePathStyle: true,
  credentials: {
    accessKeyId: process.env.NEON_STORAGE_ACCESS_KEY ?? "",
    secretAccessKey: process.env.NEON_STORAGE_SECRET_KEY ?? "",
  },
});

const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5 });
attachDatabasePool(pool);

let schemaReady: Promise<void> | null = null;

function ensureSchema(): Promise<void> {
  schemaReady ??= (async () => {
    await pool.query(`
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
      )
    `);
    await pool.query(`
      create index if not exists re_compliance_runs_started_idx
        on re_compliance_runs (started_at desc)
    `);
  })().catch((err) => {
    schemaReady = null;
    throw err;
  });
  return schemaReady;
}

// ---------------------------------------------------------------------------
// 1. Orphaned media
// ---------------------------------------------------------------------------
type MediaSweep = {
  scanned: number;
  orphaned: Array<{ sourceKey: string; variants: number }>;
  purgedSources: number;
  purgedObjects: number;
  purgedRows: number;
  truncated: boolean;
};

async function sweepOrphanMedia(): Promise<MediaSweep> {
  const { rows } = await pool.query(
    `select source_key, count(*)::int as variants, array_agg(object_key) as keys
       from re_property_media
      group by source_key
      order by max(updated_at) desc
      limit $1`,
    [MAX_MEDIA_SOURCES],
  );

  const result: MediaSweep = {
    scanned: rows.length,
    orphaned: [],
    purgedSources: 0,
    purgedObjects: 0,
    purgedRows: 0,
    truncated: false,
  };

  for (const row of rows) {
    let exists = true;
    try {
      await s3.send(new HeadObjectCommand({ Bucket: BUCKET, Key: row.source_key }));
    } catch (err) {
      const name = (err as { name?: string }).name ?? "";
      const status = (err as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode;
      if (name === "NotFound" || name === "NoSuchKey" || status === 404) exists = false;
      else continue; // a transient storage error must not justify a delete
    }
    if (exists) continue;

    result.orphaned.push({ sourceKey: row.source_key, variants: row.variants });
    if (result.purgedSources >= MAX_ORPHANS_PER_RUN) {
      result.truncated = true;   // next run continues where this one stopped
      continue;
    }

    for (const key of row.keys as string[]) {
      try {
        await s3.send(new DeleteObjectCommand({ Bucket: BUCKET, Key: key }));
        result.purgedObjects += 1;
      } catch (err) {
        console.warn("[sweep] delete failed for", key, (err as Error).name);
      }
    }
    const del = await pool.query(`delete from re_property_media where source_key = $1`, [row.source_key]);
    result.purgedRows += del.rowCount ?? 0;
    result.purgedSources += 1;
  }

  return result;
}

// ---------------------------------------------------------------------------
// 2 + 3. Retention evidence and chain integrity
// ---------------------------------------------------------------------------
async function retentionReport(nowSec: number) {
  const out: Array<Record<string, unknown>> = [];
  for (const rule of RETENTION) {
    const cutoff = nowSec - rule.days * 86_400;
    try {
      const { rows } = await pool.query(rule.sql);
      const { n, oldest } = rows[0] ?? { n: 0, oldest: null };
      const pastHorizon = oldest !== null && Number(oldest) < cutoff;
      out.push({
        class: rule.key,
        label: rule.label,
        days: rule.days,
        rows: Number(n ?? 0),
        oldest_at: oldest === null ? null : Number(oldest),
        cutoff,
        // Honest wording: the sweep measures, the owner decides.
        past_horizon: pastHorizon,
        action: pastHorizon ? "review_and_purge" : "none",
      });
    } catch (err) {
      out.push({ class: rule.key, error: (err as Error).message.slice(0, 200) });
    }
  }
  return out;
}

async function auditIntegrity() {
  const { rows } = await pool.query(
    `with base as (
       select tenant_id, seq, hash, prev_hash
         from re_audit
        order by seq desc
        limit $1
     ),
     ordered as (
       select tenant_id, seq, hash, prev_hash,
              -- The chain is per tenant: a row links to the previous row of the
              -- same tenant. seq is a global bigserial, so gaps only mean
              -- something globally (see below), never within a tenant.
              lag(hash) over (partition by tenant_id order by seq) as prior_hash,
              lag(seq)  over (order by seq) as prior_global_seq
         from base
     )
     select count(*)::int as checked,
            count(*) filter (where prior_hash is not null and prev_hash <> prior_hash)::int as broken_links,
            count(*) filter (where prior_global_seq is not null and seq <> prior_global_seq + 1)::int as global_seq_gaps,
            max(seq)::bigint as head_seq
       from ordered`,
    [AUDIT_SCAN_LIMIT],
  );
  const row = rows[0] ?? {};
  const head = await pool.query(`select hash, at from re_audit order by seq desc limit 1`);
  const { rows: dup } = await pool.query(
    `select count(*)::int as n from (select seq from re_audit group by seq having count(*) > 1) d`,
  );
  const brokenLinks = Number(row.broken_links ?? 0);
  const duplicateSeq = Number(dup[0]?.n ?? 0);
  return {
    checked: Number(row.checked ?? 0),
    broken_links: brokenLinks,
    duplicate_seq: duplicateSeq,
    // Informational only: a bigserial never reuses a value, so a rollback or a
    // failed insert leaves a permanent hole. A hole is not evidence of tampering
    // — a *broken link* is. Reporting holes as findings would train people to
    // ignore this check, so they are counted, not alarmed on.
    global_seq_gaps: Number(row.global_seq_gaps ?? 0),
    head_seq: row.head_seq === null || row.head_seq === undefined ? null : String(row.head_seq),
    head_hash: head.rows[0]?.hash ?? null,
    head_at: head.rows[0]?.at ? Number(head.rows[0].at) : null,
    ok: brokenLinks === 0 && duplicateSeq === 0,
  };
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------
export default {
  async fetch(request: Request): Promise<Response> {
    if (request.method === "GET") {
      return Response.json({ ok: true, function: "compliance-sweep", schedule: "02:00 UTC" });
    }
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, POST" } });
    }

    const parsed = await parseTriggerDelivery(request);
    if (!parsed.ok) {
      const status = parsed.error === "invalid_body" ? 400 : 401;
      return Response.json({ ok: false, error: parsed.error }, { status });
    }
    if (parsed.invocation.type !== "schedule") {
      return Response.json(
        { ok: false, error: "unsupported_trigger_type", type: parsed.invocation.type },
        { status: 400 },
      );
    }

    const started = Date.now();
    const startedSec = Math.floor(started / 1000);
    const scheduledAt = parsed.invocation.data.scheduledAt;
    const runId = `run_${createHash("sha256").update(`${parsed.invocation.invocationId}:${startedSec}`).digest("hex").slice(0, 20)}`;

    let retention: unknown = [];
    let audit: Record<string, unknown> = {};
    let media: MediaSweep | Record<string, unknown> = {};
    let status = "ok";
    let error: string | null = null;

    try {
      await ensureSchema();
      // One bad class must not abort the others: each step is guarded on its own.
      try {
        retention = await retentionReport(startedSec);
      } catch (err) {
        status = "degraded";
        error = `retention: ${(err as Error).message.slice(0, 160)}`;
      }
      try {
        audit = await auditIntegrity();
        if (audit.ok === false) status = status === "ok" ? "attention" : status;
      } catch (err) {
        status = "degraded";
        error = `${error ? error + "; " : ""}audit: ${(err as Error).message.slice(0, 160)}`;
      }
      try {
        media = await sweepOrphanMedia();
      } catch (err) {
        status = "degraded";
        error = `${error ? error + "; " : ""}media: ${(err as Error).message.slice(0, 160)}`;
      }
    } catch (err) {
      status = "failed";
      error = (err as Error).message.slice(0, 300);
    }

    const finishedSec = Math.floor(Date.now() / 1000);
    try {
      await pool.query(
        `insert into re_compliance_runs
           (id, trigger_name, scheduled_at, started_at, finished_at, status, retention, audit, media, error)
         values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10)
         on conflict (id) do nothing`,
        [
          runId,
          parsed.invocation.trigger.name,
          scheduledAt,
          startedSec,
          finishedSec,
          status,
          JSON.stringify(retention),
          JSON.stringify(audit),
          JSON.stringify(media),
          error,
        ],
      );
    } catch (err) {
      console.error("[sweep] could not record the run:", (err as Error).message);
      if (status === "ok") status = "degraded";
    }

    const body = {
      ok: status !== "failed",
      run_id: runId,
      status,
      scheduled_at: scheduledAt,
      duration_ms: Date.now() - started,
      audit,
      retention,
      media,
      error,
    };
    // 5xx only on a failed run, so the platform retries a real failure.
    return Response.json(body, { status: status === "failed" ? 500 : 200 });
  },
};
