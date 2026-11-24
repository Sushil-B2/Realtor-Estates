# 44 · Phase 3 Ads Autopilot with Hard Spend Caps, Phase 2 Office Macro / Archive-Bomb Security & Full Portal UX (`v1.33.0`)

`v1.33.0` completes the remaining engineering items across the approved Phases 1–3 roadmap (`docs/09-ROADMAP-AND-ECONOMICS.md` & `PLANS.md`) and finishes every interactive SPA surface for `Options A–E`.

---

## 1. Phase 3 Ads Autopilot with Hard Spend Caps & Human Budget Approval

### Workspace Spend-Cap Policy (`GET /api/v1/ads/policy`, `PATCH /api/v1/ads/policy`)
- `hard_daily_cap_inr` (default `₹50,000`): blocks campaign creation, direct edits, or budget approvals whose projected daily total across active/draft campaigns exceeds the workspace daily ceiling (`422 ADS_HARD_SPEND_CAP_EXCEEDED`).
- `hard_monthly_cap_inr` (default `₹10,00,000`): halts all active campaigns and refuses re-activation once cumulative campaign spend reaches the monthly cap (`422 ADS_MONTHLY_SPEND_CAP_EXCEEDED`).
- `max_cpl_inr` (default `₹2,500`) & `min_scale_roas` (default `3.0×`): govern automatic CPL pause vs. scale recommendation thresholds.
- `require_approval_above_pct` (default `20%`): refuses direct `PATCH /api/v1/campaigns/{cid}` jumps above threshold (`422 APPROVAL_REQUIRED`) unless routed through human approval.
- **MahaRERA Ad-Claim Gate:** inspects campaign names against `aigov.inspect_rera_advertising`, blocking guaranteed-return / assured-appreciation claims (`422 RERA_AD_CLAIM_BLOCKED`).

### Autopilot Rebalancer & Human-Gated Budget Scaling (`POST /api/v1/ads/autopilot/run`, `POST /api/v1/ads/budget-requests`)
- **Autonomous Downside Protection:** immediately pauses bleeding campaigns (`cpl > max_cpl_inr` or `0` leads after `> 2 × max_cpl_inr` spend) and enforces emergency halt when `total_spend_inr >= hard_monthly_cap_inr`.
- **Human-Gated Upside Scaling (`D8` / `D9`):** never raises `budget_daily` autonomously. High-ROAS campaigns stage a high-risk `ads_budget_increase` proposal in `proposals` (`status = "pending"`). Only when an authorised reviewer approves via `POST /api/v1/learning/{pid}/decide` with `acknowledge_risk = true` does `budget_daily` promote atomically.

---

## 2. Phase 2 OOXML Macro, External-Template & Archive-Bomb Upload Security (`POST /api/v1/properties/{pid}/documents/scan`)

Extends `app/connectors.py` `scan_document_upload` to cover modern Word (`.docx`) and Excel (`.xlsx`) document packs alongside PDF, PNG, JPEG, WebP, and WAV:
- **Magic-Byte & Container Verification:** verifies `PK\x03\x04` ZIP headers plus `[Content_Types].xml` and `word/document.xml` / `xl/workbook.xml` presence (`415 MIME_MAGIC_MISMATCH` on spoofed files; `422 MALICIOUS_FILE_BLOCKED` on legacy OLE2 `\xd0\xcf\x11\xe0` or RTF `\objdata`).
- **Office Macro & Remote Template Injection Rejection (`422 OFFICE_MACRO_BLOCKED`):** blocks `vbaProject.bin`, `vbaData.xml`, `macrosheets/`, `activex/`, `embeddings/`, `.bin` streams, and `TargetMode="External"` `.rels` remote template injection, plus XXE `<!ENTITY` declarations.
- **Archive Decompression-Bomb Guard (`422 ARCHIVE_BOMB_BLOCKED`):** enforces `<= 120` archive members, `<= 10 MB` uncompressed size, `<= 40:1` compression ratio, and zero `..` path traversal entries.
- **Expanded PDF Active-Content Rejection:** blocks `/JavaScript`, `/JS`, `/Launch`, `/OpenAction`, `/EmbeddedFiles`, `/RichMedia`, and `/XFA`.
- **Sandboxed Plain-Text Conversion (`sandboxed_preview`):** extracts clean, XML/HTML-stripped plain text (`<= 600` chars) in pure stdlib memory without invoking external binaries or script engines.

---

## 3. Complete Strict-CSP SPA Surfaces (`web/js/`)

- **Unauthenticated Public `#/buyer-portal/<token>` Self-Serve View (`web/js/core.js`):** buyers and NRI/OCI investors open `#/buyer-portal/<token>` directly without staff login to inspect unit carpet/all-in breakdown, MahaRERA verification link, official receipts, site visits, and FEMA Rule 24 status.
- **Pipeline Deal Modal (`web/js/views-ops.js`):** one-click *Mint Buyer / NRI Portal* and *Revoke Portal* actions on every deal card.
- **Integrations Console (`web/js/views-platform.js`):** *Website Form Connector* (`X-Ingest-Key` + copy-paste payload) and *Offline & Push* outbox sync inspector.
- **AI Workforce & Paid Campaigns (`web/js/views-ai.js`, `web/js/views-growth.js`):** interactive *Verify & Import Pack* inside Pack Exchange, plus *Spend Cap Policy*, *Run Autopilot*, and *+25% Approval* on `/#/ads`.

---

## Verification

```bash
RE_DB=json python3 scripts/proof_ads_autopilot_and_upload_security.py
RE_DB=json python3 -m pytest tests/test_ads_autopilot_and_upload_security.py -q
```
