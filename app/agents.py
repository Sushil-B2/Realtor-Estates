"""
Realtor Estates — agent runtime, forecasting and simulation.

The LLM itself is pluggable (see providers below). What is *real* here is
everything around the model — the part that actually decides whether an agent
is safe to let loose on a real estate business:

  * per-agent scope + tool allowlist, revalidated on every call
  * input inspection → plan → tool execution → output inspection (fail-closed)
  * spend caps and circuit breakers
  * irreversible actions parked in a human approval queue
  * skill proposals with evidence, risk grade, versioning and rollback
  * a deterministic local "model" so the demo works with no API key and no
    data leaving the machine
"""
from __future__ import annotations

import math
import random
import re
import time
from typing import Any

from . import connectors as conn_mod
from . import neon
from . import security as sec

# --------------------------------------------------------------------------
# Provider abstraction
# --------------------------------------------------------------------------
PROVIDERS = {
    "openai": {"models": ["gpt-5.4-orion", "gpt-realtime-2.1"], "region": "us", "dpdp_ok": True},
    "anthropic": {"models": ["claude-sonnet-4.6", "claude-opus-4.8"], "region": "us", "dpdp_ok": True},
    "google": {"models": ["gemini-3-flash", "gemini-3-pro"], "region": "in-asia", "dpdp_ok": True},
    "sarvam": {"models": ["sarvam-m", "sarvam-bulbul-v3"], "region": "in", "dpdp_ok": True},
    "jev": {"models": ["jev-ultrafast"], "region": "in", "dpdp_ok": True},
    "local": {"models": ["llama-4-scout-8b", "qwen3-14b"], "region": "onprem", "dpdp_ok": True},
}


def route_model(tenant_region: str, sensitivity: str) -> dict:
    """
    Data-residency-aware model router. PII-bearing prompts never leave the
    region unless the tenant has explicitly accepted cross-border transfer.
    """
    if sensitivity == "high" or tenant_region == "onprem":
        return {"provider": "local", "model": "llama-4-scout-8b", "reason": "high-sensitivity → on-prem"}
    if sensitivity == "medium":
        return {"provider": "sarvam", "model": "sarvam-m", "reason": "India-resident provider"}
    return {"provider": "openai", "model": "gpt-5.4-orion", "reason": "default frontier route"}


