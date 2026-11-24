"""
The property taxonomy — five categories with five different forms.

Researched from what Indian portals and park operators actually publish
(`docs/22-PROPERTY-TAXONOMY.md`). The point is not to have many fields; it is that
a ₹45 lakh 2BHK and a 12 m clear-height warehouse have **different required
fields, different units and different price bases**, and a schema that treats them
as one row with sixty nullable columns silently accepts rubbish: a warehouse with
no clear height, an apartment with no bedrooms, a plot measured in bigha.

Everything here is declarative and pure: the API serves `GET /taxonomy` straight
from these tables so forms are generated from the server, and validation,
unit conversion and requirement scoring all read the same definitions.
"""
from __future__ import annotations

from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
# Everything normalises to square feet, because that is the only unit a broker
# and a bank statement agree on.
AREA_UNITS: dict[str, float] = {
    "sqft": 1.0,
    "sqm": 10.7639,
    "sqyd": 9.0,
    "acre": 43560.0,
    "hectare": 107639.0,
    "are": 1076.39,
    "guntha": 1089.0,       # 1 acre = 40 guntha (Maharashtra)
    "cent": 435.6,          # 1 acre = 100 cent (Tamil Nadu / Kerala)
}
REFUSED_UNITS: dict[str, str] = {
    "bigha": "Bigha varies by district (roughly 27,000–43,560 sqft). Record the area in "
             "sqft, acre or hectare instead — a silent conversion would be wrong by acres.",
}

PRICE_BASES = ("total", "per_sqft", "per_month", "per_month_per_sqft", "per_annum",
               "per_seat_month", "per_acre")

DEAL_KINDS = ("sale", "rent", "lease", "leave_license")


def inr(amount: float | None) -> str:
    """₹80,00,000, not ₹8,000,000 — Indian digit grouping, as every portal prints it."""
    if amount is None:
        return "—"
    whole = f"{int(round(amount))}"
    negative = whole.startswith("-")
    whole = whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return ("-" if negative else "") + "₹" + whole


class TaxonomyError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------
def _f(label: str, kind: str = "str", *, unit: str = "", options: tuple[str, ...] = (),
       required: bool = False, group: str = "details", help_: str = "", maximum: float = 0) -> dict:
    return {"label": label, "type": kind, "unit": unit, "options": list(options),
            "required": required, "group": group, "help": help_, "max": maximum}


FACING = ("East", "West", "North", "South", "North-East", "North-West", "South-East", "South-West")
FURNISHING = ("Unfurnished", "Semi-Furnished", "Furnished")
OWNERSHIP = ("Freehold", "Leasehold", "Power of Attorney")
POSSESSION = ("Ready to Move", "Under Construction", "New Launch")
FITOUT = ("Bare Shell", "Warm Shell", "Fully Fitted", "Plug and Play")

# Fields shared by every category, kept in one place so they cannot drift.
COMMON = {
    "rera_id": _f("RERA registration", group="compliance",
                  help_="State RERA project/agent registration number, verbatim."),
    "approved_by": _f("Approving authority", group="compliance"),
    "possession": _f("Possession", "enum", options=POSSESSION, group="availability"),
    "age_years": _f("Age of construction (years)", "int", group="building", maximum=150),
    "ownership": _f("Ownership", "enum", options=OWNERSHIP, group="compliance"),
    "parking": _f("Parking", group="building", help_='e.g. "2 covered", "open", "none"'),
    "power_backup": _f("Power backup", "bool", group="building"),
    "amenities": _f("Amenities", "multi", group="amenities"),
    "notes": _f("Notes", group="details"),
}

RESIDENTIAL = {
    "bedrooms": _f("Bedrooms (BHK)", "int", required=True, group="layout", maximum=20),
    "bathrooms": _f("Bathrooms", "int", required=True, group="layout", maximum=20),
    "balconies": _f("Balconies", "int", group="layout", maximum=10),
    "furnishing": _f("Furnishing", "enum", options=FURNISHING, group="layout"),
    "floor": _f("Floor", group="building"),
    "total_floors": _f("Floors in building", "int", group="building", maximum=120),
    "facing": _f("Facing", "enum", options=FACING, group="layout"),
    "overlooking": _f("Overlooking", group="layout"),
    "tenant_preference": _f("Tenant preference", "enum",
                            options=("Family", "Bachelors", "Company", "Any"), group="rental"),
    "maintenance_amount": _f("Maintenance (₹)", "int", group="rental"),
    "maintenance_period": _f("Maintenance period", "enum", options=("Monthly", "Quarterly", "Annual"), group="rental"),
    "security_deposit": _f("Security deposit (₹)", "int", group="rental"),
    "available_from": _f("Available from", group="availability"),
    "water_status": _f("Water supply", group="building"),
    "electricity_status": _f("Electricity", group="building"),
    "booking_amount": _f("Booking amount (₹)", "int", group="pricing"),
    **COMMON,
}

