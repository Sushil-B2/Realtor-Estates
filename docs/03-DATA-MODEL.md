# 03 — Data Model

33 collections. Every record carries `id` (`<prefix>_<10 hex>`) and, except `tenants`, a `tenant_id`.
Counts in this document are checked against the code by `python3 scripts/doc_facts.py --check`.
The store API refuses any read or write without a `tenant_id`, so a missing tenant is a crash, not a leak.

Prefixes: `tnt_ tenant · usr_ user · cnt_ contact · deal_ deal · prp_ property · cnv_ conversation ·
msg_ message · act_ activity · tsk_ task · agt_ agent · prp_/prop_ proposal · aut_ automation ·
soc_ social · cmp_ campaign · fnl_ funnel · pag_ page · int_ integration · whk_ webhook ·
dlv_ delivery · run_ run · rsc_ research · aud_ audit`

---

## 1. Tenancy and identity

### `tenants`
| Field | Type | Notes |
|---|---|---|
| `id`, `tenant_id`, `name`, `slug` | text | slug unique, used in public URLs (`/f/nanded-plots`) |
| `plan` | `starter\|growth\|scale\|enterprise` | gates features and quotas |
| `region`, `currency`, `created_at` | | `ap-south-1` / `INR` for India residency |
| `settings` | object | `timezone`, `quiet_hours ["21:00","09:00"]`, `call_window`, `ai_disclosure`, `dlt_series`, `data_region` |
| `features` | object | `ai_voice`, `ai_whatsapp`, `lead_scraper`, `predictive_forecast`, `ads_autopilot`, `social_scheduler`, `public_funnels`, `mcp_gateway`, `autonomous_execution`, `redline_agent` |
| `kill_switch` | bool | owner-only; halts every agent run |

### `users`
`name`, `email` (unique per tenant), `role`, `password_hash` (`scrypt$N$r$p$salt$hash`), `active`,
`mfa_enabled`, `created_at`, `last_login`, `failed_attempts`, `updated_at`.

Roles, in rank order: `viewer < agent < manager < owner`. Rank is enforced on the server; the client
only uses it to hide affordances.

### `sessions` (in-memory + revocation set)
Token = `base64url(payload).hmac_sha256`; payload = `{uid, tid, role, exp, jti}`; 8 h TTL;
`jti` added to `REVOKED` on logout, password change, or role change.

## 2. CRM

### `contacts` — the most sensitive record in the system
| Field | Protection |
|---|---|
| `phone_enc`, `email_enc` | ChaCha20 keystream + HMAC (encrypt-then-MAC, AAD = tenant) |
| `phone_hash`, `email_hash` | HMAC-SHA256 **blind index** — enables dedupe and lookup without decryption |
| `name`, `city`, `source`, `stage`, `score`, `intent` | plaintext for search/sort |
| `budget_min`, `budget_max`, `requirement` | plaintext (deals need arithmetic) |
| `language` | `mr \| hi \| en` — drives agent voice |
| `owner_id` | assigned user |
| `consent` | `{status, purpose, channel, captured_at, notice_version, evidence}` — the legal basis |
| `tags` | free-form, `csv_safe` on export |
| `value`, `created_at`, `updated_at` | |

**Consent states:** `granted · pending · withdrawn · expired`. Anything other than `granted` blocks
outbound sends at the handler — not the UI.

**Masking:** roles without `pii:read` receive `+91 98•••••210` / `a•••@g•••.com`, computed server-side
by `out_contact()`.

### `deals`
`contact_id`, `title`, `stage`, `value`, `probability`, `expected_close`, `property_id`, `owner_id`.
Stages: `new → contacted → qualified → site_visit → negotiation → booking → closed_won / closed_lost`.
Stage moves write an `activities` row and a `deal.stage_changed` event.

### `properties`
Base fields: `title`, `city`, `locality`, `status` (`available|hold|booked|sold`), `docs[]`,
`coordinates[lat,lng]`, `source`.

**Shape since v1.10.0 (`docs/22`):**

| Field | Type | Notes |
|---|---|---|
| `category` | `residential\|commercial\|industrial\|land\|hospitality` | the form selector |
| `type` | text | scoped to the category — `apartment`, `warehouse`, `na_plot`, `banquet`… (`app/taxonomy.py`) |
| `deal_kind` | `sale\|rent\|lease\|leave_license` | per-category legal set: land is sale-only |
| `area` | `{value, unit, sqft}` | normalised to sqft on write; `bigha` refused |
| `pricing` | `{basis, amount, deposit_months, lock_in_months, cam_per_sqft_month, escalation_pct}` | seven bases incl. `per_seat_month`, `per_acre` |
| `attributes` | object | category-specific: `bedrooms`/`facing` for a flat, `clear_height_m`/`floor_load_mt_sqm` for a warehouse, `survey_number`/`occupant_class` for land. A field from another category is a 422 |