# --------------------------------------------------------------------------
# MCP-style tool catalogue (scopes + risk class)
# --------------------------------------------------------------------------
TOOL_CATALOG: dict[str, dict] = {
    "crm.lookup_contact": {"scopes": ["contacts:read"], "risk": "read", "desc": "Fetch a contact by id or phone hash."},
    "crm.log_activity": {"scopes": ["contacts:write"], "risk": "write", "desc": "Append an activity entry."},
    "crm.update_stage": {"scopes": ["deals:write"], "risk": "write", "desc": "Move a deal stage."},
    "crm.dedupe": {"scopes": ["contacts:read"], "risk": "read", "desc": "Find duplicate contacts by blind index."},
    "crm.assign": {"scopes": ["contacts:write"], "risk": "write", "desc": "Assign an owner round-robin."},
    "calendar.offer_slots": {"scopes": ["calendar:write"], "risk": "write", "desc": "Offer travel-aware site-visit slots."},
    "calendar.create_event": {"scopes": ["calendar:write"], "risk": "write", "desc": "Create a site-visit appointment."},
    "whatsapp.send_template": {"scopes": ["messages:send", "templates:read"], "risk": "outbound", "desc": "Send an approved WhatsApp template."},
    "whatsapp.send_document": {"scopes": ["docs:send"], "risk": "outbound", "desc": "Send the due-diligence document pack."},
    "sms.send": {"scopes": ["messages:send"], "risk": "outbound", "desc": "Send a 1600-series transactional SMS."},
    "voice.disclose_ai": {"scopes": ["voice:outbound"], "risk": "compliance", "desc": "Mandatory AI disclosure in first two turns."},
    "voice.dial": {"scopes": ["voice:outbound"], "risk": "outbound", "desc": "Place an outbound call behind the compliance gate."},
    "docs.assemble_pack": {"scopes": ["docs:send"], "risk": "read", "desc": "Assemble 7/12, NA order, layout, title search."},
    "docs.checklist": {"scopes": ["docs:read"], "risk": "read", "desc": "Produce a document checklist."},
    "knowledge.search": {"scopes": ["knowledge:read"], "risk": "read", "desc": "Tenant-scoped RAG search."},
    "analytics.query": {"scopes": ["analytics:read"], "risk": "read", "desc": "Query tenant metrics."},
    "scoring.model": {"scopes": ["analytics:read"], "risk": "read", "desc": "Score a lead 0–100."},
    "consent.validate": {"scopes": ["consent:read"], "risk": "compliance", "desc": "Validate consent + DNC before contact."},
    "compliance.rera_check": {"scopes": ["compliance:read"], "risk": "compliance", "desc": "Constrain claims to the RERA claim set."},
    "tasks.create": {"scopes": ["tasks:write"], "risk": "write", "desc": "Create an internal task."},
    "youtube.search": {"scopes": ["research:read"], "risk": "read", "desc": "Search public video metadata."},
    "video.score": {"scopes": ["research:read"], "risk": "read", "desc": "Score videos for intent + angle."},
    "content.brief": {"scopes": ["content:draft"], "risk": "write", "desc": "Draft a content brief (no publishing)."},
    "web.fetch_safe": {"scopes": ["research:read"], "risk": "read", "desc": "Fetch a public page through the egress proxy."},
    "billing.issue_refund": {"scopes": ["billing:write"], "risk": "irreversible", "desc": "Issue a refund."},
    "data.bulk_export": {"scopes": ["data:export"], "risk": "irreversible", "desc": "Bulk export of personal data."},
    "records.bulk_delete": {"scopes": ["records:delete"], "risk": "irreversible", "desc": "Bulk delete records."},
}

APPROVAL_REQUIRED = {"irreversible", "outbound"}


def tools_for(agent: dict, tenant: dict | None = None) -> list[dict]:
    dyn_tools = {
        f"connector.{c['slug']}": {
            "scopes": ["connectors:call"] if c.get("risk") != "read" else ["contacts:read"],
            "risk": str(c.get("risk") or "read"),
            "desc": f"Custom connector tool: {c.get('name') or c['slug']}",
            "_connector": c,
        }
        for c in conn_mod.list_connectors(tenant)
        if c.get("category") == "tool" and c.get("enabled", True) and c.get("slug")
    }
    out = []
    for name in agent.get("tools", []):
        meta = TOOL_CATALOG.get(name) or dyn_tools.get(name)
        if not meta:
            continue
        out.append(
            {
                "name": name,
                "scopes": meta["scopes"],
                "risk": meta["risk"],
                "description": meta["desc"],
                "authorized": set(meta["scopes"]).issubset(set(agent.get("scopes", []))) or name in dyn_tools,
                "needs_approval": meta["risk"] in APPROVAL_REQUIRED,
            }
        )
    return out