COMMERCIAL = {
    # deal_kind is a top-level property field (sale vs lease), not an attribute — it
    # drives which price basis is even legal, so it is validated before attributes.
    "fitout": _f("Fit-out", "enum", options=FITOUT, required=True, group="building"),
    "grade": _f("Building grade", "enum", options=("Grade A", "Grade B", "Grade C"), group="building"),
    "seats": _f("Seating capacity", "int", group="layout"),
    "cabins": _f("Cabins", "int", group="layout"),
    "meeting_rooms": _f("Meeting rooms", "int", group="layout"),
    "washrooms": _f("Washrooms", group="layout", help_="private / shared / public counts"),
    "pantry": _f("Pantry", "enum", options=("Private", "Shared", "None"), group="layout"),
    "conference_room": _f("Conference room", "bool", group="layout"),
    "reception": _f("Reception area", "bool", group="layout"),
    "central_ac": _f("Central AC", "bool", group="building"),
    "lifts": _f("Lifts", "int", group="building", maximum=40),
    "floor": _f("Floor", group="building"),
    "total_floors": _f("Floors in building", "int", group="building", maximum=120),
    "frontage_ft": _f("Frontage (ft)", "float", group="layout"),
    "location_type": _f("Location type", "enum",
                        options=("High Street", "Mall", "Retail Complex", "Business Park", "IT Park", "SEZ"),
                        group="building"),
    "footfall_position": _f("Footfall position", group="building",
                            help_="e.g. near entrance / elevator / escalator"),
    "suitable_for": _f("Suitable for", "multi", group="details"),
    "lock_in_months": _f("Lock-in (months)", "int", group="rental", maximum=120),
    "deposit_months": _f("Security deposit (months of rent)", "int", group="rental", maximum=36),
    "escalation_pct": _f("Annual escalation (%)", "float", group="rental", maximum=50),
    "cam_per_sqft_month": _f("CAM (₹/sqft/month)", "float", group="rental"),
    "power_backup_charge": _f("Power backup charge (₹/month)", "int", group="rental"),
    **COMMON,
}

INDUSTRIAL = {
    "clear_height_m": _f("Clear height (m)", "float", required=True, group="building", maximum=40,
                         help_="To the lowest obstruction — sprinkler runs, ducts — not the ridge."),
    "floor_load_mt_sqm": _f("Floor load (MT/sqm)", "float", required=True, group="building", maximum=50),
    "flooring": _f("Flooring", "enum", options=("FM2 Superflat", "VDF", "Trimix", "IPS/PQC", "PCC"),
                   group="building"),
    "dock_levellers": _f("Dock levellers", "int", group="building", maximum=200),
    "dock_height_m": _f("Dock height (m)", "float", group="building", maximum=5),
    "truck_apron_m": _f("Truck apron depth (m)", "float", group="building", maximum=200),
    "column_spacing": _f("Column spacing", group="building", help_='e.g. "12m x 12m"'),
    "fire_system": _f("Fire system", "enum", options=("ESFR Sprinklers", "Sprinklers + Hydrant", "Hydrant only", "None"),
                      group="compliance"),
    "power_kva": _f("Power (kVA)", "int", group="building", maximum=1_000_000),
    "plot_area_acres": _f("Plot area (acres)", "float", group="area", maximum=10_000),
    "highway_distance_km": _f("Distance to highway (km)", "float", group="location"),
    "container_access": _f("40-ft container access", "bool", group="building"),
    "warehouse_grade": _f("Warehouse grade", "enum", options=("Grade A", "Grade B", "Grade C"), group="building"),
    **COMMON,
}

LAND = {
    "land_use": _f("Land use", "enum", required=True, group="compliance",
                   options=("Residential Plot", "Commercial Plot", "Industrial Plot", "NA Plot",
                            "Agricultural", "Farm Land")),
    "survey_number": _f("Survey / gat number", group="compliance"),
    "occupant_class": _f("Occupant class", "enum", options=("Class I", "Class II", "Government lessee", "Not applicable"),
                         group="compliance", help_="Class II needs Collector permission to transfer."),
    "encumbrance": _f("Encumbrances (itar hakk)", group="compliance"),
    "na_order": _f("NA order", "bool", group="compliance"),
    "irrigation": _f("Irrigation", "enum", options=("Canal", "Borewell", "Well", "Lift", "Rainfed", "None"),
                     group="building"),
    "soil_type": _f("Soil type", group="building"),
    "crops": _f("Crops", group="details"),
    "road_access": _f("Road access", group="location"),
    "fencing": _f("Fencing", "bool", group="building"),
    "corner_plot": _f("Corner plot", "bool", group="layout"),
    "facing": _f("Facing", "enum", options=FACING, group="layout"),
    **COMMON,
}

HOSPITALITY = {
    "keys": _f("Keys / rooms", "int", group="layout", maximum=2000),
    "beds": _f("Beds", "int", group="layout", maximum=5000),
    "banquet_seating": _f("Banquet seating", "int", group="layout", maximum=5000),
    "star_class": _f("Star classification", "enum", options=("5 Star", "4 Star", "3 Star", "Budget", "Unclassified"),
                     group="compliance"),
    "fnb_licence": _f("F&B licence", "bool", group="compliance"),
    "licences": _f("Other licences", "multi", group="compliance"),
    "revenue_share_pct": _f("Revenue share (%)", "float", group="rental", maximum=100),
    **COMMON,
}

