# 45 · Phase 4 Platform Horizon & G5/G7 Vertical Polish (`v1.34.0`)

`v1.34.0` delivers all four owner-approved **Phase 4 Platform** capabilities from `docs/09-ROADMAP-AND-ECONOMICS.md` §5 and `docs/05-TECH-SPEC-2026-2031.md` §4.3 alongside the final **G5 & G7** vertical polish items from `PLANS.md` §9.

---

## 1. Phase 4.1 — On-Device Client Pre-Screening (`RE.onDevicePrescreen` + `/api/v1/prescreen/*`)

- **Browser Zero-Round-Trip Engine (`web/js/core.js`):** probes hardware acceleration (`navigator.gpu` WebGPU / `navigator.ml` WebNN, falling back to `cpu-deterministic-fp32`) and executes local PII redaction (`[REDACTED_AADHAAR]`, `[REDACTED_PAN]`, `[REDACTED_CARD]`) plus deterministic `0–100` lead intent pre-scoring (`hot` / `warm` / `nurture`) in `< 1 ms` before any network call.
- **Model Card & Leak Attestation (`GET /api/v1/prescreen/model-card`, `POST /api/v1/prescreen/verify`):** publishes canonical feature weights and verifies whether incoming text arrived already scrubbed (`client_already_redacted = true`), applying server-side fallback redaction if not.

---

## 2. Phase 4.2 — Agentic Commerce Settlement (`AP2` / `ACP` Mandates)

- **Signed Buyer Mandates (`POST / GET / DELETE /api/v1/commerce/mandates`):** mints HMAC-SHA256 signed `ap2_...` mandates (`realtor-estates.ap2-mandate.v1`) bound to `(tenant_id, deal_id, unit_id, allowed_action, max_amount_inr, currency, exp, jti)` after verifying regional RERA / Trakheesi / CEA compliance. Only `jti` is persisted.
- **Atomic Idempotent Settlement (`POST /api/v1/commerce/settle`):**
  - Refuses tampered, expired, cross-tenant, or revoked mandates (`401`) and rejects requests exceeding `max_amount_inr` (`422 MANDATE_CAP_EXCEEDED`).
  - Places race-safe unit blocks (`ivt.apply_block`) or bookings (`ivt.apply_booking` + non-GST custody receipt `RC-AP2-...`), returning a signed `acp1_...` settlement receipt with `idempotency_ref` replay safety.

---

## 3. Phase 4.3 — GCC & SEA Regional Expansion & Data-Residency Pinning

- **Jurisdiction Profiles (`GET /api/v1/regions/profiles`, `PATCH /api/v1/regions/pin`, `POST /api/v1/regions/compliance-check`):**
  - **`IN` (`ap-south-1`):** INR, MahaRERA / State RERA, DPDP Act 2023 + TRAI TCCCPR, GST 18%.
  - **`AE` (`me-central-1`):** AED, Dubai Land Department (DLD) RERA **Trakheesi permit** (`422 TRAKHEESI_PERMIT_REQUIRED` when absent), `4%` DLD transfer fee estimator, `5%` brokerage VAT, UAE PDPL.
  - **`SG` (`ap-southeast-1`):** SGD, Singapore **CEA licence** (`L.../R...`, `422 CEA_LICENCE_REQUIRED` when absent), **ABSD** calculator (`0%` SC 1st, `20%` SC 2nd, `5%` SPR 1st, `60%` Foreigner), `9%` brokerage GST, Singapore PDPA + DNC.
- **Residency Pin Enforcement:** refuses cross-region export outside the tenant's pinned `data_residency_zone` (`RESIDENCY_PIN_VIOLATION`).

---

## 4. Phase 4.4 — Crypto-Agility & Hybrid Post-Quantum Envelope (`docs/05` §4.3)

- **Versioned Self-Describing `v2:` Envelope (`app/phase4_platform.py`, `app/security.py`):** implements `v2:hybrid-sp80056c-chacha20hmac:<blob>` combining the classical HKDF subkey with a domain-separated PQ KEM shared-secret slot (`RE_PQ_KEM_SLOT_SECRET` / ML-KEM-768 OID `2.16.840.1.101.3.4.4.2` slot) via NIST SP 800-56Cr2 `HKDF-SHA512`. The full `v2:hybrid-...:` prefix is bound into AEAD AAD so header-stripping downgrade attempts fail with `INTEGRITY_FAILURE`.
- **Live Inventory & In-Place Archive Upgrade (`GET /api/v1/security/crypto-agility`, `POST /api/v1/security/crypto-agility/upgrade`):** audits `v1` vs `v2` field ciphertexts and upgrades tenant archives to `100%` `v2` with round-trip verification while `sec.unseal` reads both `v1` and `v2` seamlessly.

---

## 5. G5 & G7 Vertical Polish (`PLANS.md` §9)

- **G5 Photo Cover Pinning, Reordering & Floor-Plan Slots (`app/media.py`, `PATCH /api/v1/properties/{pid}/media-order`, `GET/POST /api/v1/properties/{pid}/floor-plans`):** deterministic `pinned_cover_id` + `photo_order` across all gallery and cover endpoints, plus floor-plan metadata and SSRF-guarded HTTPS 3D walkthrough links.
- **G7 Isolated Disposable `proof-mode` Workspaces (`GET/POST/DELETE /api/v1/demo/proof-tenants`):** creates isolated `tnt_proof_...` sandbox workspaces with encrypted seed rows and one-call teardown while strictly refusing (`403 REFUSED_NON_PROOF_TENANT`) teardown on real workspaces.

---

## Verification

```bash
RE_DB=json python3 scripts/proof_phase4_and_vertical_polish.py
RE_DB=json python3 -m pytest tests/test_phase4_and_vertical_polish.py -q
```
