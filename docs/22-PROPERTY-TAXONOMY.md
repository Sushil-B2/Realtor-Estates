# 22 — The property taxonomy: five categories, five different forms

"Real estate" is not one product. A ₹45 lakh 2BHK and a 12-metre clear-height
warehouse share a word and nothing else: different buyer, different unit of
measure, different due diligence, different document chain, different commission
basis. This document records what the Indian portals and standards actually
publish, and turns it into the schema the CRM now enforces.

Researched before coding: 99acres and MagicBricks commercial and residential
listing pages, MagicBricks structured field sets (as exposed through their
listing schema), Grade A warehouse specifications from the logistics literature
(CBRE/JLL language as used by park operators), and the Maharashtra land-record
regime (7/12, occupant class, guntha/hectare) because that is this deployment's
home state.

---

## 1. The five categories

| Category | Types | Who buys | Price basis in the market | Due diligence that matters |
|---|---|---|---|---|
| **Residential** | apartment/flat, builder floor, independent house, villa, row house, penthouse, studio, duplex, farm house, serviced apartment, residential plot | families, NRI investors | total price, ₹/sqft | title, approved plan, OC/CC, RERA |
| **Commercial** | office, IT-park/SEZ office, shop, showroom, mall unit, co-working (seat), commercial plot | businesses, investors | ₹/sqft sale, **₹/sqft/month rent + deposit + CAM** | lease terms, lock-in, fire NOC, occupancy certificate, zoning |
| **Industrial** | warehouse, industrial shed, factory, cold storage, logistics park, industrial plot | 3PL, manufacturers | ₹/sqft/month, ₹/pallet/month | **clear height, floor load, docks**, power (kVA), fire (ESFR), road access |
| **Land** | residential plot, NA plot, commercial plot, agricultural land, farm land, industrial plot | investors, developers, farmers | ₹/sqft (plots), **₹/acre or ₹/guntha (agri)** | 7/12, occupant class, encumbrance, **NA order**, ceiling, irrigation |
| **Hospitality** | hotel, resort, banquet, guest house | operators, investors | ₹/key, ₹/sqft, revenue share | licences, star classification, F&B permits, seating/bed capacity |

Two of these (commercial, industrial) are **lease-first** markets: the price a
buyer asks about is monthly rent, and the fields that kill a deal are lock-in,
deposit months and escalation — not BHK. That is why the schema below is
per-category and not one wide table with 60 nullable columns.

## 2. What the portals actually expose

**99acres commercial listings** (sampled across Greater Noida, Chandigarh, Pune,
Mumbai, Patna) consistently publish, for office space: *carpet area* and *super
built-up area* separately, **minimum seats**, **cabins**, **meeting rooms**, private
vs shared vs public **washrooms**, pantry (private/shared), conference room,
reception, **Grade A building**, fit-out state (**bare shell** / ready-to-move /
furnished), power back-up (sometimes as a *monthly charge*), central AC, lift
count, floor of building, and for rentals **deposit in months of rent** and a
**lock-in period**. For shops and showrooms: mall vs *market/high street* vs
retail complex, ground floor, footfall position (*near entrance / elevator /
escalator*), frontage, and an explicit "suitable for" list (bakery, boutique,
clinic, cloud kitchen, ATM, café…). For warehouses: plot area in **acres**,
carpet/built-up area, multiple warehouse blocks, attached corporate office, road
touch and highway distance.

**MagicBricks residential listings** publish the familiar set: bedrooms,
bathrooms, balconies, **carpet/covered/super area with explicit units**, furnishing
(furnished / semi-furnished / unfurnished), floor **"15 of 29"**, **facing**
including compound directions (e.g. "North - West"), **ownership** (freehold /
leasehold), **possession status** (ready to move / under construction + date),
**age of construction**, parking (**"1 Covered"** / open), **tenant preference**
(bachelors / family / company), maintenance charge + period, security deposit,
available-from, water and electricity status, brokerage, booking amount, approving
authority, RERA IDs, and amenities as a named list.

**Grade A warehousing** (the industrial category) is graded on hard numbers:
clear height **10–12 m** (up to 15 m for automation), floor load **5–8 MT/sqm**
with FM2/TR34 superflat flooring, **dock levellers at roughly 1 per 5,000–10,000
sqft** with ~1.2 m dock height, a **28–30 m truck apron** for 40-ft containers,
12×12 m column spacing, ESFR sprinklers, and dedicated HT power. Grade B/C are the
same fields with worse values — so the schema stores numbers, not "grade".

