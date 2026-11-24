# 10 — Research Sources & Evidence Register

Every claim in `05-TECH-SPEC-2026-2031.md`, `06-AI-AGENT-SPEC.md`, `07-COMPLIANCE.md`,
`08-REDLINE-REDTEAM.md` and `03-DATA-MODEL.md` that is not derived from our own measurements points at
one of the sources below. Research window: **October–November 2026**.

**How to read this register.** Each entry records what the source supports, what we did with it, and how
much weight we give it. A vendor blog post is not the same evidence as a CVE record. Where the source is
a summary of a primary document (spec, CVE, statute), the primary document governs.

Confidence: **A** = primary document / official spec / CVE record · **B** = reputable secondary reporting or vendor engineering blog consistent with primary · **C** = single secondary source, plausible, not independently confirmed.

---

## Agent protocols & MCP

### `[R1]` — MCP adoption and exploit prevalence, 2026
*brightter.com, "MCP in 2026"* · **Confidence B**
Supports: ~97M monthly MCP SDK downloads; ~41% of surveyed organisations running MCP in production; security cited as the #1 adoption blocker; independent scans finding 30–82% of public MCP servers with exploitable flaws and a minority using authentication; 30+ MCP-related CVEs in early 2026 including an RCE (CVSS 9.6) affecting 437k+ environments.
Used for: `05` §5.1 (why the tool layer is the real risk surface); the deny-by-default tool catalogue; the decision to build a gateway rather than expose provider tools directly.

### `[R2]` — MCP security patterns
*agentmelt.com, "MCP Security in 2026"* · **Confidence B**
Supports: tool poisoning (instructions hidden in tool metadata), cross-server tool shadowing, over-broad OAuth scopes, RFC 8707 Resource Indicators for audience-bound tokens, 1-hour token lifetimes, human re-consent on scope widening, the **dual-LLM pattern** (privileged planner + quarantined reader), description-integrity as an unsolved problem.
Used for: `05` §5.2 controls 1–6; the "one tool session per task" rule; the plan to hash tool descriptions at approval time.

### `[R3]` — OWASP MCP Top 10 and gateway guidance
*theagentics.co* · **Confidence B**
Supports: the OWASP MCP Top 10 categories; per-agent tool allowlists; a centralised gateway as the enforcement point; mandatory human-in-the-loop for destructive actions; Streamable HTTP transport; a NIST AI Agent Standards profile expected Q4 2026.
Used for: gateway architecture, `APPROVAL_REQUIRED` set, the risk classes in `06` §3.

### `[R4]` — Protocol landscape, late 2026
*gravity.fast* · **Confidence B**
Supports: MCP `2026-07-28` revision — stateless core, OAuth 2.1 moving from optional to default; A2A `v1.0.1` (28 May 2026); WebMCP W3C draft with a Chrome origin trial; AP2 / ACP / UCP agentic-commerce protocols; an MCP registry of 40,965 servers as of 9 Oct 2026, 63% remote.
Used for: `05` forecast rows 1, 2, 8; the WebMCP and agentic-checkout items in the PWA horizon table; the phase-3 A2A plan.

---

## The PHP2Shell breach (October 2026)

### `[R5]` — Primary incident reporting
*aviatrix.ai and thehackernews.com coverage of CVE-2026-109618 / CVE-2026-109619 ("PHP2Shell")* · **Confidence A** (CVE ids) / **B** (timeline detail)
Supports: unauthenticated RCE in PHP `session.upload_progress` handling; a European government breached in under two hours; credential-hash and MFA-secret theft leading to NTLM relay → LDAP enumeration → I2P command-and-control; agentic markers in webshell traffic (`==SHELLUSER==`, `==PASSWD==`).
Used for: `05` §6 case study; the "machine-speed" assumption in `08` §9 doctrine 3; the egress-monitoring and patch-SLA controls.

### `[R6]` — Breach Watch, 25 Oct 2026
*cisoplatform.com* · **Confidence B**
Supports: PHP2Shell exploitation attempts observed **4 days before** the vendor patch; hijack of AI coding agents via a planted `AGENTS.md` / `CLAUDE.md` (launch directory = trust boundary); the "Tailworm" Tailscale-adjacent campaign.
Used for: `05` §6 (coding-agent policy); `08` §9 doctrine 3.

