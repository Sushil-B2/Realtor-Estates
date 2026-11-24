# 46 — Government, Revenue (`7/12`), Statutory Cost-Sheet, IGR `e-Registration`, Loan/CLP, `E-Ferfar` Mutation & Marketing Trust Pack (`v1.35.0`)

**Status:** Shipped in `v1.35.0` · **Module:** `app/govt_sales_cycle.py` · **Proof:** `scripts/proof_govt_sales_cycle.py` (`6/6 PASS`) · **Tests:** `tests/test_govt_sales_cycle.py`

---

## 1. Why this exists

Indian real-estate sales—especially across Maharashtra (Nanded, Chhatrapati Sambhajinagar, Pune, MMR, Nagpur)—do not end when a buyer pays a token or signs a draft agreement. Both **employees** (Legal/Inventory, Sales, CRM/Post-Sales, Finance, Marketing) and **clients** (domestic & NRI homebuyers) spend 60–70% of their operational effort navigating six government and banking workflows:

1. **Revenue & Title Clearance (`Mahabhulekh` / Collectorate):** Verifying a fresh (<30-day) `7/12` (*Satbara*) extract, `8A` holding (*Khata*), Urban Property Card (*Malmatta Patrak*), Occupant Class (`Class I` free transfer vs `Class II` / *Bhogwatdar Varg-2* requiring Collector permission + *Nazrana*), unreleased *Itar Hakk / Boja* encumbrances, Section 44 Collector NA conversion order, `Mojni` demarcation, and a 30-year advocate Title Search.
2. **Statutory Buyer Costing (`IGR Maharashtra` + Income Tax Act):** Computing Stamp Duty on `max(Agreement Value, Ready Reckoner / ASR)` across Metro (`7%`), Tier-2 Municipal Corporations like Nanded NWCMC (`6%`), and Municipal Councils / Rural (`5%`), applying the `1%` Maharashtra Women Homebuyer Concession (with its 15-year female-only resale rule), capping Registration Fee at `₹30,000` above `₹30L`, applying GST (`0%` ready/NA plot, `1%` affordable, `5%` standard residential, `12%` commercial), and deducting `1%` Buyer TDS under Section `194-IA` (`Form 26QB` + `Form 16B`) when consideration is `>= ₹50,00,000`.
3. **Deed Execution (`IGR e-Registration` vs `iSarita` SRO):** Running either Builder-Office `e-Registration` (UIDAI biometric thumb + Aadhaar OTP) or physical `iSarita` Sub-Registrar Office (SRO) execution (2 witnesses + token slot), verifying the `GRAS` e-SBTR challan and `Form 26QB`, and recording the registered document number + **`Index-II`**.
4. **Project Bank APF, Buyer Home-Loan & RERA CLP Demands:** Tracking Approved Project Finance (`APF`) codes (`SBI`, `HDFC`, `Bank of Maharashtra`, etc.), tracking buyer loan sanction/MODT/disbursement, and enforcing **RERA Section 13 + Architect Certificate (`Form 1`)** before raising construction-linked payment (`CLP`) milestone demands.
5. **Post-Registration `E-Ferfar` Talathi Mutation & Handover:** Tracking Talathi `E-Ferfar` mutation entry, the 15-day statutory notice countdown, Circle Officer (*Mandal Adhikari*) certification, updated `7/12` / Property Card issuance, Municipal `Namuna-8` tax assessment transfer, MSEB *Mahavitaran* meter transfer, snag clearance, and Possession Letter handover.
6. **Marketing Govt.-Verified Trust Badges & All-In Cost Pack:** Surfacing *only* backed compliance badges (`MahaRERA`, `Collector NA`, `Fresh 7/12 — Zero Boja`, `30-Year Clear Title`, `Bank APF Approved`) plus a transparent All-In Cost Sheet on public funnels (`/f/<slug>`) and WhatsApp brochure copy.

---

## 2. Architecture & Domain Rules (`Tracks G-1`..`G-6`)

### Track G-1 — Land & Revenue Vault (`upsert_revenue_vault`, `evaluate_revenue_clearance`)
- **30-Day `7/12` Freshness Clock:** If `satbara_7_12.issued_at` is older than `30` days, `SATBARA_STALE_OVER_30D` warns staff and withholds `can_execute_agreement` unless an Urban Property Card (`property_card.verified`) governs the parcel.
- **Occupant Class-II Hard Gate (`CLASS_2_COLLECTOR_PERMISSION_REQUIRED`):** When `occupant_class` is `class_2` or `govt_lessee` without `collector_transfer_permission_ref` + `nazrana_paid`, `can_accept_token` is `False` and `POST /api/v1/deals/{did}/advance` to `token` returns `409 CLASS_2_COLLECTOR_PERMISSION_REQUIRED`.
- **Active *Boja* Gate (`UNRELEASED_BOJA_ENCUMBRANCE`):** When `itar_hakk_boja` lists encumbrances without `bank_noc_obtained`, `can_execute_agreement` is `False` and advancing to `agreement` / `registration` returns `409 UNRELEASED_BOJA_ENCUMBRANCE`.

### Track G-2 — Statutory Cost-Sheet Engine (`compute_statutory_cost_sheet`)
- **ASR Floor:** `assessable_market_value_inr = max(agreement_value_inr, asr_ready_reckoner_inr)` with automatic Income Tax `§43CA` / `§56(2)(x)` advisory when `ASR > Agreement`.
- **Maharashtra Stamp Duty Zones & Women Concession:**
  - `municipal_corp_metro`: `7.0%` · `municipal_corp_tier2` (Nanded NWCMC): `6.0%` · `municipal_council`: `5.0%` · `gram_panchayat_rural`: `5.0%`.
  - `buyer_gender_scheme = "sole_or_joint_female"` on non-commercial units deducts `1.0%` and emits the 15-year resale restriction advisory.
