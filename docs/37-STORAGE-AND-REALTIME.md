# 37 — Object Storage Uploads, Cross-Instance Realtime SSE & Scale-to-Zero Caching (`v1.26.0`)

> Closes `PLANS.md` `## Backend plan — Neon` **#3** (object storage upload path
> for exports & call recordings), **#4** (cross-instance SSE fan-out + 100
> concurrent inbox load test, completing **S0**), and **#6** (batched inbox SQL
> reads + static shell `ETag`/`304` caching + scale-to-zero `/healthz`).

---

## 1. Object Storage Upload Path for Exports & Recordings (`Backend plan #3`)

### Architecture (`app/neon.py` + `app/api.py`)

`app/neon.py` previously implemented query-string `presign_get` only. `v1.26.0`
adds pure-stdlib (`D2`, zero third-party SDKs) header-signed AWS SigV4 S3
operations:

- **`neon._clean_object_key(key)`**: rejects empty keys, NUL/backslash bytes,
  `//` empty segments, and `.` / `..` traversal segments before any URI is built
  or signed, preventing cross-prefix traversal across tenant namespaces.
- **`neon.put_object(key, data, content_type)`**: signs `host`,
  `x-amz-content-sha256` (actual SHA-256 of payload bytes, not
  `UNSIGNED-PAYLOAD`), `x-amz-date`, and `content-type` under
  `AWS4-HMAC-SHA256`, verifying end-to-end payload integrity on upload.
- **`neon.head_object(key)`** & **`neon.delete_object(key)`**: stat and delete
  objects without exposing credentials in logs or error payloads.

Four tenant-scoped API routes wire exports and call recordings to private object
storage (`exports/tenants/<tid>/…` and `recordings/tenants/<tid>/…`):

| Route | Gate | Behaviour |
|---|---|---|
| `POST /api/v1/exports` | `manage_tenant` (`owner`/`admin`) | Builds `audit` (`json`/`csv`, respecting DPDP PII retention window), `contacts` (`json`/`csv`), or `compliance_bundle` (`json`); uploads to `exports/tenants/<tid>/<exp_id>.<ext>` when `NEON_STORAGE_*` is configured (or bounded inline fallback in offline dev; `503 STORAGE_NOT_CONFIGURED` if `require_object_storage=true`, `502 STORAGE_UPLOAD_FAILED` on upstream error); audits `export.created` |
| `GET /api/v1/exports` | `manage_tenant` | Lists workspace export runs and mints fresh 900 s `presign_get` `download_url`s |
| `GET /api/v1/exports/{eid}` | `manage_tenant` | Tenant-scoped (`404` on foreign workspace ID), mints fresh 900 s `download_url`, audits `export.downloaded` |
| `GET /api/v1/recordings/{rid}` | `pii:read` (`owner`/`admin`/`manager`) | Tenant-scoped (`404` on foreign workspace ID, `403` for `viewer`/`channel_partner`), mints fresh 900 s `download_url` for call recordings archived by `POST /api/v1/contacts/{cid}/calls` (`recording_b64` WAV/MP3/OGG/WebM up to 512 KB or `transcript`) and `POST /api/v1/agents/{aid}/voice-test`; audits `recording.accessed` |

### Live Cloud Proof (`scripts/proof_storage_uploads.py` — `13/13 PASS`)

Verified live against Neon `projectassets` (`ap-southeast-1`):
- 6/6 path-traversal key vectors refused before signing.
- Direct `put_object` → `head_object` → `presign_get` (`HTTP 200`, exact
  SHA-256 match) → `delete_object` → `head_object` (`exists=False`).
- `compliance_bundle` JSON (`5,039 B`), `audit` CSV (`2,020 B`), `audio/wav`
  call recording (`1,644 B`), and AI voice-test transcript (`1,755 B`) uploaded,
  downloaded via presigned HTTPS URLs with byte-for-byte SHA-256 verification,
  BOLA/RBAC verified (`metro` → `404`, `viewer` → `403`), and all 5 proof keys
  deleted on exit (`remaining=0`).

---

## 2. Cross-Instance Realtime SSE Fan-Out & 100-Inbox Load Test (`Backend plan #4` / `S0`)

### Architecture (`app/shared_state.py` + `app/realtime.py` + `app/server.py`)

Replaced the per-socket `for i in range(40): STORE.all(...); time.sleep(4)` loop
in `app/server.py`:

1. **Shared-state tenant event rings (`publish_event` / `events_since`)** across
   all three adapters in `app/shared_state.py`:
   - `Redis` (in-tree RESP2, zero Lua/`EVAL` required): atomic `INCR evseq:<tid>`
     + `RPUSH` / `LTRIM -256 -1` / `PEXPIRE` on `evring:<tid>`, with fast-path
     `GET evseq:<tid>` cursor short-circuit when idle.
   - `SharedFiles` (`RE_SHARED_DIR`): `flock`-protected per-tenant ring with an
     unlocked `os.stat()` `(mtime_ns, ino, size)` fast-path so idle relay ticks
     cost zero locks and zero JSON parses.
   - `_MemoryAdapter`: thread-safe per-tenant bounded ring (`256` events, `1 h`
     TTL).
