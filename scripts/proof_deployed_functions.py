#!/usr/bin/env python3
"""
Proof against the **deployed** Neon Functions (`docs/15 §7`, `docs/36`, gap **G3**).

    python3 scripts/proof_deployed_functions.py \
        --photos-url  https://<branch>-propertyphotos.compute.../ \
        --webhook-url https://<branch>-paymentswebhook.compute.../ \
        --sweep-url   https://<branch>-compliancesweep.compute.../ \
        --fire-sweep

Why this is separate from the local proofs (`npm run photos:proof` and friends):
those run `functions/dev-serve.mjs` on loopback, which proves the *code* and the
*database*, not the *deployment*. Gap **G3** in `PLANS.md` was "built and proved
locally, never deployed".

What real cloud deployment taught us (fixing two assumptions in the pre-deploy
draft of this script):
  1. Neon's cloud edge gateway **strips** client-supplied `x-neon-trigger-invocation-id`
     headers on external HTTP ingress. External callers cannot forge a trigger
     delivery at all (`401 missing_header`); real deliveries arrive only from
     Neon's cloud trigger plane (`storage_object_created` on S3 upload and
     `schedule` on cron).
  2. `payments-webhook` verifies `x-razorpay-signature` with `PAYMENTS_WEBHOOK_SECRET`
     (not `NEON_TRIGGER_SECRET`), writes to `re_webhook_events` via Neon's
     auto-injected `DATABASE_URL`, and deduplicates replays on `(provider, event_id)`.

Every synthetic S3 object, Postgres proof row, and temporary cron trigger created
during this proof is cleaned up in `finally` so the demo workspace stays spotless.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config as cfg  # noqa: E402

FAILS: list[str] = []
SKIPS: list[str] = []


def load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, _, val = s.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def req_json(method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            raw, status = res.read(), res.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except Exception as exc:
        return 0, {"error": str(exc)}
    try:
        return status, json.loads(raw or b"{}")
    except Exception:
        return status, {"_raw": raw[:300].decode("utf-8", "replace")}


def expect(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label:<58}" + (f" → {detail}" if detail else ""))
    if not ok:
        FAILS.append(f"{label} ({detail})")


def skip(label: str) -> None:
    print(f"  [SKIP] {label:<58} → no URL supplied")
    SKIPS.append(label)


def _node_s3_and_db_photo_proof(env: dict[str, str], prop_id: str) -> dict:
    """Upload a real JPEG to S3 `projectassets/properties/<prop_id>/shot.jpg`,
    wait for Neon's cloud `on-property-photo` trigger to invoke `propertyphotos`
    and populate 3 WebP variants in `re_property_media`, then clean up S3 + DB."""
    script = r"""
import { PutObjectCommand, DeleteObjectCommand, HeadObjectCommand, S3Client } from "@aws-sdk/client-s3";
import pg from "pg";
import sharp from "sharp";

const propId = process.env.PROOF_PROP_ID;
const bucket = process.env.NEON_STORAGE_BUCKET || "projectassets";
const s3 = new S3Client({
  region: process.env.NEON_STORAGE_REGION || "ap-southeast-1",
  endpoint: process.env.NEON_STORAGE_ENDPOINT,
  forcePathStyle: true,
  credentials: {
    accessKeyId: process.env.NEON_STORAGE_ACCESS_KEY || "",
    secretAccessKey: process.env.NEON_STORAGE_SECRET_KEY || "",
  },
});
const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL, max: 2 });
const srcKey = `properties/${propId}/cloud-proof.jpg`;
let createdKeys = [srcKey];