`price` and `size_sqft` remain as derived conveniences (mirrors of `pricing.amount` and
`area.sqft`) for older readers; `type` values from before v1.10.0 (`plot|flat|villa|commercial|farm`)
are still accepted through `taxonomy.hydrate()` and upgraded on read.
`rera` and `docs` are compliance-bearing: an agent cannot describe a property with a claim outside its `docs`.

### `units` (v1.9.0, `docs/21`)
`property_id`, `unit_no`, `floor`, `type`, `area_sqft`, `price`, `facing`,
`status` (`available|blocked|booked|sold|withdrawn`), `block {deal_id, contact_id, until, reason}`,
`booking {deal_id, token_amount, at}`, `sold {deal_id, at}`, `history[]` (append-only).
A hold expires by *derivation at read time*, not by a job.

### `site_visits` (v1.6.0, `docs/18`)
`contact_id`, `property_id`, `agent_id`, `scheduled_at`, `duration_min`,
`status` (`scheduled|confirmed|visited|revisit|dropped|no_show`), `pickup`, `feedback`,
`outcome`, `history[]`. Conflicts are half-open on `[scheduled_at, +duration)`.

### `ingest_keys` (v1.7.0, `docs/19`)
`source`, `key_hash`, `label`, `active`, `created_by`, `last_used_at`, `hits`.
The plaintext `rink_…` key is shown once at creation, never stored.

### `cp_registrations` / `cp_payouts` (v1.8.0, `docs/20`)
Registration: `partner_user_id`, `partner_firm`, `rate_pct`, `protection_days`, `contact_id`,
`property_id`, `status` (`pending|approved|rejected|expired`), `expires_at` (derived), `decided_by`.
Payout: `registration_id`, `deal_id`, `amount`, `status` (`payable|paid`), `paid_at`, `reference`.

### `activities` / `tasks`
`type` (`call|whatsapp|sms|email|note|stage|visit|payment`), `contact_id`, `text`, `at`, `by`, `channel`.
Append-only.

## 3. Conversations
`conversations`: `contact_id`, `channel`, `unread`, `messages[]`.
`messages[]`: `id`, `dir` (`in|out`), `channel`, `text`, `at`, `ai` (bool), `status`
(`received|sent|delivered|read|failed`), `guardrail` (`allow|redact|block|quarantine`).
`ai: true` is set only for agent-authored messages, and only after the guardrail verdict is `allow`.

## 4. AI workforce

### `agents`
`name`, `role`, `department`, `model`, `temperature`, `status` (`active|paused|draft`), `channels[]`,
`scopes[]`, `tools[]`, `skills[]`, `guardrails{}`.
A `skills[]` entry: `{id, name, version, status: proposed|promoted|rolled_back, lift, origin: human_authored|learned}`.

### `proposals` (the learning gate)
`agent_id`, `title`, `observation`, `proposal`, `evidence[]`, `risk` (`low|medium|high|critical`),
`status` (`pending|approved|rejected`), `skill_diff {add, version_bump}`, `created_at`,
`decided_by`, `decided_at`, `note`.
Invariant: `status ≠ pending ⇒ decided_by, decided_at, note` are all present. There is no path from
`pending` to an applied skill that bypasses a person.

### `runs`
`type` (`agent_run|automation_run|redline_run`), `agent_id`, `status` (`ok|partial|blocked|held|error`),
`input_digest` (not the raw input), `latency_ms`, `blocked_reason`, `cost_usd`, `created_at`.

### `automations`
`name`, `trigger`, `steps[]`, `enabled`, `runs`, `last_run`, `compliance_gate[]`, `playbook`,
`paused_reason`. Steps are ordered and typed (see `docs/23`); gates cannot be reordered behind actions.

### `payments` / `invoices` (v1.12.0, `docs/24`)
| Collection | Key fields |
|---|---|
| `payments` | `kind` (`token\|installment\|commission\|other`), `status` (`created\|succeeded\|failed\|refunded`), `amount_paise`, `provider`, `payment_id`, `order_id`, `settled_by`, `deal_id`, `unit_id`, `contact_id`, `outcome` (`applied\|amount_mismatch\|unit_conflict\|unknown_subject\|unattributed\|duplicate`), `reconciled_at` |
| `invoices` | `type` (`receipt\|tax_invoice`), `number` (one running series per tenant per financial year), `taxable_paise`, `gst{cgst,sgst,igst,total}_paise`, `tds{section,rate_pct,withheld}_*`, `total_paise`, `net_receivable_paise`, `paid_paise`, `status`, `deal_id`, `unit_id` |

Invariants: one `payments` row per provider `payment_id` (a replay is a duplicate, not a second
payment); a `receipt` always carries `gst.total_paise = 0` (a booking amount is custody, not our
supply); an `invoices.number` is unique across receipts and invoices for the tenant.