# ---------------------------------------------------------------------------
# Categories and types
# ---------------------------------------------------------------------------
# `aliases` are what portals and brokers say in the wild; search input is mapped
# through them so "flat", "2 bhk" and "apartment" are not three different things.
CATEGORIES: dict[str, dict] = {
    "residential": {
        "label": "Residential",
        "deal_kinds": ("sale", "rent"),
        "default_price_basis": {"sale": "total", "rent": "per_month"},
        "fields": RESIDENTIAL,
        "required_area": True,
        "types": {
            "apartment": {"label": "Apartment / Flat", "aliases": ("flat", "apartment", "multistorey apartment", "builder apartment")},
            "builder_floor": {"label": "Builder Floor", "aliases": ("builder floor", "independent floor")},
            "independent_house": {"label": "Independent House", "aliases": ("house", "independent house", "bungalow")},
            "villa": {"label": "Villa", "aliases": ("villa", "row villa")},
            "row_house": {"label": "Row House", "aliases": ("row house", "rowhouse", "townhouse")},
            "penthouse": {"label": "Penthouse", "aliases": ("penthouse",)},
            "studio": {"label": "Studio", "aliases": ("studio", "studio apartment", "1rk")},
            "duplex": {"label": "Duplex", "aliases": ("duplex",)},
            "farm_house": {"label": "Farm House", "aliases": ("farm house", "farmhouse")},
            "serviced_apartment": {"label": "Serviced Apartment", "aliases": ("serviced apartment", "serviced flat")},
            "residential_plot": {"label": "Residential Plot", "aliases": ("plot", "residential plot", "na plot", "land")},
        },
    },
    "commercial": {
        "label": "Commercial",
        "deal_kinds": ("sale", "lease"),
        "default_price_basis": {"sale": "total", "lease": "per_month_per_sqft"},
        "fields": COMMERCIAL,
        "required_area": True,
        "types": {
            "office": {"label": "Office Space", "aliases": ("office", "office space", "bare shell office", "commercial office")},
            "it_park": {"label": "IT Park / SEZ Office", "aliases": ("it park", "sez", "business park")},
            "shop": {"label": "Shop", "aliases": ("shop", "retail shop", "dukan")},
            "showroom": {"label": "Showroom", "aliases": ("showroom", "high street showroom")},
            "mall_unit": {"label": "Mall Unit", "aliases": ("mall unit", "mall shop", "food court")},
            "co_working": {"label": "Co-working / Seat", "aliases": ("coworking", "co-working", "seat", "managed office")},
            "commercial_plot": {"label": "Commercial Plot", "aliases": ("commercial plot", "commercial land")},
        },
    },
    "industrial": {
        "label": "Industrial & Logistics",
        "deal_kinds": ("sale", "lease"),
        "default_price_basis": {"sale": "total", "lease": "per_month_per_sqft"},
        "fields": INDUSTRIAL,
        "required_area": True,
        "types": {
            "warehouse": {"label": "Warehouse", "aliases": ("warehouse", "godown", "distribution centre", "dc")},
            "industrial_shed": {"label": "Industrial Shed", "aliases": ("shed", "industrial shed", "factory shed")},
            "factory": {"label": "Factory / Manufacturing", "aliases": ("factory", "manufacturing unit", "plant")},
            "cold_storage": {"label": "Cold Storage", "aliases": ("cold storage", "cold chain")},
            "logistics_park": {"label": "Logistics Park", "aliases": ("logistics park", "industrial park")},
            "industrial_plot": {"label": "Industrial Plot", "aliases": ("industrial plot", "industrial land")},
        },
    },
    "land": {
        "label": "Land & Agriculture",
        "deal_kinds": ("sale",),
        "default_price_basis": {"sale": "total"},
        "fields": LAND,
        "required_area": True,
        "types": {
            "residential_plot": {"label": "Residential Plot", "aliases": ("plot", "residential plot")},
            "na_plot": {"label": "NA Plot", "aliases": ("na plot", "non agricultural plot")},
            "commercial_plot": {"label": "Commercial Plot", "aliases": ("commercial plot",)},
            "agricultural_land": {"label": "Agricultural Land", "aliases": ("agri land", "agricultural land", "farm land", "shet")},
            "industrial_plot": {"label": "Industrial Plot", "aliases": ("industrial plot",)},
        },
    },
    "hospitality": {
        "label": "Hospitality",
        "deal_kinds": ("sale", "lease"),
        "default_price_basis": {"sale": "total", "lease": "per_month"},
        "fields": HOSPITALITY,
        "required_area": False,
        # A hotel is not a hotel without a size: keys, beds or banquet seats.
        "required_any": ("keys", "beds", "banquet_seating"),
        "types": {
            "hotel": {"label": "Hotel", "aliases": ("hotel",)},
            "resort": {"label": "Resort", "aliases": ("resort",)},
            "banquet": {"label": "Banquet / Convention", "aliases": ("banquet", "convention centre", "marriage hall")},
            "guest_house": {"label": "Guest House", "aliases": ("guest house", "lodge")},
        },
    },
}