try {
  const jpg = await sharp({
    create: { width: 640, height: 480, channels: 3, background: { r: 36, g: 92, b: 140 } },
  }).jpeg({ quality: 85 }).toBuffer();

  await s3.send(new PutObjectCommand({
    Bucket: bucket, Key: srcKey, Body: jpg, ContentType: "image/jpeg",
  }));

  let rows = [];
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    const res = await pool.query(
      "select variant, object_key, format, width, height, bytes from re_property_media where property_id = $1 order by variant",
      [propId]
    );
    rows = res.rows;
    if (rows.length >= 3) break;
    await new Promise((r) => setTimeout(r, 1500));
  }

  let s3VariantsOk = 0;
  for (const r of rows) {
    createdKeys.push(r.object_key);
    const head = await s3.send(new HeadObjectCommand({ Bucket: bucket, Key: r.object_key }));
    if ((head.ContentLength || 0) > 0) s3VariantsOk += 1;
  }

  console.log(JSON.stringify({
    ok: rows.length === 3 && s3VariantsOk === 3,
    variants: rows.map((r) => r.variant),
    s3_variants_verified: s3VariantsOk,
  }));
} finally {
  for (const k of createdKeys) {
    try { await s3.send(new DeleteObjectCommand({ Bucket: bucket, Key: k })); } catch {}
  }
  try { await pool.query("delete from re_property_media where property_id = $1", [propId]); } catch {}
  await pool.end();
}
"""
    run_env = {
        **os.environ,
        **env,
        "DATABASE_URL": cfg.get("RE_ADMIN_DATABASE_URL") or cfg.get("DATABASE_URL", ""),
        "PROOF_PROP_ID": prop_id,
    }
    p = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, env=run_env, capture_output=True, text=True, timeout=75,
    )
    if p.returncode != 0:
        return {"ok": False, "error": p.stderr.strip()[-200:]}
    return json.loads(p.stdout.strip().splitlines()[-1])


def _pg_exec(sql: str, params: tuple = (), *, admin: bool = False) -> list[tuple]:
    import psycopg
    dsn = (cfg.get("RE_ADMIN_DATABASE_URL") if admin else "") or cfg.get("DATABASE_URL", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos-url", default=os.environ.get("PHOTOS_URL", ""))
    ap.add_argument("--webhook-url", default=os.environ.get("WEBHOOK_URL", ""))
    ap.add_argument("--sweep-url", default=os.environ.get("SWEEP_URL", ""))
    ap.add_argument("--env-file", default=os.path.join(ROOT, ".env.local"))
    ap.add_argument("--fire-sweep", action="store_true",
                    help="also create a temporary 1-min schedule trigger to witness live cloud execution of compliancesweep")
    args = ap.parse_args()

    env = load_env_file(args.env_file)
    webhook_secret = env.get("PAYMENTS_WEBHOOK_SECRET", "")
    print("\n=== deployed-function proof (Neon Cloud ap-southeast-1) ===")

    # Clean up any leftover proof webhook events from prior manual runs
    try:
        _pg_exec("delete from re_webhook_events where tenant_id = 'tnt_g3_proof'", admin=True)
    except Exception:
        pass

    # ---------------------------------------------------------------- photos
    print("\n1. propertyphotos — edge trigger-header stripping + real S3 upload → sharp WebP pipeline")
    if not args.photos_url:
        skip("photos: health probe")
        skip("photos: external forged trigger header stripped at cloud edge")
        skip("photos: S3 upload triggers cloud WebP derivation + DB rows")
    else:
        st_get, gbody = req_json("GET", args.photos_url)
        expect("photos: GET readiness probe returns 200",
               st_get == 200 and gbody.get("function") == "property-photos", str(st_get))

        forged = json.dumps({
            "version": 1, "invocation_id": "inv_forged_1",
            "trigger": {"type": "storage_object_created", "id": "trig_fake", "name": "on-property-photo"},
            "data": {"bucket_name": "projectassets", "object_key": "properties/prp_1/x.jpg"},
        }).encode()
        st_forge, fbody = req_json("POST", args.photos_url, forged, {
            "Content-Type": "application/json",
            "x-neon-trigger-invocation-id": "inv_forged_1",
        })
        expect("photos: forged external trigger delivery refused (401)",
               st_forge == 401 and fbody.get("error") == "missing_header", f"{st_forge} {fbody.get('error')}")

        prop_id = f"prp_g3_{int(time.time())}"
        res_photo = _node_s3_and_db_photo_proof(env, prop_id)
        expect("photos: real S3 upload → cloud trigger → 3 WebP variants in S3 & Postgres",
               bool(res_photo.get("ok")), json.dumps(res_photo))

    # --------------------------------------------------------------- webhook
    print("\n2. paymentswebhook — HMAC verification, Postgres write, and replay deduplication")
    if not args.webhook_url:
        skip("webhook: unsigned event refused")
        skip("webhook: bad signature refused")
        skip("webhook: valid HMAC event persisted once and deduplicated on replay")
    else:
        st_get, winfo = req_json("GET", args.webhook_url)
        expect("webhook: GET readiness reports configured=true",
               st_get == 200 and winfo.get("configured") is True, str(winfo))

        st_un, _ = req_json("POST", args.webhook_url, b'{"event":"payment.captured"}',
                            {"Content-Type": "application/json"})
        expect("webhook: unsigned delivery refused (401)", st_un == 401, str(st_un))

        st_bad, _ = req_json("POST", args.webhook_url, b'{"event":"payment.captured"}',
                             {"Content-Type": "application/json", "x-razorpay-signature": "00" * 32})
        expect("webhook: forged HMAC refused (401)", st_bad == 401, str(st_bad))

        event_id = f"evt_g3_deployed_{int(time.time())}"
        payload = json.dumps({
            "event": "payment.captured", "event_id": event_id,
            "payload": {"payment": {"entity": {
                "id": "pay_g3_deployed", "amount": 1250000, "currency": "INR",
                "status": "captured", "notes": {"tenant_id": "tnt_g3_proof"},
            }}},
        }).encode()
        sig = hmac.new(webhook_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        hdrs = {"Content-Type": "application/json", "x-razorpay-signature": sig}
        try:
            st1, b1 = req_json("POST", args.webhook_url, payload, hdrs)
            expect("webhook: signed delivery accepted (duplicate=false)",
                   st1 == 200 and b1.get("ok") is True and b1.get("duplicate") is False, json.dumps(b1)[:100])
            st2, b2 = req_json("POST", args.webhook_url, payload, hdrs)
            expect("webhook: replay deduplicated by unique DB index (duplicate=true)",
                   st2 == 200 and b2.get("ok") is True and b2.get("duplicate") is True, json.dumps(b2)[:100])
            rows = _pg_exec(
                "select count(*)::int, amount from re_webhook_events where event_id = %s group by amount",
                (event_id,),
            )
            expect("webhook: exactly 1 row stored in Neon Postgres with exact paise amount",
                   len(rows) == 1 and rows[0][0] == 1 and rows[0][1] == 1250000, str(rows))
        finally:
            _pg_exec("delete from re_webhook_events where event_id = %s or tenant_id = 'tnt_g3_proof'",
                     (event_id,), admin=True)

    # ----------------------------------------------------------------- sweep
    print("\n3. compliancesweep — edge trigger-header stripping + cloud schedule trigger")
    if not args.sweep_url:
        skip("sweep: GET readiness")
        skip("sweep: forged external trigger refused")
    else:
        st_get, sbody = req_json("GET", args.sweep_url)
        expect("sweep: GET readiness probe returns 200 (02:00 UTC)",
               st_get == 200 and sbody.get("function") == "compliance-sweep", str(sbody))

        forged_s = json.dumps({
            "version": 1, "invocation_id": "inv_forged_s1",
            "trigger": {"type": "schedule", "id": "trig_fake", "name": "nightly-compliance"},
            "data": {"scheduled_at": "2026-11-22T02:00:00Z"},
        }).encode()
        st_fs, fsbody = req_json("POST", args.sweep_url, forged_s, {
            "Content-Type": "application/json",
            "x-neon-trigger-invocation-id": "inv_forged_s1",
        })
        expect("sweep: forged external schedule delivery refused (401)",
               st_fs == 401 and fsbody.get("error") == "missing_header", f"{st_fs} {fsbody.get('error')}")

        if args.fire_sweep:
            trig_name = f"g3-sweep-proof-{int(time.time())}"
            trig_id = ""
            before_ts = int(time.time()) - 2
            try:
                c_out = subprocess.check_output(
                    ["bash", "scripts/neon-cli.sh", "triggers", "create",
                     "--function-slug", "compliancesweep", "--name", trig_name,
                     "--cron", "* * * * *", "--output", "json"],
                    cwd=ROOT, text=True, timeout=45,
                )
                trig_id = json.loads(c_out).get("trigger_id", "")
                matched = []
                deadline = time.time() + 90
                while time.time() < deadline:
                    matched = _pg_exec(
                        "select id, status, audit->>'ok' from re_compliance_runs where trigger_name = %s and started_at >= %s limit 1",
                        (trig_name, before_ts),
                    )
                    if matched:
                        break
                    time.sleep(3)
                expect("sweep: live cloud cron trigger executed compliancesweep & verified audit chain",
                       len(matched) == 1 and matched[0][1] == "ok" and matched[0][2] == "true",
                       str(matched))
            finally:
                if trig_id:
                    subprocess.run(["bash", "scripts/neon-cli.sh", "triggers", "delete", trig_id],
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                try:
                    _pg_exec("delete from re_compliance_runs where trigger_name = %s", (trig_name,), admin=True)
                except Exception:
                    pass

    print()
    if SKIPS:
        print(f"=== {len(SKIPS)} CHECK(S) NOT PROVEN — supply the deployed URLs ===")
        return 2
    print(f"=== {'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' FAILURE(S)'} ===")
    for f in FAILS:
        print("  ✗ " + f)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
