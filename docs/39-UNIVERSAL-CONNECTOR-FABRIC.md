# 39 · Universal Provider, Future-Tech API & Webhook Fabric (`v1.28.0`)

`v1.28.0` eliminates hardcoded provider lock-in and missing-credential blockages
across every API, AI model, telephony channel, payment gateway, agent tool, and
webhook in **Realtor Estates**.

---

## 1. Design Guarantee — Zero Blockage Today, Zero Code Changes Tomorrow

Every capability slot (`voice`, `ai`, `whatsapp`, `sms`, `payments`, `tool`)
resolves through a **3-tier hot-swappable chain**:

1. **Active Workspace Connector Profile** (`POST /api/v1/connectors`, AEAD-sealed
   at rest via `sec.seal(..., aad=f"{tid}:conn:{id}")`, re-encrypted automatically
   by `scripts/rotate_keys.py`, hot-swapped per tenant with **zero server restart**).
2. **Vault / Process Environment Keys** (`RE_SECRET_MANAGER_URL` / `.env.local` —
   `SARVAM_API_KEY`, `ELEVENLABS_API_KEY`, `WHATSAPP_TOKEN`, `SMS_API_KEY`,
   `RAZORPAY_KEY_ID`, `NEON_AI_GATEWAY_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
   `GOOGLE_API_KEY`).
3. **Built-in Full-Fidelity Local Twin** (zero external credentials needed):
   - **Voice Local Twin** synthesizes a real playable 8 kHz mono PCM `.wav`
     audio clip (`RIFF/WAVE`, `9,644 bytes`) + a bilingual AI-disclosed
     qualification transcript and archives it straight to Neon S3
     (`recordings/tenants/<tid>/<act_id>.wav`), updating `voice_minutes` and
     returning a playable presigned URL immediately.
   - **Interactive Simulator** (`POST /api/v1/connectors/simulate`) lets
     operators trigger inbound WhatsApp buyer replies (opening the 24-hour
     customer-service window + live SSE push) and voice qualification calls in
     one click from **`/#/integrations`**.

---

## 2. Connecting Any Current or Future Provider (`custom_http` Dialect)

Alongside **25 named presets** (`sarvam`, `elevenlabs`, `vapi`, `bolna`,
`retell`, `bland`, `exotel`, `twilio`, `openai_compat`, `anthropic`, `gemini`,
`sarvam_chat`, `neon_ai_gateway`, `meta_cloud`, `gupshup`, `wati`, `interakt`,
`msg91`, `kaleyra`, `fast2sms`, `razorpay`, `cashfree`, `stripe`), any brand-new
provider or tool released in 2026–2031 connects via **`preset: "custom_http"`**:

- **Auth schemes:** `bearer`, `header` (custom header name such as
  `api-subscription-key` or `xi-api-key`), `basic` (`key_id_public:api_key`),
  `query`, or `none`.
- **Safe JSON-Template Renderer (`render_json_template`):** Walks structured
  JSON dicts/lists and substitutes `{{phone}}`, `{{to}}`, `{{text}}`,
  `{{prompt}}`, `{{system_prompt}}`, `{{model}}`, `{{language}}`,
  `{{agent_name}}`, `{{amount_paise}}`, `{{currency}}`, `{{receipt}}`,
  `{{tenant_id}}`, and `{{contact_id}}` without raw-string JSON injection.
- **Dot-Path Response Extractor (`extract_dot_path`):** Extracts nested IDs or
  completions (e.g. `result.nested.session_uuid`, `choices.0.message.content`,
  `gateway.order_ref`) from any JSON schema.
- **Custom Agent Tools (`category: "tool"`):** Exposed to AI agents as
  `connector.<slug>` with risk classification (`read`, `write`, `outbound`,
  `irreversible`) — `outbound` and `irreversible` tools automatically park in
  the human approval queue (`APPROVAL_REQUIRED`).

---

## 3. Dynamic SSRF-Safe Egress Allowlist & Universal Inbound Webhooks

- **Dynamic Egress Allowlist (`POST/DELETE /api/v1/security/egress-allowlist`):**
  Workspace owners can allowlist any public HTTPS hostname (`api.newvoice.ai`)
  or subdomain wildcard (`*.newvoice.ai`) — or let `POST /api/v1/connectors`
  auto-allowlist it on save — while a **non-overridable SSRF wall** blocks plain
  HTTP, RFC1918/loopback/link-local/CGNAT/multicast IPs, cloud metadata
  (`169.254.169.254`, `metadata.google.internal`), `.internal`/`.local` domains,
  and DNS-rebinding TOCTOU in production.
- **Universal Inbound Webhooks (`POST /api/v1/connectors/inbound-hooks` →
  `POST /api/v1/webhooks/custom/<hook_id>`):** Mints a dedicated endpoint per
  external tool (n8n, Make, Zapier, custom telephony, web forms) with a sealed
  HMAC-SHA256 / header secret and dot-path `field_map` routing into
  `lead.capture`, `whatsapp.inbound`, `call.log`, or `deal.note`.

---

## 4. Sprint S2 Document-Pack Upload Pipeline (`POST /api/v1/properties/{pid}/documents/scan`)

Verifies true file magic bytes (`%PDF-`, PNG, JPEG, WebP, WAV) against declared
`content_type` (`415 MIME_MAGIC_MISMATCH` on spoofed files) and blocks EICAR
signatures, `MZ`/`ELF`/`#!` executables, `<script>` HTML/SVG polyglots, and
active PDF `/JavaScript` or `/Launch` actions (`422 MALICIOUS_FILE_BLOCKED`)
before archiving verified due-diligence documents to Neon S3.

---

## 5. Verification (`scripts/proof_universal_connectors.py`)

- **`9/9 PASS`** end-to-end (`zero_blockage_voice_local_twin_wav`,
  `zero_blockage_whatsapp_inbound_simulator`, `dynamic_egress_allowlist_ssrf_wall`,
  `future_custom_http_voice_hotswap`, `custom_ai_and_agent_tools_with_approval_gate`,
  `custom_payment_gateway_hotswap`, `connector_secrets_sealed_and_tenant_isolated`,
  `universal_inbound_webhook_dotpath_mapping`,
  `s2_document_pack_magic_and_malware_scanner`).
