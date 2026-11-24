# 15 — Listing-photo pipeline as a Neon Function

**Status:** built and proved locally against the live bucket and the live branch
database. `neon deploy` is the one remaining step and it needs a project-scoped
API key (see §7).

---

## 1. What it does

An agent uploads a listing photo to object storage:

```
PUT  projectassets/properties/prp_f24851054c/kitchen.jpg
```

Neon's storage trigger fires, calls the function, and the function:

1. authenticates the delivery with `parseTriggerDelivery`;
2. parses the key and refuses anything that is not a listing photo;
3. downloads the original and sniffs its **real** format (the extension is a
   claim, not evidence) and rejects decompression bombs by pixel count;
4. renders three WebP variants — `thumb` 320, `card` 960, `hero` 1920
   (never upscaling, honouring EXIF orientation);
5. writes them to `derived/properties/<id>/<stem>-<variant>.webp` in the same
   bucket;
6. upserts one row per variant into `re_property_media`;
7. returns a JSON summary: dimensions, bytes, checksum per variant.

The app reads the result through `GET /api/v1/properties/{id}/media`, which
returns presigned URLs (the bucket is private).

```
 upload ──▶ projectassets/properties/…
                 │
                 │ storage_object_created   (trigger `on-property-photo`)
                 ▼
        Neon Function `property-photos`  ──▶ sharp ──▶ 3 WebP variants
                 │                                      │
                 │ upsert                               │ PUT derived/…
                 ▼                                      ▼
        re_property_media  ◀── read ── app API ── presign ──▶ browser
```

## 2. Why this shape

- **The function owns media writes; the app owns documents.** `re_documents` is
  loaded into an in-memory working set at boot and assumes a single writer
  (`docs/12`). The pipeline therefore writes its own table and the app only
  reads it — a second write path into `re_documents` would silently desynchronise
  the running process.
- **Variants go to a different prefix.** A write back into `properties/` would
  re-trigger the same trigger on the function's own output. The prefix is
  `derived/`, and `isDerivedKey()` is the second line of defence if the trigger
  prefix is ever widened.
- **Idempotent by construction.** `re_property_media` is unique on
  `(source_key, variant)` and the write is an upsert, so a redelivered trigger
  updates rows instead of duplicating them. Trigger delivery is at-least-once;
  the code assumes that rather than hoping.
- **No upscaling, no trust.** A 400 px phone photo must not become a 1920 px
  "hero" (cost, and it looks worse); a 200 MP decompression bomb must not be
  decoded (memory).

## 3. Authentication and the trust boundary

- The trigger route **cannot** require a JWT or `X-Secret`: Neon sends neither.
  `parseTriggerDelivery` is the documented check — the platform strips
  client-supplied `x-neon-*` headers, so the presence of
  `x-neon-trigger-invocation-id` matching `body.invocation_id` is the proof the
  call came from the trigger path.
- Consequently the deployed function URL is public and reachable. What an
  attacker could do with a forged delivery is bounded on purpose: re-process an
  object **that already exists in the bucket**, writing the same derived keys and
  upserting the same rows. `HeadObject` runs before any download, sizes and
  pixels are capped, and nothing is written outside `derived/…` and
  `re_property_media`. There is no path from a forged delivery to a document
  write, a delete, or an unbounded render.
- Secrets (`NEON_STORAGE_*`, `DATABASE_URL`) come from the environment, live in
  `.env.local` (0600, git-ignored) and are passed with `neon deploy --env`.
  Nothing is logged: the error path returns a message summary, never a credential.

## 4. Files

| Path | What it is |
|---|---|
| `functions/property-photos.ts` | the function (module-scope `pg` pool `max:5` + `attachDatabasePool`) |
| `neon.ts` | declares the function, the `projectassets` bucket and the `on-property-photo` trigger |
| `db/005_property_media.sql` | the media table + `grant select` for `re_app` (function writes, app reads) |
| `app/db.py` → `PgBackend.property_media()` | tenant-scoped read of the variants |
| `app/api.py` → `GET /api/v1/properties/{id}/media` | the read route, with presigned URLs |
| `functions/dev-serve.mjs` | local dev server (`npm run photos:dev`) — `neon dev` replacement where the CLI cannot sign in |
| `scripts/proof-photo-pipeline.mjs` | end-to-end proof (`npm run photos:proof`) |
| `tests/test_property_media.py` | auth, tenant scoping, 503-without-Postgres, SQL tenant filter |

## 5. Two defects this work uncovered

1. **The bucket was not `assets`.** Every env file said
   `NEON_STORAGE_BUCKET=assets`; the bucket that exists on this branch is
   **`projectassets`**. A presign *signs* any key without contacting storage, so
   the earlier "presign verified" check was a false comfort — the request it
   produced would have been `404 NoSuchBucket`. Fixed in `.env`, `.env.local`
   and `neon.ts`; the proof now performs a real `PUT` and a real `GET`.
2. **`check_secrets --history` failed on its own fixtures** (7 critical findings:
   `AKIAIOSFODNN7EXAMPLE`, `ghp_0123…`, `xoxb-…`, `sk-live-…`, a fake JWT — all
   committed test data). A gate that cries wolf on fixtures trains people to
   ignore it. The history scan now ignores literal tokens that are still present
   in an allowlisted fixture path **at HEAD**, and still flags anything else — a
   real secret that was committed and later deleted is *not* excused, which is
   the case the check exists for.

## 6. Local proof