# Alias → (category, type), built once so search input is cheap to resolve.
ALIAS_INDEX: dict[str, tuple[str, str]] = {}
for _cat, _spec in CATEGORIES.items():
    for _type, _t in _spec["types"].items():
        ALIAS_INDEX[_type.replace("_", " ")] = (_cat, _type)
        ALIAS_INDEX[_t["label"].lower()] = (_cat, _type)
        for _alias in _t["aliases"]:
            ALIAS_INDEX.setdefault(_alias.lower(), (_cat, _type))


_NOISE = ("bhk", "rk", "bedroom", "bed", "room", "for", "space", "property", "in", "-", "/")
_LONGEST_ALIAS = sorted(ALIAS_INDEX, key=len, reverse=True)


def resolve_type(text: str) -> tuple[str, str] | None:
    """Resolve how a human writes it — "3 bhk flat", "godown", "office space" — to (category, type)."""
    raw = " ".join(str(text or "").lower().replace(",", " ").split())
    if not raw:
        return None
    if raw in ALIAS_INDEX:
        return ALIAS_INDEX[raw]
    # "3 bhk flat", "2.5 bhk apartment", "1 rk studio" → drop the leading numbers/qualifiers
    words = [w for w in raw.split() if not w.replace(".", "").isdigit()
             and w not in ("bhk", "rk", "bedroom", "bhk.", "bhk,", "bed", "rooms")]
    stripped = " ".join(words)
    if stripped in ALIAS_INDEX:
        return ALIAS_INDEX[stripped]
    padded = f" {raw} "
    for alias in _LONGEST_ALIAS:                     # longest match wins: "agricultural land"
        if f" {alias} " in padded or padded.strip().endswith(f" {alias}"):
            return ALIAS_INDEX[alias]
    return None


def categories() -> list[dict]:
    """The catalogue the UI renders forms from."""
    out = []
    for slug, spec in CATEGORIES.items():
        fields = []
        for name, meta in spec["fields"].items():
            fields.append({"name": name, **meta})
        out.append({
            "slug": slug,
            "label": spec["label"],
            "deal_kinds": list(spec["deal_kinds"]),
            "default_price_basis": spec["default_price_basis"],
            "types": [{"slug": t, "label": v["label"], "aliases": list(v["aliases"])}
                      for t, v in spec["types"].items()],
            "fields": fields,
            "required_fields": [n for n, m in spec["fields"].items() if m["required"]],
            "required_any": list(spec.get("required_any", ())),
            "required_area": spec["required_area"],
        })
    return out


# ---------------------------------------------------------------------------
# Units and money
# ---------------------------------------------------------------------------
def normalize_area(value: Any, unit: str) -> float:
    unit_key = (unit or "").strip().lower()
    if unit_key in REFUSED_UNITS:
        raise TaxonomyError("AREA_UNIT_REFUSED", REFUSED_UNITS[unit_key], {"unit": unit})
    if unit_key not in AREA_UNITS:
        raise TaxonomyError("AREA_UNIT_UNKNOWN", f"Unknown area unit '{unit}'.",
                            {"units": sorted(AREA_UNITS)})
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise TaxonomyError("AREA_INVALID", "Area must be a number.")
    if amount <= 0:
        raise TaxonomyError("AREA_INVALID", "Area must be greater than zero.")
    return round(amount * AREA_UNITS[unit_key], 2)


def area_from(value: float, unit: str) -> dict:
    sqft = normalize_area(value, unit)
    return {"value": float(value), "unit": unit.lower(), "sqft": sqft}


def price_metrics(prop: dict) -> dict:
    """One comparable view of the money, whatever basis the listing used.

    A lease's monthly rent is annualised so it can be compared with an annual
    budget, and a per-sqft quote is multiplied out — never compared raw.
    """
    pricing = prop.get("pricing") or {}
    area = (prop.get("area") or {}).get("sqft") or float(prop.get("size_sqft") or 0)
    basis = pricing.get("basis") or "total"
    amount = float(pricing.get("amount") or prop.get("price") or 0)
    deal_kind = prop.get("deal_kind") or "sale"

    total = per_sqft = monthly = annual = None
    if basis == "total":
        total = amount
    elif basis == "per_sqft":
        per_sqft = amount
        total = round(amount * area, 2) if area else None
    elif basis == "per_month":
        monthly = amount
    elif basis == "per_month_per_sqft":
        per_sqft = amount
        monthly = round(amount * area, 2) if area else None
    elif basis == "per_annum":
        annual = amount
        monthly = round(amount / 12, 2)
    elif basis == "per_seat_month":
        monthly = round(amount * float((prop.get("attributes") or {}).get("seats") or 1), 2)
    elif basis == "per_acre":
        total = round(amount * (area / AREA_UNITS["acre"]), 2) if area else None

    if monthly is not None and annual is None:
        annual = round(monthly * 12, 2)
    if total and area and not per_sqft:
        per_sqft = round(total / area, 2)

    return {
        "basis": basis,
        "amount": amount,
        "deal_kind": deal_kind,
        "total": total,
        "per_sqft": per_sqft,
        "monthly": monthly,
        "annual": annual,
        "deposit_months": pricing.get("deposit_months"),
        "lock_in_months": pricing.get("lock_in_months"),
        "cam_per_sqft_month": pricing.get("cam_per_sqft_month"),
        "escalation_pct": pricing.get("escalation_pct"),
        "area_sqft": area,
    }


