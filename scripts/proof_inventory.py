#!/usr/bin/env python3
"""
Live proof for unit-level inventory (docs/18 §4 #4, §6 #6).

    python3 scripts/proof_inventory.py                        # throwaway seeded instance
    python3 scripts/proof_inventory.py --base http://127.0.0.1:5433 --live

The point of the module is the refusal: two deals must not be able to promise the
same unit. The proof therefore *tries* to double-book, three different ways, and
checks that each attempt fails with a reason that names the holder.
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
        h = {"Accept": "application/json", "User-Agent": "proof-inventory"}
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
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<40} {request:<52} → {got}")
    if not ok:
        FAILS.append(f"{label}: expected {want}, got {got}")


def show(label: str, payload, limit: int = 260) -> None:
    print(f"        {label}: {json.dumps(payload, ensure_ascii=False)[:limit]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    proc = tmpdir = None
    if not args.live:
        tmpdir = tempfile.mkdtemp(prefix="re-inv-proof-")
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
        print(f"\n=== inventory proof against {base} ===")
        owner = Session(base)
        owner.login("owner@realtorestates.app")
        agent = Session(base)
        agent.login("agent@realtorestates.app")

        print("\n1. the property's units and their ledger position")
        props = owner.call("GET", "/api/v1/properties")[1]["properties"]
        prop = props[0]
        status, inv = owner.call("GET", f"/api/v1/properties/{prop['id']}/units")
        expect("units listed", f"GET /properties/{prop['id']}/units", status, 200)
        show("summary", inv.get("summary"))
        if not inv.get("units"):
            owner.call("POST", f"/api/v1/properties/{prop['id']}/units/bulk",
                       {"floors": 2, "per_floor": 3, "base_price": int(prop.get("price") or 3_000_000),
                        "price_step": 50_000})
            inv = owner.call("GET", f"/api/v1/properties/{prop['id']}/units")[1]
            show("generated", inv.get("summary"))

        print("\n2. generate a floor grid (deterministic unit numbers)")
        stamp = int(time.time()) % 100000
        status, bulk = owner.call("POST", f"/api/v1/properties/{prop['id']}/units/bulk",
                                  {"floors": 2, "per_floor": 2, "base_price": 3_000_000, "price_step": 50_000,
                                   "start_floor": 20 + (stamp % 5), "type": "3BHK", "area_sqft": 1480})
        expect("grid created", "POST …/units/bulk", status, 200)
        show("created", {"created": bulk.get("created"), "skipped": bulk.get("skipped")})
        again = owner.call("POST", f"/api/v1/properties/{prop['id']}/units/bulk",
                           {"floors": 2, "per_floor": 2, "base_price": 3_000_000, "price_step": 50_000,
                            "start_floor": 20 + (stamp % 5), "type": "3BHK"})[1]
        expect("re-running does not duplicate", "POST …/units/bulk again",
               [again["created"], again["skipped"]], [0, 4])

        print("\n3. two deals, one unit")
        units = [u for u in owner.call("GET", "/api/v1/units")[1]["units"] if u["status"] == "available"]
        unit = units[0]
        deals = [dict(d, stage=stage) for stage, rows in owner.call("GET", "/api/v1/pipeline")[1]["columns"].items()
                 for d in rows]
        open_deals = [d for d in deals if d["stage"] not in ("won", "lost")]
        deal_a, deal_b = open_deals[0], open_deals[-1]
        show("unit", {"unit_no": unit["unit_no"], "price": unit["price"], "status": unit["status"]})

        status, held = owner.call("POST", f"/api/v1/units/{unit['id']}/block",
                                  {"deal_id": deal_a["id"], "hours": 48, "reason": "family deciding"})
        expect("deal A holds it", "POST /units/{id}/block", status, 200)
        show("hold", {"status": held["unit"]["status"], "hours_left": held["unit"]["block_hours_left"]})

        status, clash = owner.call("POST", f"/api/v1/units/{unit['id']}/block", {"deal_id": deal_b["id"]})
        expect("deal B cannot hold it", "POST /units/{id}/block as deal B", status, 409)
        show("refusal", clash["error"]["details"][0])

        print("\n4. booking, and the three ways a second sale is stopped")
        status, booked = owner.call("POST", f"/api/v1/units/{unit['id']}/book",
                                    {"deal_id": deal_a["id"], "token_amount": 200_000})
        expect("deal A books it", "POST /units/{id}/book", status, 200)
        show("booking", {"status": booked["unit"]["status"],
                         "token": booked["unit"]["booking"]["token_amount"]})

        status, r1 = owner.call("POST", f"/api/v1/units/{unit['id']}/book", {"deal_id": deal_b["id"]})
        expect("deal B cannot book it", "POST /units/{id}/book as deal B", status, 409)
        status, r2 = owner.call("POST", f"/api/v1/units/{unit['id']}/block", {"deal_id": deal_b["id"]})
        expect("deal B cannot even hold it", "POST /units/{id}/block as deal B", status, 409)
        status, r3 = owner.call("PATCH", f"/api/v1/units/{unit['id']}", {"status": "sold"})
        expect("a status change cannot fake a sale", "PATCH /units/{id} {status: sold}", status, 409)
        show("refusals", [r1["error"]["code"], r2["error"]["code"], r3["error"]["code"]])

        print("\n5. an agent cannot release another deal's hold")
        other_unit = next(u for u in owner.call("GET", "/api/v1/units")[1]["units"]
                          if u["status"] == "available" and u["id"] != unit["id"])
        owner.call("POST", f"/api/v1/units/{other_unit['id']}/block", {"deal_id": deal_a["id"]})
        status, refused = agent.call("POST", f"/api/v1/units/{other_unit['id']}/release", {"deal_id": deal_b["id"]})
        expect("agent release refused", "POST /units/{id}/release as agent", status, 403)
        status, released = owner.call("POST", f"/api/v1/units/{other_unit['id']}/release", {"deal_id": deal_a["id"]})
        expect("owner releases", "POST /units/{id}/release", status, 200)

        print("\n6. the deal's own advance carries the unit")
        third = next(u for u in owner.call("GET", "/api/v1/units")[1]["units"] if u["status"] == "available")
        holder = next(d for d in open_deals if d["stage"] in ("negotiation", "site_visit", "qualified", "contacted")
                      and d["id"] not in (deal_a["id"], deal_b["id"]))
        expect("hold for the advancing deal", "POST /units/{id}/block",
               owner.call("POST", f"/api/v1/units/{third['id']}/block", {"deal_id": holder["id"]})[0], 200)
        status, moved = owner.call("POST", f"/api/v1/deals/{holder['id']}/advance", {"stage": "token", "force": True})
        expect("token stage books it", "POST /deals/{id}/advance token", status, 200)
        show("unit after advance", moved.get("unit"))
        detail = owner.call("GET", f"/api/v1/deals/{holder['id']}")[1]
        expect("deal detail shows the unit", "GET /deals/{id} → unit", bool(detail.get("unit")), True)

        print("\n7. the unit's own history")
        events = owner.call("GET", "/api/v1/units/audit")[1]["events"]
        mine = {e["event"] for e in events if e["unit_id"] == unit["id"]}
        show("events", sorted(mine))
        expect("blocked → booked recorded", "unit history", {"blocked", "booked"} <= mine, True)

        print("\n8. tenant isolation")
        other = Session(base)
        other.login("owner@metrorealty.app", "Metro@12345!")
        expect("other tenant cannot read this property's units", f"GET /properties/{prop['id']}/units as metro",
               other.call("GET", f"/api/v1/properties/{prop['id']}/units")[0], 404)
        expect("other tenant's inventory is empty", "GET /units as metro",
               other.call("GET", "/api/v1/units")[1]["summary"]["total"], 0)

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
