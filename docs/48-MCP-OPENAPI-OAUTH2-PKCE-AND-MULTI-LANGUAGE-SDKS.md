# 48 — Governed Native MCP Server, OpenAPI 3.1, OAuth 2.1 PKCE & Multi-Language SDKs (`v1.37.0`)

Closes Competitive Benchmark Gap #1 against Salesforce Headless Toolkit /
Agentforce, GoHighLevel API v2/v3 Marketplace, `Go-High-Level-MCP-2026-Complete`,
and `gohighlevel-php-sdk` with Zero-Trust agentic safeguards.

## Capabilities

1. **Governed Model Context Protocol (`2025-06-18` JSON-RPC 2.0):**
   - **Streamable HTTP (`POST /api/v1/mcp`)** and **CLI `stdio` (`scripts/mcp_stdio.py`)**.
   - **4 Profiles:** `readonly` (zero writes), `curated`, `stable`, and `full`.
   - **Readiness Diagnostics (`GET /api/v1/mcp/doctor` / `--doctor`):** emits `needsHumanAction` items and per-profile tool counts.
   - **Argument-Bound Single-Use Human Confirmation Queue:** consequential tools (`funnels.publish`, `campaigns.adjust_budget`, `deals.record_index_ii`, `contacts.erase_dpdp`) stage a `pending` `proposals` row carrying `args_sha256` — TOCTOU parameter modification or replay after human approval fails closed (`MCP_CONFIRMATION_ARGS_MISMATCH`).
   - **AI Guardrail & PII Firewall:** inspects serialized tool arguments before execution and scrubs outputs via `sec.redact_secrets`.

2. **OAuth 2.1 Authorization-Code + Mandatory `S256` PKCE Marketplace Apps:**
   - `GET|POST /api/v1/oauth/apps`, `POST /api/v1/oauth/authorize`, `POST /api/v1/oauth/token`, `POST /api/v1/oauth/revoke`.
   - Encrypts client secrets with per-tenant HKDF DEKs (`sec.seal_tenant`), enforces exact HTTPS `redirect_uri` match, requires `S256` PKCE, revokes issued tokens if an authorization code is replayed (RFC 6749 §10.5), and blocks OAuth tokens from `/security`, `/auth`, `/users`, `/stepup`, and `/learning`.

3. **Live OpenAPI 3.1 Introspection (`GET /api/v1/openapi.json`):**
   - Dynamically generated from `api.ROUTES` so documentation never drifts from code.

4. **Official Multi-Language SDKs (`sdk/`):**
   - Python 3.11+ (`sdk/python/realtor_estates_sdk.py`)
   - TypeScript / ESM JavaScript (`sdk/typescript/realtor-estates-sdk.mjs`)
   - PHP 8.1+ strict-types (`sdk/php/RealtorEstatesClient.php`)