**Land** is a different legal object again. In Maharashtra the 7/12 extract
records survey/gat number, **occupant class (I / II / government lessee)** — Class
II cannot be sold without the Collector's permission — encumbrances (`itar hakk`),
and area in **hectares/ares**, with local sale talk in **acres and guntha**
(1 acre = 40 guntha = 43,560 sqft; 1 hectare = 2.47 acres). Agricultural land also
carries ceiling laws, an agriculturist-only rule in several states, and irrigation
classification (jirayat/bagayat). A CRM that stores "area_sqft" and "price" for
this category is storing the two least useful numbers.

## 3. The schema this CRM enforces

One table per concern, declared in `app/taxonomy.py`; `GET /api/v1/taxonomy`
serves it so forms are generated from the server, not hard-coded in the UI.

**Areas** are stored with an explicit unit and normalised to sqft for comparison:
`sqft, sqm, sqyd, acre, hectare, are, guntha, cent`. Bigha is deliberately
**refused** — it varies by district and a silent conversion is worse than a
refusal.

**Price bases**: `total`, `per_sqft`, `per_month`, `per_month_per_sqft`,
`per_annum`, `per_seat_month`, `per_acre`. A rental listing's "price" is not
comparable to a sale listing's price, so comparison and matching always go
through the normaliser.

**Per-category required fields** (a property cannot be created without them):

| Category | Required | Notable optional |
|---|---|---|
| residential | area (carpet/built-up + unit), bedrooms, bathrooms | balconies, furnishing, floor/total floors, facing, ownership, possession, age, parking, tenant preference, maintenance, amenities, RERA, approving authority |
| commercial | area (carpet + unit), deal kind (sale/lease), fit-out | Grade, seats, cabins, meeting rooms, washrooms, pantry, power backup + charge, central AC, lifts, floor, frontage, mall/high-street, footfall, suitable-for, lock-in months, deposit months, escalation %, CAM ₹/sqft/mo |
| industrial | area (built-up + unit), clear height, floor load | docks + dock height, truck apron, column spacing, flooring class, fire system, power kVA, highway distance, plot area (acres) |
| land | area + unit, land use | survey/gat, occupant class, encumbrance, NA order, irrigation, soil, crops, ceiling-relevant, road access, fencing, tenure |
| hospitality | area or keys, category type | keys/rooms, beds, banquet seating, star class, licences, F&B, power, parking |

## 4. Requirements mirror the inventory

A buyer's requirement is stored in the **same vocabulary** as the listing, or
matching degrades into keyword soup. `requirement_spec` on a contact holds:
`category`, `types[]`, `deal_kind` (buy/rent), `budget_min/max` + `budget_unit`,
`area_min/max` + `area_unit`, `bedrooms_min`, `facing[]`, `furnishing[]`,
`must_have[]` (amenities/attributes), `city`, `localities[]`, `possession`.

`POST /api/v1/properties/search` and the contact matcher both score against that
spec, and every point carries a reason — the same principle as the vertical
matching in `docs/18`. The scoring is *unit-aware*: "under 60 lakh" is compared
against a total price for a sale and against **annualised** rent for a lease, so a
₹45,000/month office does not appear as "over budget" against a ₹60 lakh budget
nor as "cheap" — it appears with its annualised basis stated.

## 5. What changed in the product

* `properties` gained `category`, `type`, `deal_kind`, `attributes{}`, `area{value,unit,sqft}`
  and `pricing{basis, amount, per_sqft, deposit_months, lock_in_months, cam_per_sqft_month, escalation_pct}`.
* `POST/PATCH /api/v1/properties` validate against the schema — a warehouse with
  no clear height, an apartment with no bedrooms, or a bigha area is a 422 with
  the field named.
* `POST /api/v1/properties/search` is the desk's "find me a unit" query, working
  across all five categories with correct units.
* `POST /api/v1/contacts/{id}/requirement` stores a structured requirement and
  returns ranked matches immediately.
* The seeded inventory now spans categories: apartments, villas, a Grade A
  warehouse, a high-street showroom, an IT-park office suite, NA plots and
  agricultural land with survey numbers and occupant class.

## 5a. As built (v1.10.0)

