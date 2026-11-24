/**
 * Property photo pipeline — a Neon Function driven by Object Storage.
 *
 * Trigger: `storage_object_created` on the `projectassets` bucket, prefix `properties/`
 * (declared in ../neon.ts). When an agent uploads a listing photo to
 * `properties/<property_id>/<file>`, Neon POSTs a delivery to this function.
 * The bucket on this branch is `projectassets` (see NEON_STORAGE_BUCKET).
 *
 * What it does, in order:
 *   1. authenticates the delivery with `parseTriggerDelivery` (the only caller
 *      shape a trigger route may accept — Neon POSTs carry no JWT, no X-Secret);
 *   2. parses the object key and refuses anything that is not a listing photo;
 *   3. downloads the original, sniffs its real format (never trusts the
 *      extension), and rejects decompression bombs by pixel count;
 *   4. renders three WebP variants (thumb / card / hero) with sharp;
 *   5. writes them to `derived/properties/<id>/…` and upserts one row per variant
 *      into `re_property_media`;
 *   6. returns a JSON summary — key, dimensions, bytes, checksum per variant.
 *
 * Why variants land under `derived/`: the trigger fires on `properties/`, and a
 * write back into that prefix would re-trigger this function on its own output.
 * The guard below (`isDerivedKey`) is the second line of defence for a bucket
 * whose trigger prefix is ever widened.
 *
 * Idempotency: `re_property_media` is unique on (source_key, variant) and the
 * write is an upsert, so a redelivered trigger updates rows instead of
 * duplicating them. Deliberate no-op: the app's own `re_documents` collections
 * are written only by the application process (single-writer read cache) — this
 * function never touches them, it only *reads* the property to resolve the
 * tenant.
 */
import { createHash } from "node:crypto";

import { attachDatabasePool } from "@neon/functions";
import { parseTriggerDelivery } from "@neon/functions/triggers";
import {
  GetObjectCommand,
  HeadObjectCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { Pool } from "pg";
import sharp from "sharp";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const BUCKET = process.env.NEON_STORAGE_BUCKET ?? "projectassets";
const SOURCE_PREFIX = "properties/";
const DERIVED_PREFIX = "derived/properties/";
const MAX_SOURCE_BYTES = 40 * 1024 * 1024;   // a listing photo, not a RAW file
const MAX_INPUT_PIXELS = 80_000_000;          // decompression-bomb ceiling
const ALLOWED_FORMATS = new Set(["jpeg", "png", "webp", "avif", "tiff"]);

const VARIANTS = [
  { name: "thumb", width: 320, quality: 72 },
  { name: "card", width: 960, quality: 78 },
  { name: "hero", width: 1920, quality: 82 },
] as const;

// Storage is S3-compatible; accept Neon's names first, AWS-style as a fallback.
const s3 = new S3Client({
  region: process.env.NEON_STORAGE_REGION ?? process.env.AWS_REGION ?? "ap-southeast-1",
  endpoint: process.env.NEON_STORAGE_ENDPOINT,
  forcePathStyle: true,
  credentials: {
    accessKeyId: process.env.NEON_STORAGE_ACCESS_KEY ?? "",
    secretAccessKey: process.env.NEON_STORAGE_SECRET_KEY ?? "",
  },
});

// A pool at module scope: an isolate serves many requests, so connection setup
// is amortised instead of repeated per delivery.
const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5 });
attachDatabasePool(pool);

let schemaReady: Promise<void> | null = null;

/**
 * One table, created on first use. It is deliberately separate from
 * `re_documents`: the app loads that table into an in-memory working set at
 * boot and assumes a single writer, so a function writing documents there would
 * silently desynchronise the running process.
 */
