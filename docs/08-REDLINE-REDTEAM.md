# 08 — REDLINE: the offensive verification agent

> **Authorisation is the first control.** REDLINE attacks systems its operator owns. It refuses any
> non-loopback target unless `--i-own-this-target` is passed, and it never contacts third-party hosts.
> Using it against a system you do not own is a criminal offence (India IT Act §66 and equivalents).

---

## 1. Why a product ships an attack agent

Three reasons, in order of importance:

1. **Every release changes the attack surface.** A new tool, a new integration, a new agent scope is a new way in. A human pen test happens once a year; a scripted adversary happens on every commit.
2. **The failure mode of AI products is behavioural, not just technical.** "Can the agent be talked into exporting the database?" is a security question no scanner answers.
3. **It builds customer trust you can show.** The Security screen runs 24 checks live and prints evidence. The external harness produces a report you can hand to a buyer's CISO.

REDLINE is deliberately **not** "uncensored". Scope is a feature: an agent that will attack anything is
an agent you cannot put in a CI pipeline, in an insurance application, or in front of a customer.

## 2. Two tiers

| Tier | Location | Runs | Count | Who sees it |
|---|---|---|---|---|
| **In-process verification** | `app/redteam.py` | Inside the tenant boundary, via owner-only API | 24 weighted checks (RT-023/024 cover secret handling) | Tenant owner, Security console |
| **External offensive harness** | `redline/redline.py` | Against a live origin over HTTP, from outside | 53 checks in 8 categories | Engineering + CI |

They overlap on purpose. The in-process suite can read internals (ciphertext, scopes, chain); the
external harness can only see what an attacker sees.

## 3. External harness: categories and checks

### 3.1 Transport (8)
HSTS, CSP (script-src locked), nosniff, clickjacking, referrer policy, permissions policy, cross-origin
isolation, `no-store` on tenant data. *A failing CSP is a critical finding because it turns any future
XSS into script execution.*

### 3.2 Authentication (7)
Default/weak credentials, forged-token replay (rewrite `role: owner`, keep the MAC), unauthenticated
access, logout invalidation, brute-force resistance (lockout within 8 bad attempts), weak-password
policy, Bearer path functionality.

### 3.3 Access control (5)
BOLA/IDOR read sweep across tenant ids; BOLA on deal mutations; **BFLA** — a viewer attempting four
privileged writes (create contact, invite admin, run redline, engage kill switch); mass assignment
(inject `tenant_id`, `role`, `id`, `score`); PII masking for roles without `pii:read`.

### 3.4 Injection (10)
SQLi payload sweep, reflected XSS, stored XSS in server-rendered pages, path traversal (4 encodings),
error verbosity, CRLF/header injection, JSON bombs, CSV formula injection, Unicode normalisation
bypass of dedupe, SSTI.

### 3.5 Business logic (8)
CSRF token removal, cross-origin write, consent bypass, quiet-hours window, negative money, unlawful
lead source, rate-limit burst (AI bucket), spend caps.

### 3.6 AI / agent (6)
Six-payload injection corpus against the live agent endpoint, hidden-context extraction, excessive
agency (irreversible tool), tool-manifest least privilege, fail-closed behaviour, voice disclosure
compliance.

### 3.7 SSRF (2)
Seven hostile webhook URLs (loopback, IMDS `169.254.169.254`, `localhost`, non-allowlisted host,
userinfo trick `https://user:pass@graph.facebook.com@evil.example/`, `file://`, `gopher://`) plus a
positive control that an allowlisted host still works.

### 3.8 Agent-plane hardening (7)
Kill switch halts execution, audit chain integrity, self-verification reachable, no raw secrets in API
payloads, security headers present on error paths, **secret files unreachable from the web tier** (9 paths:
`/.env`, `/.env.bak`, `/data/keys.json`, `/../data/keys.json`, URL-encoded traversal, `/.git/config`,
`/store.json`, `/app/config.py`, `/web/../.env` — a hit requires HTTP 200 *and* key-shaped content), and
**the secret inventory endpoint leaks no values** (regex for 64-char hex, OpenAI/Google/WhatsApp shapes).

## 4. Is it an *agent*, or a script?

It is an agent in the useful sense: it **discovers surface before choosing attacks**.

```
preflight → authenticate two tenants → discover()
   discover: tenant, role, sample ids, agents, voice capability, proposal queue
   plan:     pick checks whose preconditions are satisfied
   execute:  per-category probes, throttled, with cleanup
   reflect:  score, remediation, machine-readable report
```

Concretely: if no agent exposes voice, the voice-disclosure check self-skips; if a tenant has no
unconsented contact, the consent-bypass probe reports "skipped (all consented)" instead of a false pass.
A throttle from the target's own rate limiter marks a check **skipped**, not failed — a 429 is a good sign.

## 5. Using it

```bash
python3 -m app.server --port 8000 &
python3 redline/redline.py                                  # full suite
python3 redline/redline.py --only ai,logic                  # focus
python3 redline/redline.py --base https://staging.example.in --i-own-this-target
python3 redline/redline.py --json reports/r.json --md reports/r.md
```

Exit codes: `0` clean · `1` findings · `2` unreachable/refused.
CI gate: fail the build on any `critical`/`high`, allow `medium` with a tracked ticket.

## 6. Findings → remediation workflow

1. **Evidence, not adjectives.** Every finding stores what was sent, what came back, and what should have happened.
2. **Reproduce first.** A finding that cannot be reproduced after a restart is a flake; add it to the suite with a determinism fix.
3. **Fix, then encode.** Each fix adds a regression test to `tests/` or a new RL check — the suite grows monotonically.
4. **Score honestly.** Weighted by severity (critical 10, high 6, medium 3, low 1). A 100 is a snapshot, not a certificate.

## 7. What REDLINE does *not* do

- No destructive persistence (no mass delete, no data corruption). Probes are inert and labelled `redline-probe` / `… Probe`.
- No real exfiltration — the SSRF payloads point at reserved domains, nothing is sent anywhere.
- No third-party scanning, no credential stuffing, no fuzzing of production at volume.
- No "AI jailbreak" that produces harmful content: the corpus tests *the guardrail*, not the model's willingness to misbehave.

## 8. Honest gaps in the current suite

| Gap | Impact | Plan |
|---|---|---|
| No TLS/cipher-suite scanning | Cannot catch weak TLS config | Phase 2: testssl-style checks against staging |
| No authenticated concurrency/race testing | Inventory double-sell races unproven | Phase 2: parallel hold acquisition |
| No file-upload fuzzing | Upload path not exercised | Phase 2 (upload feature ships then) |
| No multi-node testing | Rate limits are per-process today | Phase 3 with Redis |
| No automated exploit chaining | A chain (SSRF → metadata → creds) is not modelled | Phase 3: attack-graph mode |
| No host-level secret audit | REDLINE sees the web tier, not the filesystem | `scripts/check_secrets.py` covers the host; run it from cron |
| Corpus is curated, not fuzzed | Novel phrasings may pass | Phase 2: property-based generation + classifier model |

## 9. Doctrine

1. **Assume the model will be persuaded.** Move the decision out of the model.
2. **Assume one control has already failed.** Design each layer as if the previous one is down.
3. **Assume the attacker is an agent.** Machine-speed recon is the 2026 baseline, not a future scenario `[R5]`.
4. **Assume public disclosure in 48 hours.** Patch SLAs are measured in hours for CVSS ≥ 9.
5. **Prove it or delete it.** A control with no test is a claim.
