# 06 — AI Workforce Specification

**Thesis:** real-estate operators do not need a chatbot. They need a small, accountable workforce of
narrow agents that answer in 90 seconds, in the buyer's language, inside legal guardrails, and that
improve only when a human signs off on the improvement.

---

## 1. The fleet

| Agent | Department | Channels | Model route | Job | Hard limit |
|---|---|---|---|---|---|
| **Meera — Voice Qualifier** | Sales | voice, WhatsApp | `gpt-realtime-2.1` / `sarvam-bulbul-v3` | Qualify, run due-diligence handoff, book site visits in mr/hi/en | Cannot quote beyond the RERA claim set; cannot discount >3% |
| **Arjun — WhatsApp Nurture** | Marketing | WhatsApp, SMS, web chat | `claude-sonnet-4.6` | Multi-touch nurture, objection handling, re-engagement | Approved templates only; frequency cap 1/24h, 3/7d |
| **Kabir — Lead Intelligence** | Growth | system | `gemini-3-flash` | Source scoring, dedupe, consent validation, enrichment | Cannot contact anyone; cannot import without consent status |
| **Naina — Ops Copilot** | Operations | system | `gpt-5.4-orion` | Deal desk, document checklists, RERA disclosure checks | Cannot change price; cannot approve its own checklist |
| **JEV — Market Research** | Marketing | system | `jev-ultrafast` | Score public video/market signal → content briefs | **Research-only**: no publish, no spend, no outbound |
| *(tenant-created)* | any | any | any allowlisted | whatever the owner defines | inherits the same gates |

## 2. Anatomy of an agent

```json
{
  "name": "Meera — Voice Qualifier",
  "role": "Outbound voice qualification & appointment setting",
  "department": "Sales",
  "model": "gpt-realtime-2.1 / sarvam-bulbul-v3",
  "channels": ["voice", "whatsapp"],
  "scopes": ["contacts:read","contacts:write","calendar:write","docs:send",
             "messages:send","templates:read","voice:outbound"],
  "tools": ["crm.lookup_contact","calendar.offer_slots","whatsapp.send_template",
            "docs.assemble_pack","voice.disclose_ai"],
  "guardrails": {
    "human_approval_for": ["bulk_message","price_concession","delete_record","refund"],
    "max_discount_pct": 3,
    "spend_cap_usd": 250,
    "pii_redaction": true,
    "fail_closed": true,
    "claim_set": "rera_registered_only"
  },
  "skills": [ { "name": "Marathi code-switch handling", "version": 4, "origin": "human_authored", "lift": 0.18 } ]
}
```

Every field is load-bearing:

- **`scopes`** are the agent's authority. A tool whose required scopes are not a subset of these is denied at call time — and `RT-012` fails the build if the definition is internally inconsistent.
- **`tools`** are capabilities, not permissions; both must agree.
- **`guardrails`** are read by the runtime, not the prompt.
- **`skills`** are versioned artefacts with provenance (`human_authored` vs `learned`) and measured lift.

## 3. Tool catalogue (MCP-style, deny-by-default)

27 tools in 5 risk classes:

| Risk | Examples | Behaviour |
|---|---|---|
| `read` | `crm.lookup_contact`, `knowledge.search`, `analytics.query` | auto, logged |
| `write` | `crm.update_stage`, `calendar.create_event`, `tasks.create` | auto, audited |
| `compliance` | `consent.validate`, `compliance.rera_check`, `voice.disclose_ai` | auto, cannot be skipped |
| `outbound` | `whatsapp.send_template`, `sms.send`, `voice.dial` | **approval unless the compliance gate passes** |
| `irreversible` | `billing.issue_refund`, `data.bulk_export`, `records.bulk_delete` | **always human approval** |

Adding a tool is a security event: it gets a scope list, a risk class, a description hash, an owner,
and a REDLINE check. Tool descriptions are treated as untrusted input at the gateway (tool poisoning).

## 4. Execution pipeline

```
input ─▶ [guardrail inspect] ──block/quarantine──▶ STOP (no output, no tools, audit + escalate)
      ─▶ [policy: kill switch, agent status, spend cap] ──fail──▶ STOP
      ─▶ [scope check per tool] ──denied──▶ logged, skipped
      ─▶ [risk routing] ──outbound/irreversible──▶ approval queue (raw operation, not a summary)
      ─▶ [model call, residency-routed, no secrets in context]
      ─▶ [output inspect + redact]
      ─▶ [audit: actor, decision, cost, latency, tool calls]
```

Latency budget for voice (target): VAD 120 ms + STT 150 ms + LLM first token 250 ms + TTS first audio 90 ms ≈ 610 ms p50. Measured p95 target ≤ 900 ms. Above ~800 ms listeners notice; near 1,500 ms they treat the conversation as broken `[R18]`.

## 5. The learning loop (the part most vendors fake)

```
   ┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌──────────────┐
   │ 1 OBSERVE   │───▶│ 2 PROPOSE    │───▶│ 3 EVALUATE    │───▶│ 4 HUMAN GATE │
   │ transcripts │    │ skill diff + │    │ shadow run on │    │ role + risk │
   │ outcomes    │    │ evidence     │    │ held-out set  │    │ ack + note  │
   └─────────────┘    └──────────────┘    └───────────────┘    └──────┬───────┘
                                                                      ▼
   ┌─────────────┐    ┌──────────────┐    ┌───────────────┐    ┌──────────────┐
   │ 7 ROLLBACK  │◀───│ 6 MONITOR    │◀───│ 5 PROMOTE     │◀───│ approve /    │
   │ pinned ver. │    │ 72h canary  │    │ version bump  │    │ reject+note  │
   └─────────────┘    └──────────────┘    └───────────────┘    └──────────────┘
```