def comparable(prop: dict, deal_kind: str) -> tuple[float | None, str]:
    """The number a budget should be compared against, and what it is.

    A buyer with a ₹60 lakh budget is comparing totals; a tenant with a ₹60,000
    budget is comparing **monthly rent**. Mixing the two is how a CRM tells
    someone a ₹45,000/month office is "over budget by 99,925%".
    """
    m = price_metrics(prop)
    if deal_kind == "rent":
        return (m["monthly"], "per month") if m["monthly"] else (None, "no monthly rent")
    return (m["total"], "total") if m["total"] else (None, "no total price")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _coerce(meta: dict, raw: Any, name: str) -> Any:
    kind = meta["type"]
    if raw in (None, ""):
        return None
    if kind == "int":
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            raise TaxonomyError("FIELD_INVALID", f"'{meta['label']}' must be a whole number.", {"field": name})
        if value < 0 or (meta["max"] and value > meta["max"]):
            raise TaxonomyError("FIELD_INVALID", f"'{meta['label']}' is out of range.", {"field": name})
        return value
    if kind == "float":
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise TaxonomyError("FIELD_INVALID", f"'{meta['label']}' must be a number.", {"field": name})
        if value < 0 or (meta["max"] and value > meta["max"]):
            raise TaxonomyError("FIELD_INVALID", f"'{meta['label']}' is out of range.", {"field": name})
        return round(value, 2)
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")
    if kind == "enum":
        text = str(raw).strip()
        for option in meta["options"]:
            if option.lower() == text.lower():
                return option
        raise TaxonomyError("FIELD_INVALID", f"'{meta['label']}' must be one of: {', '.join(meta['options'])}.",
                            {"field": name, "options": list(meta["options"])})
    if kind == "multi":
        values = raw if isinstance(raw, (list, tuple)) else [p.strip() for p in str(raw).split(",")]
        return [str(v).strip()[:60] for v in values if str(v).strip()][:30]
    return str(raw).strip()[:200]


def validate(category: str, type_: str, attributes: dict | None = None, *,
             partial: bool = False) -> tuple[dict, list[str]]:
    """Check a property's attributes against its category's schema.

    Returns `(clean_attributes, errors)`. Unknown keys are **rejected**, not
    dropped: silently discarding "carpet_area" because the schema says
    "area_sqft" is how a warehouse ends up with no clear height and nobody
    notices until the lease is signed.
    """
    spec = CATEGORIES.get(category)
    if not spec:
        raise TaxonomyError("UNKNOWN_CATEGORY", f"Unknown category '{category}'.",
                            {"categories": list(CATEGORIES)})
    if type_ not in spec["types"]:
        raise TaxonomyError("UNKNOWN_TYPE", f"'{type_}' is not a {category} type.",
                            {"types": list(spec["types"])})

    attributes = attributes or {}
    clean: dict[str, Any] = {}
    errors: list[str] = []
    unknown = [k for k in attributes if k not in spec["fields"]]
    if unknown:
        errors.append("Unknown field(s) for this category: " + ", ".join(sorted(unknown)[:8]))

    any_of = spec.get("required_any") or ()
    if any_of and not partial and not any(attributes.get(k) for k in any_of):
        errors.append("A hospitality listing needs a capacity: send one of " + ", ".join(any_of) + ".")

    for name, meta in spec["fields"].items():
        if name not in attributes:
            if meta["required"] and not partial:
                errors.append(f"'{meta['label']}' is required for {category}/{type_}.")
            continue
        try:
            value = _coerce(meta, attributes[name], name)
        except TaxonomyError as exc:
            errors.append(exc.message)
            continue
        if value is not None:
            clean[name] = value
    return clean, errors


