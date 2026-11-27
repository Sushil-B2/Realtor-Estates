"""
Realtor Estates — multi-tenant document store.

Deliberately boring: one append/update JSON document per tenant collection,
atomic writes, a global RLock, and a hash-chained audit log. This keeps the
demo dependency-free and inspectable. The access layer *already* enforces the
two invariants that matter and that survive a swap to Postgres + RLS:

  1. every read/write is scoped by tenant_id taken from the session, never
     from the request body;
  2. every mutation appends to a tamper-evident audit chain.

Migration path to Postgres is mechanical: one table per collection, one row per
document, `tenant_id` NOT NULL + RLS policy + `audit_log` hash chain trigger.
See docs/03-DATA-MODEL.md.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
import uuid
from typing import Any

from . import config as cfg
from . import db as dbmod
from . import security as sec
from . import vertical as vt
from . import inventory as inventory_rules
from . import taxonomy as taxonomy_rules
from . import automations as automation_rules
from . import money as money_rules

DATA_DIR = os.environ.get("RE_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
STORE_FILE = os.path.join(DATA_DIR, "store.json")

COLLECTIONS = [
    "tenants",
    "users",
    "contacts",
    "deals",
    "properties",
    "conversations",
    "activities",
    "tasks",
    "agents",
    "proposals",
    "automations",
    "social",
    "campaigns",
    "funnels",
    "pages",
    "integrations",
    "audit",
    "research",
    "webhooks",
    "deliveries",
    "runs",
    # real-estate vertical: the site visit is an object, not a stage label (docs/18 §6)
    "site_visits",
    # portal ingestion: one hashed key per portal feed (docs/18 §6 #4)
    "ingest_keys",
    # channel partners: registrations (a claim with a clock) and payouts (docs/18 §6 #5)
    "cp_registrations",
    "cp_payouts",
    # unit-level inventory: the thing that actually gets blocked, booked, sold (docs/18 §6 #6)
    "units",
    # money: reconciled provider payments, and the two documents they produce
    # (a receipt for a booking amount, a tax invoice for the brokerage's own supply)
    "payments",
    "invoices",
    # sessions: one row per signed-in device, with the hashed refresh token that
    # rotates on every use. A revocable session has to be a record, not a claim
    # inside a token (docs/28)
    "sessions",
    # usage: the append-only ledger for meters no row can derive (storage bytes, a
    # credit), and the frozen snapshot of a month that has ended (docs/29). A purge
    # must not be able to shrink an invoice, so a closed month is a copy, not a query
    "usage_ledger",
    "usage_snapshots",
    # Sprint S3 compliance launch pack (docs/07, docs/40): DPDP §8(9) grievance
    # redressal tickets with 30-day statutory SLA clock, and DPDP §8(5) signed
    # erasure certificates + blind-index suppression tombstones.
    "grievances",
    "erasures",
]


def nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# Taxonomy seed — one listing per category, in the vocabulary of `app/taxonomy.py`
# ---------------------------------------------------------------------------
# Twelve rows of plots and flats cannot demonstrate five categories, so the seed
# carries real multi-category inventory: an office on a per-sqft lease, a
# high-street shop, co-working seats, a Grade-A warehouse, agricultural land with
# a survey number, a banquet hall. `normalize_property` runs on every row, so a
# seed that would be rejected by the API cannot exist.
def taxonomy_inventory(tenant_id: str) -> list[dict]:
    rows = [
        {
            "title": "Kharadi Business Hub — Office 7th floor",
            "category": "commercial", "type": "office", "deal_kind": "lease",
            "city": "Pune", "locality": "Kharadi", "status": "available",
            "area": {"value": 5000, "unit": "sqft"},
            "pricing": {"basis": "per_month_per_sqft", "amount": 65, "deposit_months": 6,
                        "lock_in_months": 36, "escalation_pct": 5, "cam_per_sqft_month": 12},
            "attributes": {"fitout": "Fully Fitted", "grade": "Grade A", "seats": 60, "cabins": 3,
                           "meeting_rooms": 2, "conference_room": True, "reception": True,
                           "pantry": "Shared", "central_ac": True, "lifts": 4, "floor": "7",
                           "total_floors": 12, "power_backup": True, "power_backup_charge": 45000,
                           "rera_id": "P52100041209", "possession": "Ready to Move"},
            "docs": ["Registered lease draft", "Society NOC", "Property tax receipt"],
        },
        {
            "title": "Bandra West — High street showroom",
            "category": "commercial", "type": "showroom", "deal_kind": "lease",
            "city": "Mumbai", "locality": "Bandra West", "status": "available",
            "area": {"value": 450, "unit": "sqft"},
            "pricing": {"basis": "per_month", "amount": 190000, "deposit_months": 6, "lock_in_months": 24},
            "attributes": {"fitout": "Warm Shell", "location_type": "High Street", "floor": "Ground",
                           "frontage_ft": 22, "footfall_position": "Opposite Starbucks, main road frontage",
                           "suitable_for": ["Boutique", "Clinic", "Cafe", "ATM"], "washrooms": "1 public",
                           "power_backup": True, "ownership": "Freehold"},
            "docs": ["Title search report", "Property tax receipt"],
        },
        {
            "title": "Nanded Hub — 40 co-working seats",
            "category": "commercial", "type": "co_working", "deal_kind": "lease",
            "city": "Nanded", "locality": "Shivaji Nagar", "status": "available",
            "area": {"value": 3800, "unit": "sqft"},
            "pricing": {"basis": "per_seat_month", "amount": 7500, "deposit_months": 1, "lock_in_months": 3},
            "attributes": {"fitout": "Plug and Play", "seats": 40, "cabins": 2, "meeting_rooms": 1,
                           "pantry": "Shared", "central_ac": True, "power_backup": True,
                           "escalation_pct": 10},
            "docs": ["Service agreement template"],
        },
        {
            "title": "Bhiwandi Logistics Park — Warehouse Block C",
            "category": "industrial", "type": "warehouse", "deal_kind": "lease",
            "city": "Mumbai", "locality": "Bhiwandi", "status": "available",
            "area": {"value": 60000, "unit": "sqft"},
            "pricing": {"basis": "per_month_per_sqft", "amount": 24, "deposit_months": 3, "lock_in_months": 36,
                        "escalation_pct": 5},
            "attributes": {"clear_height_m": 12, "floor_load_mt_sqm": 6, "flooring": "FM2 Superflat",
                           "dock_levellers": 12, "dock_height_m": 1.2, "truck_apron_m": 28,
                           "column_spacing": "12m x 12m", "fire_system": "ESFR Sprinklers",
                           "power_kva": 500, "plot_area_acres": 4.5, "highway_distance_km": 2.5,
                           "container_access": True, "warehouse_grade": "Grade A", "ownership": "Freehold"},
            "docs": ["Lease agreement draft", "Fire NOC", "Structural stability certificate"],
        },
        {
            "title": "Chakan MIDC — Industrial shed",
            "category": "industrial", "type": "factory", "deal_kind": "sale",
            "city": "Pune", "locality": "Chakan MIDC", "status": "available",
            "area": {"value": 1.35, "unit": "acre"},
            "pricing": {"basis": "total", "amount": 145000000},
            "attributes": {"clear_height_m": 8, "floor_load_mt_sqm": 5, "power_kva": 1000,
                           "fire_system": "Sprinklers + Hydrant", "plot_area_acres": 1.35,
                           "highway_distance_km": 1.2, "container_access": True,
                           "possession": "Ready to Move", "ownership": "Freehold"},
            "docs": ["Sale deed", "MIDC allotment letter", "Fire NOC"],
        },
        {
            "title": "MIDC Nanded — Industrial plot 2,400 sqm",
            "category": "industrial", "type": "industrial_plot", "deal_kind": "sale",
            "city": "Nanded", "locality": "MIDC Phase 2", "status": "available",
            "area": {"value": 2400, "unit": "sqm"},
            "pricing": {"basis": "per_sqft", "amount": 850},
            "attributes": {"clear_height_m": 0.1, "floor_load_mt_sqm": 0.1, "highway_distance_km": 3,
                           "power_kva": 250, "ownership": "Leasehold"},
            "docs": ["MIDC allotment letter"],
        },
        {
            "title": "Sinnar farm land — 5.5 acre, canal touch",
            "category": "land", "type": "agricultural_land", "deal_kind": "sale",
            "city": "Nashik", "locality": "Sinnar", "status": "available",
            "area": {"value": 5.5, "unit": "acre"},
            "pricing": {"basis": "per_acre", "amount": 3200000},
            "attributes": {"land_use": "Agricultural", "survey_number": "Gat 214/2",
                           "occupant_class": "Class I", "encumbrance": "None",
                           "irrigation": "Canal", "soil_type": "Black cotton", "crops": "Sugarcane, onion",
                           "road_access": "18 ft village road", "fencing": True},
            "docs": ["7/12 extract", "Mutation entry", "Title search report (30 years)"],
        },
        {
            "title": "Nanded — NA plot, sanctioned layout",
            "category": "land", "type": "na_plot", "deal_kind": "sale",
            "city": "Nanded", "locality": "Taroda Phase 3", "status": "available",
            "area": {"value": 2400, "unit": "sqft"},
            "pricing": {"basis": "total", "amount": 3100000},
            "attributes": {"land_use": "NA Plot", "survey_number": "Gat 88/1", "na_order": True,
                           "occupant_class": "Class I", "road_access": "30 ft internal road",
                           "corner_plot": True, "facing": "North-East", "ownership": "Freehold",
                           "approved_by": "Nanded Waghala City Municipal Corporation"},
            "docs": ["NA order", "Sanctioned layout", "7/12 extract"],
        },
        {
            "title": "Nanded — Godavari Banquet Hall",
            "category": "hospitality", "type": "banquet", "deal_kind": "lease",
            "city": "Nanded", "locality": "Vazirabad", "status": "available",
            "area": {"value": 12000, "unit": "sqft"},
            "pricing": {"basis": "per_month", "amount": 385000, "deposit_months": 6, "lock_in_months": 36},
            "attributes": {"banquet_seating": 800, "star_class": "Unclassified", "fnb_licence": True,
                           "licences": ["Fire NOC", "FSSAI", "Police permission"],
                           "parking": "120 cars", "power_backup": True},
            "docs": ["FSSAI licence", "Fire NOC"],
        },
        {
            "title": "Lonavala — 40-key resort",
            "category": "hospitality", "type": "resort", "deal_kind": "sale",
            "city": "Pune", "locality": "Lonavala", "status": "available",
            "area": {"value": 1.8, "unit": "acre"},
            "pricing": {"basis": "total", "amount": 260000000},
            "attributes": {"keys": 40, "beds": 80, "star_class": "4 Star", "fnb_licence": True,
                           "possession": "Ready to Move", "ownership": "Freehold"},
            "docs": ["Title search report", "Star classification certificate"],
        },
        {
            "title": "Godavari Heights — 2BHK 501 (rent)",
            "category": "residential", "type": "apartment", "deal_kind": "rent",
            "city": "Nanded", "locality": "Shivaji Nagar", "status": "available",
            "area": {"value": 985, "unit": "sqft"},
            "pricing": {"basis": "per_month", "amount": 18000, "deposit_months": 2},
            "attributes": {"bedrooms": 2, "bathrooms": 2, "balconies": 1, "furnishing": "Semi-Furnished",
                           "facing": "East", "floor": "5", "total_floors": 11, "tenant_preference": "Family",
                           "maintenance_amount": 2500, "maintenance_period": "Monthly",
                           "power_backup": True, "amenities": ["Lift", "Parking", "Power Backup",
                                                               "Children's play area", "CCTV"],
                           "possession": "Ready to Move"},
            "docs": ["Rent agreement draft", "Society NOC"],
        },
        {
            "title": "Wagholi — 4BHK villa with garden",
            "category": "residential", "type": "villa", "deal_kind": "sale",
            "city": "Pune", "locality": "Wagholi", "status": "available",
            "area": {"value": 210, "unit": "sqm"},
            "pricing": {"basis": "total", "amount": 9400000},
            "attributes": {"bedrooms": 4, "bathrooms": 4, "balconies": 2, "furnishing": "Unfurnished",
                           "facing": "North-East", "total_floors": 2, "parking": "2 covered",
                           "ownership": "Freehold", "possession": "Under Construction",
                           "age_years": 0, "amenities": ["Clubhouse", "Gym", "Swimming Pool", "24x7 Security"],
                           "rera_id": "P52100034567", "approved_by": "PMRDA"},
            "docs": ["RERA registration", "Approved plan"],
        },
    ]
    out = []
    for row in rows:
        clean = taxonomy_rules.normalize_property(row)
        prop = {
            "id": nid("prp"),
            "tenant_id": tenant_id,
            "title": row["title"],
            "city": row["city"],
            "locality": row["locality"],
            "status": row["status"],
            "docs": row.get("docs", []),
            "coordinates": [round(19.15 + random.random() / 10, 5), round(77.3 + random.random() / 10, 5)],
            "created_at": time.time() - random.randint(5, 200) * 86400,
            "source": "seed",
        }
        prop.update(clean)
        prop["price"] = clean["pricing"]["amount"]
        prop["size_sqft"] = clean["area"]["sqft"] if clean["area"] else None
        out.append(prop)
    return out



class Store:
    """Tenant-scoped document store.

    Two persistence backends, one API:

      * **json**     — the zero-install demo path (`data/store.json`).
      * **postgres** — Neon/Postgres via `DATABASE_URL` (see app/db.py).

    With Postgres the working set is loaded once at boot and every mutation is
    written through synchronously, so the read path stays a dict lookup while
    durability is real. Single-writer assumption is documented in docs/12.
    """

    def __init__(self, path: str = STORE_FILE):
        self.path = os.path.abspath(path)
        self.lock = threading.RLock()
        self._req_cache = threading.local()
        self.db: dict[str, Any] = {c: [] for c in COLLECTIONS}
        self.db["meta"] = {"version": 1, "created_at": now()}
        self.backend: Any = None
        self.backend_name = "json"
        self.backend_error = ""
        self.stateless_reads = False

        want_pg = cfg.get("RE_DB", "auto").lower() not in ("json", "file", "off") and dbmod.configured()
        want_stateless = cfg.get("RE_STATELESS_READS", "").strip().lower() in ("1", "true", "yes")
        require_existing = cfg.get("RE_REQUIRE_EXISTING_DATA", "").strip().lower() in ("1", "true", "yes")
        if want_pg and dbmod.driver_available():
            try:
                self.backend = dbmod.PgBackend()
                self.backend_name = "postgres"
                if want_stateless:
                    routing_colls = ("tenants", "users", "sessions", "ingest_keys", "funnels")
                    batch_fn = getattr(self.backend, "load_collections_admin", None)
                    loaded_routing = (
                        batch_fn(routing_colls)
                        if callable(batch_fn)
                        else {c: self.backend.load_collection_admin(c) for c in routing_colls}
                    )
                    if require_existing and not loaded_routing.get("tenants"):
                        raise dbmod.StorageUnavailable("Existing tenant data unavailable")
                    if not loaded_routing.get("tenants") and not sum(self.backend.counts().values()):
                        self.seed()
                        self._flush_all()
                        loaded_routing = (
                            batch_fn(routing_colls)
                            if callable(batch_fn)
                            else {c: self.backend.load_collection_admin(c) for c in routing_colls}
                        )
                    for c in COLLECTIONS:
                        self.db[c] = loaded_routing.get(c, []) if c in routing_colls else []
                    self.stateless_reads = True
                else:
                    loaded_pg = self.backend.load_all()
                    if require_existing and not loaded_pg.get("tenants"):
                        raise dbmod.StorageUnavailable("Existing tenant data unavailable")
                    for c in COLLECTIONS:
                        self.db[c] = loaded_pg.get(c, [])
                    if not any(self.db[c] for c in COLLECTIONS if c != "meta"):
                        self.seed()
                        self._flush_all()
            except Exception as exc:
                self.backend = None
                self.backend_name = "json"
                self.stateless_reads = False
                self.backend_error = f"{exc.__class__.__name__}: {exc}"[:300]
                if require_existing or cfg.get("RE_DB_STRICT", "") in ("1", "true", "yes"):
                    raise
                print(f"[store] Postgres unavailable ({self.backend_error}) — falling back to the JSON store")
        elif want_pg and not dbmod.driver_available():
            self.backend_error = "psycopg not installed"
            print("[store] DATABASE_URL is set but psycopg is missing — JSON store in use "
                  "(pip install 'psycopg[binary]')")

        if self.backend is None:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                for c in COLLECTIONS:
                    self.db[c] = loaded.get(c, [])
                self.db["meta"] = loaded.get("meta", self.db["meta"])
            else:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self.seed()
                self.save()
            try:  # existing file from an older build: tighten it now
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    # ---------------- postgres write-through ----------------
    def _flush_all(self) -> None:
        if not self.backend:
            return
        # `audit` lives in its own table (append-only, seq-ordered); writing it
        # into re_documents as well duplicated every entry and destroyed the
        # ordering the hash chain depends on.
        rows = [(c, r.get("tenant_id", ""), r) for c in COLLECTIONS if c != "audit" for r in self.db[c]]
        self.backend.upsert_documents(rows)
        for entry in self.db.get("audit", []):
            self.backend.append_audit(entry)

    def _pg_put(self, coll: str, tenant_id: str, doc: dict) -> None:
        if not self.backend:
            return
        try:
            self.backend.upsert_document(coll, tenant_id, doc)
        except Exception as exc:  # durability failure must be loud, never silent
            print(f"[store] postgres write failed for {coll}: {exc.__class__.__name__}")
            raise

    def _pg_del(self, coll: str, tenant_id: str, rid: str) -> None:
        if not self.backend:
            return
        self.backend.delete_document(coll, tenant_id, rid)

    def backend_status(self, *, probe: bool = True) -> dict:
        info = {
            "backend": self.backend_name,
            "durable": self.backend_name == "postgres",
            "stateless_reads": bool(self.stateless_reads),
            "error": self.backend_error,
            "dsn": dbmod.redacted_dsn() if self.backend else "",
        }
        if self.backend and probe:
            try:
                info.update(self.backend.ping())
                if self.stateless_reads:
                    info["documents"] = sum(self.backend.counts().values())
                    info["ram_cached_tenant_docs"] = 0
                else:
                    info["documents"] = sum(len(self.db[c]) for c in COLLECTIONS)
            except Exception as exc:
                info["ping_error"] = str(exc)[:200]
        return info

    def public_funnel_by_slug(self, slug: str, *, published_only: bool = False) -> dict | None:
        """Resolve a funnel by slug across workspaces without requiring full RAM hydration (`F-01`)."""
        rows = (
            self.backend.load_collection_admin("funnels")
            if (self.stateless_reads and self.backend)
            else self.db.get("funnels", [])
        )
        for f in rows:
            if f.get("slug") == slug and (not published_only or f.get("published")):
                return dict(f)
        return None

    # ---------------- persistence ----------------
    def save(self) -> None:
        if self.backend is not None:
            return  # Postgres is the durable store; no second copy on disk
        with self.lock:
            tmp = self.path + ".tmp"
            # 0600 from the first byte: the file holds tenant PII (some of it
            # encrypted, some of it not) and must not be world-readable on a
            # shared host. The audit found it at 0644.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.db, fh, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    # ---------------- tenant-scoped access ----------------
    _STATELESS_ROUTING = ("tenants", "users", "sessions", "ingest_keys", "funnels")

    def begin_request_cache(self) -> None:
        """Enable a strictly single-request thread-local read snapshot (`F-01` safe)."""
        rc = getattr(self, "_req_cache", None) or threading.local()
        self._req_cache = rc
        rc.active = True
        rc.snap = {}

    def end_request_cache(self) -> None:
        rc = getattr(self, "_req_cache", None)
        if rc is not None:
            rc.active = False
            rc.snap = {}

    def _invalidate_req_cache(self, tenant_id: str, coll: str | None = None) -> None:
        rc = getattr(self, "_req_cache", None)
        snap = getattr(rc, "snap", None) if rc is not None else None
        if not snap:
            return
        if coll is None:
            for k in [k for k in snap if k[0] == tenant_id]:
                snap.pop(k, None)
        else:
            snap.pop((tenant_id, coll), None)

    def prefetch(self, colls: Iterable[str], tenant_id: str) -> None:
        """Batch-load multiple tenant collections in one RLS SQL round-trip for this request."""
        if not (self.stateless_reads and self.backend and tenant_id):
            return
        rc = getattr(self, "_req_cache", None)
        if rc is None or not getattr(rc, "active", False):
            return
        snap: dict[tuple[str, str], list[dict]] = getattr(rc, "snap", None) or {}
        rc.snap = snap
        missing = [
            c for c in dict.fromkeys(colls)
            if c not in self._STATELESS_ROUTING and c != "audit" and (tenant_id, c) not in snap
        ]
        if not missing:
            return
        many = getattr(self.backend, "documents_many", None)
        if callable(many):
            loaded = many(missing, tenant_id)
            for name in missing:
                snap[(tenant_id, name)] = [dict(r) for r in (loaded.get(name) or [])]
        else:
            for name in missing:
                snap[(tenant_id, name)] = [dict(r) for r in self.backend.documents(name, tenant_id)]

    def all(self, coll: str, tenant_id: str) -> list[dict]:
        if self.stateless_reads and self.backend and coll not in self._STATELESS_ROUTING:
            rc = getattr(self, "_req_cache", None)
            snap = getattr(rc, "snap", None) if (rc is not None and getattr(rc, "active", False)) else None
            if snap is not None and (tenant_id, coll) in snap:
                return [dict(r) for r in snap[(tenant_id, coll)]]
            if coll == "audit":
                rows = [dict(r) for r in self.backend.audit_rows(tenant_id)]
            else:
                rows = [dict(r) for r in self.backend.documents(coll, tenant_id)]
            if snap is not None:
                snap[(tenant_id, coll)] = rows
            return [dict(r) for r in rows]
        with self.lock:
            return [dict(r) for r in self.db[coll] if r.get("tenant_id") == tenant_id]

    def reload(self, coll: str, tenant_id: str) -> int:
        """Re-read one collection for one tenant from Postgres.

        Writes already go through to the database (`_pg_put`); reads are served
        from the in-memory working set, which is a boot-time snapshot. For most
        screens a few seconds of staleness is invisible. For the automation tick
        it is not: the idempotency check would pass in both of two processes and
        both would send (`db/008`). So the tick refreshes what it decides on.
        No-op without a backend (a single process owns a JSON file outright).
        """
        if self.backend is None:
            return 0
        if getattr(self, "stateless_reads", False) and coll not in self._STATELESS_ROUTING:
            self._invalidate_req_cache(tenant_id, coll)
            return len(self.backend.documents(coll, tenant_id))
        docs = self.backend.documents(coll, tenant_id)
        self._invalidate_req_cache(tenant_id, coll)
        with self.lock:
            keep = [r for r in self.db[coll] if r.get("tenant_id") != tenant_id]
            self.db[coll] = keep + docs
        return len(docs)

    def invalidate_freshness(self, tenant_id: str, colls: Iterable[str] | None = None) -> None:
        """Mark cached Postgres collection snapshots stale for `tenant_id`."""
        with self.lock:
            stamps: dict[tuple[str, str], float] = getattr(self, "_fresh_at", None) or {}
            self._fresh_at = stamps
            if colls is None:
                for key in [k for k in stamps if k[0] == tenant_id]:
                    stamps.pop(key, None)
            else:
                for name in colls:
                    stamps.pop((tenant_id, name), None)

    def reload_many(self, colls: Iterable[str], tenant_id: str, *, max_age_ms: int = 0) -> int:
        """`reload` for several collections in one query (`WHERE collection = ANY(%s)`).

        When `max_age_ms > 0`, collections refreshed within that window (and not
        invalidated by a remote realtime event) are served from memory so 100
        concurrent inbox opens coalesce instead of issuing 100 identical reads.
        """
        if self.backend is None:
            return 0
        names = list(dict.fromkeys(colls))
        if getattr(self, "stateless_reads", False):
            names = [n for n in names if n in self._STATELESS_ROUTING]
        if not names:
            return 0
        if max_age_ms > 0:
            cutoff = time.monotonic() - (max_age_ms / 1000.0)
            with self.lock:
                stamps: dict[tuple[str, str], float] = getattr(self, "_fresh_at", None) or {}
                self._fresh_at = stamps
                stale = [n for n in names if stamps.get((tenant_id, n), 0.0) < cutoff]
            if not stale:
                with self.lock:
                    return sum(
                        1
                        for n in names
                        for r in self.db.get(n, [])
                        if r.get("tenant_id") == tenant_id
                    )
            names = stale
        many = getattr(self.backend, "documents_many", None)
        if not callable(many):
            return sum(self.reload(name, tenant_id) for name in names)
        fresh = many(names, tenant_id)
        total = 0
        stamped_at = time.monotonic()
        with self.lock:
            stamps = getattr(self, "_fresh_at", None) or {}
            self._fresh_at = stamps
            for name in names:
                keep = [r for r in self.db.get(name, []) if r.get("tenant_id") != tenant_id]
                rows = fresh.get(name) or []
                self.db[name] = keep + rows
                stamps[(tenant_id, name)] = stamped_at
                total += len(rows)
        return total

    def find(self, coll: str, tenant_id: str, **where: Any) -> list[dict]:
        rows = self.all(coll, tenant_id)
        for k, v in where.items():
            rows = [r for r in rows if r.get(k) == v]
        return rows

    def get(self, coll: str, tenant_id: str, rid: str) -> dict | None:
        if self.stateless_reads and self.backend and coll not in self._STATELESS_ROUTING:
            rc = getattr(self, "_req_cache", None)
            snap = getattr(rc, "snap", None) if (rc is not None and getattr(rc, "active", False)) else None
            if snap is not None and (tenant_id, coll) in snap:
                for r in snap[(tenant_id, coll)]:
                    if r.get("id") == rid:
                        return dict(r)
                return None
            if coll == "audit":
                for r in self.backend.audit_rows(tenant_id):
                    if r.get("id") == rid:
                        return dict(r)
                return None
            return self.backend.get_document(coll, tenant_id, rid)
        with self.lock:
            for r in self.db[coll]:
                if r.get("id") == rid and r.get("tenant_id") == tenant_id:
                    return dict(r)
        return None

    # ---------------- request-scoped capture (docs/26) ----------------
    # A proof has to be able to say "these rows are mine" *at the moment it
    # writes*, without every route learning about proofing. The store therefore
    # records what it created during the current request, keyed by thread — the
    # server is threaded, one request per thread — and the API stamps them once
    # the route returns. Off by default: an unstarted capture is a no-op.
    def begin_capture(self) -> None:
        self._capture = getattr(self, "_capture", None) or threading.local()
        self._capture.created = []

    def captured(self) -> list[tuple[str, str, str]]:
        cap = getattr(self, "_capture", None)
        return list(getattr(cap, "created", []) or []) if cap else []

    def end_capture(self) -> list[tuple[str, str, str]]:
        rows = self.captured()
        if getattr(self, "_capture", None) is not None:
            self._capture.created = []
        return rows

    def insert(self, coll: str, tenant_id: str, doc: dict, prefix: str | None = None) -> dict:
        with self.lock:
            rec = {
                "id": doc.get("id") or nid(prefix or coll[:3]),
                "tenant_id": tenant_id,
                "created_at": doc.get("created_at", now()),
                "updated_at": now(),
                **doc,
            }
            rec["tenant_id"] = tenant_id
            if not (self.stateless_reads and self.backend and coll not in self._STATELESS_ROUTING):
                self.db[coll].append(rec)
            self._pg_put(coll, tenant_id, rec)
            self._invalidate_req_cache(tenant_id, coll)
            self.save()
            cap = getattr(self, "_capture", None)
            if cap is not None:
                getattr(cap, "created", []).append((coll, tenant_id, rec["id"]))
            return dict(rec)

    def update(self, coll: str, tenant_id: str, rid: str, patch: dict) -> dict | None:
        if self.stateless_reads and self.backend and coll not in self._STATELESS_ROUTING:
            with self.lock:
                existing = self.backend.get_document(coll, tenant_id, rid)
                if not existing:
                    return None
                new = {**existing, **patch}
                new["tenant_id"] = tenant_id
                new["id"] = rid
                new["updated_at"] = now()
                self._pg_put(coll, tenant_id, new)
                self._invalidate_req_cache(tenant_id, coll)
                return dict(new)
        with self.lock:
            for i, r in enumerate(self.db[coll]):
                if r.get("id") == rid and r.get("tenant_id") == tenant_id:
                    new = {**r, **patch}
                    new["tenant_id"] = tenant_id  # never let a patch move tenants
                    new["id"] = rid
                    new["updated_at"] = now()
                    self.db[coll][i] = new
                    self._pg_put(coll, tenant_id, new)
                    self._invalidate_req_cache(tenant_id, coll)
                    self.save()
                    return dict(new)
        return None

    def remove(self, coll: str, tenant_id: str, rid: str) -> bool:
        if self.stateless_reads and self.backend and coll not in self._STATELESS_ROUTING:
            with self.lock:
                deleted = bool(self.backend.delete_document(coll, tenant_id, rid))
                if deleted:
                    self._invalidate_req_cache(tenant_id, coll)
                return deleted
        with self.lock:
            before = len(self.db[coll])
            self.db[coll] = [r for r in self.db[coll] if not (r.get("id") == rid and r.get("tenant_id") == tenant_id)]
            changed = len(self.db[coll]) != before
            if changed:
                self._pg_del(coll, tenant_id, rid)
                self.save()
            return changed

    # ---------------- audit chain ----------------
    def audit(self, tenant_id: str, actor: str, action: str, target: str = "", meta: dict | None = None) -> dict:
        with self.lock:
            prev = ""
            if not (self.stateless_reads and self.backend):
                for r in reversed(self.db["audit"]):
                    if r.get("tenant_id") == tenant_id:
                        prev = r.get("hash", "")
                        break
            entry = {
                "id": nid("aud"),
                "tenant_id": tenant_id,
                "actor": actor,
                "action": action,
                "target": target,
                "meta": meta or {},
                "at": now(),
                "prev_hash": prev,
            }
            entry["hash"] = sec.audit_hash(entry)
            if self.backend:
                # The database is the arbiter of `prev_hash`: with more than one
                # writer process, an in-memory chain forks (see docs/13).
                chained = self.backend.append_audit(entry)
                if chained:
                    entry = chained
            self._invalidate_req_cache(tenant_id, "audit")
            if not (self.stateless_reads and self.backend):
                self.db["audit"].append(entry)
            self.save()
            return entry

    def verify_audit(self, tenant_id: str) -> dict:
        if self.stateless_reads and self.backend:
            chain = [dict(r) for r in self.backend.audit_rows(tenant_id)]
        else:
            with self.lock:
                chain = [r for r in self.db["audit"] if r.get("tenant_id") == tenant_id]
        prev = ""
        for idx, e in enumerate(chain):
            if e.get("prev_hash") != prev:
                return {"ok": False, "broken_at": idx, "reason": "prev_hash mismatch"}
            if sec.audit_hash(e) != e.get("hash"):
                return {"ok": False, "broken_at": idx, "reason": "hash mismatch"}
            prev = e["hash"]
        return {"ok": True, "length": len(chain), "head": prev}

    # ---------------- seed ----------------
    def seed(self) -> None:
        rng = random.Random(20261117)
        t1 = nid("tnt")
        t2 = nid("tnt")
        self.db["tenants"] = [
            {
                "id": t1,
                "tenant_id": t1,
                "name": "Sunrise Land Developers",
                "slug": "sunrise",
                "plan": "growth",
                "region": "ap-south-1",
                "currency": "INR",
                "created_at": now() - 210 * 86400,
                "settings": {
                    "timezone": "Asia/Kolkata",
                    "quiet_hours": ["21:00", "09:00"],
                    "call_window": ["09:00", "21:00"],
                    "ai_disclosure": True,
                    "dlt_series": "1600",
                    "data_region": "India",
                },
                "features": {
                    "ai_voice": True,
                    "ai_whatsapp": True,
                    "lead_scraper": True,
                    "predictive_forecast": True,
                    "ads_autopilot": True,
                    "social_scheduler": True,
                    "public_funnels": True,
                    "mcp_gateway": True,
                    "autonomous_execution": False,
                    "redline_agent": True,
                },
                "kill_switch": False,
            },
            {
                "id": t2,
                "tenant_id": t2,
                "name": "Metro Realty Partners",
                "slug": "metro",
                "plan": "starter",
                "region": "ap-south-1",
                "currency": "INR",
                "created_at": now() - 40 * 86400,
                "settings": {"timezone": "Asia/Kolkata", "ai_disclosure": True, "data_region": "India"},
                "features": {"ai_voice": True, "lead_scraper": False, "autonomous_execution": False},
                "kill_switch": False,
            },
        ]

        def user(tid: str, name: str, email: str, role: str, pw: str) -> dict:
            return {
                "id": nid("usr"),
                "tenant_id": tid,
                "name": name,
                "email": email,
                "role": role,
                "password_hash": sec.hash_password(pw),
                "active": True,
                # "asked to enrol", not "has 2FA": the flag the desk shows is
                # `mfa_enrolled`, which only a verified code can set (docs/27)
                "mfa_enabled": role in ("owner", "admin"),
                "mfa_enrolled": False,
                "created_at": now() - rng.randint(10, 200) * 86400,
                "last_login": now() - rng.randint(1, 40) * 3600,
                "failed_attempts": 0,
            }

        u_owner = user(t1, "Aarav Deshmukh", "owner@realtorestates.app", "owner", "Demo@12345!")
        u_mgr = user(t1, "Priya Kulkarni", "manager@realtorestates.app", "manager", "Demo@12345!")
        u_agent = user(t1, "Rohit Patil", "agent@realtorestates.app", "agent", "Demo@12345!")
        u_view = user(t1, "Sana Sheikh", "viewer@realtorestates.app", "viewer", "Demo@12345!")
        u_other = user(t2, "Vikram Rao", "owner@metrorealty.app", "owner", "Metro@12345!")
        self.db["users"] = [u_owner, u_mgr, u_agent, u_view, u_other]

        # ---- inventory ----
        props = []
        inv = [
            ("Sunrise Greens — Plot 47", "plot", "Nanded", "Taroda Phase 2", 2_850_000, 2400, "available", "P1-2024-0912"),
            ("Sunrise Greens — Plot 48", "plot", "Nanded", "Taroda Phase 2", 2_950_000, 2400, "hold", "P1-2024-0912"),
            ("Sunrise Greens — Plot 12", "plot", "Nanded", "Taroda Phase 2", 2_400_000, 1800, "available", "P1-2024-0912"),
            ("Sunrise Meadows — Villa B4", "villa", "Pune", "Wagholi", 9_400_000, 2100, "available", "P52100034567"),
            ("Sunrise Meadows — Villa B5", "villa", "Pune", "Wagholi", 9_600_000, 2150, "booked", "P52100034567"),
            ("Godavari Heights — 3BHK 903", "apartment", "Nanded", "Shivaji Nagar", 6_200_000, 1240, "available", "P2-2025-0451"),
            ("Godavari Heights — 2BHK 704", "apartment", "Nanded", "Shivaji Nagar", 4_400_000, 985, "hold", "P2-2025-0451"),
            ("Industrial Shed — MIDC Plot 9", "commercial", "Nanded", "MIDC", 18_500_000, 5000, "available", "P3-2023-0220"),
            ("Sunrise Greens — Plot 61", "plot", "Nanded", "Taroda Phase 3", 3_100_000, 2600, "available", "P1-2024-0912"),
            ("Sunrise Greens — Plot 62", "plot", "Nanded", "Taroda Phase 3", 3_150_000, 2600, "sold", "P1-2024-0912"),
            ("Riverside Farmhouse Land 1.2 acre", "land", "Nashik", "Sinnar", 11_000_000, 5227, "available", None),
            ("Metro One — 2BHK 401", "apartment", "Pune", "Kharadi", 7_800_000, 1050, "available", "P52100041209"),
        ]
        for title, typ, city, loc, price, size, status, rera in inv:
            props.append(
                {
                    "id": nid("prp"),
                    "tenant_id": t1,
                    "title": title,
                    "type": typ,
                    "city": city,
                    "locality": loc,
                    "price": price,
                    "size_sqft": size,
                    "status": status,
                    "rera": rera,
                    "docs": ["7/12 extract", "NA order", "Sanctioned layout", "Title search report"]
                    if typ in ("plot", "land")
                    else ["RERA registration", "Approved plan", "Title search report"],
                    "coordinates": [round(19.15 + rng.random() / 10, 5), round(77.3 + rng.random() / 10, 5)],
                    "created_at": now() - rng.randint(30, 300) * 86400,
                }
            )
        props.extend(taxonomy_inventory(t1))
        self.db["properties"] = [taxonomy_rules.hydrate(p) for p in props]

        # ---- contacts ----
        first = ["Aarav", "Isha", "Rohan", "Meera", "Kabir", "Ananya", "Vivaan", "Diya", "Arjun", "Tara", "Nikhil", "Sneha",
                 "Aditya", "Pooja", "Sameer", "Riya", "Kiran", "Neha", "Manish", "Shreya", "Vikas", "Anjali", "Farhan", "Kavya"]
        last = ["Sharma", "Patil", "Desai", "Joshi", "Khan", "Reddy", "Iyer", "Gupta", "Nair", "Kulkarni", "Mehta", "Bhosale"]
        sources = ["99acres", "MagicBricks", "Meta Lead Ads", "Google Ads", "Website", "WhatsApp CTWA", "Walk-in", "Referral"]
        stages = ["new", "contacted", "qualified", "site_visit", "negotiation", "won"]
        cities = ["Nanded", "Pune", "Nashik", "Aurangabad", "Mumbai"]
        reqs = ["2400 sqft east-facing plot", "3BHK near school", "Villa with garden", "Investment plot under 30L",
                "Commercial shed near highway", "Farmhouse land 1 acre+"]
        contacts = []
        for i in range(26):
            name = f"{first[i % len(first)]} {last[(i * 5) % len(last)]}"
            phone = f"+9198{rng.randint(10000000, 99999999)}"
            email = f"{name.split()[0].lower()}.{i}@example.com"
            stage = stages[min(len(stages) - 1, int(rng.triangular(0, 5, 2)))]
            cid = nid("cnt")
            score = rng.randint(38, 96)
            contacts.append(
                {
                    "id": cid,
                    "tenant_id": t1,
                    "name": name,
                    "phone_enc": sec.seal(phone, aad=t1),
                    "phone_hash": sec.blind_index(phone, tenant_id=t1),
                    "email_enc": sec.seal(email, aad=t1),
                    "email_hash": sec.blind_index(email, tenant_id=t1),
                    "city": cities[i % len(cities)],
                    "source": sources[i % len(sources)],
                    "stage": stage,
                    "score": score,
                    "intent": rng.choice(["high", "medium", "low"]),
                    "budget_min": rng.choice([1_500_000, 2_500_000, 4_000_000, 7_000_000]),
                    "budget_max": rng.choice([3_000_000, 5_000_000, 8_000_000, 12_000_000]),
                    "requirement": reqs[i % len(reqs)],
                    "owner_id": [u_mgr["id"], u_agent["id"], u_owner["id"]][i % 3],
                    "language": rng.choice(["mr", "hi", "en"]),
                    "consent": {
                        "status": "granted" if i % 7 else "pending",
                        "purpose": "site visit follow-up",
                        "channel": sources[i % len(sources)],
                        "captured_at": now() - rng.randint(1, 60) * 86400,
                        "notice_version": "v3.1",
                        "evidence": f"form-{rng.randint(10000, 99999)}",
                    },
                    "tags": rng.sample(["hot", "nri", "investor", "first-home", "broker"], k=2),
                    "value": rng.choice([2_850_000, 4_400_000, 6_200_000, 9_400_000, 11_000_000]),
                    "created_at": now() - rng.randint(1, 120) * 86400,
                    "last_activity_at": now() - rng.randint(0, 14) * 86400,
                }
            )
        # Source history + speed-to-lead state, so the SLA board has real
        # material: most leads answered inside the window, a few fresh ones still
        # running, and two deliberately burned.
        for i, c in enumerate(contacts):
            c["sources"] = [{"slug": (c.get("source") or "Website").lower().replace(" lead ads", "").replace(" ", ""),
                             "label": c.get("source", "Website"), "campaign": "Sunrise Meadows — Phase 2",
                             "external_id": f"ext-{1000 + i}", "at": c["created_at"]}]
            c["sla"] = {"seconds": 300, "due_at": c["created_at"] + 300, "source": c["sources"][0]["slug"]}
            if c["stage"] != "new":
                c["first_response_at"] = c["created_at"] + rng.randint(45, 780)
        fresh = contacts[:3]
        for c in fresh:
            c["stage"] = "new"
            c["created_at"] = now() - rng.randint(40, 240)      # inside the 5-minute window
            c["sla"] = {"seconds": 300, "due_at": c["created_at"] + 300, "source": "99acres"}
            c["first_response_at"] = None
            c["source"] = "99acres"
            c["sources"] = [{"slug": "99acres", "label": "99acres", "campaign": "Nanded plots",
                             "external_id": f"99a-{7000 + len(c['id'])}", "at": c["created_at"]}]
        for c in contacts[3:5]:
            c["created_at"] = now() - 3 * 3600                  # burned: no reply for 3 hours
            c["sla"] = {"seconds": 300, "due_at": c["created_at"] + 300, "source": "magicbricks"}
            c["first_response_at"] = None
        # two contacts who replied straight away, to keep the average honest
        for c in contacts[5:7]:
            c["created_at"] = now() - 5400
            c["sla"] = {"seconds": 300, "due_at": c["created_at"] + 300, "source": "meta"}
            c["first_response_at"] = c["created_at"] + rng.randint(60, 260)
        self.db["contacts"] = contacts

        # second tenant contact (for cross-tenant isolation tests)
        self.db["contacts"].append(
            {
                "id": nid("cnt"),
                "tenant_id": t2,
                "name": "Metro Confidential Buyer",
                "phone_enc": sec.seal("+919900001111", aad=t2),
                "phone_hash": sec.blind_index("+919900001111", tenant_id=t2),
                "email_enc": sec.seal("buyer@metrorealty.app", aad=t2),
                "email_hash": sec.blind_index("buyer@metrorealty.app", tenant_id=t2),
                "city": "Pune",
                "source": "Website",
                "stage": "qualified",
                "score": 71,
                "consent": {"status": "granted", "purpose": "enquiry", "channel": "Website", "captured_at": now()},
                "tags": ["metro"],
                "value": 7_800_000,
                "created_at": now() - 5 * 86400,
                "last_activity_at": now() - 86400,
            }
        )

        # ---- deals ----
        deals = []
        for i, c in enumerate([c for c in contacts if c["stage"] in ("contacted", "qualified", "site_visit", "negotiation", "won")]):
            deals.append(
                {
                    "id": nid("deal"),
                    "tenant_id": t1,
                    "contact_id": c["id"],
                    "title": f"{c['name']} — {(c.get('requirement') or 'enquiry')[:28]}",
                    "stage": c["stage"],
                    "value": c["value"],
                    "probability": {"new": 10, "contacted": 20, "qualified": 45, "site_visit": 65, "negotiation": 80, "won": 100}[c["stage"]],
                    "expected_close": now() + rng.randint(5, 75) * 86400,
                    "property_id": rng.choice(props)["id"],
                    "owner_id": c.get("owner_id", u_mgr["id"]),
                    "created_at": now() - rng.randint(5, 90) * 86400,
                }
            )
        # Documents + commission on the deals that are far enough along to need
        # them; the stage gates in app/vertical.py read these.
        chain = ["KYC (Aadhaar/PAN)", "Booking form", "Sale agreement draft",
                 "Stamp duty receipt", "Registration appointment", "Registered sale deed"]
        doc_sets = {
            "": [],
            "token": chain[:2],
            "agreement": chain[:3],
            "registration": chain[:5],
            "won": chain[:6],
        }
        # A deterministic tail of money-stages: the demo must show the gates in
        # use, not just new→contacted. Random sampling above produced no `won`.
        for deal, stage, have in zip(reversed(deals), ("won", "registration", "agreement", "token"), (6, 4, 3, 2)):
            deal["stage"] = stage
            deal["probability"] = vt.PROBABILITY[stage]
            deal["_have"] = have
        for d in deals:
            names = doc_sets.get(d["stage"], [])[: d.pop("_have", 99)]
            d["documents"] = [
                {"name": n, "status": "verified" if d["stage"] == "won" else "received",
                 "at": d["created_at"] + 86400, "by": d.get("owner_id", "")}
                for n in names
            ]
            d["stage_history"] = [{"stage": d["stage"], "at": d["created_at"], "by": d.get("owner_id", ""), "forced": False}]
            if d["stage"] == "won":
                try:
                    d["commission"] = {
                        **vt.commission_breakdown(d, rate_pct=2.0, agent_share_pct=60),
                        "computed_at": now(),
                        "payout_status": rng.choice(["pending", "part_paid", "paid"]),
                    }
                except vt.StageError:
                    pass
        self.db["deals"] = deals

        # ---- site visits: the object the stage label was standing in for ----
        visits = []
        slot = now() + 3600
        for i, d in enumerate(deals):
            prop = next((p for p in props if p["id"] == d.get("property_id")), props[i % len(props)])
            if d["stage"] == "site_visit":
                status, when = ("confirmed" if i % 2 else "scheduled"), slot + i * 5400
            elif d["stage"] == "negotiation":
                status, when = "visited", now() - (i + 2) * 86400
            elif d["stage"] == "won":
                status, when = "visited", now() - (i + 9) * 86400
            elif d["stage"] == "qualified" and i % 3 == 0:
                status, when = "revisit", now() - (i + 1) * 86400
            else:
                continue
            visits.append(
                {
                    "id": nid("sv"),
                    "tenant_id": t1,
                    "contact_id": d["contact_id"],
                    "property_id": prop["id"],
                    "agent_id": d.get("owner_id", u_agent["id"]),
                    "scheduled_at": when,
                    "duration_min": 45,
                    "status": status,
                    "pickup": "Office cab, 10:15" if i % 4 == 0 else "",
                    "notes": "",
                    "outcome": {"visited": "shortlisted", "revisit": "wants family to see", "confirmed": "", "scheduled": ""}.get(status, ""),
                    "feedback": {"visited": "Liked the east-facing corner; asked about 7/12.", "revisit": "Comparing with a MagicBricks listing."}.get(status, ""),
                    "created_by": d.get("owner_id", ""),
                    "created_at": when - 2 * 86400,
                }
            )
        # a dropped visit and a future second slot, so the lifecycle is real data
        if visits:
            base = visits[0]
            visits.append({**base, "id": nid("sv"), "scheduled_at": now() + 3 * 86400,
                           "status": "scheduled", "outcome": "", "feedback": "", "created_at": now()})
            dropped = {**base, "id": nid("sv"), "scheduled_at": now() - 5 * 86400,
                       "status": "dropped", "outcome": "no-show", "feedback": "Client travelling; asked to re-plan."}
            visits.append(dropped)
        self.db["site_visits"] = visits

        # ---- channel partners ----
        # A partner is a *user* with role `channel_partner`, so it reuses the
        # session, CSRF and tenant machinery and can be revoked like any seat.
        cp1 = user(t1, "Nanded Property Mart", "partner@nandedproperty.in", "channel_partner", "Partner@12345!")
        cp2 = user(t1, "Sai Brokers", "partner@saibrokers.in", "channel_partner", "Partner@12345!")
        for cp, firm, pct in ((cp1, "Nanded Property Mart", 12.0), (cp2, "Sai Brokers", 10.0)):
            cp.update({
                "cp_firm": firm,
                "cp_commission_pct": pct,
                "cp_protection_days": 90,
                "cp_rera": f"AGENT/RERA/{rng.randint(1000, 9999)}",
                "cp_phone": f"+9198{rng.randint(10000000, 99999999)}",
            })
        self.db["users"].extend([cp1, cp2])

        # Registrations: two live claims, one pending approval, one rejected.
        cp_regs = []
        for i, (contact, cp, status, days_left) in enumerate((
            (contacts[8], cp1, "approved", 74),
            (contacts[11], cp2, "approved", 12),
            (contacts[16], cp1, "pending", 0),
            (contacts[19], cp2, "rejected", 0),
        )):
            reg = {
                "id": nid("cpr"),
                "tenant_id": t1,
                "cp_user_id": cp["id"],
                "cp_name": cp.get("cp_firm", cp["name"]),
                "contact_id": contact["id"],
                "contact_name": contact["name"],
                "status": status,
                "protection_until": now() + days_left * 86400 if status == "approved" else None,
                "decided_by": u_mgr["id"] if status in ("approved", "rejected") else None,
                "decided_at": now() - (10 - i) * 86400 if status in ("approved", "rejected") else None,
                "reason": "duplicate — buyer already in the house pipeline" if status == "rejected" else "",
                "created_at": now() - (14 - i) * 86400,
            }
            cp_regs.append(reg)
            contact["cp_user_id"] = cp["id"] if status == "approved" else contact.get("cp_user_id")
            contact["cp_registration_id"] = reg["id"] if status == "approved" else contact.get("cp_registration_id")
        self.db["cp_registrations"] = cp_regs

        # One closed deal credited to a partner, with its commission ledger
        # already carrying the partner's share, and a payout still pending.
        won = next((d for d in deals if d["stage"] == "won"), None)
        if won:
            won["commission"] = {
                **vt.commission_breakdown(won, rate_pct=2.0, agent_share_pct=60,
                                          channel_partner_pct=12.0, channel_partner_id=cp1["id"]),
                "computed_at": now() - 6 * 86400,
                "computed_by": u_mgr["id"],
                "payout_status": "pending",
            }
            won["cp_payout"] = {"status": "pending", "amount": won["commission"]["channel_partner"]}
        self.db["cp_payouts"] = [
            {
                "id": nid("cpp"),
                "tenant_id": t1,
                "cp_user_id": cp1["id"],
                "deal_id": won["id"] if won else "",
                "amount": (won or {}).get("commission", {}).get("channel_partner", 0),
                "status": "pending",
                "reference": "",
                "created_at": now() - 5 * 86400,
            }
        ] if won else []

        # ---- unit inventory ----
        units = []
        for prop in props[:4]:
            grid = inventory_rules.generate_units(
                floors=2, per_floor=4,
                base_price=float(prop.get("price") or 4_000_000),
                price_step=75_000,
                type_="2BHK" if prop.get("type") == "flat" else str(prop.get("type") or "plot").title(),
                area_sqft=float(prop.get("size_sqft") or 1200),
            )
            for i, u in enumerate(grid):
                units.append({
                    **u,
                    "id": nid("unt"),
                    "tenant_id": t1,
                    "property_id": prop["id"],
                    "label": f"{prop['title'].split('—')[-1].strip()} · {u['unit_no']}",
                    "history": [{"event": "created", "at": now() - 20 * 86400}],
                    "created_at": now() - 20 * 86400,
                })
        # three states in real data: one booked with a token, one on hold, one sold
        if units:
            won_deal = next((d for d in deals if d["stage"] == "won"), None)
            booking_unit = units[1]
            booking_unit.update({
                "status": "booked",
                "booking": {"deal_id": (won_deal or {}).get("id", ""), "contact_id": (won_deal or {}).get("contact_id", ""),
                            "at": now() - 9 * 86400, "token_amount": 100_000, "by": u_mgr["id"]},
                "history": [{"event": "booked", "by": u_mgr["id"], "at": now() - 9 * 86400, "deal_id": (won_deal or {}).get("id", "")}],
            })
            hold_deal = next((d for d in deals if d["stage"] == "negotiation"), None)
            if hold_deal:
                units[2].update({
                    "status": "blocked",
                    "block": {"deal_id": hold_deal["id"], "contact_id": hold_deal.get("contact_id", ""),
                              "by": u_agent["id"], "at": now() - 6 * 3600, "until": now() + 42 * 3600,
                              "hours": 48, "reason": "price hold while the family decides"},
                    "history": [{"event": "blocked", "by": u_agent["id"], "at": now() - 6 * 3600, "deal_id": hold_deal["id"]}],
                })
                hold_deal["unit_id"] = units[2]["id"]
            if won_deal:
                won_deal["unit_id"] = booking_unit["id"]
            units[3].update({
                "status": "sold",
                "sold": {"deal_id": (won_deal or {}).get("id", ""), "at": now() - 5 * 86400, "by": u_mgr["id"]},
                "history": [{"event": "sold", "by": u_mgr["id"], "at": now() - 5 * 86400}],
            })
        self.db["units"] = units

        # ---- money ----
        # A demo that shows a "collected ₹0" money screen proves nothing. These rows
        # are the shape the reconciliation actually writes: a token payment that is
        # custody (receipt, no GST) and a brokerage invoice that is our supply
        # (18% GST split, 5% TDS withheld, net receivable).
        self.db["payments"] = []
        self.db["invoices"] = []
        booked = next((u for u in units if u.get("status") == "booked"), None)
        booked_deal = next((d for d in deals if d.get("id") == (booked or {}).get("booking", {}).get("deal_id")), None)
        if booked and booked_deal:
            tok = int((booked.get("booking") or {}).get("token_amount") or 100_000)
            self.db["payments"].append({
                "id": nid("pay"), "tenant_id": t1, "kind": "token", "status": "succeeded",
                "amount_paise": tok * 100, "amount": tok, "currency": "INR",
                "provider": "razorpay", "payment_id": f"pay_demo{booked['id'][-6:]}",
                "event_id": f"evt_demo{booked['id'][-6:]}", "method": "upi",
                "deal_id": booked_deal["id"], "unit_id": booked["id"],
                "contact_id": booked_deal.get("contact_id", ""),
                "at": now() - 9 * 86400, "reconciled_at": now() - 9 * 86400,
                "note": "Booking amount held for the seller",
            })
            self.db["invoices"].append({
                **money_rules.token_receipt(
                    number=money_rules.invoice_number("SLD", 1, now() - 9 * 86400),
                    issued_at=now() - 9 * 86400, tenant={"name": "Sunrise Land Developers"},
                    client={"name": next((c["name"] for c in self.db["contacts"]
                                          if c["id"] == booked_deal.get("contact_id")), "Buyer")},
                    deal=booked_deal, unit=booked, amount_paise=tok * 100,
                    payment_id=self.db["payments"][-1]["payment_id"]),
                "id": nid("inv"), "tenant_id": t1, "created_at": now() - 9 * 86400,
            })
        won2 = next((d for d in deals if d["stage"] == "won"), None)
        if won2:
            try:
                breakdown = vt.commission_breakdown(won2, rate_pct=2.0, agent_share_pct=60)
            except Exception:
                breakdown = {"gross": 220_000}
            client = next((c for c in self.db["contacts"] if c["id"] == won2.get("contact_id")), {})
            inv = money_rules.commission_invoice(
                number=money_rules.invoice_number("SLD", 2, now() - 4 * 86400),
                issued_at=now() - 4 * 86400,
                tenant={"name": "Sunrise Land Developers",
                         "settings": {"gstin": "27AABCU9603R1ZM", "state": "Maharashtra"}},
                client={**client, "state": "Maharashtra", "is_business": True},
                deal=won2, breakdown=breakdown,
            )
            inv.update({"id": nid("inv"), "tenant_id": t1, "created_at": now() - 4 * 86400,
                        "paid_paise": 0, "status": "issued"})
            self.db["invoices"].append(inv)

        # ---- conversations ----
        convs = []
        snippets = [
            ("whatsapp", "in", "Plot 47 ची किंमत किती आहे? Documents तयार आहेत का?"),
            ("whatsapp", "out", "नमस्कार! Plot 47 — ₹28.5L, 2400 sqft, east facing. 7/12 + NA order सोबत पाठवतो. Site visit कधी convenient आहे?"),
            ("voice", "in", "I saw the ad on Facebook, is the villa in Wagholi still available?"),
            ("voice", "out", "Yes — Villa B4 at Sunrise Meadows is available. Shall I book a site visit this Saturday, 11 am?"),
            ("sms", "out", "Reminder: your site visit is tomorrow 11:00 at Sunrise Greens, Taroda. Reply STOP to opt out."),
            ("webchat", "in", "Do you have anything under 25 lakh with clear title near Nanded?"),
            ("whatsapp", "in", "Please share the RERA number and encumbrance certificate."),
        ]
        for i, c in enumerate(contacts[:14]):
            msgs = []
            for j, (ch, direction, text) in enumerate(snippets[i % len(snippets):] + snippets[: i % len(snippets)]):
                if j > 4:
                    break
                msgs.append(
                    {
                        "id": nid("msg"),
                        "dir": direction,
                        "channel": ch,
                        "text": text,
                        "at": now() - (24 - j) * 3600 - i * 60,
                        "ai": direction == "out" and j % 2 == 0,
                        "status": "delivered" if direction == "out" else "received",
                        "guardrail": "allow",
                    }
                )
            convs.append(
                {
                    "id": nid("cnv"),
                    "tenant_id": t1,
                    "contact_id": c["id"],
                    "channel": msgs[0]["channel"],
                    "unread": 1 if i % 3 == 0 else 0,
                    "messages": msgs,
                    "created_at": msgs[0]["at"],
                    "last_at": msgs[-1]["at"],
                }
            )
        self.db["conversations"] = convs

        # ---- activities / tasks ----
        acts = []
        for i, c in enumerate(contacts[:20]):
            for j, (typ, text) in enumerate(
                [
                    ("ai_call", "AI voice agent placed qualification call · 2m 14s · outcome: site-visit interest"),
                    ("whatsapp", "Doc pack sent (7/12, NA order, layout) · template: doc_pack_v4"),
                    ("note", "Prefers Marathi. Works night shift, call after 6 pm."),
                    ("stage", f"Moved to {c['stage']}"),
                ]
            ):
                acts.append(
                    {
                        "id": nid("act"),
                        "tenant_id": t1,
                        "contact_id": c["id"],
                        "type": typ,
                        "text": text,
                        "actor": "AI · Meera" if typ in ("ai_call",) else "Priya Kulkarni",
                        "at": now() - (i * 7 + j) * 3600,
                    }
                )
        self.db["activities"] = acts
        self.db["tasks"] = [
            {
                "id": nid("tsk"),
                "tenant_id": t1,
                "title": txt,
                "due_at": now() + rng.randint(1, 5) * 86400,
                "owner_id": u_agent["id"],
                "done": False,
                "priority": rng.choice(["high", "medium", "low"]),
                "contact_id": contacts[i]["id"],
            }
            for i, txt in enumerate(
                [
                    "Send encumbrance certificate for Plot 47",
                    "Call NRI buyer at 7 pm IST (timezone-safe)",
                    "Follow up on home loan pre-approval",
                    "Prepare negotiation sheet — Villa B4",
                    "Confirm Saturday site-visit cab pickup",
                ]
            )
        ]

        # ---- agents ----
        def agent(name, role, dept, model, channels, scopes, tools, skills, status="active", temp=0.3):
            return {
                "id": nid("agt"),
                "tenant_id": t1,
                "name": name,
                "role": role,
                "department": dept,
                "model": model,
                "temperature": temp,
                "status": status,
                "channels": channels,
                "scopes": scopes,
                "tools": tools,
                "skills": skills,
                "guardrails": {
                    "human_approval_for": ["bulk_message", "price_concession", "delete_record", "refund"],
                    "max_discount_pct": 3,
                    "spend_cap_usd": 250,
                    "pii_redaction": True,
                    "fail_closed": True,
                    "claim_set": "rera_registered_only",
                },
                "spend_usd": round(rng.uniform(8, 80), 2),
                "runs": rng.randint(120, 2400),
                "success_rate": round(rng.uniform(0.72, 0.94), 3),
                "created_at": now() - rng.randint(20, 180) * 86400,
            }

        self.db["agents"] = [
            agent(
                "Meera — Voice Qualifier",
                "Outbound voice qualification & appointment setting",
                "Sales",
                "gpt-realtime-2.1 / sarvam-bulbul-v3",
                ["voice", "whatsapp"],
                ["contacts:read", "contacts:write", "calendar:write", "docs:send", "messages:send", "templates:read", "voice:outbound"],
                ["crm.lookup_contact", "calendar.offer_slots", "whatsapp.send_template", "docs.assemble_pack", "voice.disclose_ai"],
                [
                    {"id": "skl_1", "name": "Marathi code-switch handling", "version": 4, "status": "promoted", "lift": 0.18, "origin": "human_authored"},
                    {"id": "skl_2", "name": "Plot-number disambiguation by landmark", "version": 3, "status": "promoted", "lift": 0.22, "origin": "learned"},
                    {"id": "skl_3", "name": "Pre-visit due-diligence doc handoff", "version": 2, "status": "promoted", "lift": 0.31, "origin": "learned"},
                ],
            ),
            agent(
                "Arjun — WhatsApp Nurture",
                "Multi-touch nurture sequences, objection handling, re-engagement",
                "Marketing",
                "claude-sonnet-4.6",
                ["whatsapp", "sms", "webchat"],
                ["contacts:read", "contacts:write", "messages:send", "templates:read", "knowledge:read"],
                ["crm.lookup_contact", "whatsapp.send_template", "crm.log_activity", "knowledge.search"],
                [
                    {"id": "skl_4", "name": "Budget-objection reframing", "version": 5, "status": "promoted", "lift": 0.14, "origin": "learned"},
                    {"id": "skl_5", "name": "Hindi/Devanagari tone calibration", "version": 2, "status": "promoted", "lift": 0.09, "origin": "human_authored"},
                ],
            ),
            agent(
                "Kabir — Lead Intelligence",
                "Source scoring, dedupe, consent validation, enrichment",
                "Growth",
                "gemini-3-flash",
                ["system"],
                ["contacts:read", "analytics:read", "leads:import", "consent:read"],
                ["crm.dedupe", "scoring.model", "consent.validate", "analytics.query"],
                [
                    {"id": "skl_6", "name": "Portal lead quality classifier", "version": 6, "status": "promoted", "lift": 0.27, "origin": "learned"},
                ],
            ),
            agent(
                "Naina — Ops Copilot",
                "Deal-desk automation, document checklists, RERA disclosure guard",
                "Operations",
                "gpt-5.4-orion",
                ["system"],
                ["deals:read", "deals:write", "docs:read", "compliance:read", "tasks:write"],
                ["crm.update_stage", "docs.checklist", "compliance.rera_check", "tasks.create"],
                [
                    {"id": "skl_7", "name": "Missing-document detection", "version": 3, "status": "promoted", "lift": 0.19, "origin": "learned"},
                ],
            ),
            agent(
                "JEV — Market Research",
                "YouTube/market research scoring → content briefs (research-only, no publishing)",
                "Marketing",
                "jev-ultrafast",
                ["system"],
                ["research:read", "content:draft"],
                ["youtube.search", "video.score", "content.brief", "web.fetch_safe"],
                [
                    {"id": "skl_8", "name": "Local-intent keyword scoring", "version": 2, "status": "candidate", "lift": 0.0, "origin": "learned"},
                ],
            ),
        ]

        # ---- learning proposals (human-guardrail queue) ----
        self.db["proposals"] = [
            {
                "id": nid("prp"),
                "tenant_id": t1,
                "agent_id": self.db["agents"][0]["id"],
                "title": "Ask for plot budget before quoting price",
                "observation": "Across 412 calls, quoting price in turn 1 correlated with 22% lower site-visit booking. Buyers disengaged when price exceeded stated budget.",
                "proposal": "Insert skill 'budget-first framing' before price quoting: confirm budget band, then anchor on the plot within band.",
                "evidence": ["call_transcripts:412", "booking_rate_delta:-0.22", "p<0.01"],
                "risk": "low",
                "status": "pending",
                "created_at": now() - 8 * 3600,
                "skill_diff": {"add": "budget_first_framing", "version_bump": "Meera v2.4 → v2.5"},
            },
            {
                "id": nid("prp"),
                "tenant_id": t1,
                "agent_id": self.db["agents"][1]["id"],
                "title": "Offer document pack before second follow-up",
                "observation": "Leads receiving the doc pack within 6h replied 31% more often than those receiving a second price reminder.",
                "proposal": "Reorder nurture step 2 to send doc pack before the price reminder; requires docs:send scope.",
                "evidence": ["messages:2210", "reply_rate_delta:+0.31"],
                "risk": "medium",
                "status": "pending",
                "created_at": now() - 26 * 3600,
                "skill_diff": {"scope_change": ["docs:send"], "sequence_edit": "nurture_v7 → v8"},
            },
            {
                "id": nid("prp"),
                "tenant_id": t1,
                "agent_id": self.db["agents"][4]["id"],
                "title": "Auto-publish 3 YouTube shorts per week",
                "observation": "Draft pipeline is stable; drafts pass RERA claim-set validation 98% of the time.",
                "proposal": "Enable auto-publish without human review.",
                "evidence": ["drafts:64"],
                "risk": "high",
                "status": "rejected",
                "created_at": now() - 72 * 3600,
                "decided_by": "Aarav Deshmukh",
                "decided_at": now() - 48 * 3600,
                "decision_note": "Rejected: brand-safety + RERA advertising liability. Publishing stays human-gated.",
                "skill_diff": {"remove_approval": "publish"},
            },
        ]

        # ---- automations: the runtime's playbooks, installed (`docs/23`) ----
        # These are real steps the executor can run, not descriptions: the previous
        # seed held strings like "consent.validate" that no runtime could execute,
        # which is exactly how a product ships a promise it cannot keep.
        automations = []
        fleet = [
            ("instant-response", True, 1841, 1800),
            ("visit-doc-pack", True, 612, 7200),
            ("visit-reminder-24h", True, 2403, 5400),
            ("no-show-recovery", True, 87, 3 * 86400),
            ("hold-expiry-nudge", True, 214, 3600),
            ("stale-lead-reengagement", False, 96, 9 * 86400),
            ("missed-call-textback", True, 523, 9000),
            ("review-request", True, 64, 5 * 86400),
        ]
        for slug, enabled, runs, last_run in fleet:
            doc = automation_rules.install_playbook(slug)
            automations.append({
                "id": nid("aut"),
                "tenant_id": t1,
                **doc,
                "enabled": enabled,
                "runs": runs,
                "last_run": now() - last_run,
            })
        self.db["automations"] = automations

        # ---- social / ads ----
        self.db["social"] = [
            {"id": nid("soc"), "tenant_id": t1, "channel": "instagram", "text": "Why Nanded's Taroda belt is the smartest ₹30L land bet of 2027 — 5 data points. 🧵", "media_hint": "carousel/5", "scheduled_at": now() + 6 * 3600, "status": "scheduled", "engagement": None},
            {"id": nid("soc"), "tenant_id": t1, "channel": "youtube", "text": "Site walk: Sunrise Greens Phase 3 (4K, Marathi voiceover)", "media_hint": "video/6m", "scheduled_at": now() + 2 * 86400, "status": "draft", "engagement": None},
            {"id": nid("soc"), "tenant_id": t1, "channel": "linkedin", "text": "RERA compliance is a sales asset, not a tax. Here's the doc pack we hand every buyer.", "media_hint": "document", "scheduled_at": now() - 86400, "status": "published", "engagement": {"likes": 214, "comments": 31, "shares": 12}},
            {"id": nid("soc"), "tenant_id": t1, "channel": "whatsapp_channel", "text": "This week at Sunrise: 3 plots held, 2 site visits open for Saturday.", "media_hint": "image", "scheduled_at": now() + 26 * 3600, "status": "scheduled", "engagement": None},
        ]
        self.db["campaigns"] = [
            {"id": nid("cmp"), "tenant_id": t1, "platform": "google_ads", "name": "Nanded Plots — Search (High Intent)", "objective": "leads", "budget_daily": 4500, "spend": 182_400, "leads": 186, "cpl": 981, "status": "active", "roas": 4.1},
            {"id": nid("cmp"), "tenant_id": t1, "platform": "meta_ads", "name": "Pune Villas — Advantage+ Lead Gen", "objective": "leads", "budget_daily": 6200, "spend": 241_800, "leads": 214, "cpl": 1130, "status": "active", "roas": 3.2},
            {"id": nid("cmp"), "tenant_id": t1, "platform": "meta_ads", "name": "NRI Investors — Lookalike 1%", "objective": "leads", "budget_daily": 3000, "spend": 88_200, "leads": 41, "cpl": 2151, "status": "paused", "roas": 1.4},
        ]

        # ---- funnels & website ----
        self.db["funnels"] = [
            {
                "id": nid("fnl"),
                "tenant_id": t1,
                "name": "Nanded Plots — Site Visit Funnel",
                "slug": "nanded-plots",
                "headline": "Own a clear-title plot in Nanded from ₹24L",
                "sub": "RERA-registered layout • 7/12 + NA order in hand • 48-hour price hold",
                "cta": "Book a free site visit",
                "sections": [
                    {"type": "hero", "title": "Sunrise Greens, Taroda", "body": "120 plots, sanctioned layout, 40% open space, 9m roads."},
                    {"type": "proof", "title": "Documents before you visit", "body": "7/12 extract, NA order, sanctioned layout, title search, encumbrance certificate."},
                    {"type": "form", "title": "Get the doc pack + a site visit slot", "body": "Consent-first: we call only between 09:00–21:00 IST. Opt out anytime."},
                ],
                "published": True,
                "visits": 4821,
                "submissions": 312,
                "created_at": now() - 60 * 86400,
            },
            {
                "id": nid("fnl"),
                "tenant_id": t1,
                "name": "Pune Villa — Doc-Pack Magnet",
                "slug": "pune-villa-docs",
                "headline": "The 5 documents every Pune villa buyer must check",
                "sub": "Get the checklist we use on every Sunrise Meadows transaction",
                "cta": "Send me the checklist",
                "sections": [
                    {"type": "hero", "title": "Sunrise Meadows, Wagholi", "body": "24 villas, clubhouse, 12 min from Kharadi IT park."},
                    {"type": "form", "title": "Where should we send it?", "body": "WhatsApp preferred. Consent captured with notice v3.1."},
                ],
                "published": True,
                "visits": 2214,
                "submissions": 148,
                "created_at": now() - 30 * 86400,
            },
        ]
        self.db["pages"] = [
            {"id": nid("pag"), "tenant_id": t1, "slug": "home", "title": "Sunrise Land Developers", "body": "Clear-title land, plotted layouts and villas across Maharashtra since 2009. 14 projects delivered, 100% RERA-registered.", "published": True},
            {"id": nid("pag"), "tenant_id": t1, "slug": "projects", "title": "Projects", "body": "Sunrise Greens (Nanded) · Sunrise Meadows (Pune) · Godavari Heights (Nanded) · Riverside Farms (Nashik)", "published": True},
            {"id": nid("pag"), "tenant_id": t1, "slug": "about", "title": "About", "body": "A family-run developer that hands over the document pack before the price sheet.", "published": True},
        ]

        # ---- integrations ----
        self.db["integrations"] = [
            {"id": nid("int"), "tenant_id": t1, "slug": "whatsapp_cloud", "name": "WhatsApp Cloud API", "category": "Messaging", "status": "connected", "connected_at": now() - 90 * 86400, "scopes": ["messages", "templates"], "last_sync": now() - 600, "health": "healthy"},
            {"id": nid("int"), "tenant_id": t1, "slug": "google_ads", "name": "Google Ads (offline conversions)", "category": "Ads", "status": "connected", "connected_at": now() - 80 * 86400, "scopes": ["campaigns", "conversions"], "last_sync": now() - 1800, "health": "healthy"},
            {"id": nid("int"), "tenant_id": t1, "slug": "meta_lead_ads", "name": "Meta Lead Ads", "category": "Lead source", "status": "connected", "connected_at": now() - 75 * 86400, "scopes": ["leads"], "last_sync": now() - 300, "health": "healthy"},
            {"id": nid("int"), "tenant_id": t1, "slug": "razorpay", "name": "Razorpay (booking tokens)", "category": "Payments", "status": "connected", "connected_at": now() - 60 * 86400, "scopes": ["payments"], "last_sync": now() - 9000, "health": "healthy"},
            {"id": nid("int"), "tenant_id": t1, "slug": "sarvam", "name": "Sarvam AI (STT/TTS · mr/hi/en)", "category": "Voice", "status": "connected", "connected_at": now() - 55 * 86400, "scopes": ["stt", "tts"], "last_sync": now() - 120, "health": "healthy"},
            {"id": nid("int"), "tenant_id": t1, "slug": "zoho_books", "name": "Zoho Books", "category": "Finance", "status": "available", "scopes": ["invoices"], "health": "n/a"},
            {"id": nid("int"), "tenant_id": t1, "slug": "slack", "name": "Slack (deal desk alerts)", "category": "Comms", "status": "available", "scopes": ["chat:write"], "health": "n/a"},
            {"id": nid("int"), "tenant_id": t1, "slug": "mcp_gateway", "name": "MCP Gateway (agent tools)", "category": "AI", "status": "connected", "connected_at": now() - 20 * 86400, "scopes": ["tools:read", "tools:invoke"], "last_sync": now() - 60, "health": "healthy"},
            {"id": nid("int"), "tenant_id": t1, "slug": "evolution_api", "name": "Evolution API (WhatsApp bridge, self-hosted)", "category": "Messaging", "status": "available", "scopes": ["messages"], "health": "n/a"},
        ]

        # ---- research jobs ----
        self.db["research"] = [
            {
                "id": nid("rsc"),
                "tenant_id": t1,
                "topic": "plots in Nanded for investment 2027",
                "engine": "JEV (research-only)",
                "status": "complete",
                "created_at": now() - 2 * 86400,
                "videos": [
                    {"title": "Nanded real estate 2027: 5 growth corridors", "channel": "MH Realty Insights", "views": 184_000, "score": 87, "intent": "high", "angle": "data-led growth corridors"},
                    {"title": "Plot vs flat in tier-2 Maharashtra", "channel": "Paisa Pundit", "views": 96_400, "score": 79, "intent": "medium", "angle": "comparison, ROI framing"},
                    {"title": "How to verify 7/12 and NA order before buying land", "channel": "Legal Land India", "views": 312_000, "score": 91, "intent": "high", "angle": "due-diligence authority"},
                ],
                "brief": "Series: 'Verify before you visit' — 5 shorts (45–60s) on 7/12, NA order, encroachment, road width, encumbrance. CTA: doc-pack funnel. Publish windows Tue/Thu/Sat 19:30 IST. Human approval required before publish.",
                "recommendation": "Lead with due-diligence authority content; local-intent keywords score 2.4× higher than price-led videos.",
            }
        ]

        # ---- webhooks ----
        self.db["webhooks"] = [
            {"id": nid("whk"), "tenant_id": t1, "url": "https://hooks.zapier.com/hooks/catch/000/demo", "events": ["contact.created", "deal.stage_changed", "appointment.confirmed"], "secret_hint": "whsec_••••7f2a", "active": True, "created_at": now() - 30 * 86400},
        ]
        self.db["deliveries"] = [
            {"id": nid("dlv"), "tenant_id": t1, "event": "contact.created", "status": "delivered", "attempts": 1, "code": 200, "at": now() - 3600},
            {"id": nid("dlv"), "tenant_id": t1, "event": "deal.stage_changed", "status": "dead_letter", "attempts": 6, "code": 503, "at": now() - 7200},
        ]

        self.db["runs"] = []

        # ---- audit seed ----
        for action, actor in [
            ("tenant.created", "system"),
            ("user.invited", "Aarav Deshmukh"),
            ("agent.skill.promoted", "Aarav Deshmukh"),
            ("settings.compliance.updated", "Aarav Deshmukh"),
            ("integration.connected", "Priya Kulkarni"),
        ]:
            self.audit(t1, actor, action, target="seed", meta={"seed": True})

    # ---------------- derived analytics ----------------
    def analytics(self, tenant_id: str) -> dict:
        """Twelve months of deterministic operational metrics for the tenant."""
        rng = random.Random(hash(tenant_id) % 10_000)
        months = ["Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
        base = 210
        leads, visits, bookings, revenue = [], [], [], []
        for i in range(12):
            seasonal = 1 + 0.18 * ((i % 6) / 6 - 0.25)
            growth = 1 + 0.035 * i
            l = int(base * seasonal * growth * rng.uniform(0.92, 1.09))
            v = int(l * rng.uniform(0.19, 0.29))
            b = int(v * rng.uniform(0.22, 0.34))
            leads.append(l)
            visits.append(v)
            bookings.append(b)
            revenue.append(int(b * rng.uniform(2.6e6, 4.4e6)))
        return {
            "months": months,
            "leads": leads,
            "site_visits": visits,
            "bookings": bookings,
            "revenue": revenue,
            "funnel": [
                {"stage": "Captured", "count": sum(leads)},
                {"stage": "Consented", "count": int(sum(leads) * 0.91)},
                {"stage": "AI-qualified", "count": int(sum(leads) * 0.62)},
                {"stage": "Site visit", "count": sum(visits)},
                {"stage": "Negotiation", "count": int(sum(visits) * 0.41)},
                {"stage": "Booked", "count": sum(bookings)},
            ],
            "sources": [
                {"name": "99acres", "leads": 412, "cpl": 740, "conversion": 0.11},
                {"name": "MagicBricks", "leads": 366, "cpl": 690, "conversion": 0.09},
                {"name": "Meta Lead Ads", "leads": 520, "cpl": 1130, "conversion": 0.14},
                {"name": "Google Ads", "leads": 286, "cpl": 981, "conversion": 0.18},
                {"name": "WhatsApp CTWA", "leads": 233, "cpl": 310, "conversion": 0.21},
                {"name": "Organic / SEO", "leads": 174, "cpl": 0, "conversion": 0.13},
                {"name": "Referral", "leads": 96, "cpl": 0, "conversion": 0.27},
                {"name": "Walk-in", "leads": 71, "cpl": 0, "conversion": 0.31},
            ],
        }


STORE = Store()
