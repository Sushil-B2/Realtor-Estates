/**
 * Payments webhook receiver — a Neon Function with a public HTTPS endpoint.
 *
 * This is not a trigger: the caller is a payments provider (Razorpay today;
 * the app already declares `razorpay` but had no inbound handler), so the
 * function authenticates the request itself:
 *
 *   X-Razorpay-Signature: <hex HMAC-SHA256 of the raw body, key = webhook secret>
 *
 * Rules this file follows, all of them things webhook receivers get wrong:
 *
 *   - **Sign the raw bytes.** The HMAC is computed over the exact body received,
 *     before any JSON parsing. Parsing first and re-serialising changes key order
 *     and whitespace, and every signature then fails — or, worse, a receiver
 *     that "fixes" it ends up validating something the provider never sent.
 *   - **Compare in constant time**, and compare equal-length buffers only
 *     (`timingSafeEqual` throws on a length mismatch, which is itself a signal).
 *   - **Assume at-least-once delivery.** Providers retry. Events are deduped on
 *     `(provider, event_id)` with a unique index, so a retry is a no-op that
 *     still answers 200 — a 4xx on a duplicate makes the provider retry forever.
 *   - **Never persist an unauthenticated body.** A bad signature gets a 401 and
 *     nothing is written, so the endpoint cannot be used to fill the database
 *     with attacker-controlled JSON.
 *   - **Cap the body** before buffering it.
 *
 * The handler does not mutate the app's document store. It records the verified
 * event; the app reads it (`/api/v1/webhooks/events`) and reconciles. One writer
 * per table stays true: this function owns `re_webhook_events`, the app only
 * reads it.
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

import { attachDatabasePool } from "@neon/functions";
import { Pool } from "pg";

const MAX_BODY_BYTES = 64 * 1024;
const PROVIDER = "razorpay";
/** Secrets are read per request so a rotated value takes effect on redeploy without a code change. */
const secretNames = ["PAYMENTS_WEBHOOK_SECRET", "PAYMENTS_WEBHOOK_SECRET_PREV"] as const;

const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 5 });
attachDatabasePool(pool);

let schemaReady: Promise<void> | null = null;

function ensureSchema(): Promise<void> {
  schemaReady ??= (async () => {
    await pool.query(`
      create table if not exists re_webhook_events (
        id                 text primary key,
        provider           text not null,
        event_id           text,
        event_type         text,
        status             text not null,
        received_at        bigint not null,
        tenant_id          text,
        payment_id         text,
        amount             bigint,
        currency           text,
        key_fingerprint    text not null,
        payload            jsonb
      )
    `);
    // Defensive: on a branch where the function ran before db/007, the column is
    // missing. `add column if not exists` keeps both paths converging.
    await pool.query(`alter table re_webhook_events add column if not exists tenant_id text`);
    // The dedupe guarantee lives in the database, not in a read-then-write race.
    await pool.query(`
      create unique index if not exists re_webhook_events_dedupe_idx
        on re_webhook_events (provider, event_id)
        where event_id is not null
    `);
    await pool.query(`
      create index if not exists re_webhook_events_received_idx
        on re_webhook_events (received_at desc)
    `);
    // Tenant attribution comes from the provider's `notes`; see extractPayment.
    await pool.query(`
      create index if not exists re_webhook_events_tenant_idx
        on re_webhook_events (tenant_id, received_at desc)
    `);
  })().catch((err) => {
    schemaReady = null;
    throw err;
  });
  return schemaReady;
}

function signatureFrom(request: Request): string {
  const razorpay = request.headers.get("x-razorpay-signature");
  if (razorpay) return razorpay.trim();
  const generic = request.headers.get("x-webhook-signature") ?? "";
  // GitHub-style "sha256=<hex>" is accepted so the same endpoint can receive a
  // portal's webhook without a second function.
  return generic.replace(/^sha256=/i, "").trim();
}

/**
 * True when either the current or the previous secret validates the body.
 * Returns the fingerprint of the key that matched (never the key itself), so a
 * stored event shows which secret era it belongs to during a rotation.
 */
function verify(raw: Buffer, signature: string): { ok: boolean; fingerprint: string } {
  const provided = Buffer.from(signature.toLowerCase().replace(/[^0-9a-f]/g, ""), "hex");
  let fingerprint = "";
  for (const name of secretNames) {
    const secret = process.env[name];
    if (!secret) continue;
    const expected = createHmac("sha256", secret).update(raw).digest();
    const mark = createHash("sha256").update(secret).digest("hex").slice(0, 8);
    if (!fingerprint) fingerprint = mark;
    if (provided.length === expected.length && timingSafeEqual(provided, expected)) {
      return { ok: true, fingerprint: mark };
    }
  }
  return { ok: false, fingerprint };
}

