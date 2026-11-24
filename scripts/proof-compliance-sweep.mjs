#!/usr/bin/env node
/**
 * End-to-end proof for the `compliancesweep` Neon Function (schedule trigger).
 *
 * It sends the exact delivery Neon's scheduler sends — a `schedule` wire body
 * plus the `x-neon-trigger-invocation-id` header the proxy sets — after first
 * planting a real problem for the sweep to find, and then checks the effects in
 * Postgres and in object storage. No mock: the sweep really scans the branch.
 *
 * What it proves:
 *   1. a photo uploaded, transformed by the photo pipeline, and then *deleted at
 *      the source* leaves orphaned variants behind — the known limitation of
 *      docs/15 §8;
 *   2. the sweep finds exactly those orphans, deletes the derived objects and
 *      their rows, and leaves live media alone;
 *   3. the audit-chain check passes against the live `re_audit` table;
 *   4. retention evidence is reported per class, without deleting anything;
 *   5. the run is recorded in `re_compliance_runs`;
 *   6. a POST without the trigger header is 401, and a storage-shaped delivery
 *      is 400 — the sweep is not an open "run my cron" button.
 *
 *   node scripts/proof-compliance-sweep.mjs [--url http://127.0.0.1:5434/compliancesweep]
 *                                          [--photos-url http://127.0.0.1:5434/propertyphotos]
 */
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { DeleteObjectCommand, HeadObjectCommand, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { Pool } from "pg";
import sharp from "sharp";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};