### `[R5]` — supporting detail: discovery and remediation timeline
*matrixlabx.com, thepermissionlayer.com* · **Confidence B**
Supports: found by frontier AI research tooling (Wiz, 19 Aug 2026); fixed 21 Oct 2026 in PHP 8.5.12 / 8.4.27 / 8.3.36 / 8.2.35; CVSS 4.0 9.2; a third related RCE (**CVE-2026-115560**) mitigated by `session.upload_progress.enabled=0`.
*(No separate number — this is evidence filed under `[R5]`; `[R9]` is reserved for the OWASP Agentic Top 10 below.)*

> ⚠️ This entry also functions as a warning about our own tooling: a frontier model found this class of bug. Assume adversaries run the same models against us.

---

## AI/LLM threat taxonomy

### `[R7]` — OWASP LLM Top 10 (2026)
*cycode.com, neuraltrust.ai, aembit.io summaries* · **Confidence B**
Supports: LLM01 Prompt Injection, LLM02 Sensitive Information Disclosure, **LLM03 Excessive Agency (up 3)**, LLM04 Supply Chain, LLM05 Data & Model Poisoning, **LLM06 Unbounded Consumption (up 4)**, LLM07 Misinformation, LLM08 Hidden Context Exposure (broadened), LLM09 Vector & Embedding Weaknesses, **LLM10 Improper Output Handling (down 5)**; approvals must show the raw operation, not a model-written summary.
Used for: `redteam.OWASP_LLM` (the mapping rendered in the Security console); `06` §3 risk classes; the "raw operation in the approval card" rule.

### `[R8]` — Corroborating analysis of the same reordering
*aembit.io / neuraltrust.ai* · **Confidence B**
Used to confirm the 2026 ordering before we hard-coded the control matrix. Two independent summaries agreeing raises our confidence from C to B.

### `[R9]` — OWASP Agentic Top 10 / agentic threats in the wild
*theagentics.co and related OWASP agentic-security material* · **Confidence B**
Supports: tool poisoning, schema poisoning, tool shadowing, command injection through agent input, shadow MCP servers, context over-sharing — now observed rather than theoretical.
Used for: `05` §5.3 agentic threat model; `08` RL-6xx category design.

---

## PWA platform reality (iOS & audit)

### `[R10]` — Installability and auditing in the post-Lighthouse era
*progressivewebapps.com* · **Confidence B**
Supports: installability no longer requires a fetch handler; the Lighthouse PWA category was **removed**; teams must audit offline behaviour, update flow and manifest quality themselves.
Used for: `05` §2.3; our own audit list (manifest icons, offline fallback, update flow, SW cache versioning) since no tool grades it for us.

### `[R11]` — iOS PWA constraints
*firt.dev* · **Confidence B**
Supports: Web Push requires home-screen installation since iOS 16.4; Declarative Web Push on Safari 18.4+; iOS 26 standalone home-screen behaviour; **no** Background Sync / Background Fetch; no home-screen widgets; WebGPU in Safari 26.
Used for: `05` §2.1–2.2 (why offline writes queue in memory and flush on foreground; why push is a bonus channel, not a load-bearing one).

### `[R12]` — iOS 27 / Safari 27 (14 Sep 2026)
*forasoft.com* · **Confidence C**
Supports: Service Worker Static Routing API; nothing new for install prompts or device access.
Used for: `05` §2.2 horizon table. Treated as low-stakes: if wrong, we lose a nice-to-have, not a design pillar.

---

## Privacy & AI regulation (India + EU)

### `[R13]` — DPDP applied to AI processing
*begig.io* · **Confidence B**
Supports: the notice must name AI processing **and third-party model APIs**; cross-border transfer needs a notified-country basis; the most common gaps are no data map and form-designed consent; penalty ceilings ₹250 crore / ₹200 crore.
Used for: `03` §11 PII map (why "no third-party model without DPA + notice"); `05` §8 compliance engineering.

### `[R14]` — Scraping legality in India
*promptcloud.com* · **Confidence B**
Supports: public business data generally scrapable; personal data needs a DPDP basis; document sources, check AI-specific ToS clauses, rate-limit; `hiQ v. LinkedIn` protects public-data scraping in the US but does not license contract breach.
Used for: `07` §2.3; `SCRAPE_SOURCES` — LinkedIn and direct MagicBricks scraping blocked with `451` and an explanation in the UI.

