#!/usr/bin/env node
/**
 * Upload listing photographs for one property, through the real pipeline.
 *
 *   node scripts/upload-listing-photos.mjs --property prp_xxx photos/*.jpg
 *   node scripts/upload-listing-photos.mjs --property prp_xxx --url http://127.0.0.1:5434 a.jpg b.jpg
 *
 * What it does, and deliberately does not do:
 *   1. PUTs each file to `properties/<id>/listing-<timestamp>-<n>.<ext>` in the
 *      configured bucket — the same prefix a browser upload would use;
 *   2. POSTs a storage-trigger delivery to the `property-photos` function (local
 *      dev server, or the deployed URL via FUNCTION_URL / --url);
 *   3. reports the variants the function rendered.
 *
 * It never writes `re_property_media` itself. That table belongs to the function
 * (docs/15): the app reads it, the function writes it, and a utility that
 * inserted rows would be a second writer with a different idea of what a variant
 * is. If the function is not reachable, the upload still lands and the rows do
 * not exist — which is the honest state, and `GET /properties/{id}/media`
 * answers `none` rather than inventing a photo.
 *
 * Secrets come from .env and .env.local and are never printed.
 */
import { randomUUID } from "node:crypto";
import { readFileSync, statSync } from "node:fs";
import { basename, dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { PutObjectCommand, S3Client } from "@aws-sdk/client-s3";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const args = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : fallback;
};
const flag = (name) => args.includes(`--${name}`);

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

const PROPERTY_ID = arg("property", "");
const FUNCTION_URL = arg("url", process.env.FUNCTION_URL ?? "http://127.0.0.1:5434");
const BUCKET = process.env.NEON_STORAGE_BUCKET ?? "assets";
const DRY = flag("dry-run");

const files = args.filter((a) => !a.startsWith("--") && a !== PROPERTY_ID && a !== FUNCTION_URL && /\.(jpe?g|png|webp|avif)$/i.test(a));
if (!PROPERTY_ID || !files.length) {
  console.error("usage: node scripts/upload-listing-photos.mjs --property prp_xxx photo1.jpg photo2.jpg [--dry-run]");
  process.exit(2);
}

const s3 = new S3Client({
  region: process.env.NEON_STORAGE_REGION ?? "ap-southeast-1",
  endpoint: process.env.NEON_STORAGE_ENDPOINT,
  forcePathStyle: true,
  credentials: {
    accessKeyId: process.env.NEON_STORAGE_ACCESS_KEY ?? "",
    secretAccessKey: process.env.NEON_STORAGE_SECRET_KEY ?? "",
  },
});

const stamp = Date.now();
let uploaded = 0;
let derived = 0;

for (const [i, file] of files.entries()) {
  const ext = (extname(file) || ".jpg").toLowerCase();
  const key = `properties/${PROPERTY_ID}/listing-${stamp}-${i + 1}${ext}`;
  const type = ext === ".png" ? "image/png" : ext === ".webp" ? "image/webp" : "image/jpeg";
  const size = statSync(file).size;
  if (DRY) {
    console.log(`would PUT ${file} (${(size / 1024).toFixed(0)} KB) → s3://${BUCKET}/${key}`);
    continue;
  }
  await s3.send(new PutObjectCommand({ Bucket: BUCKET, Key: key, Body: readFileSync(file), ContentType: type }));
  uploaded += 1;
  console.log(`✓ PUT ${basename(file)} (${(size / 1024).toFixed(0)} KB) → ${key}`);

  const invocationId = `inv_${randomUUID()}`;
  const res = await fetch(FUNCTION_URL, {
    method: "POST",
    headers: { "content-type": "application/json", "x-neon-trigger-invocation-id": invocationId },
    body: JSON.stringify({
      version: 1,
      invocation_id: invocationId,
      trigger: { type: "storage_object_created", id: "trigger_property_photos", name: "on-property-photo" },
      data: { bucket_name: BUCKET, object_key: key },
    }),
  }).catch((err) => ({ status: 0, json: async () => ({ error: String(err) }) }));
  const body = await res.json().catch(() => ({}));
  if (res.status === 200 && body.ok) {
    derived += 1;
    console.log(`  → ${body.variants.length} variants in ${body.durationMs} ms: ${body.variants.map((v) => v.variant).join(", ")}`);
  } else {
    console.warn(`  ! trigger delivery failed (HTTP ${res.status}). The object is uploaded; re-deliver it once the function is up.`);
    console.warn(`    ${JSON.stringify(body).slice(0, 300)}`);
  }
}

console.log(`\n${uploaded} object(s) uploaded, ${derived} derived into variants — property ${PROPERTY_ID}`);
console.log("Check: GET /api/v1/properties/" + PROPERTY_ID + "/media");
