#!/usr/bin/env node
/**
 * Safe, zero-secret-stdout deployment bundle builder & uploader.
 *
 * Guarantees:
 *   1. Runs `python3 scripts/check_secrets.py` before archiving — aborts on any finding.
 *   2. Packs ONLY allowlisted runtime directories (`app`, `db`, `web`, `redline`, `sentinel`, `reports`)
 *      and verifies zero `.env*`, `.secrets`, `store.json`, or key files exist in the archive.
 *   3. Reads S3 storage config silently in-memory from `.env.local` / `.env` without printing
 *      any secret, endpoint credential, or header to stdout/stderr.
 *   4. Emits ONLY `BUNDLE_KEY`, `BUNDLE_SHA256`, `BUNDLE_BYTES`, and `BUNDLE_FILES`.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { PutObjectCommand, S3Client } from "@aws-sdk/client-s3";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function loadEnvSilently() {
  const out = {};
  for (const name of [".env", ".env.local"]) {
    const full = join(ROOT, name);
    if (!existsSync(full)) continue;
    const raw = readFileSync(full, "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq <= 0) continue;
      const k = trimmed.slice(0, eq).trim();
      let v = trimmed.slice(eq + 1).trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
        v = v.slice(1, -1);
      }
      if (v) out[k] = v;
    }
  }
  return { ...out, ...process.env };
}

async function main() {
  // 1. Secret hygiene gate first
  execFileSync("python3", ["scripts/check_secrets.py"], { cwd: ROOT, stdio: "inherit" });

  // 2. Build deterministic clean tarball in /tmp
  let headSha = "release";
  try {
    headSha = execFileSync("git", ["rev-parse", "--short=7", "HEAD"], { cwd: ROOT, encoding: "utf8" }).trim();
  } catch {
    headSha = "release";
  }

  const workDir = mkdtempSync(join(tmpdir(), "re-bundle-"));
  const tarPath = join(workDir, "bundle.tar.gz");
  try {
    const includePaths = ["app", "db", "web", "redline", "sentinel", "reports"].filter((p) =>
      existsSync(join(ROOT, p))
    );
    execFileSync(
      "tar",
      [
        "-czf",
        tarPath,
        "--exclude=__pycache__",
        "--exclude=*.pyc",
        "--exclude=.env*",
        "--exclude=.secrets*",
        "--exclude=store.json",
        "--exclude=kms-kek.json",
        ...includePaths,
      ],
      { cwd: ROOT, stdio: "pipe" }
    );

    const listing = execFileSync("tar", ["-tzf", tarPath], { encoding: "utf8" })
      .split(/\r?\n/)
      .filter(Boolean);

    const forbidden = listing.filter((entry) =>
      /(^|\/)(\.env|\.secrets|store\.json|kms-kek\.json|.*\.(pem|key|p12))$/i.test(entry)
    );
    if (forbidden.length > 0) {
      console.error("ABORT: forbidden path detected in bundle archive.");
      process.exit(1);
    }

    const buf = readFileSync(tarPath);
    const sha256 = createHash("sha256").update(buf).digest("hex");
    const objectKey = `deployments/realtor-estates-${headSha}-${sha256.slice(0, 12)}.tar.gz`;

    if (process.argv.includes("--dry-run")) {
      console.log(`BUNDLE_KEY=${objectKey}`);
      console.log(`BUNDLE_SHA256=${sha256}`);
      console.log(`BUNDLE_BYTES=${buf.length}`);
      console.log(`BUNDLE_FILES=${listing.length}`);
      return;
    }

    const env = loadEnvSilently();
    const endpoint = env.NEON_STORAGE_ENDPOINT || "";
    const region = env.NEON_STORAGE_REGION || "ap-southeast-1";
    const bucket = env.NEON_STORAGE_BUCKET || "";
    const accessKeyId = env.NEON_STORAGE_ACCESS_KEY || "";
    const secretAccessKey = env.NEON_STORAGE_SECRET_KEY || "";

    if (!endpoint || !bucket || !accessKeyId || !secretAccessKey) {
      console.error("ABORT: Neon Object Storage variables missing in local environment.");
      process.exit(2);
    }

    const s3 = new S3Client({
      region,
      endpoint,
      forcePathStyle: true,
      credentials: { accessKeyId, secretAccessKey },
    });

    try {
      await s3.send(
        new PutObjectCommand({
          Bucket: bucket,
          Key: objectKey,
          Body: buf,
          ContentType: "application/gzip",
        })
      );
    } catch (err) {
      const code = (err && err.$metadata && err.$metadata.httpStatusCode) || "ERR";
      const name = (err && err.name) || "S3UploadError";
      console.error(`ABORT: bundle upload failed (${name}, HTTP ${code}). If keys were rotated, update .env.local.`);
      process.exit(3);
    }

    console.log(`BUNDLE_KEY=${objectKey}`);
    console.log(`BUNDLE_SHA256=${sha256}`);
    console.log(`BUNDLE_BYTES=${buf.length}`);
    console.log(`BUNDLE_FILES=${listing.length}`);
  } finally {
    rmSync(workDir, { recursive: true, force: true });
  }
}

main().catch(() => {
  console.error("ABORT: bundle operation failed; diagnostic details withheld.");
  process.exitCode = 1;
});