## 5. Growth
| Collection | Key fields |
|---|---|
| `funnels` | `name`, `slug`, `headline`, `sub`, `cta`, `sections[{type,title,body}]`, `published`, `visits`, `submissions` |
| `pages` | website pages: `slug`, `title`, `blocks[]`, `published` |
| `campaigns` | `platform` (`google_ads\|meta_ads\|linkedin\|youtube`), `objective`, `budget_daily`, `spend`, `leads`, `cpl`, `roas`, `status` |
| `social` | `platform`, `title`, `body`, `scheduled_at`, `status` (`draft\|scheduled\|published`), `approval` |
| `research` (JEV) | `topic`, `engine`, `videos[{title,channel,views,score,intent,angle}]`, `brief`, `recommendation`, `status` |

## 6. Platform
| Collection | Key fields |
|---|---|
| `integrations` | `slug`, `name`, `category`, `status`, `scopes[]`, `last_sync`, `health` |
| `webhooks` | `url` (SSRF-allowlisted), `events[]`, `secret_hint` (never the secret), `active` |
| `deliveries` | `webhook_id`, `event`, `status`, `attempts`, `signature_algo` |
| `audit` | `seq`, `actor`, `action`, `target`, `at`, `prev_hash`, `hash` — hash-chained |

## 7. Audit chain

```
hash_n = SHA256(prev_hash_n + canonical_json({seq, actor, action, target, at}))
```

`verify_audit()` recomputes the chain from genesis. Any edit, insertion or deletion of a row breaks
every subsequent hash — cheap tamper evidence with no external dependency. Production target: the same
fields written to a partitioned Postgres table by a trigger, with a nightly WORM export to object-lock
storage so a privileged operator cannot rewrite history.

## 8. Derived and computed (not stored)

| Thing | How |
|---|---|
| Lead score | `score_lead()` — source quality, budget fit, intent, engagement, recency; explainable factors returned with the number |
| Forecast | `forecast()` — stage-weighted pipeline, historical conversion, seasonality; returns interval |
| Posture score | `redteam.control_matrix()` — weighted pass rate |
| Funnel conversion | `submissions / visits` at read time |

## 9. Retention and deletion

| Data | Retention | Basis |
|---|---|---|
| Contacts + deals | Life of tenant + 30 d grace | contract |
| Call recordings | 12 months | TRAI / dispute defence |
| Transcripts | 24 months, PII-redacted | quality + DPDP minimisation |
| Audit log | 7 years (immutable) | DPDP §8(4)-style accountability, CERT-In |
| Consent ledger | 7 years | proof of lawful basis |
| Agent runs | 18 months | cost + security forensics |
| Session tokens | 8 h + revocation set | — |

**Erasure:** a verified erasure request removes PII fields, keeps the hashed blind index as a
suppression marker so a re-import cannot silently re-add the person. Within 30 days, with a
certificate. Backup expiry within 90 days.

## 10. Encryption at rest, field by field

Key material comes from `RE_MASTER_KEY` via HKDF-SHA256 (see `docs/11-SECRETS-MANAGEMENT.md`); the
subkey names below are the derivation labels.

| Data | At rest |
|---|---|
| Phone, email | ChaCha20 + HMAC, per-record nonce, AAD = tenant, key = HKDF(root, "field_enc") |
| Password | scrypt N=32768 r=8 p=1, 16-byte salt, 64 KB maxmem (unsalted by tenant by design — a global salt per record is the salt) |
| Blind indexes | HMAC-SHA256 with HKDF(root, "blind_index"); emails lower-cased, phones digit-only |
| Webhook secret | never stored in plaintext; `secret_hint` only |
| Integration tokens | envelope-encrypted (target: KMS-backed DEK per tenant) |
| Transcripts | encrypted with tenant DEK (target), PII redacted before write |
| Audit log | plaintext + chain (it must be searchable; contains no PII by design) |

## 11. PII map (the DPDP artefact)

| Field | Category | Why stored | Consent needed | Cross-border |
|---|---|---|---|---|
| `name` | identity | addressing | yes | no (India region) |
| `phone` | identity + contact | calling/WhatsApp | **yes, per channel** | no |
| `email` | identity + contact | email | yes | no |
| `budget_*` | financial inference | qualification | yes | no |
| `requirement` | preference | matching | yes | no |
| `language` | preference | service quality | implicit | no |
| voice recording | biometric-adjacent | quality, disputes | **explicit, separate** | no |
| IP address | technical | security, rate limits | legitimate use | no |

Nothing in this table is sent to a third-party model API without a tenant-level data-processing
agreement and, under DPDP, notice naming that processing `[R13]`.

## 12. Migration notes (JSON → Postgres)

1. One table per collection; `id TEXT PRIMARY KEY`, `tenant_id UUID NOT NULL`.
2. `phone_enc` stays `BYTEA`; add `phone_hash` as `CREATE INDEX` (unique per tenant) for the blind index.
3. `messages` becomes its own table with `(conversation_id, at)` index — it is the only unbounded array.
4. `audit` gets `PARTITION BY RANGE (at)` monthly plus the same hash trigger.
5. RLS: `CREATE POLICY tenant_isolation ON <t> USING (tenant_id = current_setting('app.tid')::uuid);`
6. JSON blobs (`settings`, `features`, `guardrails`, `consent`, `skill_diff`) stay `JSONB` — they change shape faster than the code around them.