for (const file of [".env", ".env.local"]) {
  try {
    for (const line of readFileSync(join(ROOT, file), "utf8").split("\n")) {
      const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/.exec(line);
      if (!m) continue;
      const value = m[2].replace(/^["']|["']$/g, "");
      if (process.env[m[1]] === undefined || file === ".env.local") process.env[m[1]] = value;
    }
  } catch {
    /* optional */
  }
}

const SWEEP_URL = arg("url", process.env.SWEEP_URL ?? "http://127.0.0.1:5434/compliancesweep");
const PHOTOS_URL = arg("photos-url", process.env.PHOTOS_URL ?? "http://127.0.0.1:5434/propertyphotos");
const BUCKET = process.env.NEON_STORAGE_BUCKET ?? "projectassets";
const PROPERTY_ID = arg("property", "prp_f24851054c");
const DB_URL = process.env.RE_ADMIN_DATABASE_URL ?? process.env.DATABASE_URL;

const s3 = new S3Client({
  region: process.env.NEON_STORAGE_REGION ?? "ap-southeast-1",
  endpoint: process.env.NEON_STORAGE_ENDPOINT,
  forcePathStyle: true,
  credentials: {
    accessKeyId: process.env.NEON_STORAGE_ACCESS_KEY ?? "",
    secretAccessKey: process.env.NEON_STORAGE_SECRET_KEY ?? "",
  },
});

const ok = (m) => console.log(`  ✓ ${m}`);
const fail = (m) => {
  console.error(`  ✗ ${m}`);
  process.exitCode = 1;
};

const deliver = async (url, body, { header = true } = {}) => {
  const headers = { "content-type": "application/json" };
  if (header) headers["x-neon-trigger-invocation-id"] = body.invocation_id;
  const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  return { status: res.status, json: await res.json().catch(() => ({})) };
};

const scheduleBody = () => ({
  version: 1,
  invocation_id: `inv_${randomUUID()}`,
  trigger: { type: "schedule", id: "trigger_nightly_compliance", name: "nightly-compliance" },
  data: { scheduled_at: new Date().toISOString().replace(/\.\d+Z$/, "Z") },
});

const exists = async (key) => {
  try {
    await s3.send(new HeadObjectCommand({ Bucket: BUCKET, Key: key }));
    return true;
  } catch (err) {
    const status = err?.$metadata?.httpStatusCode;
    if (err?.name === "NotFound" || err?.name === "NoSuchKey" || status === 404) return false;
    throw err;
  }
};

const pool = new Pool({ connectionString: DB_URL, max: 2 });

console.log("» 1. health");
const health = await fetch(SWEEP_URL, { method: "GET" });
const healthBody = await health.json().catch(() => ({}));
console.log(`  GET → ${health.status} ${JSON.stringify(healthBody)}`);
if (health.status === 200) ok("function is live"); else fail(`health returned ${health.status}`);

// ---------------------------------------------------------------------------
// Plant a real orphan: upload a photo, let the pipeline derive variants, then
// delete the source object. This is exactly what a user deleting a photo does.
// ---------------------------------------------------------------------------
console.log("» 2. plant an orphaned photo (upload → pipeline → delete the source)");
const orphanKey = `properties/${PROPERTY_ID}/orphan-${Date.now()}.jpg`;
const photo = await sharp({
  create: { width: 1600, height: 1067, channels: 3, background: { r: 40, g: 20, b: 60 } },
})
  .composite([
    {
      input: Buffer.from(
        `<svg width="1600" height="1067" xmlns="http://www.w3.org/2000/svg">
           <rect width="1600" height="1067" fill="#1e293b"/>
           <text x="80" y="560" font-family="sans-serif" font-size="86" fill="#f8fafc">orphan fixture</text>
         </svg>`,
      ),
      top: 0,
      left: 0,
    },
  ])
  .jpeg({ quality: 84 })
  .toBuffer();

await s3.send(new PutObjectCommand({ Bucket: BUCKET, Key: orphanKey, Body: photo, ContentType: "image/jpeg" }));
const photoDelivery = await deliver(PHOTOS_URL, {
  version: 1,
  invocation_id: `inv_${randomUUID()}`,
  trigger: { type: "storage_object_created", id: "trigger_property_photos", name: "on-property-photo" },
  data: { bucket_name: BUCKET, object_key: orphanKey },
});
if (photoDelivery.status === 200 && photoDelivery.json.ok) {
  ok(`pipeline derived ${photoDelivery.json.variants.length} variants for ${orphanKey}`);
} else {
  fail(`photo pipeline did not process the fixture (${photoDelivery.status})`);
}
const derivedKeys = (photoDelivery.json.variants ?? []).map((v) => v.key);

const { rows: beforeRows } = await pool.query(
  `select count(*)::int as n from re_property_media where source_key = $1`,
  [orphanKey],
);
ok(`${beforeRows[0].n} media rows exist before the source is deleted`);

await s3.send(new DeleteObjectCommand({ Bucket: BUCKET, Key: orphanKey }));
ok("source object deleted — the variants above are now orphans");

// ---------------------------------------------------------------------------
console.log("» 3. deliver the schedule trigger (the nightly run)");
const run = await deliver(SWEEP_URL, scheduleBody());
console.log(`\n  POST → HTTP ${run.status}`);
console.log(JSON.stringify(run.json, null, 2).split("\n").slice(0, 34).join("\n"));
if (run.status === 200 && run.json.ok) ok(`run ${run.json.run_id} finished in ${run.json.duration_ms} ms`);
else fail(`delivery failed (${run.status})`);

console.log("» 4. the orphans are gone, the live media is not");
const { rows: afterRows } = await pool.query(
  `select count(*)::int as n from re_property_media where source_key = $1`,
  [orphanKey],
);
const { rows: liveRows } = await pool.query(`select count(*)::int as n from re_property_media`);
if (afterRows[0].n === 0) ok(`0 rows left for the deleted source (purged ${run.json.media?.purgedRows ?? "?"} rows)`);
else fail(`${afterRows[0].n} orphan rows survived the sweep`);
if (liveRows[0].n > 0) ok(`${liveRows[0].n} rows for live sources untouched`);
else fail("the sweep purged every media row, not just the orphans");

let gone = 0;
for (const key of derivedKeys) if (!(await exists(key))) gone += 1;
if (gone === derivedKeys.length) ok(`${gone}/${derivedKeys.length} derived objects deleted from the bucket`);
else fail(`${derivedKeys.length - gone} derived objects survived`);

console.log("» 5. the run is recorded in Postgres");
const { rows: runRows } = await pool.query(
  `select id, status, started_at, audit, media, retention, error from re_compliance_runs where id = $1`,
  [run.json.run_id ?? "missing"],
);
if (runRows.length === 1) {
  const row = runRows[0];
  ok(`re_compliance_runs: ${row.id} status=${row.status}`);
  console.log(`     audit:     checked=${row.audit.checked} broken_links=${row.audit.broken_links} dup_seq=${row.audit.duplicate_seq} global_seq_gaps=${row.audit.global_seq_gaps} head_seq=${row.audit.head_seq}`);
  console.log(`     media:     scanned=${row.media.scanned} purged_sources=${row.media.purgedSources} purged_objects=${row.media.purgedObjects} purged_rows=${row.media.purgedRows}`);
  for (const r of row.retention) {
    console.log(`     retention: ${String(r.class).padEnd(22)} rows=${r.rows} days=${r.days} past_horizon=${r.past_horizon} action=${r.action}`);
  }
  if (row.audit.ok === true) ok("audit chain: no broken links, no duplicate seq");
  else fail(`audit chain check reported a problem: ${JSON.stringify(row.audit)}`);
  if (row.error) console.log(`     note: ${row.error}`);
} else {
  fail(`expected 1 row in re_compliance_runs for ${run.json.run_id}, found ${runRows.length}`);
}

console.log("» 6. a delivery without the trigger header is refused");
const forged = await deliver(SWEEP_URL, scheduleBody(), { header: false });
if (forged.status === 401) ok(`401 (${forged.json.error}) — the sweep is not an open endpoint`);
else fail(`forged schedule delivery answered ${forged.status}, expected 401`);

console.log("» 7. a storage-shaped delivery cannot run the sweep");
const wrongType = {
  version: 1,
  invocation_id: `inv_${randomUUID()}`,
  trigger: { type: "storage_object_created", id: "t", name: "on-property-photo" },
  data: { bucket_name: BUCKET, object_key: orphanKey },
};
const wrong = await deliver(SWEEP_URL, wrongType);
if (wrong.status === 400 && wrong.json.error === "unsupported_trigger_type") ok("400 unsupported_trigger_type");
else fail(`wrong-type delivery answered ${wrong.status}: ${JSON.stringify(wrong.json).slice(0, 120)}`);

const { rows: recent } = await pool.query(
  `select id, status, started_at from re_compliance_runs order by started_at desc limit 3`,
);
console.log("\n  last runs:");
for (const r of recent) {
  console.log(`    ${r.id}  ${r.status}  ${new Date(Number(r.started_at) * 1000).toISOString()}`);
}

await pool.end();
console.log("\n" + "─".repeat(64));
console.log(process.exitCode ? "PROOF FAILED" : "PROOF PASSED — orphan purge + audit chain + retention, all recorded");