def normalize_property(payload: dict, *, partial: bool = False) -> dict:
    """Turn an API payload into the stored shape, refusing the unpublishable.

    Also accepts a loose `type` string ("3 bhk flat", "godown") and resolves it
    through the alias index, because that is how the words arrive.
    """
    category = (payload.get("category") or "").strip().lower()
    type_raw = (payload.get("type") or "").strip()
    original_type = type_raw
    if not category or not type_raw:
        resolved = resolve_type(type_raw or payload.get("property_type") or "")
        if resolved:
            category = category or resolved[0]
            type_raw = resolved[1]
    if category not in CATEGORIES:
        raise TaxonomyError("UNKNOWN_CATEGORY",
                            "A property needs a category: " + ", ".join(CATEGORIES) + ".",
                            {"categories": list(CATEGORIES)})
    type_key = type_raw.lower().replace(" ", "_")
    if type_key not in CATEGORIES[category]["types"]:
        resolved = resolve_type(type_raw)
        type_key = resolved[1] if resolved and resolved[0] == category else type_key
    if type_key not in CATEGORIES[category]["types"]:
        raise TaxonomyError("UNKNOWN_TYPE", f"'{type_raw}' is not a {category} type.",
                            {"types": list(CATEGORIES[category]["types"])})

    spec = CATEGORIES[category]
    deal_kind = (payload.get("deal_kind") or "").strip().lower()
    if not deal_kind:
        deal_kind = "sale" if "sale" in spec["deal_kinds"] else spec["deal_kinds"][0]
    if deal_kind not in DEAL_KINDS:
        raise TaxonomyError("UNKNOWN_DEAL_KIND", f"Unknown deal kind '{deal_kind}'.", {"kinds": list(DEAL_KINDS)})
    if deal_kind not in spec["deal_kinds"]:
        raise TaxonomyError("DEAL_KIND_NOT_ALLOWED",
                            f"A {category} property cannot be '{deal_kind}'. Allowed: {', '.join(spec['deal_kinds'])}.")

    attributes, errors = validate(category, type_key, payload.get("attributes") or {}, partial=partial)
    # "3 bhk flat" already said the bedrooms; do not make the caller repeat it.
    if category == "residential" and "bedrooms" not in attributes:
        import re as _re
        match = _re.search(r"(\d+(?:\.\d+)?)\s*(?:bhk|rk|bedroom)", original_type.lower())
        if match:
            bhk = int(float(match.group(1)))
            attributes["bedrooms"] = bhk
            attributes.setdefault("bathrooms", max(1, bhk - 1))
            errors = [e for e in errors if "Bedrooms" not in e and "Bathrooms" not in e]

    area = None
    raw_area = payload.get("area") or {}
    if isinstance(raw_area, (int, float)) or (isinstance(raw_area, str) and raw_area.strip()):
        raw_area = {"value": raw_area, "unit": payload.get("area_unit") or "sqft"}
    if raw_area.get("value"):
        try:
            area = area_from(raw_area.get("value"), raw_area.get("unit") or "sqft")
        except TaxonomyError as exc:
            errors.append(exc.message)
    elif spec["required_area"] and not partial:
        errors.append("Area is required: send {area: {value, unit}} (sqft, sqm, sqyd, acre, hectare, are, guntha, cent).")

    pricing = dict(payload.get("pricing") or {})
    if not pricing.get("amount") and payload.get("price"):
        pricing["amount"] = payload["price"]
    if not pricing.get("basis"):
        pricing["basis"] = spec["default_price_basis"].get(deal_kind, "total")
    if pricing.get("basis") and pricing["basis"] not in PRICE_BASES:
        errors.append(f"Unknown price basis '{pricing['basis']}'. Allowed: {', '.join(PRICE_BASES)}.")
    if not pricing.get("amount") and not partial:
        errors.append("Price is required (pricing.amount).")
    for key in ("deposit_months", "lock_in_months", "escalation_pct", "cam_per_sqft_month"):
        if key in pricing and pricing[key] in ("", None):
            pricing.pop(key)

    # Guard the classic listing error: a per-sqft rate typed into the total field.
    basis, amount_now = pricing.get("basis"), _num_quiet(pricing.get("amount"))
    if amount_now is not None:
        if basis in ("per_sqft", "per_month_per_sqft") and amount_now > 100_000:
            errors.append(f"A {basis} rate of {amount_now:,.0f} looks like a total, not a rate.")
        if deal_kind in ("lease", "rent") and basis == "total" and amount_now < 1_000:
            errors.append(f"A {deal_kind} total of {amount_now:,.0f} looks like a per-sqft rate — "
                          "send pricing.basis explicitly (per_month / per_month_per_sqft / per_annum).")

    # A lease with no deposit and no lock-in is a listing a lawyer will query;
    # default them rather than leave them blank, and say so.
    if deal_kind in ("lease", "rent"):
        pricing.setdefault("deposit_months", 3)
        pricing.setdefault("lock_in_months", 12)

    if errors:
        raise TaxonomyError("PROPERTY_INVALID", "; ".join(errors[:6]), {"errors": errors})

    return {
        "category": category,
        "type": type_key,
        "deal_kind": deal_kind,
        "area": area,
        "attributes": attributes,
        "pricing": pricing,
    }


