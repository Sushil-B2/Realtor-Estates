# 18 — Market research: a real-estate-vertical CRM

**Question.** What does a CRM for the real-estate vertical have to do that a
horizontal CRM does not — and which of those things does this codebase already
have, which does it lack, and which are worth building first?

Researched before writing code, as asked. Sources are listed in §9; competitor
pricing is what the vendors publish or what comparison sites report, and it moves,
so treat the numbers as order-of-magnitude, not contracts.

---

## 1. The market has four distinct shapes

| Shape | Examples | What it sells | Where it breaks in real estate |
|---|---|---|---|
| **Enterprise platform** | Salesforce (+ Real Estate Cloud / partner apps) | Objects, flows, customisation, ecosystem | Powerful and abstract: pipelines, objects and permissions must be *modelled* before an agent can book a site visit. Cost and admin weight. |
| **Agency / marketing OS** | GoHighLevel ($97 / $297 / $497) | Funnels, SMS/email automation, calendars, reviews, white-label resale | No native IDX/MLS, no transaction management, no commission tracking, no compliance document store. It is the *marketing layer*, and GHL itself is positioned that way ([1](https://realestatesnapshotforghl.com/blog/gohighlevel-for-real-estate/), [5](https://oneexpand.com/gohighlevel-for-real-estate-agents/)). |
| **Self-hosted open-source stack** | `open-agency-os` (Twenty CRM + Chatwoot + Mautic + Cal.com + n8n + Authelia + Caddy + Postgres) | "A GHL alternative on your own server", composed of best-of-breed parts, with 9 automation playbooks | Composition is its strength and its tax: five services to run, and the *real-estate* semantics (unit inventory, site visit states, channel partners, RERA) are not modelled anywhere. |
| **Vertical CRM** | India: Sell.Do, Propacity, Grovia, DueDoor, AbsoluteCX, LeadSquared, Realatic · US: Follow Up Boss, kvCORE, BoomTown, LionDesk | The vocabulary of the trade: enquiry → site visit → negotiation → token → agreement → registration, plus inventory, channel partners, commission | Narrow by design; deep. Sell.Do's weakness, per a competitor's teardown, is that it "essentially stops at booking" — no payment tracking, no TDS, no buyer portal, no possession ([5](https://realatic.com/blog/best-crm-real-estate-india/)). |

**Read:** the horizontal platforms compete on *channels and automation*; the
vertical CRMs compete on *state machine and money*. A real-estate CRM that only
has a pipeline board is a GHL clone with worse marketing. The defensible core is
the deal's journey and the money attached to it.

## 2. Pricing, as published

| Product | Price | Notes |
|---|---|---|
| GoHighLevel | **$97 / $297 / $497** per month | Per account, unlimited users; SMS/voice usage on top (~$20–80/mo typical) |
| Follow Up Boss | **$69–$499**/mo (per user at the low end) | Real-estate CRM only; add IDX and email tools separately |
| kvCORE | **$499–$1,200**/mo | IDX + CRM depth for teams/brokerages |
| BoomTown | **$1,000–$1,500**/mo | Lead-gen + CRM, brokerage scale |
| LionDesk | **$39–$139**/mo | Entry-level real-estate CRM |
| Sell.Do (India) | **₹1,500–₹3,000 per user/mo**, or ₹20,000–₹60,000/mo for developer suites | Portal capture, inventory, channel partners, RERA docs |
| DueDoor / Realatic / Grovia (India) | low thousands ₹/user/mo | Brokerage-side; Grovia is the rare one with commission payouts |

Two structural observations:

1. **Per-seat vs per-account.** GHL's flat $297 for unlimited users is why teams
   defect from $69/user CRMs at 5+ seats. A vertical product priced per seat has
   to be *much* better at the vertical job, not merely cheaper.
2. **The stack being replaced costs $500–$1,000/mo** (CRM + email + SMS +
   scheduling + reviews + funnels). Any new product must account for what it
   *removes*, or it is a sixth subscription.

## 3. What the two reference repositories actually teach

**`SahibYar/open-agency-os`** — a GHL alternative as a compose file. Its value is
the *playbook catalogue* rather than the code: missed-call text-back, lead
follow-up sequence (form → instant SMS → 10-minute follow-up → nurture), 24-hour
appointment reminder, review request 2 hours after completion, and a CRM sync
from the inbox. Every one of those is a **short-latency, event-driven** action
triggered by something a lead did. It also ships SSO (Authelia) and one-command
secret generation — i.e. the unglamorous parts that decide whether a small agency
can actually run it.

**`BusyBee3333/Go-High-Level-MCP-2026-Complete`** — 927 tools over GHL's v3 API.
Read as a specification of GHL's object model: contacts, opportunities with
custom pipelines and stages, calendars/appointments, conversations, custom
fields and values, locations (sub-accounts), invoices, payments, funnels, forms,
surveys, reputation. The lesson is breadth-of-objects, and the trap is
breadth-without-depth: 927 endpoints do not include a site-visit lifecycle or a
commission ledger.

**Salesforce** contributes the *architecture* lesson rather than a feature list:
model the domain as objects with relationships (property ↔ requirement ↔ visit ↔
deal ↔ commission) and let permissions and audit be first-class. That is exactly
what this codebase already does with `re_documents` + an append-only audit chain.

## 4. What is specific to the Indian market (this deployment's context)

From the India-focused comparisons ([1](https://codingclave.com/blog/best-real-estate-crm-india-2026),
[6](https://hellogrowthcrm.com/best-crm-for-real-estate-india),
[7](https://duedoor.com/resources/real-estate-crm/best-crm-for-real-estate-agents-india.html)):

1. **Multi-portal lead capture** — 99acres, MagicBricks, Housing.com, NoBroker,
   Meta lead ads, walk-ins, channel partners — with the source preserved through
   to booking, because source decides cost-per-closure.
2. **A real site-visit pipeline**, not a stage label: *scheduled → confirmed →
   visited / revisit / dropped*, with slot, executive, and pickup/cab arrangement
   on the record; OTP check-in and GPS in the paid tools.
3. **Channel partners (CPs)** registered, credited and reported separately from
   direct buyers, so brokerage disputes are settled from data.
4. **Inventory** — units, availability, blocking, price on request, double-booking
   prevention.
5. **RERA-grade audit trail** for every customer touchpoint, and document chains:
   KYC, booking form, agreement, registration.
6. **WhatsApp Business API with template approval and prior consent** — India's
   default channel, and the one with the sharpest consent rules.
7. **Money** — token/booking payments via Razorpay, GST-compliant invoicing, and
   commission payouts. Notably: Sell.Do, Propacity, LeadSquared and Zoho have
   **no commission-tracking module**; Grovia is the exception.
8. **Time-to-first-response** is the metric that decides conversion: leads
   contacted within 5 minutes are ~21× more likely to convert (vendor-reported).

## 5. Must-have vs differentiator

| Capability | Verdict | Market evidence |
|---|---|---|
| Contact + requirement capture, dedupe | **Must** | Every CRM |
| Visual pipeline with real-estate stages | **Must** | Sell.Do, FUB, kvCORE |
| Site-visit object with lifecycle states | **Must (India)** | "Site visit + driver ✓" is used as a column in every India comparison |
| Property ↔ requirement matching | **Differentiator (brokers)** | Named as a core broker need; most CRMs leave it to the agent's memory |
| Channel-partner credit + commission ledger | **Differentiator** | Absent from Sell.Do/LeadSquared/Zoho; Grovia's headline feature |
| Document checklist gating the stage | **Differentiator** | Document automation exists in Sell.Do; *gating* is rare |
| Portal lead ingestion + SLA timer | **Must** | "Source intact to booking", 5-minute response |
| Consent + audit trail | **Must (India/DPDP)** | RERA-grade audit is explicitly sold |
| Funnels, SMS campaigns, review requests | **Parity only** | GHL/open-agency-os own this; do not compete here first |
| MLS/IDX | **N/A here** | Not how Indian resale works |

## 6. Honest gap analysis of this codebase (before this build)

Already present: multi-tenant isolation with RLS + an append-only hash-chained
audit log; contacts with requirement fields (`budget_min/max`, `intent`, `source`,
`requirement`, encrypted phone/email with searchable hashes); deals with
stage/value/probability/property link; properties with type, price, size,
locality, status, RERA number and documents; tasks; a two-way inbox with consent
and quiet-hours enforcement; analytics; three deployed-on-demand Neon Functions
(photo pipeline, compliance sweep, payments webhook).

Missing, ranked by how much it makes this a *real-estate* CRM rather than a
contact manager:

| # | Gap | Why it matters | Built in this pass? |
|---|---|---|---|
| 1 | **Site visits as records** (slot, agent, lifecycle) | The stage exists; the object does not. Agents cannot double-book or forget. | **Yes** |
| 2 | **Requirement → property matching** | 90 contacts × 12 properties is already too many to eyeball; explainable ranking is the broker's daily job. | **Yes** |
| 3 | **Stage gates + commission ledger** | Money and documents are what separate a vertical CRM from a pipeline toy. | **Yes** |
| 4 | Portal ingestion with SLA timers and round-robin assignment | `leads/import` exists; no SLA clock, no assignment rule. | Next |
| 5 | Channel-partner portal (separate login, own inventory view) | Differentiator, but a second product surface. | Next |
| 6 | Unit-level inventory with blocking on token payment | Properties are already unit-shaped; blocking needs the payment ledger to be authoritative. | Next |
| 7 | WhatsApp templates with approval + opt-in registry | Consent exists; template registry and BSP wiring do not. | Next |

## 7. Positioning this product

> **A compliance-native, self-hostable CRM for Indian brokerages: enquiry to
> registration on one auditable timeline, with the site visit, the match and the
> commission treated as first-class objects.**

Three claims that are defensible *because the code already backs them*, not
because a brochure says so:

1. **Audit is not a feature toggle.** Every mutation lands in an append-only,
   hash-chained `re_audit` whose integrity a nightly function verifies — which is
   what "RERA-grade" actually requires.
2. **Consent is enforced, not recorded.** Quiet hours, DND and consent purpose
   block outbound sends at the API layer.
3. **It runs where you want.** Postgres (Neon) + object storage + functions; no
   MLS dependency; deployable on one box, with a self-hosted stack as the
   precedent (`open-agency-os` proves the demand).

Pricing implication: at ₹1,000–1,500 per user/month it undercuts Sell.Do's
₹1,500–3,000 while offering what it does not (commission, per-stage document
gating, verifiable audit). That is a *wedge*, not a full replacement — funnels,
WhatsApp BSP and CP portals are the follow-on work.

## 8. Roadmap this research implies

- **P0 (built now):** site-visit objects with conflict detection and lifecycle;
  explainable property matching; deal stage machine with document gates;
  commission ledger; CRM summary endpoint and a working board UI.
- **P1:** portal ingestion (99acres/MagicBricks/Meta) with dedupe on phone hash,
  round-robin assignment and an SLA clock; token payments reconciled against the
  existing `re_webhook_events` table.
- **P2:** channel-partner portal; WhatsApp template registry; GST invoicing;
  unit blocking; buyer portal with construction updates (the gap Sell.Do leaves).

## 9. Sources

Fetched/read 2026-11-17/18:

- GoHighLevel real-estate guides and pricing: realestatesnapshotforghl.com, pzmeer.com, nextgenchannels.com, thestackinsiders.com, oneexpand.com, ghlscaleup.com, netpartners.marketing, highlevelautomationteam.com
- Salesforce, `github.com/salesforce`, `github.com/orgs/salesforce/repositories` (object/flow model, ecosystem)
- `github.com/SahibYar/open-agency-os` (self-hosted GHL alternative; 9 automation playbooks)
- `github.com/BusyBee3333/Go-High-Level-MCP-2026-Complete` (927 tools; GHL v3 object model)
- `github.com/MusheAbdulHakim/gohighlevel-php-sdk` (API surface, v2/v3)
- India: codingclave.com (best real-estate CRM India 2026), sell.do blog, groviaos.com, realatic.com, duedoor.com, hellogrowthcrm.com, absolutecx.com
- `github.com/GoHighLevel`, `gohighlevel.com` (platform, pricing)

Where a number is vendor-reported (e.g. the "21× more likely" statistic) it is
labelled as such above and should not be repeated as independent fact.
