#!/usr/bin/env node
/**
 * End-to-end proof for the property-photos Neon Function.
 *
 * It does what a real upload does, without a mock:
 *   1. renders a 3200×2133 JPEG (a listing-photo-shaped file);
 *   2. PUTs it into the live `assets` bucket at properties/<property_id>/…;
 *   3. POSTs a trigger delivery to the function (local dev server, or the
 *      deployed URL via FUNCTION_URL) with the x-neon-trigger-invocation-id
 *      header that Neon's proxy sets;
 *   4. checks the three variants exist in the bucket and the rows exist in
 *      Postgres;
 *   5. re-delivers to prove idempotency, and delivers a `derived/` key to prove
 *      the self-retrigger guard.
 *
 * Secrets are read from .env / .env.local and never printed.
 *
 *   node scripts/proof-photo-pipeline.mjs [--property prp_xxx] [--url http://127.0.0.1:5434]
 */
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { HeadObjectCommand, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
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

const BUCKET = process.env.NEON_STORAGE_BUCKET ?? "assets";
const FUNCTION_URL = arg("url", process.env.FUNCTION_URL ?? "http://127.0.0.1:5434");
const PROPERTY_ID = arg("property", "prp_f24851054c");
const KEY = `properties/${PROPERTY_ID}/proof-${Date.now()}.jpg`;
// The app role (re_app) has no grant on the function-owned media table yet, so
// verification reads with the same privileged DSN the function connects as.
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

const fail = (msg) => {
  console.error(`\n✗ ${msg}`);
  process.exitCode = 1;
};
const ok = (msg) => console.log(`  ✓ ${msg}`);

console.log("» 1. render a listing photo (3200×2133 JPEG)");
const source = await sharp({
  create: { width: 3200, height: 2133, channels: 3, background: { r: 26, g: 40, b: 64 } },
})
  .composite([
    {
      input: Buffer.from(
        `<svg width="3200" height="2133" xmlns="http://www.w3.org/2000/svg">
           <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
             <stop offset="0%" stop-color="#0f766e"/><stop offset="55%" stop-color="#1d4ed8"/>
             <stop offset="100%" stop-color="#7c2d12"/>
           </linearGradient></defs>
           <rect width="3200" height="2133" fill="url(#g)"/>
           <circle cx="2400" cy="520" r="330" fill="#fde68a" opacity="0.85"/>
           <rect x="0" y="1500" width="3200" height="633" fill="#0b1220" opacity="0.55"/>
           <text x="160" y="1820" font-family="sans-serif" font-size="150" fill="#ffffff">Sunrise Greens — Plot 12</text>
           <text x="160" y="1960" font-family="sans-serif" font-size="82" fill="#cbd5e1">Nanded · 2400 sqft · proof photo</text>
         </svg>`,
      ),
      top: 0,
      left: 0,
    },
  ])
  .jpeg({ quality: 88 })
  .toBuffer({ resolveWithObject: true });

console.log(`  photo: ${source.info.width}×${source.info.height}, ${(source.info.size / 1024).toFixed(0)} KB`);
ok(`sha256 ${createHash("sha256").update(source.data).digest("hex").slice(0, 16)}…`);

console.log("» 2. upload to the live bucket");
await s3.send(
  new PutObjectCommand({
    Bucket: BUCKET,
    Key: KEY,
    Body: source.data,
    ContentType: "image/jpeg",
  }),
);
ok(`PUT s3://${BUCKET}/${KEY}`);

const deliver = async (objectKey, label) => {
  const invocationId = `inv_${randomUUID()}`;
  const body = {
    version: 1,
    invocation_id: invocationId,
    trigger: { type: "storage_object_created", id: "trigger_property_photos", name: "on-property-photo" },
    data: { bucket_name: BUCKET, object_key: objectKey },
  };
  const res = await fetch(FUNCTION_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-neon-trigger-invocation-id": invocationId, // set by Neon's proxy in production
    },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  console.log(`\n» ${label} → HTTP ${res.status}`);
  console.log(JSON.stringify(json, null, 2));
  return { status: res.status, json };
};

console.log("» 3. deliver the storage trigger to the function");
const first = await deliver(KEY, "trigger delivery #1");
if (first.status !== 200 || !first.json.ok) fail(`delivery failed (${first.status})`);
else ok(`3 variants rendered in ${first.json.durationMs} ms`);

const variants = first.json.variants ?? [];
for (const v of variants) {
  const head = await s3.send(new HeadObjectCommand({ Bucket: BUCKET, Key: v.key }));
  const same = Number(head.ContentLength) === v.bytes;
  if (same) ok(`${v.variant.padEnd(5)} ${String(v.width).padStart(4)}×${String(v.height).padEnd(4)} ${(v.bytes / 1024).toFixed(0).padStart(4)} KB  ${v.key}`);
  else fail(`${v.variant}: size mismatch (${head.ContentLength} vs ${v.bytes})`);
}
if (!variants.length) fail("no variants returned");

console.log("» 4. rows in Postgres (re_property_media)");
const pool = new Pool({ connectionString: DB_URL, max: 2 });
const { rows } = await pool.query(
  `select variant, width, height, bytes, object_key, tenant_id
     from re_property_media where source_key = $1 order by width`,
  [KEY],
);
for (const r of rows) ok(`${r.variant.padEnd(5)} ${r.width}×${r.height} ${r.bytes}B tenant=${r.tenant_id ?? "null"}`);
if (rows.length !== variants.length) fail(`expected ${variants.length} rows, found ${rows.length}`);

console.log("» 5. idempotency: deliver the same object again");
const second = await deliver(KEY, "trigger delivery #2 (redelivery)");
const { rows: after } = await pool.query(
  `select count(*)::int as n from re_property_media where source_key = $1`,
  [KEY],
);
if (second.status === 200 && after[0].n === rows.length) ok(`still ${after[0].n} rows after redelivery — no duplicates`);
else fail(`redelivery changed the row count (${rows.length} → ${after[0].n})`);

console.log("» 6. loop guard: deliver a derived key (the function's own output)");
const derivedKey = variants[0]?.key ?? `derived/properties/${PROPERTY_ID}/x.webp`;
const loop = await deliver(derivedKey, "trigger delivery #3 (derived object)");
if (loop.status === 200 && loop.json.skipped === "derived_object") ok("skipped — variants can never re-trigger the pipeline");
else fail(`derived object was not skipped (${loop.status} ${JSON.stringify(loop.json).slice(0, 120)})`);

console.log("» 7. rejects a forged delivery (no trigger header)");
const forged = await fetch(FUNCTION_URL, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({ version: 1, invocation_id: "inv_x", trigger: { type: "storage_object_created", id: "t", name: "n" }, data: { bucket_name: BUCKET, object_key: KEY } }),
});
if (forged.status === 401) ok("401 — a POST without the trigger header is refused");
else fail(`forged delivery answered ${forged.status}, expected 401`);

await pool.end();

console.log("\n" + "─".repeat(64));
console.log(process.exitCode ? "PROOF FAILED" : "PROOF PASSED — real upload → 3 WebP variants → Postgres rows");
console.log(`source key: ${KEY}`);
console.log(`bucket: ${BUCKET} · property: ${PROPERTY_ID} · function: ${FUNCTION_URL}`);
