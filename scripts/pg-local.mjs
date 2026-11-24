/**
 * Local Postgres for development/CI when Neon is not reachable.
 *
 * PGlite is the real Postgres query engine compiled to WASM; this server puts
 * the genuine Postgres wire protocol in front of it, so `psycopg` connects
 * exactly as it would to Neon. It is a test stand-in, not a substitute:
 *
 *   - single connection, single process  (no real concurrency)
 *   - no TLS, no roles/GRANT enforcement by default
 *   - data lives on disk under .pgdata/ (git-ignored)
 *
 * Usage:  node scripts/pg-local.mjs [port]
 * Then:   DATABASE_URL=postgresql://postgres@127.0.0.1:5433/postgres python3 -m app.db migrate
 */
import { PGlite } from "@electric-sql/pglite";
import { createServer } from "pglite-server";
import { mkdirSync } from "node:fs";

const port = Number(process.argv[2] ?? 5433);  // 0 = let the OS choose
const dataDir = process.env.PG_LOCAL_DATA || ".pgdata";

mkdirSync(dataDir, { recursive: true });
const db = new PGlite(dataDir);
await db.waitReady;

const server = createServer(db);
server.listen(port, "127.0.0.1", () => {
  const actual = server.address().port;
  // PORT= is parsed by the test harness; pass 0 to avoid port collisions.
  console.log(`PORT=${actual}`);
  console.log(`pglite (real Postgres wire protocol) listening on 127.0.0.1:${actual}`);
  console.log(`data dir: ${dataDir}`);
  console.log(`DSN: postgresql://postgres@127.0.0.1:${actual}/postgres`);
});