export default {
  async fetch(request: Request): Promise<Response> {
    if (request.method === "GET") {
      return Response.json({
        ok: true,
        function: "payments-webhook",
        provider: PROVIDER,
        configured: secretNames.some((n) => Boolean(process.env[n])),
      });
    }
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, POST" } });
    }

    const configured = secretNames.map((n) => process.env[n]).filter(Boolean) as string[];
    if (!configured.length) {
      // Fail loudly rather than accept unverified payments traffic.
      return Response.json({ ok: false, error: "webhook_secret_not_configured" }, { status: 503 });
    }

    const declared = Number(request.headers.get("content-length") ?? 0);
    if (declared > MAX_BODY_BYTES) {
      return Response.json({ ok: false, error: "body_too_large" }, { status: 413 });
    }

    const raw = Buffer.from(await request.arrayBuffer());
    if (raw.length > MAX_BODY_BYTES) {
      return Response.json({ ok: false, error: "body_too_large" }, { status: 413 });
    }

    const signature = signatureFrom(request);
    if (!signature) {
      return Response.json({ ok: false, error: "missing_signature" }, { status: 401 });
    }
    const verdict = verify(raw, signature);
    if (!verdict.ok) {
      // Deliberately writes nothing: an unauthenticated body must not reach a table.
      return Response.json({ ok: false, error: "invalid_signature" }, { status: 401 });
    }

    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(raw.toString("utf8"));
    } catch {
      return Response.json({ ok: false, error: "invalid_json" }, { status: 400 });
    }

    const bodyHash = createHash("sha256").update(raw).digest("hex").slice(0, 24);
    const eventId =
      asString(payload.event_id) ||
      request.headers.get("x-razorpay-event-id")?.trim() ||
      asString(payload.id) ||
      `body_${bodyHash}`;
    const eventType = asString(payload.event) || asString(payload.type) || "unknown";
    const payment = extractPayment(payload);
    const receivedAt = Math.floor(Date.now() / 1000);

    try {
      await ensureSchema();
      const inserted = await pool.query(
        `insert into re_webhook_events
           (id, provider, event_id, event_type, status, received_at, tenant_id, payment_id, amount, currency, key_fingerprint, payload)
         values ($1,$2,$3,$4,'accepted',$5,$6,$7,$8,$9,$10,$11::jsonb)
         on conflict (provider, event_id) where event_id is not null do nothing
         returning id`,
        [
          `evt_${bodyHash}`,
          PROVIDER,
          eventId,
          eventType,
          receivedAt,
          payment.tenantId,
          payment.id,
          payment.amount,
          payment.currency,
          verdict.fingerprint,
          JSON.stringify(payload),
        ],
      );
      const duplicate = (inserted.rowCount ?? 0) === 0;
      return Response.json(
        {
          ok: true,
          duplicate,
          event_id: eventId,
          event_type: eventType,
          tenant_id: payment.tenantId,
          received_at: receivedAt,
          // 200 either way: a provider that gets 409 on a retry retries forever.
          note: duplicate ? "already recorded; no side effects applied twice" : "recorded",
        },
        { status: 200 },
      );
    } catch (err) {
      console.error("[webhook] persist failed:", (err as Error).message.slice(0, 200));
      // 500 invites the provider's retry, which is what we want for a transient failure.
      return Response.json({ ok: false, error: "persist_failed" }, { status: 500 });
    }
  },
};

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Razorpay nests the entity; keep the two fields the app reconciles on. */
function extractPayment(payload: Record<string, unknown>): {
  id: string | null;
  amount: number | null;
  currency: string | null;
  tenantId: string | null;
} {
  const container = (payload.payload ?? {}) as Record<string, unknown>;
  const wrapper = (container.payment ?? container.order ?? {}) as Record<string, unknown>;
  // Razorpay nests one level deeper than it looks: payload.payment.entity.{...}
  const entity = ((wrapper.entity ?? wrapper) ?? {}) as Record<string, unknown>;
  const amount = entity.amount;
  // The provider has no idea which workspace a payment belongs to; the tenant is
  // carried in `notes` (set when the order is created) and copied here so the app
  // can filter by tenant_id instead of digging through the payload.
  const notes = (entity.notes ?? payload.notes ?? {}) as Record<string, unknown>;
  return {
    id: asString(entity.id) || null,
    amount: typeof amount === "number" && Number.isFinite(amount) ? Math.trunc(amount) : null,
    currency: asString(entity.currency) || null,
    tenantId: asString(notes.tenant_id) || null,
  };
}
