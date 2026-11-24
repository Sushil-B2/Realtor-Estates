# 07 — Compliance Engineering

Compliance here is not a policy PDF. Each obligation is mapped to a control, a code path and a test.
If an obligation has no test, it is marked **gap** and tracked.

> Not legal advice. This is the engineering translation of the rules; counsel signs the final text.

---

## 1. India: DPDP Act 2023 (Digital Personal Data Protection)

### 1.1 Timeline we design against

| Date | Milestone | What changes for us |
|---|---|---|
| 13 Nov 2025 | Phase 1 — Data Protection Board operational | Complaint handling, breach reporting process |
| **13 Nov 2026** | **Phase 2 — Consent Manager registration, Rule 4** | Consent artefacts must be transferable and auditable; Consent Managers must be India-incorporated, ₹2 cr net worth, AES-256 at rest, 7-year records `[R15]` |
| **13 May 2027** | **Full enforcement** | All obligations live, penalties up to ₹250 cr |

Design consequence: our consent ledger is already a first-class collection with `purpose`, `channel`,
`captured_at`, `notice_version`, `evidence` — the exact fields a Consent Manager needs — so becoming or
interoperating with a Consent Manager is an integration, not a data-model migration.

### 1.2 Obligation → control → test

| DPDP obligation | Control | Where | Test |
|---|---|---|---|
| §6 Consent, free/specific/informed/unambiguous | Consent ledger per contact, per channel, per purpose | `contacts.consent`, `POST /contacts/{id}/consent` | `test_send_without_consent_is_refused`, RL-501 |
| §6(4) Purpose limitation | Purpose field checked at send time; enquiry consent ≠ consent for a different project | `api.send` handler | RL-501, RL-505 |
| §6(6) Withdrawal as easy as giving | One-click withdraw from any message; immediate effect | `POST /contacts/{id}/consent` | `test_consent_withdrawal_blocks_sends` |
| §5 Notice | Short notice + link, notice version recorded | funnel form, `notice_version` | `test_public_funnel_renders_and_escapes` |
| §8(4) Accuracy, §8(5) Erasure | Erasure job keeps only blind-index suppression marker + signed certificate | `POST /contacts/{id}/erase`, `erasures` | `test_s3_consent_withdrawal_propagation_and_erasure_certificate` |
| §8(6) Breach notification | 72 h default; our internal target is **6 h to CERT-In, 72 h to the Board** | incident runbook §7 | tabletop, quarterly |
| §9 Children | Under-18 processing requires verifiable parental consent ⇒ we **do not knowingly market property to minors**; age gate on lead forms (`422 MINOR_PROCESSING_REFUSED`) | `public_funnel_submit` | `test_s3_privacy_pack_grievance_sla_minor_gate_and_dsr` |
| §10(2) Cross-border | Data residency default `ap-south-1`; model providers must be in a notified country or use in-region endpoints | `tenant.region`, provider router | config review; provider DPA |
| §8(7) Security safeguards | Crypto, RBAC, audit chain, guardrails, REDLINE | whole system | pytest + 53 RL + 48 Sentinel |
| §8(9) Grievance redressal | Named officer + 30-day statutory SLA ledger surfaced in-app & publicly | `GET/POST/PATCH /compliance/grievances`, `/public/grievance/{slug}` | `test_s3_privacy_pack_grievance_sla_minor_gate_and_dsr` |
| §8(10) Consent manager | Rule-4 signed 7-year transferable consent artefact (`dpdp-rule4-cm-1.0`) | `GET /contacts/{id}/consent-artefact` | `proof_s3_s4_compliance_and_reliability.py` |

### 1.3 AI-specific DPDP reading

- **Notice must name AI processing and third-party model APIs** `[R13]`. The privacy notice in the funnel
  footer does exactly this in plain language.
- **"Enquiry ≠ consent"** is the most common violation in Indian real-estate lead flows: a buyer asking
  about Project A on a portal has not consented to calls about Project B. Enforced by the purpose field.