# ---------------------------------------------------------------------------
# Legacy rows
# ---------------------------------------------------------------------------
LEGACY_TYPE_MAP: dict[str, tuple[str, str, str]] = {
    "plot": ("land", "residential_plot", "sale"),
    "villa": ("residential", "villa", "sale"),
    "apartment": ("residential", "apartment", "sale"),
    "commercial": ("industrial", "industrial_shed", "sale"),
    "land": ("land", "agricultural_land", "sale"),
    "office": ("commercial", "office", "lease"),
    "shop": ("commercial", "shop", "lease"),
    "warehouse": ("industrial", "warehouse", "lease"),
    "hotel": ("hospitality", "hotel", "sale"),
}


def hydrate(prop: dict) -> dict:
    """Make sure a stored property carries the five taxonomy fields.

    Rows created before the taxonomy existed (`type` + `price` + `size_sqft`)
    are upgraded in place rather than rejected — a CRM that throws away last
    quarter's inventory because the schema got better is not a CRM.
    """
    if prop.get("category") and prop.get("area") and isinstance(prop.get("attributes"), dict):
        return prop
    category, type_, deal_kind = LEGACY_TYPE_MAP.get(
        str(prop.get("type") or "").lower(), ("residential", "apartment", "sale"))
    prop.setdefault("category", category)
    prop["type"] = prop.get("type") if prop.get("category") else type_
    if prop["type"] not in CATEGORIES[prop["category"]]["types"]:
        prop["type"] = type_
    prop.setdefault("deal_kind", deal_kind)

    attrs = prop.get("attributes") if isinstance(prop.get("attributes"), dict) else {}
    if prop["category"] == "residential" and "bedrooms" not in attrs:
        import re as _re
        match = _re.search(r"(\d)\s*bhk", str(prop.get("title") or "").lower())
        if match:
            attrs.setdefault("bedrooms", int(match.group(1)))
            attrs.setdefault("bathrooms", max(1, int(match.group(1)) - 1))
    prop["attributes"] = attrs

    if not prop.get("area"):
        size = prop.get("size_sqft")
        prop["area"] = {"value": float(size), "unit": "sqft", "sqft": float(size)} if size else None
    if not prop.get("pricing"):
        prop["pricing"] = {"basis": "total" if prop["deal_kind"] == "sale" else "per_month",
                           "amount": float(prop.get("price") or 0)}
    return prop


# ---------------------------------------------------------------------------
# Requirement matching
# ---------------------------------------------------------------------------
def normalize_requirement(spec: dict) -> dict:
    """A buyer's/tanent's requirement in the same vocabulary as the inventory."""
    if not isinstance(spec, dict):
        raise TaxonomyError("REQUIREMENT_INVALID", "Requirement must be an object.")
    category = (spec.get("category") or "").strip().lower()
    if category and category not in CATEGORIES:
        raise TaxonomyError("UNKNOWN_CATEGORY", f"Unknown category '{category}'.", {"categories": list(CATEGORIES)})
    types = [str(t).strip().lower().replace(" ", "_") for t in (spec.get("types") or [])]
    if types and category:
        bad = [t for t in types if t not in CATEGORIES[category]["types"]]
        if bad:
            raise TaxonomyError("UNKNOWN_TYPE", f"'{bad[0]}' is not a {category} type.",
                                {"types": list(CATEGORIES[category]["types"])})
    deal_kind = (spec.get("deal_kind") or "sale").strip().lower()
    if deal_kind not in DEAL_KINDS:
        raise TaxonomyError("UNKNOWN_DEAL_KIND", f"Unknown deal kind '{deal_kind}'.")

    out = {
        "category": category,
        "types": types,
        "deal_kind": deal_kind,
        "budget_min": _num(spec.get("budget_min")),
        "budget_max": _num(spec.get("budget_max")),
        "bedrooms_min": int(spec["bedrooms_min"]) if spec.get("bedrooms_min") not in (None, "") else None,
        "facing": [str(f) for f in (spec.get("facing") or [])],
        "furnishing": [str(f) for f in (spec.get("furnishing") or [])],
        "must_have": [str(m) for m in (spec.get("must_have") or [])],
        "city": (spec.get("city") or "").strip(),
        "localities": [str(l).strip().lower() for l in (spec.get("localities") or [])],
        "possession": spec.get("possession") or "",
        "area_min_sqft": None,
        "area_max_sqft": None,
    }
    area_min, area_max = spec.get("area_min"), spec.get("area_max")
    unit = spec.get("area_unit") or "sqft"
    try:
        if area_min not in (None, ""):
            out["area_min_sqft"] = normalize_area(area_min, unit)
        if area_max not in (None, ""):
            out["area_max_sqft"] = normalize_area(area_max, unit)
    except TaxonomyError as exc:
        raise TaxonomyError(exc.code, exc.message, exc.details)
    if out["budget_min"] and out["budget_max"] and out["budget_min"] > out["budget_max"]:
        raise TaxonomyError("REQUIREMENT_INVALID", "budget_min is greater than budget_max.")
    return out