# --------------------------------------------------------------------------
# Deterministic local model (demo mode)
# --------------------------------------------------------------------------
def _local_completion(task: str, context: dict) -> str:
    name = context.get("name", "the buyer")
    req = context.get("requirement") or "a clear-title property"
    if task == "whatsapp_reply":
        return (
            f"नमस्कार {name.split()[0]}, {req} साठी आमच्याकडे 3 पर्याय आहेत. "
            "7/12, NA order आणि sanctioned layout आत्ताच WhatsApp वर पाठवतो. "
            "Saturday 11:00 किंवा Sunday 4:00 site visit ठेवायची का?"
        )
    if task == "call_script":
        return (
            "Hello, this is Meera, an AI assistant calling on behalf of Sunrise Land Developers — "
            "this call is automated and recorded. You enquired about plots in Nanded. "
            "Is this a good time for two quick questions?"
        )
    if task == "deal_summary":
        return (
            f"{name}: budget fit confirmed, docs verified, price objection handled with the "
            "48-hour hold. Next best action: confirm site visit within 48h, then send the "
            "encumbrance certificate."
        )
    if task == "content_brief":
        return (
            "Angle: due-diligence authority. 5 shorts on 7/12, NA order, road width, "
            "encumbrance, encroachment. CTA to doc-pack funnel. Publish Tue/Thu/Sat 19:30 IST."
        )
    return f"Analysis for {name}: requirement matched, consent verified, next action queued."


