import { defineConfig } from "@neon/config/v1";

/**
 * Neon policy for this project (project broad-sound-07229115, branch production).
 *
 * Declared here and applied with `neon deploy`:
 *   functions  propertyphotos      — the listing-photo pipeline (functions/property-photos.ts)
 *   functions  compliancesweep     — nightly retention + audit-chain + orphan-media sweep
 *   functions  paymentswebhook     — HMAC-verified inbound payment webhooks (public endpoint)
 *   triggers   on-property-photo   — storage_object_created on `projectassets`, prefix `properties/`
 *   triggers   nightly-compliance  — schedule, 02:00 UTC daily (07:30 IST)
 *
 * The template's placeholder `hello` function was removed: it pointed at a
 * `./hello.ts` that does not exist, so `neon deploy` could never apply this
 * policy. `auth`, `aiGateway`, `preview.realtime` and the `assets` bucket are the
 * original declarations, untouched — removing them from this file would change
 * the branch's services, which is not what adding a function should do.
 */
export default defineConfig({
  preview: {
    realtime: true,
  },
  auth: true,
  aiGateway: true,
  functions: {
    // Slugs are ^[a-z0-9]{1,20}$ — no hyphens, no underscores. The source files
    // keep their hyphenated names; only the deployed slug is constrained.
    propertyphotos: {
      name: "Property photo pipeline",
      source: "./functions/property-photos.ts",
      externalPackages: ["sharp"],
      env: {
        NEON_STORAGE_BUCKET: "projectassets",
        NEON_STORAGE_REGION: { value: process.env.NEON_STORAGE_REGION, required: "default-branch" },
        NEON_STORAGE_ENDPOINT: { value: process.env.NEON_STORAGE_ENDPOINT, required: "default-branch" },
        NEON_STORAGE_ACCESS_KEY: { value: process.env.NEON_STORAGE_ACCESS_KEY, required: "default-branch" },
        NEON_STORAGE_SECRET_KEY: { value: process.env.NEON_STORAGE_SECRET_KEY, required: "default-branch" },
      },
    },
    compliancesweep: {
      name: "Nightly compliance sweep",
      source: "./functions/compliance-sweep.ts",
      env: {
        NEON_STORAGE_BUCKET: "projectassets",
        NEON_STORAGE_REGION: { value: process.env.NEON_STORAGE_REGION, required: "default-branch" },
        NEON_STORAGE_ENDPOINT: { value: process.env.NEON_STORAGE_ENDPOINT, required: "default-branch" },
        NEON_STORAGE_ACCESS_KEY: { value: process.env.NEON_STORAGE_ACCESS_KEY, required: "default-branch" },
        NEON_STORAGE_SECRET_KEY: { value: process.env.NEON_STORAGE_SECRET_KEY, required: "default-branch" },
      },
    },
    paymentswebhook: {
      name: "Payments webhook receiver",
      source: "./functions/payments-webhook.ts",
      env: {
        PAYMENTS_WEBHOOK_SECRET: { value: process.env.PAYMENTS_WEBHOOK_SECRET, required: "default-branch" },
      },
    },
  },
  buckets: {
    // The bucket that actually exists on this branch is `projectassets` — the
    // env files said `assets`, which is why every storage call returned
    // NoSuchBucket. Listings media stays private; the app serves it through
    // short-lived presigned GETs (app/neon.py::presign_get).
    projectassets: { access: "private" },
  },
  triggers: {
    "on-property-photo": {
      type: "storage_object_created",
      function: "propertyphotos",
      bucket: "projectassets",
      prefix: "properties/",
    },
    "nightly-compliance": {
      // 02:00 UTC = 07:30 IST: the sweep's findings are on screen when the
      // office opens, and the window is outside the demo's working hours so a
      // long run cannot collide with a burst of writes.
      type: "schedule",
      function: "compliancesweep",
      cron: "0 2 * * *",
    },
  },
  // Branch policy: per-branch tuning
  branch: (branch) => {
    if (branch.isDefault) {
      // Default branch: no overrides, uses project defaults
      return {};
    }
    if (!branch.exists) {
      // New non-default branches: auto-expire
      // Run `neon checkout <name>` to create a new branch with these settings
      return { ttl: "7d" };
    }
    // Existing branch: no changes
    return {};
  },
});