---

### `[R15]` — DPDP implementation timeline and EU AI Act enforcement
*indiajuris.com, responsibleailabs.ai, recordinglaw.com* · **Confidence B**
Supports: DPDP Phase 1 (Board) 13 Nov 2025; **Phase 2 — Consent Manager registration — 13 Nov 2026**: India-incorporated, ₹2 crore net worth, AES-256 at rest, 7-year records; **full enforcement 13 May 2027**; penalties to ₹250 crore; 72-hour breach default; CERT-In 6-hour reporting; children under 18 need verifiable parental consent; EU AI Act full enforcement **2 Aug 2026**, extraterritorial.
Used for: `07` §1.1 timeline and `07` §3; the consent-ledger field design in `03`.

### `[R16]` — Breach reporting clocks and Board process
*responsibleailabs.ai* · **Confidence B**
Supports: "without delay" notification to the Data Protection Board and affected principals, operationalised as ≤ 72 h; CERT-In 6 h.
Used for: `07` §7 incident runbook; `05` §9 IR timelines.

## Voice economics & UX

### `[R17]` — Voice stack pricing, late 2026
*poly.ai, artrealmai.com* · **Confidence C**
Supports: OpenAI Realtime `gpt-realtime-2.1`; ElevenLabs Eleven Realtime v1 Mini/Max (20 Oct 2026, ~$0.08/min, 0.57–0.62 s TTFB); Deepgram ~$0.075/min; Telnyx ~$0.05; Bland $0.12–0.14; **Sarvam ₹30–45/hour (≈$0.01/min)**; the real blended cost of a production call is 3–6× the advertised per-minute list price once telephony, STT/TTS, retries and human handoff are counted.
Used for: `06` §6.1 cost model; `05` forecast row 4; the decision to make the provider an adapter and default Indian tenants to a Sarvam-class provider.

### `[R18]` — Latency perception in voice agents
*poly.ai, eightpine.xyz* · **Confidence C**
Supports: listeners notice a pause around 800 ms and treat ~1,500 ms as broken; target p95 turn latency ≤ 900 ms.
Used for: `06` §4 latency budget and §10 p95 target.

---

## Domain inputs (real-estate practice in India)

*ampcome.com, caller.digital, vexyl.ai* · **Confidence C**
Supports: DPDP purpose binding in practice (an enquiry on one project is not consent for another); RERA making every agent statement a promoter representation; TRAI DLT header/template registration and dial-time DNC scrub.
Used for: `07` §2.1–2.2; the claim-set guardrail; the lookalike-project refusal.

---

## User-supplied context pack

`/home/user/uploads/*.md` — the BHOOMI land-sales operating-system pack (data model, API/webhook shapes,
voice-agent spec, India compliance notes, integrations, design system, roadmap/economics, pytest config).
Honoured as follows: the voice-agent compliance gates (§6.1), the API envelope and webhook signing
conventions (`04`), the India compliance emphasis (`07`), the design-system discipline (PWA typography,
density, no dark-pattern consent), and the economics framing (`09`). Where the pack conflicted with the
Realtor Estates brief (e.g. its narrower land-only scope), the brief took precedence and the pack's
detail was reused inside the wider model.

---

## What we could not verify

| Claim | Why it matters | Handling |
|---|---|---|
| Exact Phase-2 Consent Manager technical spec | Drives our ledger interop | Built to the described fields; will re-read the final rules before the Nov 2026 deadline |
| Provider prices and latency figures | Margin model | Treated as ranges, re-quoted before any contract; adapters keep us portable |
| 30–82% exploit prevalence in public MCP servers | Justifies gateway design | Range is wide because methodologies differ; the direction is consistent across three sources, so the design decision holds regardless of the exact number |
| NIST AI Agent Standards timeline (Q4 2026) | Certification path | Monitored, not depended on |
| iOS 27 specifics | Nice-to-have capabilities | Low dependency by construction |

**Rule we kept:** every externally-sourced number in the spec is attached to a design consequence. If a
number changes, we can re-read this register and know exactly which decisions to revisit — and which to
leave alone because they were never load-bearing.
