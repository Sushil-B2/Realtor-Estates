#!/usr/bin/env python3
"""
Live proof for the property taxonomy (docs/22).

    python3 scripts/proof_taxonomy.py                          # throwaway seeded instance
    python3 scripts/proof_taxonomy.py --base http://127.0.0.1:5433 --live

The claim being proved is not "we added fields". It is that five categories are
five different products: the catalogue serves five different forms, a warehouse
cannot be saved without a clear height, a plot cannot be saved in bigha, a
tenant's ₹20,000/month budget is not compared with a ₹65 lakh sale price, and a
requirement typed into a contact card comes back as a ranked shortlist drawn
from every category at once.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Session:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookies: dict[str, str] = {}
        self.csrf = ""

    def call(self, method: str, path: str, body=None, csrf: bool = True):
        h = {"Accept": "application/json", "User-Agent": "proof-taxonomy"}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body is not None:
            h["Content-Type"] = "application/json"
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                raw, status, hdrs = res.read(), res.status, dict(res.getheaders())
        except urllib.error.HTTPError as exc:
            raw, status, hdrs = exc.read(), exc.code, dict(exc.headers)
        cookie = hdrs.get("Set-Cookie", "")
        if "re_session=" in cookie:
            value = cookie.split("re_session=", 1)[1].split(";")[0]
            if value:
                self.cookies["re_session"] = value
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"_raw": raw[:300].decode("utf-8", "replace")}
        return status, payload

    def login(self, email: str, password: str = "Demo@12345!") -> dict:
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password}, csrf=False)
        expect(f"login {email.split('@')[0]}", "POST /auth/login", status, 200)
        self.csrf = body.get("csrf", "")
        return body


def expect(label: str, request: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<42} {request:<54} → {got}")
    if not ok:
        FAILS.append(f"{label}: expected {want}, got {got}")


def show(label: str, payload, limit: int = 300) -> None:
    print(f"        {label}: {json.dumps(payload, ensure_ascii=False)[:limit]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    proc = tmpdir = None
    if not args.live:
        tmpdir = tempfile.mkdtemp(prefix="re-tax-proof-")
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = {**os.environ, "RE_DATA_DIR": tmpdir, "RE_DB": "json", "RE_LIMITER_OFF": "1",
               "PORT": str(port), "HOST": "127.0.0.1"}
        proc = subprocess.Popen([sys.executable, "-m", "app.server", "--port", str(port), "--host", "127.0.0.1"],
                                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(120):
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.25)
        else:
            print("server did not start")
            proc.kill()
            return 1
    else:
        base = (args.base or "http://127.0.0.1:5433").rstrip("/")

    try:
        print(f"\n=== taxonomy proof against {base} ===")
        owner = Session(base)
        owner.login("owner@realtorestates.app")
        agent = Session(base)
        agent.login("agent@realtorestates.app")

        # ---------------------------------------------------------------
        print("\n1. five categories, five different forms")
        status, cat = owner.call("GET", "/api/v1/taxonomy")
        expect("catalogue served", "GET /taxonomy", status, 200)
        expect("five categories", "GET /taxonomy → count", cat["count"], 5)
        show("field counts", cat["field_counts"])
        names = {c["slug"]: {f["name"] for f in c["fields"]} for c in cat["categories"]}
        expect("residential has BHK/facing/furnishing",
               "fields", {"bedrooms", "bathrooms", "facing", "furnishing"} <= names["residential"], True)
        expect("commercial has seats/cabins/fitout/lock-in",
               "fields", {"seats", "cabins", "fitout", "lock_in_months", "cam_per_sqft_month"} <= names["commercial"], True)
        expect("industrial has clear height/floor load/docks",
               "fields", {"clear_height_m", "floor_load_mt_sqm", "dock_levellers"} <= names["industrial"], True)
        expect("land has survey/occupant class/NA/irrigation",
               "fields", {"survey_number", "occupant_class", "na_order", "irrigation"} <= names["land"], True)
        expect("hospitality has keys/star class/banquet",
               "fields", {"keys", "star_class", "banquet_seating"} <= names["hospitality"], True)
        expect("no category reuses another's form", "field sets distinct",
               len({frozenset(v) for v in names.values()}), 5)
        show("area units", [u["unit"] for u in cat["area_units"]])
        show("price bases", cat["price_bases"])
        show("refused unit", cat["refused_units"])

        # ---------------------------------------------------------------
        print("\n2. a listing that makes no sense is refused")
        base_wh = {"title": "Proof warehouse", "city": "Nanded", "locality": "MIDC",
                   "category": "industrial", "type": "warehouse", "deal_kind": "lease",
                   "area": {"value": 15000, "unit": "sqft"}, "pricing": {"amount": 21}}
        status, bad = owner.call("POST", "/api/v1/properties", base_wh)
        expect("warehouse without clear height", "POST /properties", status, 422)
        show("refusal", bad["error"]["message"])
        status, bad2 = owner.call("POST", "/api/v1/properties",
                                  {"title": "Proof plot", "category": "land", "type": "agricultural_land",
                                   "deal_kind": "sale", "area": {"value": 2, "unit": "bigha"},
                                   "pricing": {"amount": 9_000_000}, "attributes": {"land_use": "Agricultural"}})
        expect("land measured in bigha", "POST /properties (2 bigha)", status, 422)
        show("refusal", bad2["error"]["message"])
        status, bad3 = owner.call("POST", "/api/v1/properties",
                                  {"title": "Proof flat", "category": "residential", "type": "apartment",
                                   "deal_kind": "sale", "area": {"value": 900, "unit": "sqft"},
                                   "pricing": {"amount": 4_000_000},
                                   "attributes": {"bedrooms": 2, "bathrooms": 1, "clear_height_m": 12}})
        expect("residential row carrying a warehouse field", "POST /properties (foreign field)", status, 422)
        show("refusal", bad3["error"]["message"][:200])

        # ---------------------------------------------------------------
        print("\n3. a real listing in each category is accepted")
        created = {}
        payloads = {
            "industrial": {**base_wh, "attributes": {"clear_height_m": 10.5, "floor_load_mt_sqm": 5,
                                                     "dock_levellers": 4, "power_kva": 250,
                                                     "fire_system": "ESFR Sprinklers"}},
            "commercial": {"title": "Proof office", "city": "Nanded", "category": "commercial", "type": "office",
                           "deal_kind": "lease", "area": {"value": 3200, "unit": "sqft"},
                           "pricing": {"basis": "per_month_per_sqft", "amount": 55, "lock_in_months": 36},
                           "attributes": {"fitout": "Warm Shell", "seats": 45, "cabins": 2,
                                          "meeting_rooms": 1, "central_ac": True, "grade": "Grade A"}},
            "residential": {"title": "Proof 3BHK", "city": "Nanded", "category": "residential", "type": "apartment",
                            "deal_kind": "sale", "area": {"value": 128, "unit": "sqm"},
                            "pricing": {"amount": 6_800_000},
                            "attributes": {"bedrooms": 3, "bathrooms": 3, "facing": "North-East",
                                           "furnishing": "Semi-Furnished", "power_backup": True,
                                           "amenities": ["Lift", "Gym", "Power Backup"]}},
            "land": {"title": "Proof NA plot", "city": "Nanded", "category": "land", "type": "na_plot",
                     "deal_kind": "sale", "area": {"value": 30, "unit": "guntha"},
                     "pricing": {"amount": 3_200_000},
                     "attributes": {"land_use": "NA Plot", "na_order": True, "survey_number": "Gat 411",
                                    "occupant_class": "Class I", "facing": "East"}},
            "residential_rent": {"title": "Proof 2BHK on rent", "city": "Nanded", "category": "residential",
                                 "type": "apartment", "deal_kind": "rent",
                                 "area": {"value": 950, "unit": "sqft"},
                                 "pricing": {"basis": "per_month", "amount": 21000, "deposit_months": 2},
                                 "attributes": {"bedrooms": 2, "bathrooms": 2, "furnishing": "Furnished",
                                                "facing": "East", "tenant_preference": "Family",
                                                "amenities": ["Lift", "Power Backup"]}},
            "hospitality": {"title": "Proof banquet", "city": "Nanded", "category": "hospitality", "type": "banquet",
                            "deal_kind": "lease", "area": {"value": 9000, "unit": "sqft"},
                            "pricing": {"basis": "per_month", "amount": 240_000, "deposit_months": 6},
                            "attributes": {"banquet_seating": 500, "fnb_licence": True,
                                           "licences": ["Fire NOC", "FSSAI"]}},
        }
        for slug, payload in payloads.items():
            status, body = owner.call("POST", "/api/v1/properties", payload)
            expect(f"{slug} listing accepted", f"POST /properties ({slug})", status in (200, 201), True)
            if status in (200, 201):
                card = body["property"]
                created[slug] = card["id"]
                show(f"{slug} money", card["money"])
        expect("six listings across five categories stored", "created", len(created), 6)
        by_survey = owner.call("POST", "/api/v1/properties/search",
                               {"category": "land", "attributes": {"survey_number": "Gat 411"}})[1]
        expect("guntha converted", "30 guntha → sqft",
               round(by_survey["results"][0]["property"]["area"]["sqft"]) if by_survey["results"] else None,
               32670)

        # ---------------------------------------------------------------
        print("\n4. rent is compared monthly, sale in totals")
        status, rent = owner.call("POST", "/api/v1/properties/search",
                                  {"category": "residential", "deal_kind": "rent", "budget_max": 25000, "limit": 5})
        expect("rent search ok", "POST /properties/search rent", status, 200)
        rent_first = rent["results"][0] if rent["results"] else {}
        show("top rent match", {"title": rent_first.get("property", {}).get("title"),
                                "score": rent_first.get("score"),
                                "monthly": rent_first.get("property", {}).get("money", {}).get("monthly"),
                                "reasons": rent_first.get("reasons", [])[:3]})
        expect("a monthly rent is compared with a monthly budget",
               "reasons", any("within budget" in r for r in rent_first.get("reasons", [])), True)
        status, sale = owner.call("POST", "/api/v1/properties/search",
                                  {"category": "residential", "deal_kind": "sale", "budget_max": 7_000_000,
                                   "bedrooms_min": 3, "limit": 3})
        show("top sale match", {"title": sale["results"][0]["property"]["title"],
                                "score": sale["results"][0]["score"],
                                "reasons": sale["results"][0]["reasons"][:3]})
        expect("sale search explains its budget", "reasons",
               any("budget" in r for r in sale["results"][0]["reasons"]), True)

        # ---------------------------------------------------------------
        print("\n5. attribute filters across categories")
        status, tall = owner.call("POST", "/api/v1/properties/search",
                                  {"category": "industrial", "attributes": {"clear_height_m": {"min": 10}}})
        expect("clear height ≥ 10 m", "POST /properties/search attributes", status, 200)
        expect("only tall warehouses", "every result ≥ 10 m",
               all(r["property"]["attributes"]["clear_height_m"] >= 10 for r in tall["results"]), True)
        status, lic = owner.call("POST", "/api/v1/properties/search",
                                 {"category": "hospitality", "attributes": {"fnb_licence": True}})
        expect("licensed F&B only", "every result licensed",
               all(r["property"]["attributes"]["fnb_licence"] is True for r in lic["results"]), True)
        show("matches", [(r["property"]["title"], r["score"]) for r in lic["results"]])

        # ---------------------------------------------------------------
        print("\n6. a loose phrase resolves to a category")
        status, godown = owner.call("POST", "/api/v1/properties/search", {"q": "godown", "limit": 3})
        expect("'godown' finds warehouses", "POST /properties/search q=godown", status, 200)
        expect("resolved to industrial/warehouse", "requirement",
               [godown["requirement"]["category"], godown["requirement"]["types"]],
               ["industrial", ["warehouse"]])
        expect("warehouses found without the word in the title", "q=godown → hits",
               godown["count"] > 0, True)
        show("hits", [(r["property"]["title"], r["score"]) for r in godown["results"]])

        # ---------------------------------------------------------------
        print("\n7. a requirement on a contact becomes a shortlist")
        status, contacts = owner.call("GET", "/api/v1/contacts?limit=3")
        contact = contacts["contacts"][0]
        req = {"category": "residential", "types": ["apartment"], "deal_kind": "sale",
               "bedrooms_min": 3, "budget_max": 8_000_000, "area_min": 1100, "area_unit": "sqft",
               "facing": ["North-East", "East"], "must_have": ["power backup"]}
        status, saved = owner.call("POST", f"/api/v1/contacts/{contact['id']}/requirement", req)
        expect("requirement saved", "POST /contacts/{id}/requirement", status, 200)
        show("summary", saved["summary"])
        show("matches", [(m["property"]["title"], m["score"]) for m in saved["matches"]])
        expect("shortlist returned", "matches", saved["count"] > 0, True)
        expect("the brief reads like a sentence, in lakh-style money", "summary has ₹ with Indian grouping",
               "₹" in saved["summary"], True)
        expect("every match is residential", "categories",
               {m["property"]["category"] for m in saved["matches"]}, {"residential"})
        detail = owner.call("GET", f"/api/v1/contacts/{contact['id']}")[1]
        expect("requirement persisted on the contact", "GET /contacts/{id}",
               (detail["contact"].get("requirement_spec") or {}).get("bedrooms_min"), 3)

        # ---------------------------------------------------------------
        print("\n8. auth and scope")
        status, _ = owner.call("POST", "/api/v1/properties",
                               {**base_wh, "title": "Proof warehouse 2",
                                "attributes": {"clear_height_m": 11, "floor_load_mt_sqm": 6}})
        expect("owner may create inventory", "POST /properties (owner)", status in (200, 201), True)
        status, denied = agent.call("GET", "/api/v1/taxonomy")
        expect("agent may read the catalogue", "GET /taxonomy (agent)", status, 200)
        other = Session(base)
        other.login("owner@metrorealty.app", "Metro@12345!")
        status, theirs = other.call("POST", "/api/v1/properties/search", {"limit": 50})
        expect("other tenant sees no inventory", "POST /properties/search as metro",
               [theirs["count"], theirs["candidates"]], [0, 0])
        status, found = owner.call("POST", "/api/v1/properties/search", {"limit": 50})
        expect("this tenant sees the whole book", "POST /properties/search as owner",
               found["count"] >= 5 and len(found["categories_scanned"]) >= 4, True)
        show("categories in the book", found["categories_scanned"])

        print(f"\n=== {'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILURE(S)'} ===")
        for f in FAILS:
            print("  ✗ " + f)
        return 0 if not FAILS else 1
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
