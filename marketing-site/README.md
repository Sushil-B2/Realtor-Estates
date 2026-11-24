# Realtor Estates marketing website integration

Imported from https://github.com/Sushil-B2/Realtor-Estates, preserving the React/TypeScript website separately from the Python CRM. This directory is the editable marketing source; `web/website/` is its deployable static output.

## Build

Use Node.js 22 or later:

```sh
npm ci --legacy-peer-deps --ignore-scripts
npm run build
```

Copy the contents of the resulting `dist/` directory into the platform's `web/website/` directory after a secret scan. Do not copy environment files, local data, `node_modules`, or unrelated public source/configuration artifacts. The platform bundle includes `web/website`, not this directory; rebuild and copy after source changes.

## Operator workflow

1. Open the CRM's Partners & Growth → Funnels & Website section.
2. Select “Realtor Estates Nagpur · imported 3D website”.
3. Select a published CRM funnel as the enquiry destination.
4. Open the selected website. Its `/website/?funnel=...` URL is shareable. Project navigation remains under `/website/projects/...`.

The selector does not replace the default `/site` landing page or existing `/f/<slug>` funnels. Without a selected published funnel, the imported site is preview-only for enquiry capture. The funnel slug is retained per browser tab while navigating marketing pages. The form bridge retrieves the existing public funnel's anti-bot token and submits through the CRM's consent-gated public handler. The server resolves the receiving tenant from the published funnel, never a caller-provided tenant ID.

This integration does not run the old Express local-CRM server. Its anonymous lead/analytics APIs are not exposed. The public CRM viewer has been replaced by a sign-in link. Optional event/heatmap telemetry is not recorded. Enquiries do not imply a confirmed appointment, delivered document pack, sent email or scheduled automation.

## Safety and known limitations

- A credential-pattern-bearing public configuration artifact in the upstream repository was excluded, together with unrelated source/config artifacts in `public/assets/aistudio`. If the upstream credential was real, its owner must rotate it and separately remediate public Git history. This integration does not rewrite that history.
- No Google Maps key is imported. Map features requiring a key remain unavailable; provision only an appropriately domain-restricted public browser key after review. Vite environment-file discovery is disabled for this build.
- The platform's strict same-origin CSP remains in place. Third-party fonts, remote images, maps and other external resources may be blocked; no broad CSP relaxation was added.
- This is a JavaScript-dependent, media-heavy website, unlike the lightweight server-rendered site. Validate mobile performance, accessibility, image/document rights, inventory status, pricing and legal claims before public promotion.
- The public submission handler supports a bounded requirement field; long visit notes are truncated and do not create calendar appointments. Advisors must confirm requested visits inside the CRM.
- Source attribution does not establish ownership or licensing rights. Confirm authorization for all imported content before publishing.
- Read the root `AGENTS.md` for mandatory secret-handling rules. Never copy the upstream secret-bearing configuration into a deployment or commit.
