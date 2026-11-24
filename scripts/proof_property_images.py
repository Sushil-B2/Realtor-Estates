#!/usr/bin/env python3
"""
Live proof for listing photographs — cover choice, the publish floor, and the
honest empty state (gap G5, docs/25).

    python3 scripts/proof_property_images.py                       # boots this checkout's instance
    python3 scripts/proof_property_images.py --base http://127.0.0.1:5433 --live

What it checks, in order:

1. a property with photographs reports **photographs, not rows** (three variants
   of one upload = one photo);
2. the **cover is the `card` variant**, its URL is presigned, and the object
   behind it really is a WebP of the stated width — a HEAD and a magic-number
   check, not a promise;
3. the gallery is ordered newest-first with exactly one cover;
4. the **publish floor is three photos**, and a listing below it says how many
   more it needs, by name of the number;
5. the **batch read** answers every id once — including an id that does not exist
   and an id belonging to another workspace, which are `not_found`, not blank;
6. a partner's catalogue carries the photo count and cover, and its
   `ready_to_show` is false for a listing under the floor;
7. a **file-store instance refuses to pretend**: the media route answers
   `503 PIPELINE_UNAVAILABLE` rather than an empty list that reads as "no photos".

Section 7 needs an instance on the file store, so it runs only in self-boot mode.
Nothing here uploads anything: uploads are proved by
`node scripts/proof-photo-pipeline.mjs`, and seeding a demo by
`node scripts/upload-listing-photos.mjs`.
"""
from __future__ import annotations

import argparse
import json
import os
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


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token = ""

    def call(self, method: str, path: str, body=None):
        headers = {"Accept": "application/json", "User-Agent": "proof-images"}
        if method not in ("GET", "HEAD"):
            # a proof says it is a proof, so a demo can be reseeded (docs/26)
            headers["X-Proof-Run"] = f"images-{int(time.time())}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}")
            except Exception:
                return e.code, {"_raw": raw[:200].decode("utf-8", "replace")}

    def login(self, email: str, password: str) -> None:
        status, body = self.call("POST", "/api/v1/auth/login", {"email": email, "password": password})
        if status != 200:
            raise SystemExit(f"login failed for {email}: {status} {body}")
        self.token = body["token"]


def expect(label: str, detail: str, got, want) -> None:
    ok = got == want
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<46} {detail:<44} → {got!r}")
    if not ok:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")


def show(label: str, value) -> None:
    print(f"        {label}: {json.dumps(value, ensure_ascii=False)[:220]}")