```bash
npm run photos:dev       # function on 127.0.0.1:5434 (env from .env / .env.local)
npm run photos:proof     # real PUT → trigger delivery → variants → rows → guard tests
```

Result of the last run:

```
» 3. deliver the storage trigger to the function
» trigger delivery #1 → HTTP 200
  photo 3200×2133 JPEG, 142 KB
  thumb  320×213   3 KB  derived/properties/prp_f24851054c/proof-…-thumb.webp
  card   960×640  12 KB  derived/properties/prp_f24851054c/proof-…-card.webp
  hero  1920×1280 32 KB  derived/properties/prp_f24851054c/proof-…-hero.webp
  ✓ 3 variants rendered in 2173 ms
» 4. rows in Postgres (re_property_media)   ✓ 3 rows, tenant resolved from the property document
» 5. idempotency     ✓ still 3 rows after redelivery — no duplicates
» 6. loop guard      ✓ derived object skipped — variants can never re-trigger
» 7. forged delivery  ✓ 401 without the trigger header
PROOF PASSED
```

And through the app, with a presigned URL that returns the actual image:

```
GET /api/v1/properties/prp_f24851054c/media   → 200
thumb 320×213 / card 960×640 / hero 1920×1280   presigned GET → 200 image/webp, byte-exact
```

## 7. What is left: deploy

`neon deploy` needs an authenticated CLI. `npx -y neon@latest login` cannot be
completed in this environment (the OAuth callback is
`http://127.0.0.1:<random>/callback` inside the sandbox, which no browser can
reach), so a project-scoped API key is required:

**Easiest path: the app writes the key for you.** Open `/keysetup.html` on the
running app (e.g. `https://<preview-host>/keysetup.html`), sign in as the owner,
paste the key, save. It lands in `.secrets/neon-api-key` (0600, git-ignored, *not*
in the runtime env) — `docs/17-SECRET-HANDLING.md` explains why, and what that
does and does not protect. That page is the only place the web tier writes a
credential: owner-only, one named key, strict character allowlist (no newlines →
no injecting other variables), atomic replace, mode forced to 0600, and the value
is never echoed, logged, or stored in an audit record — only a SHA-256
fingerprint. Everything below is the manual equivalent.

```bash
# 1. the key, into the CLI-credential file only (never echoed, never in argv)
mkdir -p .secrets && printf 'NEON_API_KEY=%s\n' '<key>' > .secrets/neon-api-key && chmod 600 .secrets/neon-api-key

# 2. link + plan (read-only: shows what would change)
scripts/neon-cli.sh link --project-id broad-sound-07229115 --branch production

# 3. deploy, then confirm the functions and triggers exist remotely
scripts/neon-cli.sh deploy --env .env.local
scripts/neon-cli.sh triggers list --output json

# 4. burn the credential once the work lands (overwrite + unlink)
NEON_SHRED=1 scripts/neon-cli.sh triggers list --output json
```

If the CLI reports `function triggers not available for this project`, deploy the
function first, then create the trigger with `neon triggers create
--function-slug property-photos --bucket projectassets --prefix properties/`.

### 7a. The one-command path (staged, v1.10.1)

```bash
scripts/deploy_functions.sh                  # preflight → link → config plan → deploy
scripts/deploy_functions.sh --proof          # … then prove the deployed URLs
```

`scripts/deploy_functions.sh` refuses to start without `.secrets/neon-api-key` (or an exported
`NEON_API_KEY`) and says exactly how to supply one — it is the same script whether the key arrives
through `/keysetup.html` or CI. `scripts/proof_deployed_functions.py` is the closure for gap **G3** in
`PLANS.md` §9: it proves each function's security surface against a real URL — an unsigned delivery is
refused, a signed one is accepted once, a replay is deduped. A URL that is not supplied is reported
`SKIP` and the script exits **2**, because "not proven" must never render as "proven".

State as of `v1.25.0`: **deployed and proved live on Neon Cloud (`ap-southeast-1`, `nodejs24`, `2048 MiB`, gap G3 closed — `docs/36-NEON-CLOUD-PROOFS.md`):**
- `propertyphotos`: `https://br-patient-fire-aouhmkxr-propertyphotos.compute.c-2.ap-southeast-1.aws.neon.tech/` (`trigger-e04867e9-6d5d-400a-975d-9d8a8d07ee77`, `on-property-photo` on `projectassets` prefix `properties/`)
- `compliancesweep`: `https://br-patient-fire-aouhmkxr-compliancesweep.compute.c-2.ap-southeast-1.aws.neon.tech/` (`trigger-599200bd-7afb-4fac-b584-03b95ba9b82b`, `nightly-compliance` `0 2 * * *`)
- `paymentswebhook`: `https://br-patient-fire-aouhmkxr-paymentswebhook.compute.c-2.ap-southeast-1.aws.neon.tech/` (`configured: true`)
- Verified end-to-end by `scripts/proof_deployed_functions.py --fire-sweep` (**12/12 PASS**).

## 8. Deliberate non-goals

- **No image field on `properties`.** Adding `images[]` to the property document
  means a second writer for `re_documents`; the media table is the right home.
  When the UI needs a thumbnail it calls the media route.
- **No deletes.** Removing a photo from the bucket leaves rows behind. A
  `storage_object_deleted` trigger is the correct fix and is a separate decision
  (soft-delete vs hard-delete, retention).
- **No AI tagging / alt-text.** Tempting (the AI Gateway is configured) but it
  is a paid-plan surface and a separate feature, not part of "make the upload
  useful".
