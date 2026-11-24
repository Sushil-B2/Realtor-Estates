# 41 · Phase 2 AI Governance, Dual-LLM Quarantined Intake, Canary Auto-Rollback & Observability (`v1.30.0`)

`v1.30.0` completes the Phase 1/2 AI safety, learning-promotion, RERA growth,
and SRE observability workstreams (`PLANS.md` **W4**, **W5**, **W6**, **W10**,
**W13** · `docs/06` §5 · `docs/09` §5).

---

## 1. W4 — Dual-LLM Quarantined Document Intake (`POST /api/v1/agents/intake-document`)

Untrusted broker sheets, buyer emails, and due-diligence documents never reach a
tool-bearing planner directly:

1. **Quarantined Reader (`quarantined_reader_extract`):** Runs with zero tools
   and zero tenant secrets. Inspects untrusted prose via `sec.inspect_text` +
   indirect-injection rules, quarantining hostile instructions
   (`override_system_prompt`, `data_exfiltration`, `bypass_guardrail`,
   `unauthorized_payout`) and emitting **only** typed primitives (`project_name`,
   `rera_id`, `survey_no`, `carpet_area_sqft`, `quoted_price_inr`,
   `discount_requested_pct`, `encumbrance_flags`).
2. **Privileged Planner (`privileged_planner_decide`):** Never receives raw
   untrusted prose (`raw_text_exposed_to_planner: false`). Enforces RERA
   registration requirements, creates due-diligence checklist tasks, and parks
   any concession above `max_discount_pct` (`<= 3%`) in the human approval queue.

---

## 2. W6 — Shadow-Run Eval Harness, 72h Canary Split & Automatic Rollback

- **Shadow-Run Harness (`POST /api/v1/learning/{pid}/shadow-benchmark`):** Evaluates
  candidate skills against a 12-case Marathi/Hindi/English + RERA + prompt-injection
  held-out benchmark, blocking promotion (`422 SHADOW_EVAL_REGRESSION`) if any
  RERA appreciation claim, `> 3%` discount, or gate-bypass regression appears.
- **Canary Rollout & Pinning (`POST /api/v1/learning/{pid}/canary`):** Pins a
  deep copy of the agent's pre-promotion `skills` snapshot and routes
  deterministic canary traffic (`SHA-256(subject) % 100 < canary_pct`).
- **Automatic Rollback & Auto-Graduation (`POST /api/v1/learning/{pid}/record-outcome`,
  `POST /api/v1/learning/{pid}/rollback`):** Immediately restores the pinned
  skill snapshot (`status = "rolled_back"`) on any guardrail block or error-rate
  breach (`> 20%` over `>= 3` runs), and graduates clean canaries to `promoted`
  after 10 clean runs.

---

## 3. W5 & W10 — Guardrail Benchmark & RERA Social Publish Gate

- **`POST /api/v1/security/guardrail-benchmark`:** Runs a 14-case adversarial +
  multilingual benign corpus, proving **`100%` adversarial recall (`1.0`)** and
  **`0%` benign false-positive rate (`0.0`)** across Marathi, Hindi, and English.
- **`POST /api/v1/social/{sid}/publish`:** Enforces `docs/07` §2.1 — refuses
  publishing posts linked to properties without a RERA ID or containing
  guaranteed-return claims (`422 RERA_PUBLISH_BLOCKED`).

---

## 4. W13 — Structured Trace Spans, Route Histograms & SRE Alerts (`app/observability.py`)

- Every API response carries an `X-Trace-Id` (`trc_...`) correlation header and
  records a bounded span in `TelemetryRegistry` (`p50`/`p95`/`p99` ms per route,
  `4xx`/`5xx` counters, zero PII/secrets).
- **`GET /api/v1/observability/metrics`** (JSON + `?format=prometheus`
  OpenMetrics exposition) and **`GET /api/v1/observability/alerts`** (live SRE
  alerts across 5xx rate, DLQ runs, overdue DPDP 30-day grievances, and the
  `docs/09` §4 Five Cost Drivers).

---

## 5. Verification (`scripts/proof_ai_governance_and_observability.py`)

- **`7/7 PASS`** end-to-end.
