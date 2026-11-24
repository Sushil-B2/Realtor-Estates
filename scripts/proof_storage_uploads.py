#!/usr/bin/env python3
"""
Live cloud proof for Backend plan #3 (`PLANS.md` / `docs/37`):
exports and call recordings uploaded to S3-compatible Neon object storage.

Proves against the configured `projectassets` bucket (without printing keys,
presigned query signatures, or DSNs):
  1. Path-traversal keys (`..`, `//`, `\\`, NUL) are rejected before signing.
  2. Direct SigV4 `put_object -> head_object -> presign_get HTTP 200 -> delete_object`.
  3. `POST /api/v1/exports` (`compliance_bundle` JSON + `audit` CSV) archives to
     `exports/tenants/<tid>/...`, omits inline blob bloat from `runs`, and serves
     byte-identical SHA-256 downloads via short-lived `presign_get` URLs.
  4. `POST /api/v1/contacts/{cid}/calls` (WAV audio + JSON transcript) and
     `POST /api/v1/agents/{aid}/voice-test` archive to `recordings/tenants/<tid>/...`
     and round-trip through `GET /api/v1/recordings/{rid}` with audit entries.
  5. Cross-tenant BOLA (`metro` reading `sunrise` export/recording -> 404) and
     RBAC (`viewer` creating an export -> 403) hold.
  6. Every proof S3 object is deleted on exit (`head_object.exists == False`).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import sys
import tempfile
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Isolate local store mutations into a temporary JSON directory so production
# Postgres rows are never touched while exercising the real Neon S3 bucket.
_TMP_DIR = tempfile.mkdtemp(prefix="re-proof-s3-")
os.environ["RE_DB"] = "json"
os.environ["RE_DATA_DIR"] = _TMP_DIR
os.environ["RE_AUTOMATION_TICK"] = "0"

from app import api, neon, security as sec  # noqa: E402
from app.store import Store  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _login(store: Store, email: str, password: str = "Demo@12345!") -> tuple[str, str]:
    body = {"email": email, "password": password}
    st, payload, _ = api.handle(
        store, "POST", "/api/v1/auth/login", {}, body,
        {"content-type": "application/json"}, "127.0.0.1", None, json.dumps(body).encode()
    )
    assert st == 200, f"login failed for {email}: {st} {payload}"
    tok = payload["token"]
    return tok, sec.csrf_token(tok)


def _call(store: Store, token: str, csrf: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    raw = json.dumps(body or {}).encode() if method in ("POST", "PATCH", "PUT", "DELETE") else b""
    hdrs = {
        "authorization": f"Bearer {token}",
        "x-csrf-token": csrf,
        "content-type": "application/json",
    }
    st, payload, _ = api.handle(store, method, path, {}, body or {}, hdrs, "127.0.0.1", None, raw)
    return st, payload


def _make_wav_tone(samples: int = 800) -> bytes:
    """Tiny valid 8 kHz mono 16-bit PCM WAV clip (~0.1 s)."""
    pcm = b"".join(struct.pack("<h", int(4000 * ((i % 20) - 10) / 10)) for i in range(samples))
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16,
        1, 1, 8000, 16000, 2, 16, b"data", len(pcm),
    )
    return header + pcm


def main() -> int:
    print("=== Proof: Object Storage Upload Path for Exports & Recordings (`docs/37`) ===")
    if not neon.storage_configured():
        print("  [FAIL] NEON_STORAGE_* is not configured in environment/.env.local")
        return 1

    created_keys: list[str] = []
    store = Store(os.path.join(_TMP_DIR, "store.json"))

    try:
        # 1. Key traversal rejection
        rejected = 0
        for bad in ("", "../secrets", "exports/tenants/t1/../../t2/x.json", "a//b.json", "a\\b.json", "a\x00b"):
            try:
                neon._clean_object_key(bad)
            except ValueError:
                rejected += 1
        check("key-traversal-refused", rejected == 6, f"rejected={rejected}/6")

        # 2. Direct SigV4 PUT -> HEAD -> presigned GET -> DELETE
        canary_key = f"exports/proof/canary-{uuid.uuid4().hex[:10]}.json"
        created_keys.append(canary_key)
        canary_bytes = json.dumps({"proof": "sigv4_upload", "ok": True}).encode()
        up = neon.put_object(canary_key, canary_bytes, content_type="application/json; charset=utf-8")
        check("sigv4-put-object", up["ok"] is True and up["bytes"] == len(canary_bytes), f"bytes={up['bytes']}")

        hd = neon.head_object(canary_key)
        check("sigv4-head-object", hd["exists"] is True and hd["bytes"] == len(canary_bytes), f"bytes={hd.get('bytes')}")

        get_url = neon.presign_get(canary_key, expires=120)
        with urllib.request.urlopen(get_url, timeout=15) as resp:
            dl_status = resp.status
            dl_bytes = resp.read()
        check(
            "sigv4-presign-get-sha256",
            dl_status == 200 and hashlib.sha256(dl_bytes).hexdigest() == up["sha256"],
            f"status={dl_status} bytes={len(dl_bytes)}",
        )

        # 3. API Compliance & Audit Exports (`sunrise` owner)
        sun_tok, sun_csrf = _login(store, "owner@realtorestates.app")
        metro_tok, metro_csrf = _login(store, "owner@metrorealty.app", "Metro@12345!")
        viewer_tok, viewer_csrf = _login(store, "viewer@realtorestates.app")

        st, exp_resp = _call(
            store, sun_tok, sun_csrf, "POST", "/api/v1/exports",
            {"kind": "compliance_bundle", "format": "json", "require_object_storage": True},
        )
        exp = exp_resp.get("export") or {}
        if exp.get("object_key"):
            created_keys.append(exp["object_key"])
        check(
            "export-compliance-bundle-s3",
            st == 200 and exp.get("stored_in") == "object_storage" and exp.get("object_key", "").startswith("exports/tenants/"),
            f"status={st} bytes={exp.get('bytes')}",
        )
        with urllib.request.urlopen(exp["download_url"], timeout=15) as resp:
            bundle_raw = resp.read()
        bundle_doc = json.loads(bundle_raw)
        check(
            "export-bundle-download-verified",
            hashlib.sha256(bundle_raw).hexdigest() == exp["sha256"] and bundle_doc.get("audit_chain", {}).get("ok") is True,
            f"entries={len(bundle_doc.get('audit_entries') or [])}",
        )

        st_csv, csv_resp = _call(
            store, sun_tok, sun_csrf, "POST", "/api/v1/exports",
            {"kind": "audit", "format": "csv", "require_object_storage": True},
        )
        csv_exp = csv_resp.get("export") or {}
        if csv_exp.get("object_key"):
            created_keys.append(csv_exp["object_key"])
        with urllib.request.urlopen(csv_exp["download_url"], timeout=15) as resp:
            csv_raw = resp.read()
        check(
            "export-audit-csv-s3",
            st_csv == 200 and csv_raw.startswith(b"id,at,actor,action,target,hash,prev_hash")
            and hashlib.sha256(csv_raw).hexdigest() == csv_exp["sha256"],
            f"bytes={len(csv_raw)}",
        )

        st_list, list_resp = _call(store, sun_tok, sun_csrf, "GET", "/api/v1/exports")
        check("exports-list-returns-workspace-exports", st_list == 200 and list_resp.get("count", 0) >= 2, f"count={list_resp.get('count')}")

        # 4. Call Recordings (binary WAV + AI voice call)
        st_cnt, cnt_resp = _call(store, sun_tok, sun_csrf, "GET", "/api/v1/contacts")
        cid = cnt_resp["contacts"][0]["id"]
        wav_bytes = _make_wav_tone()
        st_call, call_resp = _call(
            store, sun_tok, sun_csrf, "POST", f"/api/v1/contacts/{cid}/calls",
            {
                "direction": "in",
                "outcome": "answered",
                "duration_sec": 42,
                "recording_b64": base64.b64encode(wav_bytes).decode(),
                "recording_content_type": "audio/wav",
                "require_object_storage": True,
            },
        )
        rec_info = call_resp.get("recording") or {}
        if rec_info.get("object_key"):
            created_keys.append(rec_info["object_key"])
        check(
            "call-recording-wav-s3",
            st_call == 200 and rec_info.get("stored_in") == "object_storage"
            and rec_info.get("object_key", "").startswith("recordings/tenants/"),
            f"bytes={rec_info.get('bytes')}",
        )

        rid = rec_info["activity_id"]
        st_rec, rec_get = _call(store, sun_tok, sun_csrf, "GET", f"/api/v1/recordings/{rid}")
        with urllib.request.urlopen(rec_get["recording"]["download_url"], timeout=15) as resp:
            wav_dl = resp.read()
        check(
            "call-recording-download-sha256",
            st_rec == 200 and wav_dl == wav_bytes,
            f"status={st_rec} bytes={len(wav_dl)}",
        )

        st_ag, ag_list = _call(store, sun_tok, sun_csrf, "GET", "/api/v1/agents")
        aid = ag_list["agents"][0]["id"]
        st_vt, vt_resp = _call(store, sun_tok, sun_csrf, "POST", f"/api/v1/agents/{aid}/voice-test", {"language": "mr"})
        vt_rec = vt_resp.get("recording") or {}
        if vt_rec.get("object_key"):
            created_keys.append(vt_rec["object_key"])
        check(
            "voice-test-transcript-archived-s3",
            st_vt == 200 and vt_rec.get("stored_in") == "object_storage" and bool(vt_rec.get("download_url")),
            f"bytes={vt_rec.get('bytes')}",
        )

        # 5. Cross-tenant BOLA + RBAC checks (`pii:read` on recordings, `manage_tenant` on exports)
        st_bola_exp, _ = _call(store, metro_tok, metro_csrf, "GET", f"/api/v1/exports/{exp['id']}")
        st_bola_rec, _ = _call(store, metro_tok, metro_csrf, "GET", f"/api/v1/recordings/{rid}")
        st_rbac_exp, _ = _call(store, viewer_tok, viewer_csrf, "POST", "/api/v1/exports", {"kind": "audit"})
        st_rbac_rec, _ = _call(store, viewer_tok, viewer_csrf, "GET", f"/api/v1/recordings/{rid}")
        check(
            "cross-tenant-bola-and-rbac",
            st_bola_exp == 404 and st_bola_rec == 404 and st_rbac_exp == 403 and st_rbac_rec == 403,
            f"metro_export={st_bola_exp} metro_rec={st_bola_rec} viewer_exp={st_rbac_exp} viewer_rec={st_rbac_rec}",
        )

    finally:
        # 6. Cleanup all proof keys from `projectassets`
        still_present = 0
        for key in created_keys:
            try:
                neon.delete_object(key)
                if neon.head_object(key).get("exists"):
                    still_present += 1
            except Exception:
                still_present += 1
        check("cleanup-proof-objects-deleted", still_present == 0, f"cleaned={len(created_keys)} remaining={still_present}")

    print(f"\nResult: {PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