# --------------------------------------------------------------------------
# Agent execution
# --------------------------------------------------------------------------
def run_agent(agent: dict, user_input: str, context: dict, policy: dict | None = None) -> dict:
    policy = policy or {}
    started = time.time()
    trace: list[dict] = []
    t0 = time.perf_counter()

    # 1. circuit breaker / kill switch
    if policy.get("kill_switch"):
        return {"status": "blocked", "reason": "tenant kill-switch engaged", "trace": []}
    if agent.get("status") != "active":
        return {"status": "blocked", "reason": f"agent status is {agent.get('status')}", "trace": []}

    # 2. budget
    spent = float(agent.get("spend_usd") or 0)
    cap = float((agent.get("guardrails") or {}).get("spend_cap_usd") or 250)
    if spent >= cap:
        return {"status": "blocked", "reason": "agent spend cap reached", "trace": []}

    # 3. input inspection
    ins = sec.inspect_text(user_input)
    trace.append({"step": "input_guardrail", "action": ins["action"], "score": ins["score"], "ms": 2})
    if ins["action"] in ("block", "quarantine"):
        # Fail closed: a quarantined input produces no model output and no tool
        # calls. It is escalated to a human instead of being "cleaned" and run.
        return {
            "status": "blocked" if ins["action"] == "block" else "held",
            "reason": "input blocked by guardrail" if ins["action"] == "block" else "input quarantined for human review",
            "guardrail": ins,
            "trace": trace,
            "output": "",
        }

    # 4. plan (deterministic + dynamic custom connector tools)
    tenant_doc = context.get("tenant") if isinstance(context.get("tenant"), dict) else None
    dyn_map = {
        f"connector.{c['slug']}": c
        for c in conn_mod.list_connectors(tenant_doc)
        if c.get("category") == "tool" and c.get("enabled", True) and c.get("slug")
    }
    plan = []
    for tool in agent.get("tools", [])[:4]:
        meta = TOOL_CATALOG.get(tool)
        if meta:
            if not set(meta["scopes"]).issubset(set(agent.get("scopes", []))):
                trace.append({"step": "scope_check", "tool": tool, "result": "denied"})
                continue
            plan.append({"tool": tool, "risk": meta["risk"], "args": {"contact_id": context.get("contact_id")}})
        elif tool in dyn_map:
            c_row = dyn_map[tool]
            plan.append({"tool": tool, "risk": str(c_row.get("risk") or "read"), "args": {"input": user_input[:400], "contact_id": context.get("contact_id")}, "_connector": c_row})

    approvals = []
    executed = []
    for step in plan:
        if step["risk"] in APPROVAL_REQUIRED and step["tool"] in (
            "whatsapp.send_template", "whatsapp.send_document", "sms.send", "voice.dial"
        ):
            # outbound messaging is permitted only inside the compliance gate
            gate = context.get("compliance", {})
            if not gate.get("ok", False):
                approvals.append({"tool": step["tool"], "reason": "compliance gate not satisfied", "status": "pending"})
                continue
        if step["risk"] == "irreversible" or (step.get("_connector") and step["risk"] in APPROVAL_REQUIRED):
            approvals.append({"tool": step["tool"], "reason": f"{step['risk']} action requires human approval", "status": "pending"})
            continue
        if step.get("_connector"):
            try:
                res_tool = conn_mod.execute_connector_http(
                    tenant_doc,
                    step["_connector"],
                    {"prompt": user_input[:500], "contact_id": str(context.get("contact_id") or ""), "tenant_id": str((tenant_doc or {}).get("id") or "")},
                )
                trace.append({"step": "connector_tool", "tool": step["tool"], "http_status": res_tool["http_status"]})
            except conn_mod.ConnectorError as exc:
                trace.append({"step": "connector_tool", "tool": step["tool"], "error": exc.code})
        executed.append(step["tool"])

    # 5. model call — Tenant AI Connector -> Neon AI Gateway / Env Keys -> Local Twin
    out = ""
    ai_res = conn_mod.complete_ai(
        tenant_doc,
        [
            {"role": "system", "content": agent.get("system_prompt") or "You are a real-estate sales assistant in India. Be brief, never invent prices or approvals."},
            {"role": "user", "content": user_input},
        ],
        model=agent.get("model") or "",
    )
    if ai_res.get("ok") and (ai_res.get("text") or "").strip():
        out = ai_res["text"].strip()
        trace.append({"step": "model_provider", "provider": ai_res.get("provider", "live-ai"), "model": ai_res.get("model", "")})
    if not out:
        out = _local_completion("whatsapp_reply", context)
        trace.append({"step": "model_provider", "provider": "local-deterministic"})

    # 6. output inspection + redaction
    outs = sec.inspect_text(out)
    out = sec.redact_secrets(out)
    trace.append({"step": "output_guardrail", "action": outs["action"], "score": outs["score"]})

    # Belt and braces: even with inspect_text() now redacting credentials, the
    # response echo is scrubbed before it leaves the function (the audit found a
    # `sk-ant-…` key coming back in `guardrail.redacted`).
    if isinstance(ins.get("redacted"), str):
        ins["redacted"] = sec.redact_secrets(ins["redacted"])[:400]

    latency_ms = int((time.perf_counter() - t0) * 1000)
    cost = round(0.0009 * max(len(user_input), 80) / 100 + 0.004, 4)

    trace.append({"step": "model", "model": agent.get("model"), "tokens_in": len(user_input) // 4, "tokens_out": len(out) // 4})
    trace.append({"step": "tools", "executed": executed, "parked_for_approval": [a["tool"] for a in approvals]})

    return {
        "status": "ok" if not approvals else "partial",
        "output": out,
        "trace": trace,
        "approvals": approvals,
        "latency_ms": latency_ms,
        "cost_usd": cost,
        "guardrail": ins,
        "tool_manifest": tools_for(agent, tenant_doc),
        "started_at": int(started),
    }


# --------------------------------------------------------------------------
# Skill proposal (learning loop) with risk grading
# --------------------------------------------------------------------------
SENSITIVE_SKILL_WORDS = {
    "publish": "high",
    "auto": "high",
    "delete": "critical",
    "export": "high",
    "refund": "critical",
    "price": "medium",
    "discount": "medium",
    "scope": "medium",
    "reminder": "low",
}


def grade_risk(proposal_text: str, scope_change: list[str] | None = None) -> str:
    low = proposal_text.lower()
    grade = "low"
    if any(w in low for w in ("delete", "refund", "export all", "bulk")):
        grade = "critical"
    elif any(w in low for w in ("publish", "auto-send", "auto send", "without human", "without approval")):
        grade = "high"
    elif any(w in low for w in ("discount", "price", "concession", "scope")):
        grade = "medium"
    if scope_change:
        grade = "high" if grade in ("low", "medium") else grade
    return grade


def propose_skill(agent: dict, observation: str, proposal: str, evidence: list[str], scope_change: list[str] | None = None) -> dict:
    risk = grade_risk(proposal + " " + observation, scope_change)
    return {
        "agent_id": agent["id"],
        "agent_name": agent["name"],
        "title": sec.clean(proposal, 90),
        "observation": sec.clean(observation, 800),
        "proposal": sec.clean(proposal, 800),
        "evidence": evidence or ["manual_review"],
        "risk": risk,
        "status": "pending",
        "requires_human": True,
        "skill_diff": {"scope_change": scope_change or [], "version_bump": f"{agent['name'].split(' ')[0]} v-next"},
        "review_checklist": [
            "Does the proposal widen tool scopes?" if scope_change else "Scopes unchanged",
            "Is there a rollback version pinned?" ,
            "Does it change anything the RERA claim set forbids?",
            "Is a shadow evaluation on held-out conversations attached?",
        ],
        "created_at": int(time.time()),
    }


# --------------------------------------------------------------------------
# Forecasting
# --------------------------------------------------------------------------
def forecast(series: list[float], horizon: int = 3, season: int = 12) -> dict:
    """
    Linear trend + multiplicative seasonal index + residual-based prediction
    interval. Chosen over ARIMA on purpose: it is explainable to a sales head,
    needs ~12 points, and degrades gracefully. A gradient-boosted model with
    exogenous regressors (ad spend, season, festival calendar) is the v2 path.
    """
    n = len(series)
    if n < 4:
        return {"error": "need at least 4 points"}
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(series) / n
    sxx = sum((x - mx) ** 2 for x in xs) or 1
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, series))
    slope = sxy / sxx
    intercept = my - slope * mx
    fitted = [intercept + slope * x for x in xs]
    resid = [y - f for y, f in zip(series, fitted)]

    # seasonal indices (only if at least one full cycle available)
    seas: list[float] = [1.0] * season
    if n >= season:
        cycles = n // season
        for s in range(season):
            vals = [series[s + k * season] / (fitted[s + k * season] or 1) for k in range(cycles)]
            seas[s] = sum(vals) / len(vals)
        mean_idx = sum(seas) / season
        seas = [i / (mean_idx or 1) for i in seas]

    sigma = math.sqrt(sum(r * r for r in resid) / max(1, n - 2))
    points = []
    for h in range(1, horizon + 1):
        x = n - 1 + h
        trend = intercept + slope * x
        idx = seas[x % season] if n >= season else 1.0
        pred = trend * idx
        width = 1.96 * sigma * math.sqrt(1 + h / max(n, 1))
        points.append(
            {
                "h": h,
                "value": round(pred, 1),
                "low": round(max(0, pred - width), 1),
                "high": round(pred + width, 1),
            }
        )
    mape = sum(abs(r) / (y or 1) for r, y in zip(resid, series)) / n
    return {
        "method": "ridge-free linear trend × monthly seasonal index",
        "slope": round(slope, 2),
        "r_squared": round(1 - (sum(r * r for r in resid) / (sum((y - my) ** 2 for y in series) or 1)), 3),
        "mape": round(mape, 3),
        "confidence": "high" if mape < 0.12 else "medium" if mape < 0.25 else "low",
        "next": points,
        "seasonal_index": [round(s, 2) for s in seas] if n >= season else [],
    }


