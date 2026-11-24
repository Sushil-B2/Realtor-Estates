#!/usr/bin/env node
/**
 * Local dev server for the Neon Functions in this repo.
 *
 * `neon dev` needs an authenticated, linked CLI; this harness is the fallback
 * for a machine where the CLI cannot sign in (and for a sandbox where the OAuth
 * callback is unreachable). It loads the same env the functions get in
 * production, maps a URL path to a function, converts HTTP to a web `Request`,
 * and calls that module's `fetch` — nothing about the handlers changes between
 * this and the deployed functions.
 *
 *   /propertyphotos  → functions/property-photos.ts   (storage trigger target)
 *   /compliancesweep → functions/compliance-sweep.ts  (schedule trigger target)
 *   /paymentswebhook → functions/payments-webhook.ts  (public endpoint, HMAC)
 *
 * Hyphenated aliases (/property-photos, …) resolve to the same handlers, and `/`
 * maps to the photo pipeline, so the first proof script keeps working.
 *
 * Usage:
 *   npx tsx functions/dev-serve.mjs           # Node 20 (what we run here)
 *   node --experimental-strip-types ...       # Node 22+
 *
 * Env: reads .env and .env.local (0600). DATABASE_URL is overridden with the
 * privileged DSN — see the note below. Values are never printed.
 */
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const PORT = Number(process.env.PORT ?? 5434);

// Keys are the deployed slugs (^[a-z0-9]{1,20}$, per @neon/config validation).
const FUNCTIONS = {
  propertyphotos: "./property-photos.ts",
  compliancesweep: "./compliance-sweep.ts",
  paymentswebhook: "./payments-webhook.ts",
};
// The source filenames are hyphenated; accept those paths too so a hand-typed
// curl does not 404 on a naming difference.
const ALIASES = {
  "property-photos": "propertyphotos",
  "compliance-sweep": "compliancesweep",
  "payments-webhook": "paymentswebhook",
};
const DEFAULT_SLUG = "propertyphotos";

function loadEnvFile(name) {
  try {
    const text = readFileSync(join(ROOT, name), "utf8");
    for (const line of text.split("\n")) {
      const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/.exec(line);
      if (!m) continue;
      const key = m[1];
      const value = m[2].replace(/^["']|["']$/g, "");
      // .env.local wins over .env, exactly like app/config.py
      if (process.env[key] === undefined || name === ".env.local") process.env[key] = value;
    }
  } catch {
    /* a missing file is fine */
  }
}
loadEnvFile(".env");
loadEnvFile(".env.local");

// A Function gets DATABASE_URL injected by the platform, connecting as the
// project owner. Locally, .env's DATABASE_URL points at `re_app` — a role with
// no CREATE on schema public (by design: it is the app's least-privilege role),
// which would fail each function's one-time `create table`. So the harness uses
// the privileged DSN to mirror what the runtime actually connects as.
if (process.env.RE_ADMIN_DATABASE_URL) {
  process.env.DATABASE_URL = process.env.RE_ADMIN_DATABASE_URL;
}

const handlers = {};
for (const [slug, file] of Object.entries(FUNCTIONS)) {
  handlers[slug] = (await import(file)).default;
}

const server = createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;

  const url = new URL(req.url ?? "/", `http://127.0.0.1:${PORT}`);
  const segment = url.pathname.replace(/^\/+/, "").split("/")[0] || "";
  const slug = handlers[segment] ? segment : (ALIASES[segment] ?? DEFAULT_SLUG);

  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) {
    if (typeof v === "string") headers.set(k, v);
    else if (Array.isArray(v)) headers.set(k, v.join(", "));
  }

  const request = new Request(url, {
    method: req.method,
    headers,
    body: req.method === "GET" || req.method === "HEAD" ? undefined : body,
  });

  try {
    const response = await handlers[slug].fetch(request);
    const text = await response.text();
    res.writeHead(response.status, { ...Object.fromEntries(response.headers), "x-dev-function": slug });
    res.end(text);
  } catch (err) {
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: false, error: "harness_exception", message: String(err?.message ?? err) }));
  }
});

server.listen(PORT, "127.0.0.1", () => {
  const configured = ["NEON_STORAGE_BUCKET", "NEON_STORAGE_ENDPOINT", "NEON_STORAGE_ACCESS_KEY",
    "NEON_STORAGE_SECRET_KEY", "DATABASE_URL", "PAYMENTS_WEBHOOK_SECRET"].filter((k) => Boolean(process.env[k]));
  console.log(`functions dev server on http://127.0.0.1:${PORT}`);
  console.log(`routes: ${Object.keys(FUNCTIONS).map((s) => `/${s}`).join("  ")}`);
  console.log(`env present: ${configured.join(", ")}`);
});