- **Registration & `§194-IA` TDS:** Registration is `1%` capped at `₹30,000` above `₹30,00,000`. Consideration `>= ₹50,00,000` triggers `1%` buyer TDS (`Form 26QB` + `Form 16B`), computing `net_payable_to_seller_inr = agreement_value_inr - tds_amount_inr`.

### Track G-3 — IGR `e-Registration` & `iSarita` SRO Desk (`upsert_registration_desk`, `complete_registration_index2`)
- Refuses scheduling if Track G-1 `can_execute_agreement` is `False` (`409 REVENUE_CLEARANCE_BLOCKED`).
- Verifies `GRAS` challan number + full Stamp Duty & Registration payment, `Form 26QB` acknowledgement (when `>= ₹50L`), and mode-specific execution gates (`TWO_WITNESSES_REQUIRED` for `isarita_sro_physical`; `AADHAAR_BIOMETRIC_DEVICE_REQUIRED` for `e_registration_builder_office`).
- Completing `Index-II` (`POST /api/v1/deals/{did}/registration/index-ii`) records `registered_doc_no` + `index_ii_ref`, syncs `"Stamp duty receipt"`, `"Registration appointment"`, and `"Registered sale deed"` (`verified`) into `deal.documents` (satisfying `app/vertical.py` stage gates), and auto-seeds Track G-5 handover tracking.

### Track G-4 — Bank APF, Buyer Home Loan & RERA CLP (`add_project_apf`, `upsert_home_loan`, `init_rera_clp_schedule`, `raise_clp_milestone_demand`)
- Stores project-level `bank_apf` approvals (`SBI`, `HDFC`, etc.) and deal-level `home_loan` progression (`login` → `sanctioned` → `legal_technical_clear` → `tripartite_noc_signed` → `disbursed`).
- Generates 7-stage RERA building (`10/20/15/25/15/10/5%`) or 3-stage NA plot (`20/30/50%`) schedules. Construction stages (`plinth`, `slabs`, `brickwork`, `mep_lifts`, `oc_possession`) hard-refuse demand generation unless `architect_cert_ref` is supplied (`409 ARCHITECT_CERTIFICATE_REQUIRED`).

### Track G-5 — `E-Ferfar` Talathi Mutation & Handover (`upsert_post_reg_handover`)
- Computes `ferfar_notice_clears_at = ferfar_applied_at + 15 days`, tracks Circle Officer certification, updated `7/12` / Property Card, `Namuna-8` municipal tax transfer, MSEB meter transfer, snag clearance, and Possession Letter (`completion_pct` `0..100%`).
- Automatically exposed to the buyer on `#/buyer-portal/<token>` (`GET /api/v1/public/buyer-portal/{token}`).

### Track G-6 — Marketing Verified Trust Badges & Cost Pack (`compute_marketing_trust_pack`)
- Emits up to 5 verified badges (`MAHARERA_VERIFIED`, `NA_COLLECTOR_VERIFIED`, `FRESH_7_12_ZERO_BOJA`, `TITLE_30YR_CLEAR`, `BANK_APF_APPROVED`) strictly from verified records, plus WhatsApp brochure copy and SSR rendering on `/f/<slug>` when `funnel.property_id` is linked.

---

## 3. HTTP `/api/v1` Endpoints (`13` routes)

| Method & Path | Scope | Purpose |
|---|---|---|
| `GET /api/v1/properties/{pid}/revenue-vault` | Auth | Read property `revenue_vault` + live `evaluate_revenue_clearance` |
| `PUT /api/v1/properties/{pid}/revenue-vault` | `write` | Upsert `7/12`, `8A`, Property Card, NA Order, `Mojni` & 30-yr Title Search |
| `POST /api/v1/govt/cost-sheet` | Auth | Compute statutory Maharashtra Stamp Duty, Reg, GST & `§194-IA` TDS sheet |
| `GET /api/v1/deals/{did}/registration` | Auth | Read deal `govt_registration`, `home_loan`, `clp_schedule` & `post_reg_handover` |
| `PUT /api/v1/deals/{did}/registration` | `write` | Upsert `e-Registration` / `iSarita` SRO desk, GRAS challan & witness/biometric gates |
| `POST /api/v1/deals/{did}/registration/index-ii` | `write` | Record `registered_doc_no` + `index_ii_ref` & sync `deal.documents` |
| `GET /api/v1/properties/{pid}/bank-apf` | Auth | List approved Bank APF codes for a property |
| `POST /api/v1/properties/{pid}/bank-apf` | `write` | Add/update a Bank APF approval on a property |
| `PUT /api/v1/deals/{did}/home-loan` | `write` | Update buyer home-loan bank, APF code, sanction & disbursement status |
| `POST /api/v1/deals/{did}/clp/init` | `write` | Initialize `rera_building_7stage` or `na_plot_3stage` CLP schedule |
| `POST /api/v1/deals/{did}/clp/demand` | `write` | Raise a CLP milestone demand (enforcing `ARCHITECT_CERTIFICATE_REQUIRED`) |
| `PUT /api/v1/deals/{did}/handover` | `write` | Update `E-Ferfar` Talathi mutation, Circle Officer, `Namuna-8`, MSEB & Possession |
| `GET /api/v1/properties/{pid}/marketing-trust-pack` | Auth | Compute verified marketing trust badges, sample cost sheet & WhatsApp copy |

---

## 4. Verification

```bash
RE_DB=json python3 scripts/proof_govt_sales_cycle.py
RE_DB=json python3 -m pytest tests/test_govt_sales_cycle.py -q
```
