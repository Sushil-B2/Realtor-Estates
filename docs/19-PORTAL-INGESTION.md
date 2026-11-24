# 19 — Portal ingestion, de-duplication and speed-to-lead

Companion to `docs/18` §4 (India's must-haves) and §8 P1. This is the operational
doc: how a 99acres / MagicBricks / Housing / NoBroker / Meta / Google lead gets
into the CRM, what happens when the same buyer enquires twice, and how the
first-response clock works.

---

## 1. Why this exists

Two numbers from the research drove it:

* **Source must survive to booking.** Cost-per-closure is computed per portal.
  A CRM that de-duplicates by dropping the second enquiry destroys the only
  evidence that the second portal produced a buyer — and the marketing budget
  moves to the wrong channel.
* **Speed-to-lead decides conversion.** Five minutes is the number vendors keep
  citing. A lead with no clock is a lead nobody owns.

So: every inbound lead is normalised, attributed, assigned, and time-boxed
*before* a human sees it.

## 2. The endpoint

```
POST /api/v1/ingest/{source}
X-Ingest-Key: rink_…
Content-Type: application/json
```

`{source}` is one of `99acres`, `magicbricks`, `housing`, `nobroker`, `meta`,
`google`, `website` (`GET /api/v1/leads/sources` lists them with lead counts).
The body is whatever the portal sends — the adapters in `app/ingest.py` map its
field names to ours, so **no transformation layer is needed in front of us**.

Replies:

| Status | Meaning |
|---|---|
| 200 `{ok, duplicate:false, contact_id, assigned_to, sla}` | new lead, owned, clock running |
| 200 `{ok, duplicate:true, contact_id, merged_fields, sources}` | known buyer, merged and re-attributed |
| 401 `INGEST_UNAUTHORIZED` | missing/unknown/revoked key, or tenant kill-switch |
| 422 `MISSING_PHONE` / `MISSING_NAME` / `UNKNOWN_SOURCE` | unreadable payload — never a blank contact |

A portal that retries five times gets one contact and five source entries at most
(identical `(source, external_id, campaign)` is not appended twice).

## 3. Keys

* Minted by an owner/admin: `POST /api/v1/tenant/ingest-keys {label, source}`.
* The token (`rink_…`, 37 chars) is returned **once**. Only an HMAC blind index
  of it is stored, so the plaintext cannot be recovered from the database, from
  the audit log, or from `GET /tenant/ingest-keys` (which returns `prefix` only).
* Revoke: `DELETE /api/v1/tenant/ingest-keys/{id}` → subsequent calls 401.
* Rotation = mint new, reconfigure the portal, revoke old. There is no "edit".
* Lookup is a constant-time comparison over active keys, and a bad key and a
  dead tenant return the same 401 so tenants cannot be enumerated.

## 4. Field mapping, and the two parsers that matter

Adapters are declarative alias lists matched case-insensitively after flattening
(Meta's `field_data[]` and Google's `user_column_data[]` are unwrapped).

**Phones.** `normalize_phone()` strips formatting, drops a leading `0`/`91`, and
accepts only a 10-digit Indian mobile starting 6–9. Anything else returns empty —
the lead is refused with `MISSING_PHONE` rather than stored with a number that
can neither be dialled nor de-duplicated.

**Budgets.** `parse_budget()` reads `₹30,00,000`, `30 Lakh`, `30L`, `1.2 Cr`,
`30 Lakh - 50 Lakh`, `40-60 Lakh`, `Under 25 lakh`, `Above 2 crore` and plain
digits. Commas are group separators in Indian formatting, never decimals — a
first version turned `30,00,000` into ₹3,000 and the test suite now pins it.
Unreadable text returns an empty range and a warning, never a guess.

## 5. De-duplication

Keyed on `phone_hash` (an HMAC blind index over the canonical number) inside the
tenant. On a repeat enquiry:

* **blanks are filled** — email, city, locality, requirement, property type,
  budget, campaign. An agent's edited requirement is never overwritten by a
  portal retry.
* **the source is appended** to `sources[]`, with campaign, external id and
  timestamp. `sources[0]` stays the first touch (attribution); the rest is
  history, capped at 20 entries.
* the contact is tagged `repeat-enquiry`, and `last_activity_at` moves to now.

## 6. Assignment

`assign_owner()` picks the active `agent`/`manager` with the **fewest open
deals**, then the fewest contacts, then lowest id. Load-based rather than a
rotating pointer: an agent who was away for a week should not still be draining
a backlog because a pointer forgot about it. Ties break on id so two servers
assign identically.

## 7. The clock

Every contact carries `sla: {seconds, due_at, source}`; the window comes from the
source (portals 300 s, website 600 s, walk-in 60 s, referral 900 s, CSV 3600 s)
unless the tenant sets `settings.sla_seconds` (30–86 400, clamped).

The clock stops on the **first outbound message** (`POST /inbox/{id}/send`) or on
a logged call (`POST /leads/{id}/respond`) — the events that prove a human
reached the buyer. `GET /api/v1/leads/sla` splits leads into `open` (still
catchable, seconds remaining), `breached` (overdue, minutes over) and `met` /
`met_late` with the average response time. Because `due_at` is stored on the
contact, changing the policy later does not rewrite history.

## 8. Consent basis — stated plainly

A portal enquiry is a form the buyer submitted asking to be contacted, so the
ingest route records consent as **granted** with `purpose: "portal enquiry"`,
`channel: <source>`, `notice_version: "portal-tos-v1"` and the portal submission
id as `evidence`. That is a *different* basis from our own notice
(`v3.1`), which is why the two versions are distinguishable on the record. Quiet
hours, DND and the output guardrail still apply to every send — the consent basis
is not a licence to message at 23:00.

## 9. What is deliberately not here

* **No polling of portal APIs.** We are a receiver; pulling would need partner
  credentials we do not have and would duplicate the portal's own retry logic.
* **No WhatsApp BSP wiring.** The inbox is WhatsApp-shaped but the template
  registry and provider are P2 (docs/18 §6 #7).
* **No lead scoring at ingest.** Score is 0 and `intent` is `unknown`; the
  existing `re-score-v4` job scores later, so a portal cannot inflate a lead's
  priority by guessing at our field names.

## 10. Verifying it

```
python3 scripts/proof_portal_ingest.py --base http://127.0.0.1:5433
```

Mints a key, posts a messy ₹-formatted 99acres lead, re-sends the same buyer from
MagicBricks, watches the clock start and stop, revokes the key, and checks the
refusals and the audit entries. `tests/test_portal_ingest.py` covers the same
ground plus the parsers (41 tests).
