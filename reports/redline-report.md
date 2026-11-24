# REDLINE security report

**Target:** `http://127.0.0.1:8000`  
**Generated:** 2026-11-17T18:33:04.829791+00:00  
**Score:** 100/100 · 53 passed · 0 failed · 0 skipped  
**Scope:** authorised verification of a single deployment. No third-party systems were contacted.

## Findings

| ID | Check | Category | OWASP | Severity | Result |
|---|---|---|---|---|---|
| RL-001 | HSTS with long max-age | transport | A05:2021 | high | PASS |
| RL-002 | CSP blocks inline script execution | transport | A05:2021 | critical | PASS |
| RL-003 | MIME sniffing disabled | transport | A05:2021 | medium | PASS |
| RL-004 | Clickjacking blocked | transport | A05:2021 | high | PASS |
| RL-005 | Referrer policy restricts leakage | transport | A05:2021 | low | PASS |
| RL-006 | Device permissions denied by default | transport | A05:2021 | low | PASS |
| RL-007 | Cross-origin isolation headers | transport | A05:2021 | medium | PASS |
| RL-008 | Tenant data not cacheable by intermediaries | transport | A01:2021 | high | PASS |
| RL-101 | No default/weak credentials | auth | A07:2021 | critical | PASS |
| RL-102 | Signed-token forgery rejected | auth | A07:2021 | critical | PASS |
| RL-103 | Unauthenticated access rejected | auth | A01:2021 | critical | PASS |
| RL-104 | Logout invalidates the session | auth | A07:2021 | high | PASS |
| RL-105 | Brute-force resistance | auth | A07:2021 | high | PASS |
| RL-106 | Password policy enforced server-side | auth | A07:2021 | high | PASS |
| RL-107 | API-token (Bearer) path functional | auth | A07:2021 | low | PASS |
| RL-201 | BOLA / IDOR on contact reads | access | API1:2025 | critical | PASS |
| RL-202 | BOLA on deal mutations | access | API1:2025 | critical | PASS |
| RL-203 | BFLA — viewer cannot perform privileged writes | access | API5:2025 | critical | PASS |
| RL-204 | Mass assignment / over-posting | access | API3:2025 | high | PASS |
| RL-205 | PII masked for roles without pii:read | access | API3:2025 | high | PASS |
| RL-301 | SQL injection | injection | A03:2021 | critical | PASS |
| RL-302 | Reflected XSS | injection | A03:2021 | high | PASS |
| RL-303 | Stored XSS in server-rendered pages | injection | A03:2021 | critical | PASS |
| RL-304 | Path traversal / arbitrary file read | injection | A01:2021 | critical | PASS |
| RL-305 | Error messages don't leak internals | injection | A09:2021 | medium | PASS |
| RL-306 | CRLF / response-header injection | injection | A03:2021 | medium | PASS |
| RL-307 | Malformed JSON handled safely | injection | A04:2021 | medium | PASS |
| RL-308 | CSV / spreadsheet formula injection | injection | A03:2021 | medium | PASS |
| RL-309 | Unicode normalisation bypass on dedupe | injection | A04:2021 | medium | PASS |
| RL-310 | Server-side template injection | injection | A03:2021 | high | PASS |
| RL-401 | CSRF protection on cookie-auth writes | logic | A01:2021 | critical | PASS |
| RL-402 | Cross-origin write rejected | logic | A01:2021 | high | PASS |
| RL-403 | Consent gate cannot be bypassed | logic | DPDP §6 | critical | PASS |
| RL-404 | Quiet-hours / call-window gate | logic | TCCCPR | high | PASS |
| RL-405 | Negative / nonsensical business values | logic | A04:2021 | medium | PASS |
| RL-406 | Unlawful lead-source extraction blocked | logic | DPDP §4 | critical | PASS |
| RL-407 | Rate limiting under burst | logic | API4:2025 | high | PASS |
| RL-408 | Denial-of-wallet guard (agent spend caps) | logic | LLM06:2026 | high | PASS |
| RL-501 | Prompt-injection corpus blocked at the agent boundary | ai | LLM01:2026 | critical | PASS |
| RL-502 | Hidden context / system-prompt extraction | ai | LLM08:2026 | high | PASS |
| RL-503 | Excessive agency — irreversible actions gated | ai | LLM03:2026 | critical | PASS |
| RL-504 | Tool manifest least privilege | ai | LLM03:2026 | high | PASS |
| RL-505 | Fail-closed guardrails (no output when blocked) | ai | LLM01:2026 | high | PASS |
| RL-506 | Voice agent compliance disclosure | ai | TCCCPR / EU AI Act | critical | PASS |
| RL-601 | SSRF via webhook URL | ssrf | SSRF | critical | PASS |
| RL-602 | Allowlisted host still works | ssrf | SSRF | low | PASS |
| RL-701 | Kill switch halts agent execution | agentplane | A16:2021 | high | PASS |
| RL-702 | Audit chain integrity | agentplane | A09:2021 | high | PASS |
| RL-703 | Self-verification suite operational | agentplane | A09:2021 | medium | PASS |
| RL-704 | No raw secrets exposed to the client | agentplane | A02:2021 | critical | PASS |
| RL-705 | Security headers on error paths | agentplane | A05:2021 | medium | PASS |
| RL-706 | Secret files unreachable from the web tier | agentplane | A05:2021 | critical | PASS |
| RL-707 | Secret inventory endpoint leaks no values | agentplane | A02:2021 | critical | PASS |

All executed checks passed. Re-run after every release and whenever a new tool, integration or agent scope is added.