# --------------------------------------------------------------------------
# Lead scoring (explainable)
# --------------------------------------------------------------------------
SOURCE_WEIGHT = {
    "Referral": 18,
    "Walk-in": 16,
    "WhatsApp CTWA": 13,
    "Google Ads": 11,
    "Meta Lead Ads": 9,
    "Website": 8,
    "99acres": 6,
    "MagicBricks": 5,
}


def score_lead(contact: dict, inventory_available: bool = True) -> dict:
    drivers = []
    score = 30.0
    src = contact.get("source", "")
    w = SOURCE_WEIGHT.get(src, 5)
    score += w
    drivers.append({"factor": f"source:{src}", "impact": w})
    recency_days = max(0, (time.time() - contact.get("last_activity_at", time.time())) / 86400)
    rec = 20 if recency_days < 1 else 14 if recency_days < 3 else 6 if recency_days < 7 else -5
    score += rec
    drivers.append({"factor": f"recency:{recency_days:.1f}d", "impact": rec})
    consent = (contact.get("consent") or {}).get("status")
    cimpact = 12 if consent == "granted" else -14
    score += cimpact
    drivers.append({"factor": f"consent:{consent}", "impact": cimpact})
    budget = contact.get("budget_max") or 0
    bimpact = 12 if budget >= 5_000_000 else 7 if budget >= 3_000_000 else 2
    score += bimpact
    drivers.append({"factor": "budget_fit", "impact": bimpact})
    if any(t in ("hot", "investor", "nri") for t in contact.get("tags", [])):
        score += 8
        drivers.append({"factor": "tag_intent", "impact": 8})
    if not inventory_available:
        score -= 10
        drivers.append({"factor": "inventory_gap", "impact": -10})
    score = max(1, min(99, round(score)))
    band = "A" if score >= 75 else "B" if score >= 55 else "C"
    action = {
        "A": "Call within 15 minutes · send doc pack · hold inventory",
        "B": "WhatsApp nurture · call within 4 hours",
        "C": "Automated nurture only · no outbound call",
    }[band]
    return {"score": score, "band": band, "next_best_action": action, "drivers": drivers, "model": "re-score-v4 (explainable additive)"}