| Piece | Where | What it does |
|---|---|---|
| Schema + validation | `app/taxonomy.py` | `CATEGORIES` (5) → `types` → field metadata; `validate`, `normalize_property`, `normalize_requirement`, `price_metrics`, `comparable`, `score_requirement`, `rank`, `hydrate`, `inr` |
| Route surface | `app/api.py` (+4) | `GET /api/v1/taxonomy`, `POST /api/v1/properties`, `POST /api/v1/properties/search`, `POST /api/v1/contacts/{id}/requirement` |
| Seed | `app/store.py :: taxonomy_inventory` | 12 listings across all five categories, each passed through `normalize_property` before it can be stored; legacy rows upgraded by `hydrate` |
| Existing DBs | `scripts/seed_taxonomy_inventory.py` | idempotent, title-keyed insert + legacy-row upgrade for a database seeded before the taxonomy existed (`--dry-run` supported) |
| Desk UI | `web/crm.html` + `web/js/crm.js` | "Property catalogue" (category chips → category-specific form generated from `/taxonomy`) and "Find a match" (scored results with reasons) |
| Tests | `tests/test_taxonomy.py` | 28 tests: category distinctness, unit maths, bigha refusal, per-category required fields, foreign-field rejection, rate-in-total-field guard, hydration, scoring caps, HTTP surface, RBAC, tenant isolation |
| Proof | `scripts/proof_taxonomy.py` | 40 live checks, self-boot or `--live` against a running instance |

Behaviour worth knowing, all of it deliberate:

* **Budget and area are scored, not filtered.** `category`, `types`, `city`,
  `localities` and explicit `attributes` are hard filters — those are facts the
  user stated. Budget is soft, so a listing 10% over budget still appears with
  "slightly over budget: ₹65,00,000 vs ₹60,00,000" in its reasons.
* **Caps on the score.** A listing missing a stated `must_have` caps at 85; the
  wrong category caps at 60; the wrong deal kind caps at 65. Nothing can be a
  "100% match" while failing a stated requirement.
* **A product word searches the product.** `q: "godown"` resolves through the
  alias index to `industrial/warehouse` and returns warehouses whose titles never
  say "godown"; it does not degrade into a substring search.
* **Loose input is resolved, not rejected.** `"3 bhk flat"` → `residential /
  apartment` with `bedrooms: 3, bathrooms: 2` filled in; `"marriage hall"` →
  `hospitality / banquet`; `"agricultural land"` → `land / agricultural_land`
  (longest alias wins, so "land" cannot swallow it).
* **A lease without deposit/lock-in is given defaults** (3 months / 12 months)
  rather than stored blank, because that is the first thing a lawyer asks.
* **The classic listing error is caught:** a per-sqft rate typed into the total
  field ("₹45,00,000 per month per sqft") is a 422 with the reason stated.

## 6. Honest limits

* **Portal ingestion still defaults to residential framing** (`docs/19`): the
  adapters parse name/phone/budget, and only the residential adapters fill BHK
  and area. Commercial portal payloads need their own adapter fields before this
  is a commercial-lead machine.
* **No automated 7/12 lookup.** The record fields are captured and audited, not
  fetched from Mahabhulemi — that needs a government API that does not exist for
  this use.
* **Bigha refused, not converted.** Documented above, deliberately.
* **Hospitality is thin.** It exists so hotels do not get shoehorned into
  "commercial"; the revenue-management depth (ADR, occupancy, RevPAR) is out of
  scope.

## 7. Sources

Fetched 2026-11-18:

* 99acres commercial listing pages — Greater Noida, Chandigarh (sale and
  rent/lease), Pune, Mumbai (Bandra West), Patna, Bhubaneswar, Lucknow, Mohali
* MagicBricks residential listing pages (Hyderabad, Shillong, Chhindwara, Hubli)
  and the structured field sets published for their listings (`pricePerSqft`,
  `carpetArea/coveredArea/superArea`, `facing`, `ownership`, `possessionStatus`,
  `tenantPreference`, `maintenanceCharges`, `securityDeposit`, `reraIds`)
* Grade A warehouse specifications: wareongo.com, leasewarehouse.in,
  casagrandindustrial.co.in, bhagyashreegroup.co.in, chaurasiaenterprises.com
* Land records and agricultural land rules: lordsofthelands.com (7/12 reading),
  blog.ipleaders.in (7/12 contents and acres/guntha/hectare conversion),
  agrifarming.in and gmore.app (state-wise buyer rules, ceilings, documents),
  legiscore.in (occupant class, kharab, classification)