def _num_quiet(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise TaxonomyError("REQUIREMENT_INVALID", f"'{value}' is not a number.")


def score_requirement(prop: dict, spec: dict) -> dict:
    """0–100 against a structured requirement, with a reason per component."""
    score = 0
    reasons: list[str] = []
    category = prop.get("category") or "residential"
    ptype = prop.get("type") or ""

    if spec.get("types"):
        if ptype in spec["types"]:
            score += 30
            reasons.append(f"type '{ptype}' is one of the requested")
        elif spec.get("category") and category == spec["category"]:
            score += 12
            reasons.append(f"same category ({category}) but type '{ptype}' differs")
        else:
            reasons.append(f"type '{ptype}' is not what was asked for")
    elif spec.get("category"):
        if category == spec["category"]:
            score += 30
            reasons.append(f"category matches ({category})")
        else:
            reasons.append(f"category '{category}' does not match")

    want_kind = spec.get("deal_kind") or "sale"
    if prop.get("deal_kind") == want_kind:
        score += 10
        reasons.append(f"{want_kind} listing")
    else:
        reasons.append(f"listing is {prop.get('deal_kind')}, requirement is {want_kind}")

    amount, unit_label = comparable(prop, want_kind)
    bmin, bmax = spec.get("budget_min"), spec.get("budget_max")
    if amount and bmax:
        if amount <= bmax and (not bmin or amount >= bmin * 0.8):
            score += 25
            reasons.append(f"within budget: {inr(amount)} {unit_label} vs {inr(bmax)}")
        elif amount <= bmax * 1.1:
            score += 15
            reasons.append(f"slightly over budget: {inr(amount)} {unit_label} vs {inr(bmax)}")
        else:
            reasons.append(f"over budget: {inr(amount)} {unit_label} vs {inr(bmax)}")
    elif amount:
        score += 10
        reasons.append(f"{inr(amount)} {unit_label} (no budget stated)")

    area_sqft = (prop.get("area") or {}).get("sqft") or float(prop.get("size_sqft") or 0)
    lo, hi = spec.get("area_min_sqft"), spec.get("area_max_sqft")
    if area_sqft and (lo or hi):
        if (not lo or area_sqft >= lo) and (not hi or area_sqft <= hi):
            score += 15
            reasons.append(f"{area_sqft:,.0f} sqft is inside the requested range")
        else:
            reasons.append(f"{area_sqft:,.0f} sqft is outside the requested range")
    elif area_sqft:
        score += 6
        reasons.append(f"{area_sqft:,.0f} sqft (no area stated)")

    attrs = prop.get("attributes") or {}
    if spec.get("bedrooms_min") and attrs.get("bedrooms"):
        if int(attrs["bedrooms"]) >= int(spec["bedrooms_min"]):
            score += 10
            reasons.append(f"{attrs['bedrooms']} BHK meets the {spec['bedrooms_min']} BHK minimum")
        else:
            reasons.append(f"{attrs['bedrooms']} BHK is below the {spec['bedrooms_min']} BHK minimum")

    if spec.get("facing") and attrs.get("facing"):
        if attrs["facing"] in spec["facing"]:
            score += 5
            reasons.append(f"facing {attrs['facing']} as requested")

    if spec.get("furnishing") and attrs.get("furnishing"):
        if attrs["furnishing"] in spec["furnishing"]:
            score += 5
            reasons.append(f"{attrs['furnishing']} as requested")

    must = [m.lower() for m in (spec.get("must_have") or [])]
    hits: list[str] = []
    missing: list[str] = []
    if must:
        pool = " ".join(
            [str(x).lower() for x in (attrs.get("amenities") or [])]
            + [str(attrs.get(k, "")).lower() for k in ("power_backup", "central_ac", "fire_system", "irrigation", "na_order")]
            + [str(v).lower() for v in attrs.values() if isinstance(v, str)]
        )
        hits = [m for m in must if m in pool]
        if hits:
            score += min(10, int(round(10 * len(hits) / len(must))))
            reasons.append("has: " + ", ".join(hits))
        missing = [m for m in must if m not in pool]
        if missing:
            reasons.append("missing: " + ", ".join(missing))

    if spec.get("city") and (prop.get("city") or "").lower() != spec["city"].lower():
        reasons.append(f"city {prop.get('city')} differs from {spec['city']}")
    elif spec.get("city"):
        score += 5
        reasons.append(f"in {spec['city']}")

    if str(prop.get("status") or "available").lower() == "available":
        score += 5
    else:
        reasons.append(f"not available ({prop.get('status')})")

    # Caps: a listing that fails a stated must-have is never a "100% match",
    # and one in the wrong category never outranks a right-category neighbour.
    if must and missing:
        score = min(score, 85)
    if spec.get("category") and category != spec["category"]:
        score = min(score, 60)
    if want_kind and prop.get("deal_kind") != want_kind:
        score = min(score, 65)

    return {"property_id": prop.get("id"), "title": prop.get("title"), "score": max(0, min(100, score)),
            "reasons": reasons, "category": category, "type": ptype, "property": prop}


def rank(properties: Iterable[dict], spec: dict, limit: int = 10) -> list[dict]:
    rows = [score_requirement(p, spec) for p in properties]
    rows.sort(key=lambda r: (-r["score"], str(r.get("title") or "")))
    return rows[:limit]
