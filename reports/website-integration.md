# Existing marketing website + CRM integration

## Implemented locally

Source repository: https://github.com/Sushil-B2/Realtor-Estates

- Imported editable React/TypeScript website into `marketing-site/`, keeping the CRM at the project root.
- Built and included deployable static output in `web/website/`.
- Added a website-option selector to Partners & Growth → Funnels & Website. Existing `/site` and `/f/<slug>` experiences remain intact.
- The imported option opens `/website/?funnel=<published-slug>`. Public project and legal deep links stay under `/website/`.
- Integrated enquiry submissions with the existing published public funnel handler, including anti-bot form tokens, consent checks and server-resolved tenant routing. No new anonymous CRM data endpoint was added.
- The imported site's public CRM drawer now links to private sign-in instead of exposing records. Legacy anonymous telemetry is disabled, not represented as persisted analytics.
- Confirmation copy no longer claims delivered messages or a calendar booking based solely on enquiry capture.
- Excluded unrelated public source/configuration artifacts, including a file matching a database-credential pattern. If real, the credential must be rotated by its owner and the original public history remediated separately; no value was displayed. Existing Git history is not rewritten by this integration.

## Verification

- Marketing production build: passed.
- Existing JavaScript syntax gate: passed, 15 files.
- Project secret scanner: no findings in its configured scope.
- Existing accessibility parser: four existing surfaces, zero reported violations. This is not an accessibility certification of the imported React website.
- Focused integration/documentation/bootstrap suite: 14 passed.
- Full isolated Python regression suite: **662 passed, 0 failed, 16 skipped**.
- Chromium check (GPU disabled in the sandbox): template selection, imported homepage render, synthetic consented enquiry HTTP 200, project deep-link HTTP 200, zero page errors.
- Full TypeScript typecheck: incomplete/failed in this sandbox; compiler child terminated with SIGKILL. Vite production bundling passed, but this does not replace a successful typecheck.
- Some earlier browser attempts stalled in the media-heavy renderer. Browser coverage is limited; complete mobile/WebGL/performance and real-user accessibility review before public promotion.

## Repository delivery

Prepared a combined working tree based on the target repository's existing `main`, on local branch `integration/crm-and-marketing-website`. The existing website source is moved under `marketing-site`; built marketing assets are under `web/website`. A new branch avoids changing the existing main website's deployment settings. No force-push or history rewrite is authorized or performed.

A GitHub write-access dry run failed. Actual push status must be checked separately; a local branch/commit or downloadable ZIP is not evidence of upload. No Vercel production deployment is part of this integration verification.

## Operational limitations

See `marketing-site/README.md`. The default public site is not replaced by choosing a preview option. Google Maps requires separately configured, restricted public browser credentials; no keys were imported. Strict same-origin CSP is retained, which can block third-party fonts/maps/media. Review imported inventory/pricing/legal statements and media/document rights before publication. Long visit preferences are truncated by the platform requirement field and do not create calendar appointments.