**What is real in this build:** proposals with observation/evidence/risk/diff/checklist; risk grading
(`grade_risk`); human decisions with mandatory note; explicit risk acknowledgement for high/critical;
owner-only for critical; version bump appended to the agent's skills; immutable decision history; a
seeded rejected proposal ("auto-publish 3 YouTube shorts/week") that stays rejected for brand-safety
and RERA advertising liability.

**What is phase 2 (`v1.30.0` shipped in `docs/41`):** 12-case shadow-run evaluation harness (`POST /learning/{pid}/shadow-benchmark`), 72-hour canary traffic split with pinned skill snapshots (`POST /learning/{pid}/canary`), automatic rollback on guardrail/error regression (`POST /learning/{pid}/record-outcome`, `POST /learning/{pid}/rollback`). Still deferred to 2028+: differential-privacy pooling across tenants (opt-in only).

**Why the human gate stays:** an agent that can rewrite its own instructions can rewrite its own
guardrails. The loop is deliberately rate-limited by human attention — that is the safety property.

## 6. Channels

### 6.1 Voice

| Aspect | Specification |
|---|---|
| Stack | Cascaded STT→LLM→TTS or speech-to-speech; provider adapter (`sarvam`, `elevenlabs`, `openai`, `local`) |
| Languages | Marathi, Hindi, Indian English with code-switching mid-sentence |
| Disclosure | First two turns: AI + recording; cannot be disabled (`451` if a tenant tries) |
| Compliance gate before dial | Consent, DNC scrub, 09:00–21:00 IST, frequency cap, series routing, recording notice |
| Extraction | Budget band, intent, documents requested, preferred slot, language detected |
| Handoff | Live transfer to a human with a context summary; SMS/WhatsApp follow-up with the doc pack |
| Cost model | ~$0.04–0.08/min blended; Sarvam-class Indian providers roughly an order of magnitude cheaper than US speech-to-speech `[R17]` |
| Recording | Dual-channel, retained 12 months (TRAI), access logged, transcript redacted for PII |

### 6.2 WhatsApp / SMS
Approved templates only; opt-in enforced; 24-hour customer-service window respected; SMS on 1600-series for transactional; every send carries an idempotency key; quality-rating watch with automatic pause.

### 6.3 Web chat / forms
Funnel forms are the highest-integrity consent capture: the checkbox, notice version and IP are written to the ledger at submission.

## 7. MCP / skills / plugins

| Concept | Implementation |
|---|---|
| **MCP server** | Our gateway exposes the tool catalogue; each tool carries scopes + risk + description hash |
| **Skills** | Versioned prompt+procedure artefacts; promoted only through the human gate |
| **Plugins** | Third-party capabilities land as tools with their own scopes; no ambient authority |
| **Automations** | Deterministic step graphs (`trigger → steps[]`) with compliance gates compiled in — used where an LLM adds risk without adding value |
| **A2A (phase 3)** | Specialist agents (title search, valuation) hired over A2A with per-hop authorisation and a delegated-scope token |

## 8. JEV (research → content)

JEV runs **research-only**: it searches public video metadata, scores intent/angle/freshness/authority,
and emits a brief plus a recommendation. It cannot publish, cannot spend, and cannot message. The seeded
history contains the one autonomy request it made — auto-publishing three shorts a week — and the
owner's rejection, because a RERA advertisement is a representation by the promoter.

Output contract:

```json
{ "topic": "...", "videos": [{ "title": "...", "score": 87, "intent": "high", "angle": "due-diligence authority" }],
  "brief": "5 shorts: 7/12, NA order, encumbrance, road width, encroachment. CTA: doc-pack funnel. Tue/Thu/Sat 19:30 IST.",
  "recommendation": "Authority angles out-perform price-led content 2.4×.",
  "publish": "blocked — human approval required" }
```

## 9. Prompt architecture (production intent)

```
[SYSTEM — trusted]        role, claim set, escalation rules, refusal rules, tool contract
[POLICY — trusted]        tenant settings: language, quiet hours, approval matrix
[SKILLS — versioned]      the promoted procedures for this agent version
[CONTEXT — semi-trusted]  contact profile (PII-minimised), inventory facts, document status
[UNTRUSTED — tagged]      conversation history, documents, web results, tool output
[OUTPUT CONTRACT]         JSON schema for extraction + human-readable reply
```

Rules: no secrets in any layer (assume the window is public — LLM08:2026); untrusted blocks are
delimited and labelled; the model is told that tagged content is data, never instruction; the output
contract is validated before use; nothing from the model reaches a shell, an HTML sink or a SQL string.

## 10. Evaluation & monitoring

| Metric | Target | Instrument |
|---|---|---|
| Injection resistance | ≥ 99% on 500-case corpus | REDLINE + CI corpus run |
| Task success (gold set) | ≥ 85% | weekly eval job |
| Hallucinated claim rate | 0 | claim-set validator (hard fail) |
| Escalation recall | ≥ 90% | human-labelled sample |
| p95 turn latency | ≤ 900 ms | edge telemetry |
| Cost per qualified lead | tracked per tenant | usage meter |
| Approval queue age | < 4 h median | platform metric |
| Skill regression | < 2% drop post-promotion | canary |

## 11. Anti-patterns we refuse

1. **"Autonomous by default."** Off, owner-gated, audit-logged.
2. **Prompt-only guardrails.** If the only thing stopping a mass message is a sentence in a prompt, it is not stopped.
3. **Silent improvement.** Every skill change is attributable to a human and a timestamp.
4. **Cross-tenant learning without consent.** Tenant-private by default; pooling is a separate, opt-in, differentially-private programme.
5. **Synthetic conversations in the customer's inbox.** If it was not sent, it never appears as sent.