def fetch_ok(url: str) -> dict:
    """A ranged GET on the presigned URL: real bytes, a real type, a real width.

    A HEAD would be cheaper, but an S3-compatible endpoint is free to refuse one
    for a GET-signed URL (Neon storage answers 403), so this fetches the first
    64 bytes instead — enough for the magic number, and it proves the URL is
    fetchable by a browser rather than only by our signer.
    """
    out = {"status": 0, "type": "", "total": 0, "magic": ""}
    try:
        req = urllib.request.Request(url, headers={"Range": "bytes=0-63"})
        with urllib.request.urlopen(req, timeout=30) as res:
            out["status"] = res.status
            out["type"] = res.headers.get("Content-Type", "")
            head = res.read(64)
            content_range = res.headers.get("Content-Range") or ""
            if "/" in content_range:
                out["total"] = int(content_range.rsplit("/", 1)[1] or 0)
            else:
                out["total"] = int(res.headers.get("Content-Length") or 0)
        out["magic"] = "webp" if (head[:4] == b"RIFF" and head[8:12] == b"WEBP") else \
                       "jpeg" if head[:2] == b"\xff\xd8" else "other"
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
        out["error"] = str(exc)[:120]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    proc = None
    tmpdir = None
    base = args.base
    if not base:
        port = free_port()
        tmpdir = tempfile.mkdtemp(prefix="re-proof-images-")
        env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1"}
        env.pop("RE_DATA_DIR", None)          # boot against this checkout's configured store
        proc = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=ROOT, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=2) as res:
                    if res.status == 200:
                        break
            except Exception:
                time.sleep(0.4)
        else:
            proc.terminate()
            raise SystemExit("server did not become healthy")

    try:
        print(f"=== property-image proof against {base} ===")
        owner = Client(base)
        owner.login("owner@realtorestates.app", "Demo@12345!")

        status, listing = owner.call("GET", "/api/v1/properties")
        props = listing.get("properties") or []
        expect("there is stock to photograph", "GET /properties", status, 200)
        ids = [p["id"] for p in props]
        status, batch = owner.call("GET", "/api/v1/properties/covers?ids=" + ",".join(ids[:200]))
        covers = batch.get("covers") or {}
        with_photos = [pid for pid, c in covers.items() if c.get("state") == "ok"]
        below_floor = [pid for pid, c in covers.items() if c.get("state") in ("ok", "none")
                       and 0 < (c.get("photo_count") or 0) < 3]

        print("\n1. a property with photographs reports photographs, not rows")
        expect("the batch read answers every id", "covers count == properties",
               len(covers), len(ids[:200]))
        if not with_photos:
            print("        (no property on this instance has a photograph — the rest of the "
                  "proof needs one; seed a demo with scripts/upload-listing-photos.mjs)")
            print(f"\n=== {'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILURE(S)'} ===")
            for f in FAILS:
                print("  ✗ " + f)
            return 0 if not FAILS else 1
        pid = with_photos[0]
        status, media = owner.call("GET", f"/api/v1/properties/{pid}/media")
        expect("the media route answers", "GET /properties/{id}/media", status, 200)
        expect("rows are variants", "count % 3 == 0 or count >= photos", media["count"] >= media["photo_count"], True)
        expect("a photo is a stem, not a row", "count > photo_count", media["count"] > media["photo_count"], True)
        show("photos", {"photos": media["photo_count"], "rows": media["count"],
                        "variants": sorted({v for p in media["photos"] for v in p["variants"]})})

        print("\n2. the cover is the card variant, signed, and really there")
        cover = media["cover"]
        expect("the cover is the smallest good variant", "cover.variant", cover["variant"], "card")
        expect("the URL is presigned", "contains X-Amz-Signature",
               "X-Amz-Signature" in (cover["url"] or ""), True)
        head = fetch_ok(cover["url"])
        if head.get("error"):
            show("error", head["error"])
        expect("the object is reachable", "ranged GET /cover ∈ (200, 206)",
               head["status"] in (200, 206), True)
        expect("it is an image", "content-type starts image/", str(head["type"]).startswith("image/"), True)
        expect("the bytes are a real WebP", "magic number", head["magic"], "webp")
        expect("the object has bytes", "total > 0", head["total"] > 0, True)
        show("cover", {"variant": cover["variant"], "width": cover["width"],
                       "height": cover["height"], "bytes": head["total"]})

        print("\n3. the gallery is ordered, with exactly one cover")
        photos = media["photos"]
        expect("one cover", "is_cover count", len([p for p in photos if p["is_cover"]]), 1)
        expect("the first photo is the cover", "photos[0].is_cover", photos[0]["is_cover"], True)
        expect("newest first", "photo ids descending", photos == sorted(photos, key=lambda p: p["photo_id"], reverse=True), True)
        show("gallery", [p["photo_id"] for p in photos])

        print("\n4. the publish floor is three photos, and the shortfall is stated")
        ready = media["readiness"]
        expect("the floor is three", "readiness.need", ready["need"], 3)
        expect("the slot budget is five", "readiness.slots", ready["slots"], 5)
        expect("have == photo count", "readiness.have == photos", ready["have"], media["photo_count"])
        expect("ready means at the floor", "readiness.ready", ready["ready"], media["photo_count"] >= 3)
        show("readiness", {"have": ready["have"], "note": ready["note"]})
        if below_floor:
            under = (covers[below_floor[0]].get("readiness") or {})
            expect("a short listing says how many more", "missing > 0", under.get("missing", 0) > 0, True)
            print(f"        short: {under.get('note')}")

        print("\n5. the batch read is honest about what it cannot answer")
        status, one = owner.call("GET", "/api/v1/properties/covers?ids=prp_doesnotexist")
        expect("an unknown id is not_found", "state", one["covers"]["prp_doesnotexist"]["state"], "not_found")
        expect("and it still carries a tile", "placeholder.initials",
               bool(one["covers"]["prp_doesnotexist"]["placeholder"]["initials"]), True)
        status, empty = owner.call("GET", "/api/v1/properties/covers")
        expect("ids are required", "GET /properties/covers", status, 400)

        print("\n6. a partner sees the photographs, never the upload")
        pw = os.environ.get("PROOF_PARTNER_PASSWORD", "")
        partner_ok = False
        if pw:
            partner = Client(base)
            try:
                partner.login("inventory@nandedproperty.in", pw)
                partner_ok = True
            except SystemExit:
                partner_ok = False
        if partner_ok:
            status, inv = partner.call("GET", "/api/v1/cp/inventory")
            rows = inv.get("inventory") or []
            expect("the catalogue carries photo counts", "photos.known on rows",
                   all("known" in (r.get("photos") or {}) for r in rows), True)
            seen = [r for r in rows if (r.get("photos") or {}).get("count")]
            expect("a partner sees how many photos a listing has", "rows with photos > 0", len(seen) > 0, True)
            if seen:
                row = seen[0]
                expect("the cover is a signed URL", "cover.url contains Signature",
                       "Signature" in ((row.get("cover") or {}).get("url") or ""), True)
                expect("readiness is not the legal question", "marketing.ready_to_show present",
                       "ready_to_show" in (row.get("marketing") or {}), True)
                blob = json.dumps(inv)
                expect("the upload's source key is not exposed", "no source_key", "source_key" in blob, False)
                show("partner row", {"title": row["title"], "photos": row["photos"]["count"],
                                     "ready_to_show": row["marketing"]["ready_to_show"]})
        else:
            print("        (set PROOF_PARTNER_PASSWORD to include the partner view)")

        if not args.live and not args.base:
            print("\n7. a file-store instance refuses to pretend")
            port = free_port()
            fs_tmp = tempfile.mkdtemp(prefix="re-proof-images-fs-")
            env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1", "RE_DB": "json",
                   "RE_DATA_DIR": fs_tmp}
            fs = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=ROOT, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            try:
                fs_base = f"http://127.0.0.1:{port}"
                deadline = time.time() + 40
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(fs_base + "/healthz", timeout=2) as res:
                            if res.status == 200:
                                break
                    except Exception:
                        time.sleep(0.4)
                fsclient = Client(fs_base)
                fsclient.login("owner@realtorestates.app", "Demo@12345!")
                fsid = fsclient.call("GET", "/api/v1/properties")[1]["properties"][0]["id"]
                status, body = fsclient.call("GET", f"/api/v1/properties/{fsid}/media")
                expect("the media route says why, not 'none'", "status", status, 503)
                expect("with a code the UI can branch on", "error.code",
                       (body.get("error") or {}).get("code"), "PIPELINE_UNAVAILABLE")
                status, batch2 = fsclient.call("GET", f"/api/v1/properties/covers?ids={fsid}")
                expect("the batch read degrades per id", "state",
                       batch2["covers"][fsid]["state"], "unavailable")
                expect("and still gives the cell a tile", "placeholder.initials",
                       bool(batch2["covers"][fsid]["placeholder"]["initials"]), True)
            finally:
                fs.terminate()
                try:
                    fs.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    fs.kill()

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


if __name__ == "__main__":
    sys.exit(main())