- **Inference is data.** A budget band inferred from a chat is personal data; it lives under encryption rules.
- **Erasure must reach the model pipeline.** Transcripts are redacted before storage and deleted with the contact.

## 2. Real-estate sector rules (India)

### 2.1 RERA
Every agent statement is a **representation by the promoter**. Consequences baked into the product:

| Rule | Control |
|---|---|
| Only registered projects may be advertised | A property without a `rera` id cannot be published to a funnel or a social post (blocked) |
| Claims must match sanctioned documents | Agents carry a `claim_set: rera_registered_only`; a claim outside `docs[]` is a hard failure in the output validator |
| Carpet area / plot area disclosure | Structured fields only; the model never free-texts a dimension |
| Promoter identity on every ad | Funnel footer + social template carry the registration id |
| No guarantee of returns | Guardrail rule: phrases promising appreciation/returns are blocked, not softened |

That last rule is why the seeded JEV autonomy proposal ("auto-publish 3 shorts/week") is **rejected**:
unattended content publication is an advertising liability, not a growth tactic.

### 2.2 TRAI (voice + SMS)
| Requirement | Control |
|---|---|
| DLT registration of headers/templates | `tenant.settings.dlt_series`, template approval gate before send |
| DNC scrub at dial time | `automations.compliance_gate` includes DNC; a DNC hit blocks the call |
| 09:00–21:00 calling window | `call_window` enforced server-side, returns 422 outside it |
| Consent + purpose for promotional calls | Consent ledger; promotional purpose requires explicit grant |
| Recording disclosure | First two turns; a tenant attempting to disable `ai_disclosure` gets **451** |

### 2.3 Lead sourcing and scraping
- Public business data is generally lawful; personal data needs a DPDP basis `[R14]`.
- Platform terms are contract law: **LinkedIn and direct MagicBricks scraping are blocked (451)** in
  `SCRAPE_SOURCES`, and the UI says why. `hiQ v. LinkedIn` protects public-data scraping in the US — it
  does not license breaching a contract or ignoring DPDP.
- Every captured lead carries `source` + `consent` + `evidence`; a lead without a lawful basis cannot be imported.

## 3. EU AI Act (extraterritorial)

| Question | Our position |
|---|---|
| Are we an AI system provider? | Yes — we ship agents. Full obligations from **2 Aug 2026** |
| Risk tier | Real-estate sales/nurture is **limited risk** (transparency), not high-risk. Prohibited practices (subliminal manipulation, social scoring) are absent by design |
| Transparency duty | Users are told they are talking to an AI (`ai_disclosure`), and outputs are labelled AI-assisted in the UI |
| Human oversight (Art. 14) | The learning gate and approval queue are literal implementations |
| Documentation (Art. 11) | This doc set + `05-TECH-SPEC` + model cards per agent |
| Logging (Art. 12) | Audit chain + `runs` collection with input digests |
| GPAI provider duties | Inherited from providers; we keep a provider register with model versions |

## 4. GDPR-style baseline (for NRI/GCC tenants)
Lawful basis register, DSR workflow (access/erase/portability), records of processing, DPA with every
sub-processor, SCCs for any non-India processing, 72 h breach clock, DPIA for the voice agent
(biometric-adjacent data). The DPIA template lives in `docs/` appendix of the tenant onboarding pack.

## 5. Consent engineering (the mechanism, not the promise)

```
capture ──▶ ledger entry {status,purpose,channel,notice_version,evidence,at,ip_hash}
   │
   ├── send path: consent.status == granted AND purpose matches AND channel matches
   │              else 422 CONSENT_REQUIRED (never a warning, never a soft prompt)
   ├── withdrawal: status → withdrawn, propagates to automations and queued jobs within one tick
   └── expiry: notice_version drift or TTL → status expired → re-consent request only
```

