# 50 — Stateless W3C OTLP Observability, Enterprise OIDC/SSO & Automated `sa11y` WCAG 2.2 AA Gate (`v1.39.0`)

Closes Competitive Benchmark Gaps #5 and #6 against `hyperdx` (stateless OTel /
ClickHouse observability), Enterprise CRM Corporate SSO (`Okta` / `Microsoft
Entra ID` / `Google Workspace`), and Salesforce `sa11y` automated accessibility
governance.

## Capabilities & Security Invariants

1. **Stateless W3C `traceparent` + Bounded OTLP v1 Telemetry (`app/enterprise_observability_sso.py`):**
   - Validates incoming `traceparent` (`00-<32hex>-<16hex>-<02hex>`), rejects all-zero trace/span IDs, emits correlated response `traceparent` headers, and stores secret-redacted spans in a bounded `1,000`-span ring buffer.
   - `GET /api/v1/observability/traces`, `GET /api/v1/observability/otlp`, `POST /api/v1/observability/flush`: enforces strict per-tenant trace isolation (`span.tenant_id == ctx.tid`) and exports OpenTelemetry `resourceSpans` JSON via SSRF-guarded `sec.safe_urlopen`.

2. **Enterprise OIDC / Corporate SSO (`/api/v1/sso/config`, `/api/v1/auth/sso/start`, `/api/v1/auth/sso/callback`):**
   - Pure-stdlib RFC 8017 §8.2.2 `RS256` JWKS signature verification (`>= 2048-bit` RSA modulus, odd exponent `>= 65537`, constant-time full-length EMSA-PKCS1-v1_5 check).
   - Blocks `alg: none`, `HS256` public-key algorithm confusion, SSRF IdP URLs, unverified emails, unauthorized email domains (`allowed_domains`), privilege escalation on JIT provisioning (clamped to `viewer`/`agent`/`manager`), and `state`/`nonce` replays (`claim_once`).

3. **Automated `sa11y`-Style WCAG 2.2 AA Accessibility Gate (`GET /api/v1/accessibility/audit`, `scripts/check_a11y.py`):**
   - Audits `web/index.html`, `/site`, and published `/f/<slug>` funnels for `html-has-lang`, `document-title`, `meta-viewport`, `heading-order`, `image-alt`, `form-field-label`, `button-name`, and W3C relative-luminance `color-contrast` (`>= 4.5:1`), wired directly into `npm run verify`.