function ensureSchema(): Promise<void> {
  schemaReady ??= (async () => {
    await pool.query(`
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
      )
    `);
    await pool.query(`
      create unique index if not exists re_property_media_source_variant_idx
        on re_property_media (source_key, variant)
    `);
    await pool.query(`
      create index if not exists re_property_media_property_idx
        on re_property_media (property_id)
    `);
  })().catch((err) => {
    schemaReady = null; // retry on the next delivery rather than caching the failure
    throw err;
  });
  return schemaReady;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
type KeyParts = { propertyId: string; fileName: string };

function parseKey(objectKey: string): KeyParts | null {
  if (!objectKey.startsWith(SOURCE_PREFIX)) return null;
  const rest = objectKey.slice(SOURCE_PREFIX.length);
  const slash = rest.indexOf("/");
  if (slash <= 0) return null;
  const propertyId = rest.slice(0, slash);
  const fileName = rest.slice(slash + 1);
  // `prp_…`, `prop-…`: keep it to ids, not arbitrary paths.
  if (!/^[A-Za-z0-9_-]{3,48}$/.test(propertyId)) return null;
  if (!fileName || fileName.includes("/") || fileName.startsWith(".")) return null;
  return { propertyId, fileName };
}

function isDerivedKey(objectKey: string): boolean {
  return objectKey.startsWith(DERIVED_PREFIX) || objectKey.includes("/variants/");
}

function baseName(fileName: string): string {
  const dot = fileName.lastIndexOf(".");
  const stem = dot > 0 ? fileName.slice(0, dot) : fileName;
  return stem.replace(/[^A-Za-z0-9_-]/g, "-").slice(0, 60) || "photo";
}

function derivedKey(propertyId: string, stem: string, variant: string): string {
  return `${DERIVED_PREFIX}${propertyId}/${stem}-${variant}.webp`;
}

async function resolveTenant(propertyId: string): Promise<string | null> {
  try {
    const { rows } = await pool.query(
      `select doc->>'tenant_id' as tenant_id
         from re_documents
        where collection = 'properties' and doc->>'id' = $1
        limit 1`,
      [propertyId],
    );
    return rows[0]?.tenant_id ?? null;
  } catch (err) {
    // A missing table (JSON-store deployments) must not fail the delivery.
    console.warn("[photos] tenant lookup failed:", (err as Error).message);
    return null;
  }
}

async function put(key: string, body: Buffer, contentType: string): Promise<void> {
  await s3.send(
    new PutObjectCommand({
      Bucket: BUCKET,
      Key: key,
      Body: body,
      ContentType: contentType,
      CacheControl: "public, max-age=31536000, immutable",
      Metadata: { pipeline: "property-photos/v1" },
    }),
  );
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------
export default {
  async fetch(request: Request): Promise<Response> {
    if (request.method === "GET") {
      // Health probe: no DB, no storage — a liveness answer only.
      return Response.json({ ok: true, function: "property-photos", variants: VARIANTS.map((v) => v.name) });
    }
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, POST" } });
    }

    const parsed = await parseTriggerDelivery(request);
    if (!parsed.ok) {
      const status = parsed.error === "invalid_body" ? 400 : 401;
      return Response.json({ ok: false, error: parsed.error }, { status });
    }

    const invocation = parsed.invocation;
    if (invocation.type !== "storage_object_created") {
      return Response.json({ ok: false, error: "unsupported_trigger_type", type: invocation.type }, { status: 400 });
    }

    const objectKey = invocation.data.objectKey;
    const bucketName = invocation.data.bucketName;

    if (bucketName !== BUCKET) {
      return Response.json({ ok: true, skipped: "other_bucket", bucketName });
    }
    if (isDerivedKey(objectKey)) {
      // Our own output landing back in the bucket: stop here, never loop.
      return Response.json({ ok: true, skipped: "derived_object", objectKey });
    }

    const parts = parseKey(objectKey);
    if (!parts) {
      return Response.json({ ok: true, skipped: "not_a_property_photo", objectKey });
    }

    const started = Date.now();
    try {
      await ensureSchema();

      const head = await s3.send(new HeadObjectCommand({ Bucket: BUCKET, Key: objectKey }));
      const size = Number(head.ContentLength ?? 0);
      if (size > MAX_SOURCE_BYTES) {
        return Response.json(
          { ok: false, error: "source_too_large", bytes: size, limit: MAX_SOURCE_BYTES },
          { status: 413 },
        );
      }

      const object = await s3.send(new GetObjectCommand({ Bucket: BUCKET, Key: objectKey }));
      if (!object.Body) {
        return Response.json({ ok: false, error: "empty_body" }, { status: 422 });
      }
      const source = Buffer.from(await object.Body.transformToByteArray());

      // Sniff the real format from the bytes; the key's extension is a claim.
      const meta = await sharp(source).metadata();
      const format = meta.format ?? "unknown";
      if (!ALLOWED_FORMATS.has(format)) {
        return Response.json({ ok: false, error: "unsupported_format", format }, { status: 415 });
      }
      const pixels = (meta.width ?? 0) * (meta.height ?? 0);
      if (pixels > MAX_INPUT_PIXELS) {
        return Response.json(
          { ok: false, error: "too_many_pixels", pixels, limit: MAX_INPUT_PIXELS },
          { status: 413 },
        );
      }
      if (!meta.width || !meta.height) {
        return Response.json({ ok: false, error: "unreadable_image" }, { status: 422 });
      }

      const tenantId = await resolveTenant(parts.propertyId);
      const stem = baseName(parts.fileName);
      const now = Math.floor(Date.now() / 1000);
      const variants: Array<Record<string, unknown>> = [];

      for (const spec of VARIANTS) {
        // Never upscale: a 400px photo should not become a 1920px hero.
        const width = Math.min(spec.width, meta.width);
        const rendered = await sharp(source)
          .rotate()                                  // honour EXIF orientation
          .resize({ width, withoutEnlargement: true })
          .webp({ quality: spec.quality, effort: 4 })
          .toBuffer({ resolveWithObject: true });

        const key = derivedKey(parts.propertyId, stem, spec.name);
        await put(key, rendered.data, "image/webp");

        const checksum = sha256(rendered.data).toString("hex").slice(0, 32);
        await pool.query(
          `insert into re_property_media
             (id, property_id, tenant_id, bucket, source_key, variant, object_key,
              format, width, height, bytes, checksum, created_at, updated_at)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13)
           on conflict (source_key, variant) do update set
             object_key = excluded.object_key,
             width      = excluded.width,
             height     = excluded.height,
             bytes      = excluded.bytes,
             checksum   = excluded.checksum,
             tenant_id  = coalesce(excluded.tenant_id, re_property_media.tenant_id),
             updated_at = excluded.updated_at`,
          [
            `med_${spec.name}_${sha256(Buffer.from(objectKey)).toString("hex").slice(0, 12)}`,
            parts.propertyId,
            tenantId,
            BUCKET,
            objectKey,
            spec.name,
            key,
            "webp",
            rendered.info.width,
            rendered.info.height,
            rendered.info.size,
            checksum,
            now,
          ],
        );

        variants.push({
          variant: spec.name,
          key,
          width: rendered.info.width,
          height: rendered.info.height,
          bytes: rendered.info.size,
          checksum,
        });
      }

      return Response.json({
        ok: true,
        propertyId: parts.propertyId,
        tenantId,
        bucket: BUCKET,
        source: { key: objectKey, bytes: size, format, width: meta.width, height: meta.height },
        variants,
        durationMs: Date.now() - started,
        trigger: { id: invocation.trigger.id, name: invocation.trigger.name },
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[photos] failed:", message);
      // 500 lets the trigger retry; the message is a summary, never a credential.
      return Response.json({ ok: false, error: "processing_failed", message }, { status: 500 });
    }
  },
};

/** SHA-256 of a buffer — used for the content checksum and the row id. */
function sha256(input: Buffer): Buffer {
  return createHash("sha256").update(input).digest();
}
