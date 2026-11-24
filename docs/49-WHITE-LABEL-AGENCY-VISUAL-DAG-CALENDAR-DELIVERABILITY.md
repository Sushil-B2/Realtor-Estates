# 49 — White-Label Agency/Builder SaaS Mode, Visual Node-DAG Builder, 2-Way Calendar & Deliverability Suite (`v1.38.0`)

Closes Competitive Benchmark Gaps #2, #3, and #4 against GoHighLevel Agency
SaaS Mode, GoHighLevel Visual Workflow & Funnel Builders, and GoHighLevel
Calendars / Email Deliverability / Reputation Management.

## Capabilities & Security Invariants

1. **White-Label Builder / Channel-Partner Agency SaaS Mode (`app/agency_growth_suite.py`):**
   - `GET|POST /api/v1/agency/whitelabel` & `POST /api/v1/agency/whitelabel/verify-dns`: validates `#RRGGBB` brand hex colors (blocking CSS injection) and requires cryptographic HMAC DNS TXT ownership proof (`_realtor-verify.<domain>`) before activating custom domains.
   - `GET|POST /api/v1/agency/subaccounts`, `POST /api/v1/agency/snapshots/apply`, `GET /api/v1/agency/rebilling`: provisions isolated child workspaces with per-tenant HKDF DEKs, clones **PII-free** funnel/workflow blueprints (`parent_agency_tenant_id` verified), and rolls up wholesale vs retail GST 18% rebilling margins.

2. **Cycle-Safe Visual Node-DAG Workflow Compiler & Composable Funnel Blocks:**
   - `POST /api/v1/workflows/dag/validate` & `POST /api/v1/workflows/{wid}/dag`: Kahn topological sort rejects cycles (`DAG_CYCLE_DETECTED`), bounds depth (`<= 20`), inspects node templates via AI guardrails, and validates webhook node URLs via `sec.validate_egress_url`.
   - `GET /api/v1/funnels/block-catalog` & `POST /api/v1/funnels/{fid}/blocks`: allowlisted real-estate conversion/trust blocks rendered into `/f/<slug>` with 100% HTML escaping.

3. **2-Way RFC 5545 `iCal` Calendar, `SPF/DKIM/DMARC` Doctor & Ethical Reputation Loop:**
   - `POST /api/v1/calendar/availability`, `GET /api/v1/calendar/slots`, `POST /api/v1/calendar/book`, `POST /api/v1/calendar/ical/import`, `GET /api/v1/calendar/ical/export`: atomic `claim_once` anti-double-booking + RFC 5545 §3.3.11 newline/VALARM injection escaping.
   - `POST /api/v1/email/deliverability/check`: detects `+all` open relays, multiple SPF records, short DKIM keys, and `p=none` DMARC policies.
   - `POST /api/v1/reputation/request`, `POST /api/v1/reputation/respond`, `GET /api/v1/reputation/summary`: enforces DPDP consent, escalates `1–3★` detractors to urgent recovery tasks, and never gates public review links.