2. **Per-process `RealtimeHub` multiplexer (`app/realtime.py`)**:
   - Publishers (`POST /inbox/{cid}/send`, `POST /contacts`, `POST /contacts/{cid}/calls`,
     `POST /agents/{aid}/propose`, `POST /learning/{pid}/decide`, and background
     `automations.Runtime` outbound messages) emit compact, PII-free (`D14`)
     envelopes.
   - Local SSE subscribers receive frames in memory in `<1 ms`; **one** daemon
     relay thread per server process polls active tenant rings and fans remote
     events out to 100+ local `Subscription` queues while invoking
     `STORE.invalidate_freshness(tid, collections)` so subsequent reads refresh
     from Postgres.
   - Supports W3C `Last-Event-ID` (and `?since=`) reconnect replay without
     losing frames across reconnects or rolling deploys.

### Measured 100-Concurrent-Inbox Multi-Node Load Test (`scripts/proof_realtime_inboxes.py` — `11/11 PASS`)

- **Topology**: 2 live `app.server` OS processes (`Node A` + `Node B`) sharing
  state, with **100 concurrent open `text/event-stream` sockets** (`60` `sunrise`
  split `30/30` across `Node A` & `Node B`; `40` `metro` split `20/20` across
  `Node A` & `Node B`).
- **Throughput & latency**: `5` concurrent cross-node mutations (`3` `sunrise` on
  `Node A`, `2` `metro` on `Node B`) delivered **`260/260` frames** (`180/180`
  `sunrise`, `80/80` `metro`) in **`240.4 ms` total wall time** (**cross-node
  `p50 = 45.3 ms`, `p95 = 77.9 ms`, `p99 = 82.2 ms`**), **`0` errors**, and
  **`0` cross-tenant leaks**.
- **`Last-Event-ID` cursor replay** across nodes (`Node A` write → `Node B`
  reconnect replay) and **cross-node session revocation** (`DELETE /auth/sessions/{sid}`
  on `Node A` → `401` on `Node B` SSE) verified.

---

## 3. Batched Inbox Reads & Scale-to-Zero Static Shell Caching (`Backend plan #6`)

1. **Batched multi-collection inbox SQL reads (`app/store.py` + `app/api.py`)**:
   - `GET /api/v1/inbox` and `GET /api/v1/inbox/{cid}` now call
     `ctx.store.reload_many(("conversations", "contacts"), ctx.tid, max_age_ms=250)`,
     issuing **one single batched SQL query** (`SELECT collection, doc FROM re_documents WHERE tenant_id = %s AND collection = ANY(%s)`)
     coalesced across simultaneous inbox opens and invalidated immediately on
     remote realtime events.
2. **Static shell RAM cache + `ETag` / `304 Not Modified` (`app/server.py`)**:
   - `_static()` caches `(mtime_ns, size, bytes, etag)` in memory under
     `_STATIC_CACHE`, emits strong SHA-256 `ETag` headers, answers conditional
     `If-None-Match` (including weak `W/` prefixes and tag lists) with **`304 Not Modified`**
     (`0` body bytes, `0` DB queries), keeps `Cache-Control: no-cache` on
     `index.html` / `sw.js` and `public, max-age=3600, stale-while-revalidate=86400`
     on fingerprinted shell assets, while keeping `Cache-Control: no-store` on
     every `/api/**` response (`SEN-C601` / `SEN-C605` green).
3. **Scale-to-zero anonymous `/healthz` (`app/store.py` + `app/server.py`)**:
   - `_health_payload()` previously called `STORE.backend_status()` unconditionally,
     running `SELECT version()` against Neon Postgres on every anonymous load-balancer
     liveness poll even though anonymous `/healthz` strips DB details (`SEN-007`).
   - `STORE.backend_status(probe=privileged)` now skips `ping()` for anonymous
     callers so static page loads and liveness checks **never wake scaled-to-zero
     Neon compute**, while authenticated `owner`/`admin`/`manager` checks still
     probe Postgres and inspect `realtime` + `shared_state` health.

---

## 4. Four Production Defects Caught & Fixed While Building `v1.26.0`

1. **`channel_partner` scope & `audit` actor leak on `/api/v1/events`**:
   `_sse()` previously checked `verify_session_token` without rejecting
   `channel_partner` accounts (`403 FORBIDDEN_SCOPE`) or role-gating `audit`
   actor summaries (`SEN-008`), and did not re-check session revocation mid-stream.
2. **Stdlib `ThreadingHTTPServer.request_queue_size = 5` (`listen(5)`) SYN drops**:
   50–100 simultaneous SSE connections overflowed Python's default 5-slot listen
   backlog; `_HTTPServer` raises `request_queue_size = 256`.
3. **HTTP/1.1 unchunked SSE `Connection: keep-alive` socket wedge**:
   With `protocol_version = "HTTP/1.1"` and no `Content-Length`, returning from
   `_sse()` with `Connection: keep-alive` left server handler threads blocked in
   `handle_one_request()`; `_sse()` now sets `self.close_connection = True` and
   `Connection: close`.
4. **Anonymous `/healthz` waking scaled-to-zero Neon compute**:
   Every unauthenticated `/healthz` poll previously executed `self.backend.ping()`
   over TLS and discarded the result.

---

## 5. Reproducible Commands

```bash
npm run storage:proof      # 13/13 checks against live Neon S3 (`projectassets`)
npm run realtime:proof     # 11/11 checks: 2 nodes, 100 concurrent SSE inboxes, Redis RESP2, ETag/304
RE_DB=json pytest tests/test_storage_and_realtime.py -v
```