Consent is checked **in the handler that sends**, not in the UI, and not only in the agent's prompt.
BLOCK-level guardrail verdicts short-circuit before any tool call.

## 6. Data-subject rights runbook

| Request | SLA | Steps | Evidence produced |
|---|---|---|---|
| Access | 30 d | Export contact + activities + messages + transcripts | JSON + PDF certificate |
| Correction | 15 d | Update fields, re-hash blind index, audit | Audit entry |
| Erasure | 30 d | Purge PII, keep suppression hash, notify sub-processors | Erasure certificate |
| Portability | 30 d | Machine-readable bundle | JSON |
| Withdraw consent | immediate | Ledger update + queue purge | Ledger entry |
| Object to automated decision | immediate | Lead score explanation + human review path | Explanation report |

Automated scoring is **advisory**: a human can override any score, and the override is logged.

## 7. Incident response (aligned to CERT-In 6 h / DPDP 72 h)

| T | Action | Owner |
|---|---|---|
| 0 | Detect (alert, report, REDLINE finding) | on-call |
| +15 m | Triage, severity, kill switch if agent-related | on-call + security lead |
| +1 h | Contain: revoke sessions, rotate keys, disable integration, isolate tenant | security lead |
| +6 h | **CERT-In notification** with indicators | compliance |
| +24 h | Scope, root cause, affected tenants, evidence preservation | security + legal |
| +72 h | **Board / DPA notification** if personal data affected | DPO |
| +7 d | Post-incident report, remediation tickets, control added to REDLINE | eng |

Preserved for forensics: audit chain snapshot, `runs` with input digests, access logs, container image
digest. **Never** delete logs during an incident — that is itself a finding.

## 8. Records of processing (summary)

| Activity | Data | Purpose | Basis | Retention | Recipients |
|---|---|---|---|---|---|
| Lead capture | name, phone, email, budget, requirement | contact about a project | consent | life + 30 d | none |
| Voice qualification | + recording, transcript | qualify, book visit | explicit consent + disclosure | 12 mo | STT/TTS provider (in-region, DPA) |
| WhatsApp nurture | phone, message content | follow-up | consent (+ template approval) | life + 30 d | Meta (WhatsApp Cloud) |
| AI drafting | transcript/context | assistance | legitimate use + tenant DPA | 18 mo (redacted) | model provider (in-region) |
| Analytics | aggregated events | reporting | legitimate use | 24 mo | none |
| Audit | actor, action, target | accountability | legal obligation | 7 y | regulator on request |

## 9. Compliance test matrix (what proves the claims)

| Claim | Proof |
|---|---|
| Consent enforced | `test_send_without_consent_is_refused`, RL-501 |
| Quiet hours enforced | RL-502 (`422` outside window) |
| AI disclosure cannot be disabled | RL-503 + `451` on the tenant settings route |
| Unlawful sources refused | RL-505 (`451`), `test_scraper_blocks_linkedin` |
| Irreversible actions need a human | `test_irreversible_actions_need_approval`, RT agentic checks |
| Critical learning needs owner | `test_critical_proposal_requires_ack` |
| PII masked by role | `test_viewer_sees_masked_pii`, RL-305 |
| Audit tamper-evident | `test_audit_chain_detects_tampering`, RL-802 |
| CSV export cannot inject formulas | RL-408 |
| Kill switch halts agents | RL-801 |

## 10. Open compliance gaps (tracked, not hidden)

1. **No Consent Manager integration** — Phase 2 (due before 13 Nov 2026 registration deadline for our own operations).
2. **DPIA / model cards written but not externally audited.**
3. **Children's age gate is a self-declaration**, not verification.
4. **No ISO 27001 / SOC 2 certification** — design is aligned; audit is a company decision, not a code change.
5. **Cross-border model inference** depends on provider endpoints; region-pinned routing is implemented at the adapter level, contractual guarantees are pending.
6. **Grievance officer** is a config value today; a staffed function is an org dependency.
