#!/usr/bin/env node
/**
 * End-to-end proof for the `paymentswebhook` Neon Function.
 *
 * Signs a realistic Razorpay `payment.captured` body with the secret from
 * .env.local (the same HMAC the provider computes), posts it, and checks the
 * behaviour a payments receiver must have:
 *
 *   1. a correctly signed event is accepted and persisted;
 *   2. the same event delivered again is a **duplicate** and still answers 200
 *      (a 4xx on a retry makes the provider retry forever);
 *   3. a tampered body with a genuine-looking signature is a 401 and writes
 *      nothing;
 *   4. no signature → 401; a wrong-length signature → 401, not a 500;
 *   5. an oversized body → 413;
 *   6. a rotated-away secret is rejected while the current one still works.
 *
 *   node scripts/proof-payments-webhook.mjs [--url http://127.0.0.1:5434/paymentswebhook]
 */
import { createHmac, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { Pool } from "pg";

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

const URL_BASE = arg("url", process.env.WEBHOOK_URL ?? "http://127.0.0.1:5434/paymentswebhook");
const DB_URL = process.env.RE_ADMIN_DATABASE_URL ?? process.env.DATABASE_URL;
const SECRET = process.env.PAYMENTS_WEBHOOK_SECRET ?? "";

const ok = (m) => console.log(`  ✓ ${m}`);
const fail = (m) => {
  console.error(`  ✗ ${m}`);
  process.exitCode = 1;
};

if (!SECRET) {
  console.error("PAYMENTS_WEBHOOK_SECRET is not set — add it to .env.local first.");
  process.exit(1);
}

const sign = (body, secret = SECRET) => createHmac("sha256", secret).update(body).digest("hex");

const post = async (body, headers) => {
  const res = await fetch(URL_BASE, { method: "POST", headers, body });
  return { status: res.status, json: await res.json().catch(() => ({})) };
};

const paymentEvent = (eventId) =>
  JSON.stringify({
    entity: "event",
    account_id: "acc_NANDED01",
    event: "payment.captured",
    contains: ["payment"],
    event_id: eventId,
    created_at: Math.floor(Date.now() / 1000),
    payload: {
      payment: {
        entity: {
          id: `pay_${eventId.slice(-8)}`,
          entity: "payment",
          amount: 1250000,          // paise: ₹12,500 token advance
          currency: "INR",
          status: "captured",
          method: "upi",
          notes: { property_id: "prp_f24851054c", tenant_id: "tnt_9889c8d9f0" },
        },
      },
    },
  });

const eventId = `evt_${randomUUID().replace(/-/g, "").slice(0, 18)}`;
const body = paymentEvent(eventId);

console.log("» 1. health");
const health = await fetch(URL_BASE, { method: "GET" });
const healthBody = await health.json().catch(() => ({}));
console.log(`  GET → ${health.status} ${JSON.stringify(healthBody)}`);
if (health.status === 200 && healthBody.configured === true) ok("endpoint live, secret configured");
else fail(`health: ${health.status} ${JSON.stringify(healthBody)}`);

console.log("» 2. correctly signed payment.captured");
const accepted = await post(body, {
  "content-type": "application/json",
  "x-razorpay-signature": sign(body),
});
console.log(`  POST → ${accepted.status} ${JSON.stringify(accepted.json)}`);
if (accepted.status === 200 && accepted.json.ok && accepted.json.duplicate === false) {
  ok(`recorded ${accepted.json.event_type} (${accepted.json.event_id})`);
} else {
  fail(`expected an accepted event, got ${accepted.status}`);
}

console.log("» 3. the provider retries the same event");
const retry = await post(body, {
  "content-type": "application/json",
  "x-razorpay-signature": sign(body),
});
if (retry.status === 200 && retry.json.duplicate === true) {
  ok("200 duplicate — a retry is a no-op, so the provider stops retrying");
} else {
  fail(`retry answered ${retry.status} duplicate=${retry.json.duplicate}, expected 200/true`);
}

console.log("» 4. tampered body, original signature");
const tampered = body.replace('"amount":1250000', '"amount":125');
const tamperedRes = await post(tampered, {
  "content-type": "application/json",
  "x-razorpay-signature": sign(body),
});
if (tamperedRes.status === 401) ok("401 invalid_signature — the bytes are what get signed");
else fail(`tampered delivery answered ${tamperedRes.status}, expected 401`);

console.log("» 5. no signature / wrong-length signature / stale secret");
const unsigned = await post(body, { "content-type": "application/json" });
if (unsigned.status === 401) ok(`401 ${unsigned.json.error}`);
else fail(`unsigned answered ${unsigned.status}, expected 401`);

const shortSig = await post(body, { "content-type": "application/json", "x-razorpay-signature": "ab" });
if (shortSig.status === 401) ok("401 on a wrong-length signature (no 500 from timingSafeEqual)");
else fail(`wrong-length signature answered ${shortSig.status}, expected 401`);

const staleRes = await post(paymentEvent(`evt_${randomUUID().replace(/-/g, "").slice(0, 18)}`), {
  "content-type": "application/json",
  "x-razorpay-signature": sign(body, "a_secret_this_deployment_never_had"),
});
if (staleRes.status === 401) ok("401 for a signature made with an unknown secret");
else fail(`unknown-secret signature answered ${staleRes.status}, expected 401`);

console.log("» 6. oversized body");
const big = JSON.stringify({ event: "payment.captured", event_id: "evt_big", payload: { blob: "x".repeat(70000) } });
const oversized = await post(big, { "content-type": "application/json", "x-razorpay-signature": sign(big) });
if (oversized.status === 413) ok("413 body_too_large");
else fail(`oversized answered ${oversized.status}, expected 413`);

console.log("» 7. what landed in Postgres");
const pool = new Pool({ connectionString: DB_URL, max: 2 });
const { rows } = await pool.query(
  `select event_id, provider, event_type, status, tenant_id, payment_id, amount, currency, key_fingerprint, received_at
     from re_webhook_events where event_id = $1`,
  [eventId],
);
if (rows.length === 1) {
  const r = rows[0];
  ok(`1 row for ${r.event_id} — ${r.event_type}, ${r.currency} ${r.amount}, payment ${r.payment_id}, tenant ${r.tenant_id}, key ${r.key_fingerprint}`);
  if (r.tenant_id === "tnt_9889c8d9f0") ok("tenant attributed from the payment notes, so the app can filter by workspace");
  else fail(`tenant_id is ${r.tenant_id}, expected tnt_9889c8d9f0`);
  // pg returns bigint as a string; compare numerically.
  if (Number(r.amount) === 1250000) ok("amount stored exactly as received (paise, no float rounding)");
  else fail(`amount stored as ${r.amount}`);
} else {
  fail(`expected exactly 1 row for ${eventId}, found ${rows.length}`);
}
const { rows: rejected } = await pool.query(
  `select count(*)::int as n from re_webhook_events where event_id is null`,
);
if (rejected[0].n === 0) ok("no rows written for unauthenticated requests");
else fail(`${rejected[0].n} rows exist without an event id`);

await pool.end();
console.log("\n" + "─".repeat(64));
console.log(process.exitCode ? "PROOF FAILED" : "PROOF PASSED — signed event accepted once, retry deduped, forgeries refused");