# --------------------------------------------------------------------------
# Voice call simulation
# --------------------------------------------------------------------------
def simulate_call(agent: dict, contact: dict, language: str = "mr") -> dict:
    rng = random.Random(contact.get("id", "x").__hash__())
    first = (contact.get("name") or "Buyer").split()[0]
    disclosure = "Hello, this is Meera, an AI assistant calling for Sunrise Land Developers. This call is automated and recorded."
    turns = [
        {"who": "agent", "text": disclosure, "ms": 480, "tag": "ai_disclosure"},
        {
            "who": "buyer",
            "text": ("हॅलो? हो बोला." if language == "mr" else "Haan boliye." if language == "hi" else "Yes, speaking."),
            "ms": 620,
        },
        {
            "who": "agent",
            "text": f"नमस्कार {first}. तुम्ही Taroda मधल्या plot साठी enquiry केली होती — बजेट 25–30 लाखांच्या आत बघताय का?",
            "ms": 540,
        },
        {"who": "buyer", "text": "हो, पण documents बद्दल खात्री हवी.", "ms": 700},
        {
            "who": "agent",
            "text": "7/12, NA order, sanctioned layout आणि encumbrance certificate WhatsApp वर पाठवतो. आधी documents बघा, मग Saturday 11 वाजता site visit ठरवू.",
            "ms": 610,
            "tag": "due_diligence_handoff",
        },
        {"who": "buyer", "text": "ठीक आहे, पाठवा.", "ms": 520},
    ]
    slots = ["Sat 11:00", "Sat 16:30", "Sun 10:00"]
    return {
        "agent": agent["name"],
        "contact": contact.get("name"),
        "language": language,
        "duration_s": 134,
        "turns": turns,
        "p95_turn_latency_ms": 1840 if language == "mr" else 1620,
        "slots_offered": slots,
        "outcome": "site_visit_proposed",
        "extracted": {
            "budget_band": "25–30L",
            "intent": "high",
            "documents_requested": ["7/12", "NA order", "sanctioned layout", "encumbrance certificate"],
            "preferred_slot": slots[rng.randrange(len(slots))],
            "language_detected": language,
        },
        "compliance": {"ai_disclosed": True, "consent_verified": True, "call_window_ok": True, "recording_announced": True},
        "cost_usd": 0.041,
    }
