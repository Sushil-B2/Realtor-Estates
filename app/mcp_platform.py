"""
Realtor Estates — `v1.37.0` Governed Native MCP Operating Layer, OAuth 2.1 PKCE
Scoped Marketplace Apps & Live OpenAPI 3.1 Generator.

Competitive parity with Salesforce Headless Toolkit / Agentforce, GoHighLevel
API v2/v3 Marketplace, `Go-High-Level-MCP-2026-Complete`, and `gohighlevel-php-sdk`,
engineered with Zero-Trust agentic security against October 2026 `REF8801` /
Unit 42 post-exploitation vectors:

  1. Native Model Context Protocol (`2025-06-18` JSON-RPC 2.0) over Streamable
     HTTP (`POST /api/v1/mcp`) and CLI `stdio` (`scripts/mcp_stdio.py`).
  2. Four governed tool profiles: `readonly`, `curated`, `stable`, `full`.
  3. Mandatory Guardrail Firewall + PII/Secret Output Scrubbing + Cryptographically
     Argument-Bound (`args_sha256`) Single-Use Human Confirmation Queue (`proposals`)
     before any consequential mutation executes.
  4. `GET /api/v1/mcp/doctor` structured readiness & `needsHumanAction` diagnostics.
  5. OAuth 2.1 Authorization-Code + Mandatory `S256` PKCE Scoped Apps (`re_oa_...`),
     with RFC 6749 §10.5 auth-code replay detection & least-privilege route gates.
  6. Live OpenAPI 3.1 introspection (`GET /api/v1/openapi.json`) over `api.ROUTES`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

from . import compliance as comp_mod
from . import govt_sales_cycle as gsc_mod
from . import phase3_expansion as p3_mod
from . import realtime as rt
from . import security as sec
from . import shared_state as shared

MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_PROFILES = ("readonly", "curated", "stable", "full")

VALID_OAUTH_SCOPES = {
    "crm:read",
    "crm:write",
    "inventory:read",
    "govt:read",
    "analytics:read",
    "mcp:readonly",
    "mcp:curated",
    "mcp:stable",
    "mcp:full",
}

# Consequential MCP tools that ALWAYS require an approved human confirmation proposal
HIGH_CONSEQUENCE_TOOLS = {
    "funnels.publish",
    "campaigns.adjust_budget",
    "deals.record_index_ii",
    "contacts.erase_dpdp",
}

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "contacts.list",
        "profiles": ("readonly", "curated", "stable", "full"),
        "oauth_scope": "crm:read",
        "min_role": "viewer",
        "risk": "read",
        "description": "List tenant contacts with masked PII, stage, lead score, and SLA status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "contacts.get",
        "profiles": ("readonly", "curated", "stable", "full"),
        "oauth_scope": "crm:read",
        "min_role": "viewer",
        "risk": "read",
        "description": "Fetch a single contact record (PII masked; raw ciphertexts never exposed).",
        "inputSchema": {
            "type": "object",
            "required": ["contact_id"],
            "properties": {"contact_id": {"type": "string"}},
        },
    },
    {
        "name": "properties.list",
        "profiles": ("readonly", "curated", "stable", "full"),
        "oauth_scope": "inventory:read",
        "min_role": "viewer",
        "risk": "read",
        "description": "List workspace properties with RERA ID, pricing, and 7/12 trust-pack readiness.",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
    },
    {
        "name": "inventory.summary",
        "profiles": ("readonly", "curated", "stable", "full"),
        "oauth_scope": "inventory:read",
        "min_role": "viewer",
        "risk": "read",
        "description": "Summarize unit/plot availability, active holds, and booked counts.",
        "inputSchema": {"type": "object", "properties": {"property_id": {"type": "string"}}},
    },
    {
        "name": "deals.list",
        "profiles": ("readonly", "curated", "stable", "full"),
        "oauth_scope": "crm:read",
        "min_role": "viewer",
        "risk": "read",
        "description": "List pipeline deals with stage, deal value, and document completion count.",
        "inputSchema": {
            "type": "object",
            "properties": {"stage": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        },
    },
    {
        "name": "govt.cost_sheet",
        "profiles": ("readonly", "curated", "stable", "full"),
        "oauth_scope": "govt:read",
        "min_role": "viewer",
        "risk": "read",
        "description": "Compute statutory Maharashtra Stamp Duty, Registration, GST, and §194-IA TDS.",
        "inputSchema": {
            "type": "object",
            "required": ["agreement_value_inr"],
            "properties": {
                "agreement_value_inr": {"type": "integer", "minimum": 100000},
                "asr_ready_reckoner_inr": {"type": "integer"},
                "jurisdiction": {"type": "string"},
                "property_category": {"type": "string"},
                "buyer_gender": {"type": "string"},
            },
        },
    },
    {
        "name": "compliance.fema_check",
        "profiles": ("curated", "stable", "full"),
        "oauth_scope": "govt:read",
        "min_role": "viewer",
        "risk": "read",
        "description": "Evaluate NRI/OCI/Foreign-National buyer eligibility under FEMA NDI Rules 2019 Rule 24.",
        "inputSchema": {
            "type": "object",
            "required": ["residency_status", "property_category"],
            "properties": {
                "residency_status": {"type": "string"},
                "property_category": {"type": "string"},
                "payment_rail": {"type": "string"},
            },
        },
    },
    {
        "name": "inbox.draft_reply",
        "profiles": ("curated", "stable", "full"),
        "oauth_scope": "crm:read",
        "min_role": "agent",
        "risk": "low",
        "description": "Generate a RERA-compliant WhatsApp/email reply draft without dispatching externally.",
        "inputSchema": {
            "type": "object",
            "required": ["contact_id"],
            "properties": {"contact_id": {"type": "string"}, "language": {"type": "string"}},
        },
    },
    {
        "name": "contacts.create",
        "profiles": ("stable", "full"),
        "oauth_scope": "crm:write",
        "min_role": "agent",
        "risk": "medium",
        "description": "Create a consented CRM lead with tenant-scoped blind indexing and encrypted PII.",
        "inputSchema": {
            "type": "object",
            "required": ["name", "phone", "consent_granted"],
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "city": {"type": "string"},
                "requirement": {"type": "string"},
                "consent_granted": {"type": "boolean"},
            },
        },
    },
    {
        "name": "tasks.create",
        "profiles": ("stable", "full"),
        "oauth_scope": "crm:write",
        "min_role": "agent",
        "risk": "low",
        "description": "Create a follow-up CRM task assigned to the current operator.",
        "inputSchema": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "contact_id": {"type": "string"},
                "priority": {"type": "string"},
            },
        },
    },
    {
        "name": "funnels.publish",
        "profiles": ("full",),
        "oauth_scope": "crm:write",
        "min_role": "manager",
        "risk": "high",
        "requires_confirmation": True,
        "description": "Publish a public funnel page (consequential — stages human confirmation first).",
        "inputSchema": {
            "type": "object",
            "required": ["funnel_id"],
            "properties": {
                "funnel_id": {"type": "string"},
                "confirmation_proposal_id": {"type": "string"},
            },
        },
    },
    {
        "name": "campaigns.adjust_budget",
        "profiles": ("full",),
        "oauth_scope": "crm:write",
        "min_role": "manager",
        "risk": "high",
        "requires_confirmation": True,
        "description": "Adjust paid ad campaign daily budget within workspace hard caps (requires human confirmation).",
        "inputSchema": {
            "type": "object",
            "required": ["campaign_id", "budget_daily_inr"],
            "properties": {
                "campaign_id": {"type": "string"},
                "budget_daily_inr": {"type": "integer", "minimum": 100},
                "confirmation_proposal_id": {"type": "string"},
            },
        },
    },
    {
        "name": "deals.record_index_ii",
        "profiles": ("full",),
        "oauth_scope": "crm:write",
        "min_role": "manager",
        "risk": "high",
        "requires_confirmation": True,
        "description": "Record statutory Index-II registered document number on a deal (requires human confirmation).",
        "inputSchema": {
            "type": "object",
            "required": ["deal_id", "registered_doc_no", "index_ii_ref"],
            "properties": {
                "deal_id": {"type": "string"},
                "registered_doc_no": {"type": "string"},
                "index_ii_ref": {"type": "string"},
                "confirmation_proposal_id": {"type": "string"},
            },
        },
    },
    {
        "name": "contacts.erase_dpdp",
        "profiles": ("full",),
        "oauth_scope": "crm:write",
        "min_role": "admin",
        "risk": "critical",
        "requires_confirmation": True,
        "description": "Execute irreversible DPDP §8(5) contact PII erasure (requires owner/admin confirmation).",
        "inputSchema": {
            "type": "object",
            "required": ["contact_id"],
            "properties": {
                "contact_id": {"type": "string"},
                "reason": {"type": "string"},
                "confirmation_proposal_id": {"type": "string"},
            },
        },
    },
]

MCP_RESOURCES: list[dict[str, str]] = [
    {
        "uri": "realtor://workspace/summary",
        "name": "Workspace Operational Summary",
        "mimeType": "application/json",
        "description": "High-level counts of contacts, properties, deals, open proposals, and SLA health.",
    },
    {
        "uri": "realtor://inventory/available",
        "name": "Available Units & Sanctioned Plots",
        "mimeType": "application/json",
        "description": "Live available inventory with RERA & pricing metadata.",
    },
    {
        "uri": "realtor://compliance/posture",
        "name": "DPDP, RERA & Telecom Compliance Posture",
        "mimeType": "application/json",
        "description": "Workspace regulatory posture across DPDP 2023, MahaRERA, and TRAI DLT.",
    },
]

MCP_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "maharashtra_title_scrutiny",
        "description": "Checklist prompt for 7/12 Satbara, Class-II Nazrana, Boja, NA order & 30-year title verification.",
        "arguments": [{"name": "property_id", "required": True}],
    },
    {
        "name": "speed_to_lead_whatsapp",
        "description": "90-second speed-to-lead RERA-compliant WhatsApp qualification script.",
        "arguments": [{"name": "contact_id", "required": True}, {"name": "language", "required": False}],
    },
]

ROLE_RANK = {"channel_partner": 0, "viewer": 1, "agent": 2, "manager": 3, "admin": 4, "owner": 5}


def _now() -> int:
    return int(time.time())


def _canonical_args_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    clean_args = {k: v for k, v in (arguments or {}).items() if k != "confirmation_proposal_id"}
    blob = json.dumps({"tool": tool_name, "arguments": clean_args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _mask_contact(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "city": c.get("city", ""),
        "stage": c.get("stage", "new"),
        "score": c.get("score", 0),
        "intent": c.get("intent", "unknown"),
        "source": c.get("source", ""),
        "consent_status": (c.get("consent") or {}).get("status", "pending") if isinstance(c.get("consent"), dict) else "pending",
        "phone_masked": "****** (encrypted)",
    }


def visible_tools(
    profile: str,
    role: str,
    oauth_scopes: set[str] | None = None,
) -> list[dict[str, Any]]:
    prof = profile if profile in MCP_PROFILES else "curated"
    rank = ROLE_RANK.get(role, 0)
    out: list[dict[str, Any]] = []
    for t in MCP_TOOLS:
        if prof not in t["profiles"]:
            continue
        if rank < ROLE_RANK.get(t["min_role"], 1):
            continue
        if oauth_scopes is not None:
            if t["oauth_scope"] not in oauth_scopes:
                continue
            prof_rank = MCP_PROFILES.index(prof)
            allowed_prof = max(
                [MCP_PROFILES.index(s.split(":", 1)[1]) for s in oauth_scopes if s.startswith("mcp:") and s.split(":", 1)[1] in MCP_PROFILES]
                or [-1]
            )
            if allowed_prof < prof_rank and f"mcp:{prof}" not in oauth_scopes and "mcp:full" not in oauth_scopes:
                # Allow if any mcp:* scope covers at least the tool's lowest profile
                min_tool_prof = min(MCP_PROFILES.index(p) for p in t["profiles"])
                if allowed_prof < min_tool_prof:
                    continue
        out.append({
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
            "annotations": {
                "readOnlyHint": t["risk"] == "read",
                "destructiveHint": bool(t.get("requires_confirmation")),
                "risk": t["risk"],
            },
        })
    return out


def mcp_doctor(store: Any, tenant: dict, role: str, *, oauth_scopes: set[str] | None = None) -> dict[str, Any]:
    """Structured MCP readiness & `needsHumanAction` diagnostic (`GET /api/v1/mcp/doctor`)."""
    tid = tenant["id"]
    pending_props = [
        p for p in store.all("proposals", tid)
        if p.get("status") == "pending" and p.get("kind") == "mcp_tool_confirmation"
    ]
    kill_switch = bool(tenant.get("kill_switch"))
    mcp_enabled = bool((tenant.get("features") or {}).get("mcp_gateway", True))
    needs_human: list[dict[str, Any]] = []
    if kill_switch:
        needs_human.append({"code": "TENANT_KILL_SWITCH_ACTIVE", "action": "Disable workspace kill switch in Security console."})
    if not mcp_enabled:
        needs_human.append({"code": "MCP_GATEWAY_DISABLED", "action": "Enable mcp_gateway feature flag for workspace."})
    for p in pending_props[:10]:
        needs_human.append({
            "code": "PENDING_MCP_CONFIRMATION",
            "proposal_id": p["id"],
            "tool": p.get("mcp_tool"),
            "action": f"Approve or reject proposal {p['id']} in Approval Queue (/api/v1/learning/{p['id']}/decide).",
        })

    return {
        "ok": (not kill_switch) and mcp_enabled,
        "protocol_version": MCP_PROTOCOL_VERSION,
        "profiles_available": list(MCP_PROFILES),
        "recommended_profile": "curated" if role in ("agent", "viewer") else "stable",
        "checks": {
            "auth_valid": ROLE_RANK.get(role, 0) >= 1,
            "tenant_kill_switch_off": not kill_switch,
            "mcp_gateway_enabled": mcp_enabled,
            "guardrail_firewall_active": True,
            "pii_output_redaction_active": True,
            "arg_bound_confirmation_queue": True,
        },
        "tool_counts_by_profile": {
            p: len(visible_tools(p, role, oauth_scopes))
            for p in MCP_PROFILES
        },
        "pending_confirmations": len(pending_props),
        "needsHumanAction": len(needs_human) > 0,
        "human_actions": needs_human,
    }


def _stage_mcp_confirmation(
    store: Any,
    tid: str,
    actor_name: str,
    tool_meta: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    tool_name = tool_meta["name"]
    args_hash = _canonical_args_hash(tool_name, arguments)
    agents = store.all("agents", tid)
    aid = agents[0]["id"] if agents else "agt_mcp"
    clean_preview = sec.redact_secrets(json.dumps({k: v for k, v in arguments.items() if k != "confirmation_proposal_id"}, ensure_ascii=False)[:400])
    prop = store.insert(
        "proposals",
        tid,
        {
            "agent_id": aid,
            "kind": "mcp_tool_confirmation",
            "mcp_tool": tool_name,
            "args_sha256": args_hash,
            "title": f"MCP Confirmation: {tool_name}",
            "observation": f"External MCP client ({actor_name}) requested consequential tool '{tool_name}'.",
            "proposal": f"Approve single-use execution of {tool_name} with arguments {clean_preview}",
            "evidence": [f"args_sha256:{args_hash[:16]}"],
            "scope_change": [tool_name],
            "risk": tool_meta["risk"],
            "status": "pending",
        },
        prefix="prp",
    )
    store.audit(tid, f"mcp:{actor_name}", "mcp.confirmation_staged", prop["id"], {"tool": tool_name, "args_sha256": args_hash[:16]})
    rt.HUB.publish(tid, "learning.updated", prop["id"], {"status": "pending", "risk": tool_meta["risk"]}, collections=("proposals",))
    return prop


def _verify_and_consume_confirmation(
    store: Any,
    tid: str,
    tool_name: str,
    arguments: dict[str, Any],
    proposal_id: str,
) -> tuple[bool, str]:
    p = store.get("proposals", tid, proposal_id)
    if not p or p.get("kind") != "mcp_tool_confirmation":
        return False, "Confirmation proposal not found."
    if p.get("mcp_tool") != tool_name:
        return False, "Confirmation proposal belongs to a different MCP tool."
    if p.get("status") != "promoted":
        return False, f"Confirmation proposal {proposal_id} is '{p.get('status')}', not approved/promoted."
    if p.get("mcp_consumed_at"):
        return False, f"Confirmation proposal {proposal_id} was already consumed (single-use policy)."
    expected_hash = str(p.get("args_sha256") or "")
    actual_hash = _canonical_args_hash(tool_name, arguments)
    if not expected_hash or not hmac.compare_digest(expected_hash, actual_hash):
        return False, "MCP_CONFIRMATION_ARGS_MISMATCH: Tool arguments differ from the human-approved payload."
    store.update("proposals", tid, proposal_id, {"mcp_consumed_at": _now()})
    return True, "ok"


def _execute_mcp_tool(
    store: Any,
    tenant: dict,
    user: dict,
    role: str,
    profile: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    oauth_scopes: set[str] | None = None,
) -> dict[str, Any]:
    tid = tenant["id"]
    if tenant.get("kill_switch"):
        return {"isError": True, "error_code": "KILL_SWITCH_ACTIVE", "content": [{"type": "text", "text": "Workspace kill switch is active; all MCP tool calls are halted."}]}
    if not (tenant.get("features") or {}).get("mcp_gateway", True):
        return {"isError": True, "error_code": "MCP_DISABLED", "content": [{"type": "text", "text": "MCP gateway is disabled for this workspace."}]}

    allowed_names = {t["name"] for t in visible_tools(profile, role, oauth_scopes)}
    tool_meta = next((t for t in MCP_TOOLS if t["name"] == tool_name), None)
    if not tool_meta or tool_name not in allowed_names:
        return {
            "isError": True,
            "error_code": "MCP_TOOL_FORBIDDEN",
            "content": [{"type": "text", "text": f"Tool '{tool_name}' is not permitted under profile '{profile}' and caller scopes."}],
        }

    # Guardrail firewall on serialized arguments (blocks prompt injection / SQLi / command injection / exfil)
    serialized_args = json.dumps(arguments or {}, ensure_ascii=False)
    insp = sec.inspect_text(serialized_args)
    if insp["action"] == "block":
        store.audit(tid, f"mcp:{user.get('email', 'client')}", "mcp.guardrail_blocked", tool_name, {"score": insp["score"], "owasp": insp["owasp"]})
        return {
            "isError": True,
            "error_code": "MCP_GUARDRAIL_BLOCKED",
            "content": [{"type": "text", "text": f"Blocked by AI Guardrail Firewall (score={insp['score']}, {', '.join(insp['owasp'])})."}],
        }

    # Consequential write human-confirmation gate (`proposals`)
    if tool_meta.get("requires_confirmation") or tool_name in HIGH_CONSEQUENCE_TOOLS:
        conf_id = str((arguments or {}).get("confirmation_proposal_id") or "").strip()
        if not conf_id:
            prop = _stage_mcp_confirmation(store, tid, user.get("email") or user.get("name") or "mcp-client", tool_meta, arguments or {})
            payload = {
                "status": "requires_human_confirmation",
                "needsHumanAction": True,
                "proposal_id": prop["id"],
                "approve_endpoint": f"/api/v1/learning/{prop['id']}/decide",
                "message": f"Tool '{tool_name}' is consequential ({tool_meta['risk']} risk). Approve proposal {prop['id']} with acknowledge_risk=true, then re-call with confirmation_proposal_id.",
            }
            return {"isError": False, "structuredContent": payload, "content": [{"type": "text", "text": json.dumps(payload)}]}
        ok_conf, reason_conf = _verify_and_consume_confirmation(store, tid, tool_name, arguments or {}, conf_id)
        if not ok_conf:
            return {"isError": True, "error_code": "MCP_CONFIRMATION_INVALID", "content": [{"type": "text", "text": reason_conf}]}

    # Execute verified tool
    args = arguments or {}
    result_obj: dict[str, Any]
    if tool_name == "contacts.list":
        lim = max(1, min(100, int(args.get("limit") or 25)))
        rows = store.all("contacts", tid)
        if args.get("stage"):
            rows = [r for r in rows if r.get("stage") == args["stage"]]
        result_obj = {"contacts": [_mask_contact(c) for c in rows[:lim]], "count": len(rows)}

    elif tool_name == "contacts.get":
        c = store.get("contacts", tid, str(args.get("contact_id") or ""))
        if not c:
            return {"isError": True, "error_code": "NOT_FOUND", "content": [{"type": "text", "text": "Contact not found."}]}
        result_obj = {"contact": _mask_contact(c)}

    elif tool_name == "properties.list":
        lim = max(1, min(50, int(args.get("limit") or 20)))
        props = store.all("properties", tid)
        if args.get("city"):
            props = [p for p in props if str(p.get("city") or "").lower() == str(args["city"]).lower()]
        result_obj = {
            "properties": [
                {
                    "id": p["id"],
                    "title": p.get("title"),
                    "city": p.get("city"),
                    "type": p.get("type"),
                    "price": p.get("price"),
                    "rera": p.get("rera", ""),
                    "status": p.get("status", "available"),
                }
                for p in props[:lim]
            ]
        }

    elif tool_name == "inventory.summary":
        units = store.all("units", tid)
        if args.get("property_id"):
            units = [u for u in units if u.get("property_id") == args["property_id"]]
        by_status: dict[str, int] = {}
        for u in units:
            st = str(u.get("status") or "available")
            by_status[st] = by_status.get(st, 0) + 1
        result_obj = {"total_units": len(units), "by_status": by_status}

    elif tool_name == "deals.list":
        lim = max(1, min(100, int(args.get("limit") or 25)))
        deals = store.all("deals", tid)
        if args.get("stage"):
            deals = [d for d in deals if d.get("stage") == args["stage"]]
        result_obj = {
            "deals": [
                {
                    "id": d["id"],
                    "title": d.get("title"),
                    "stage": d.get("stage"),
                    "value": d.get("value", 0),
                    "contact_id": d.get("contact_id"),
                }
                for d in deals[:lim]
            ]
        }

    elif tool_name == "govt.cost_sheet":
        result_obj = gsc_mod.compute_statutory_cost_sheet(args)

    elif tool_name == "compliance.fema_check":
        result_obj = p3_mod.evaluate_fema_eligibility(args)

    elif tool_name == "inbox.draft_reply":
        c = store.get("contacts", tid, str(args.get("contact_id") or ""))
        if not c:
            return {"isError": True, "error_code": "NOT_FOUND", "content": [{"type": "text", "text": "Contact not found."}]}
        lang = sec.clean(str(args.get("language") or "en"), 10)
        firm = tenant.get("name") or "Sunrise Land Developers"
        draft = (
            f"[AI Assistant · {firm} ({lang})]: Namaste {c.get('name')}, thank you for your enquiry regarding "
            f"{c.get('requirement') or 'our MahaRERA-registered project'}. Verified 7/12, NA Order & transparent "
            f"statutory cost sheet are ready — would Saturday 11:00 AM suit for a site visit?"
        )
        result_obj = {"contact_id": c["id"], "draft": sec.redact_secrets(draft), "dispatched": False}

    elif tool_name == "contacts.create":
        if not args.get("consent_granted"):
            return {"isError": True, "error_code": "CONSENT_REQUIRED", "content": [{"type": "text", "text": "DPDP consent_granted=true is required."}]}
        phone = sec.clean(str(args.get("phone") or ""), 20)
        name = sec.clean(str(args.get("name") or ""), 80)
        if not name or not sec.valid_phone(phone):
            return {"isError": True, "error_code": "VALIDATION_FAILED", "content": [{"type": "text", "text": "Valid name and phone are required."}]}
        if comp_mod.is_suppressed(store, tid, phone=phone):
            return {"isError": True, "error_code": "SUPPRESSED_BY_ERASURE", "content": [{"type": "text", "text": "Contact is on DPDP erasure suppression list."}]}
        existing = sec.find_by_blind_index(store, "contacts", tid, "phone_hash", phone)
        if existing:
            result_obj = {"duplicate": True, "contact": _mask_contact(existing[0])}
        else:
            rec = store.insert(
                "contacts",
                tid,
                {
                    "name": name,
                    "phone_enc": sec.seal(phone, aad=tid),
                    "phone_hash": sec.blind_index(phone, tenant_id=tid),
                    "email_enc": "",
                    "email_hash": "",
                    "city": sec.clean(str(args.get("city") or ""), 60),
                    "requirement": sec.clean(str(args.get("requirement") or "MCP enquiry"), 160),
                    "source": "MCP Agent",
                    "stage": "new",
                    "score": 60,
                    "owner_id": user.get("id", ""),
                    "consent": {"status": "granted", "purpose": "mcp_assisted_enquiry", "channel": "mcp", "captured_at": _now()},
                    "last_activity_at": _now(),
                },
                prefix="cnt",
            )
            result_obj = {"duplicate": False, "contact": _mask_contact(rec)}

    elif tool_name == "tasks.create":
        title = sec.clean(str(args.get("title") or ""), 160)
        if not title:
            return {"isError": True, "error_code": "VALIDATION_FAILED", "content": [{"type": "text", "text": "Task title is required."}]}
        tsk = store.insert(
            "tasks",
            tid,
            {
                "title": title,
                "contact_id": sec.clean(str(args.get("contact_id") or ""), 40),
                "priority": sec.clean(str(args.get("priority") or "medium"), 20),
                "done": False,
                "owner_id": user.get("id", ""),
                "due_at": _now() + 86400,
            },
            prefix="tsk",
        )
        result_obj = {"task": tsk}

    elif tool_name == "funnels.publish":
        fn = store.get("funnels", tid, str(args.get("funnel_id") or ""))
        if not fn:
            return {"isError": True, "error_code": "NOT_FOUND", "content": [{"type": "text", "text": "Funnel not found."}]}
        upd = store.update("funnels", tid, fn["id"], {"published": True})
        result_obj = {"funnel_id": fn["id"], "published": bool(upd and upd.get("published"))}

    elif tool_name == "campaigns.adjust_budget":
        camp = store.get("campaigns", tid, str(args.get("campaign_id") or ""))
        if not camp:
            return {"isError": True, "error_code": "NOT_FOUND", "content": [{"type": "text", "text": "Campaign not found."}]}
        new_daily = max(100, int(args.get("budget_daily_inr") or 1000))
        headroom = p3_mod.check_campaign_spend_headroom(store, tenant, campaign_id=camp["id"], proposed_daily_inr=new_daily)
        if headroom["daily_cap_exceeded"] or headroom["monthly_cap_exceeded"]:
            return {"isError": True, "error_code": "SPEND_CAP_EXCEEDED", "content": [{"type": "text", "text": "Requested budget breaches workspace hard spend cap."}]}
        store.update("campaigns", tid, camp["id"], {"budget_daily": new_daily})
        result_obj = {"campaign_id": camp["id"], "budget_daily_inr": new_daily}

    elif tool_name == "deals.record_index_ii":
        deal = store.get("deals", tid, str(args.get("deal_id") or ""))
        if not deal:
            return {"isError": True, "error_code": "NOT_FOUND", "content": [{"type": "text", "text": "Deal not found."}]}
        reg = gsc_mod.complete_index_ii(store, tid, deal, {
            "registered_doc_no": args.get("registered_doc_no"),
            "index_ii_ref": args.get("index_ii_ref"),
        }, actor=user.get("name") or "mcp")
        result_obj = {"deal_id": deal["id"], "registration": reg}

    elif tool_name == "contacts.erase_dpdp":
        erased = comp_mod.erase_contact(
            store, tenant, str(args.get("contact_id") or ""),
            actor=f"mcp:{user.get('email', 'admin')}",
            reason=sec.clean(str(args.get("reason") or "MCP confirmed DPDP erasure"), 160),
        )
        result_obj = {"erased": erased}
    else:  # pragma: no cover
        return {"isError": True, "error_code": "UNIMPLEMENTED", "content": [{"type": "text", "text": "Tool handler missing."}]}

    store.audit(tid, f"mcp:{user.get('email', 'client')}", f"mcp.tool.{tool_name}", "", {"profile": profile})
    scrubbed_json = sec.redact_secrets(json.dumps(result_obj, ensure_ascii=False, default=str))
    return {
        "isError": False,
        "structuredContent": json.loads(scrubbed_json),
        "content": [{"type": "text", "text": scrubbed_json}],
    }


def handle_mcp_rpc(
    store: Any,
    tenant: dict,
    user: dict,
    role: str,
    rpc_body: dict[str, Any],
    *,
    profile: str = "curated",
    oauth_scopes: set[str] | None = None,
) -> dict[str, Any]:
    """Process one JSON-RPC 2.0 MCP request frame."""
    req_id = rpc_body.get("id")
    method = str(rpc_body.get("method") or "")
    params = rpc_body.get("params") if isinstance(rpc_body.get("params"), dict) else {}
    prof = profile if profile in MCP_PROFILES else str(params.get("profile") or "curated")
    if prof not in MCP_PROFILES:
        prof = "curated"

    def _ok(res: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": res}

    def _err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return _ok({
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": "realtor-estates-mcp", "version": "1.37.0"},
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "profile": prof,
        })
    if method in ("notifications/initialized", "ping"):
        return _ok({"ok": True})
    if method == "tools/list":
        return _ok({"tools": visible_tools(prof, role, oauth_scopes), "profile": prof})
    if method == "tools/call":
        t_name = str(params.get("name") or "").strip()
        t_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not t_name:
            return _err(-32602, "Missing tool name in params.name.")
        res = _execute_mcp_tool(store, tenant, user, role, prof, t_name, t_args, oauth_scopes=oauth_scopes)
        return _ok(res)
    if method == "resources/list":
        return _ok({"resources": MCP_RESOURCES})
    if method == "resources/read":
        uri = str(params.get("uri") or "")
        tid = tenant["id"]
        if uri == "realtor://workspace/summary":
            data = {
                "workspace": tenant.get("name"),
                "contacts": len(store.all("contacts", tid)),
                "properties": len(store.all("properties", tid)),
                "deals": len(store.all("deals", tid)),
                "pending_proposals": sum(1 for p in store.all("proposals", tid) if p.get("status") == "pending"),
            }
        elif uri == "realtor://inventory/available":
            avail = [u for u in store.all("units", tid) if u.get("status") == "available"][:50]
            data = {"available_units": len(avail), "sample": avail[:15]}
        elif uri == "realtor://compliance/posture":
            data = {"region": tenant.get("region", "ap-south-1"), "ai_disclosure": True, "dpdp_erasure_ready": True}
        else:
            return _err(-32602, f"Unknown resource URI: {uri}")
        return _ok({"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, default=str)}]})
    if method == "prompts/list":
        return _ok({"prompts": MCP_PROMPTS})
    if method == "prompts/get":
        p_name = str(params.get("name") or "")
        p_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if p_name == "maharashtra_title_scrutiny":
            text = (
                f"Perform Maharashtra revenue & RERA scrutiny for property {sec.clean(p_args.get('property_id', ''), 40)}: "
                "1) Verify 7/12 Satbara <=30 days old; 2) Check Class-I vs Class-II Collector permission; "
                "3) Confirm zero unreleased Boja; 4) Verify NA Order + Mojni + 30-yr Title Search."
            )
        elif p_name == "speed_to_lead_whatsapp":
            text = (
                f"Draft a 90-second speed-to-lead WhatsApp greeting for contact {sec.clean(p_args.get('contact_id', ''), 40)} "
                f"in language '{sec.clean(p_args.get('language', 'mr'), 10)}' with AI disclosure and zero ROI guarantees."
            )
        else:
            return _err(-32602, f"Unknown prompt: {p_name}")
        return _ok({"messages": [{"role": "user", "content": {"type": "text", "text": text}}]})

    return _err(-32601, f"Unsupported MCP JSON-RPC method '{method}'.")


# =============================================================================
# OAuth 2.1 Authorization-Code + Mandatory S256 PKCE Scoped Marketplace Apps
# =============================================================================
def _pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def list_oauth_apps(tenant: dict) -> list[dict[str, Any]]:
    apps = ((tenant.get("settings") or {}).get("oauth_apps") or [])
    return [
        {
            "client_id": a["client_id"],
            "name": a["name"],
            "redirect_uris": a["redirect_uris"],
            "scopes": a["scopes"],
            "active": bool(a.get("active", True)),
            "created_at": a.get("created_at", 0),
        }
        for a in apps
    ]


def register_oauth_app(store: Any, tenant: dict, payload: dict[str, Any]) -> dict[str, Any]:
    tid = tenant["id"]
    name = sec.clean(str(payload.get("name") or ""), 80)
    if not name:
        raise ValueError("App name is required.")
    raw_uris = payload.get("redirect_uris") or []
    if not isinstance(raw_uris, list) or not raw_uris:
        raise ValueError("At least one HTTPS redirect_uri is required.")
    redirect_uris: list[str] = []
    for u in raw_uris[:5]:
        us = str(u or "").strip()
        if not (us.startswith("https://") or us.startswith("http://127.0.0.1:") or us.startswith("http://localhost:")):
            raise ValueError("redirect_uri must use https:// (or localhost for local CLI).")
        if "#" in us:
            raise ValueError("redirect_uri must not contain URL fragments.")
        redirect_uris.append(us)

    req_scopes = [str(s).strip() for s in (payload.get("scopes") or ["crm:read", "mcp:curated"]) if str(s).strip()]
    invalid = [s for s in req_scopes if s not in VALID_OAUTH_SCOPES]
    if invalid:
        raise ValueError(f"Unsupported OAuth scopes: {', '.join(invalid)}")

    client_id = "re_cid_" + secrets.token_hex(10)
    client_secret = "re_csec_" + secrets.token_urlsafe(24)
    settings = dict(tenant.get("settings") or {})
    apps = list(settings.get("oauth_apps") or [])
    app_row = {
        "client_id": client_id,
        "name": name,
        "client_secret_enc": sec.seal_tenant(client_secret, tid, aad=f"{tid}:oauth:{client_id}"),
        "redirect_uris": redirect_uris,
        "scopes": sorted(set(req_scopes)),
        "active": True,
        "created_at": _now(),
    }
    apps.append(app_row)
    settings["oauth_apps"] = apps
    tenant["settings"] = settings
    store.update("tenants", tid, tid, {"settings": settings})
    return {
        "app": list_oauth_apps({"settings": {"oauth_apps": [app_row]}})[0],
        "client_secret_once": client_secret,
    }


def create_oauth_authorization_code(
    store: Any,
    tenant: dict,
    user: dict,
    payload: dict[str, Any],
) -> dict[str, Any]:
    tid = tenant["id"]
    client_id = str(payload.get("client_id") or "").strip()
    redirect_uri = str(payload.get("redirect_uri") or "").strip()
    challenge = str(payload.get("code_challenge") or "").strip()
    method = str(payload.get("code_challenge_method") or "").strip().upper()
    state = sec.clean(str(payload.get("state") or ""), 120)

    if method != "S256" or len(challenge) < 42:
        raise ValueError("PKCE_S256_REQUIRED: OAuth 2.1 requires code_challenge_method=S256.")

    apps = (tenant.get("settings") or {}).get("oauth_apps") or []
    app_row = next((a for a in apps if a["client_id"] == client_id and a.get("active", True)), None)
    if not app_row:
        raise LookupError("OAuth client_id not found or inactive.")
    if redirect_uri not in app_row["redirect_uris"]:
        raise ValueError("REDIRECT_URI_MISMATCH: redirect_uri does not exactly match registered URIs.")

    requested_scopes = [str(s).strip() for s in (payload.get("scopes") or app_row["scopes"]) if str(s).strip()]
    if any(s not in app_row["scopes"] for s in requested_scopes):
        raise ValueError("SCOPE_ESCALATION_DENIED: Requested scopes exceed registered app scopes.")

    code_jti = secrets.token_urlsafe(18)
    claims = {
        "typ": "oauth_code",
        "jti": code_jti,
        "tid": tid,
        "uid": user["id"],
        "role": user["role"],
        "cid": client_id,
        "ruri": redirect_uri,
        "cc": challenge,
        "scp": sorted(set(requested_scopes)),
        "exp": _now() + 60,
    }
    raw_claims = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode("ascii")
    sig = hmac.new(sec.KEYS["session"], f"oacode:{raw_claims}".encode("ascii"), hashlib.sha256).hexdigest()
    return {
        "code": f"re_oac_{raw_claims}.{sig}",
        "state": state,
        "expires_in": 60,
        "scopes": claims["scp"],
    }


def exchange_oauth_code(store: Any, payload: dict[str, Any]) -> dict[str, Any]:
    grant_type = str(payload.get("grant_type") or "authorization_code").strip()
    if grant_type != "authorization_code":
        raise ValueError("UNSUPPORTED_GRANT_TYPE: Only authorization_code with PKCE S256 is supported.")
    code = str(payload.get("code") or "").strip()
    verifier = str(payload.get("code_verifier") or "").strip()
    client_id = str(payload.get("client_id") or "").strip()
    client_secret = str(payload.get("client_secret") or "").strip()
    redirect_uri = str(payload.get("redirect_uri") or "").strip()

    if not code.startswith("re_oac_") or "." not in code:
        raise PermissionError("INVALID_GRANT: Malformed authorization code.")
    raw_claims, sig = code[len("re_oac_"):].split(".", 1)
    expected_sig = hmac.new(sec.KEYS["session"], f"oacode:{raw_claims}".encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise PermissionError("INVALID_GRANT: Invalid authorization code signature.")

    pad = "=" * (-len(raw_claims) % 4)
    claims = json.loads(base64.urlsafe_b64decode(raw_claims + pad).decode("utf-8"))
    if int(claims.get("exp") or 0) < _now():
        raise PermissionError("INVALID_GRANT: Authorization code expired.")

    # RFC 6749 §10.5: single-use authorization code; if replayed, revoke associated access token jti!
    code_jti = str(claims["jti"])
    derived_tok_jti = hashlib.sha256(f"oatok_jti:{code_jti}".encode("utf-8")).hexdigest()[:24]
    if not shared.ADAPTER.claim_once(f"oauth_code:{code_jti}", 600.0):
        shared.ADAPTER.set_ttl(f"oauth_revoked:{derived_tok_jti}", "1", 7200.0)
        raise PermissionError("OAUTH_CODE_REPLAY_DETECTED: Authorization code already used; issued grant revoked.")

    if claims.get("cid") != client_id or claims.get("ruri") != redirect_uri:
        raise PermissionError("INVALID_GRANT: client_id or redirect_uri mismatch.")

    # Verify confidential client secret
    tid = claims["tid"]
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), None)
    if not tenant:
        raise PermissionError("INVALID_GRANT: Workspace not found.")
    app_row = next((a for a in ((tenant.get("settings") or {}).get("oauth_apps") or []) if a["client_id"] == client_id and a.get("active", True)), None)
    if not app_row:
        raise PermissionError("INVALID_CLIENT: OAuth app revoked or missing.")
    expected_secret = sec.unseal(app_row["client_secret_enc"], aad=f"{tid}:oauth:{client_id}")
    if not client_secret or not hmac.compare_digest(client_secret, expected_secret):
        raise PermissionError("INVALID_CLIENT: Invalid client_secret.")

    # Mandatory PKCE S256 check
    if len(verifier) < 43 or not hmac.compare_digest(_pkce_s256(verifier), str(claims["cc"])):
        raise PermissionError("PKCE_VERIFICATION_FAILED: code_verifier does not match S256 challenge.")

    tok_claims = {
        "typ": "oauth_access",
        "jti": derived_tok_jti,
        "tid": tid,
        "uid": claims["uid"],
        "role": claims["role"],
        "cid": client_id,
        "scp": claims["scp"],
        "exp": _now() + 3600,
    }
    raw_tok = base64.urlsafe_b64encode(json.dumps(tok_claims, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode("ascii")
    tok_sig = hmac.new(sec.KEYS["session"], f"oatok:{raw_tok}".encode("ascii"), hashlib.sha256).hexdigest()
    return {
        "access_token": f"re_oa_{raw_tok}.{tok_sig}",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": " ".join(claims["scp"]),
    }


def revoke_oauth_token(raw_token: str) -> bool:
    tok = (raw_token or "").strip()
    if not tok.startswith("re_oa_") or "." not in tok:
        return False
    raw_part, sig = tok[len("re_oa_"):].split(".", 1)
    expected = hmac.new(sec.KEYS["session"], f"oatok:{raw_part}".encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    pad = "=" * (-len(raw_part) % 4)
    claims = json.loads(base64.urlsafe_b64decode(raw_part + pad).decode("utf-8"))
    shared.ADAPTER.set_ttl(f"oauth_revoked:{claims['jti']}", "1", 7200.0)
    return True


def _oauth_route_allowed(method: str, path: str, scopes: set[str]) -> bool:
    """Enforce least-privilege route allowlist on OAuth2 `re_oa_...` bearer tokens."""
    # Admin/security/stepup/auth/user-management surfaces are NEVER accessible to third-party OAuth apps
    forbidden_prefixes = (
        "/api/v1/security",
        "/api/v1/stepup",
        "/api/v1/users",
        "/api/v1/auth",
        "/api/v1/tenant",
        "/api/v1/oauth",
        "/api/v1/learning",
        "/api/v1/agency",
        "/api/v1/sso",
        "/api/v1/observability",
    )
    if any(path.startswith(p) for p in forbidden_prefixes):
        return False
    if path.startswith("/api/v1/mcp"):
        return bool(scopes & {"mcp:readonly", "mcp:curated", "mcp:stable", "mcp:full"})
    if path.startswith("/api/v1/openapi.json"):
        return True
    if path.startswith(("/api/v1/contacts", "/api/v1/deals", "/api/v1/tasks", "/api/v1/visits", "/api/v1/funnels")):
        return ("crm:write" in scopes) if method in ("POST", "PUT", "PATCH", "DELETE") else bool(scopes & {"crm:read", "crm:write"})
    if path.startswith(("/api/v1/properties", "/api/v1/units", "/api/v1/inventory")) and method == "GET":
        return "inventory:read" in scopes
    if path.startswith("/api/v1/govt/cost-sheet") or path.startswith("/api/v1/fema/check"):
        return "govt:read" in scopes
    if path.startswith("/api/v1/analytics") and method == "GET":
        return "analytics:read" in scopes
    return False


def resolve_oauth_bearer(
    store: Any,
    token: str,
    method: str,
    path: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, set[str] | None, str]:
    """Verify `re_oa_...` token; returns `(claims, user, scopes, error_code)`."""
    if not token or not token.startswith("re_oa_") or "." not in token:
        return None, None, None, ""
    raw_part, sig = token[len("re_oa_"):].split(".", 1)
    expected = hmac.new(sec.KEYS["session"], f"oatok:{raw_part}".encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None, None, None, "UNAUTHENTICATED"
    pad = "=" * (-len(raw_part) % 4)
    claims = json.loads(base64.urlsafe_b64decode(raw_part + pad).decode("utf-8"))
    if int(claims.get("exp") or 0) < _now():
        return None, None, None, "UNAUTHENTICATED"
    if shared.ADAPTER.get(f"oauth_revoked:{claims.get('jti')}"):
        return None, None, None, "UNAUTHENTICATED"

    tid = claims["tid"]
    user = next((u for u in store.db["users"] if u["id"] == claims["uid"] and u["tenant_id"] == tid), None)
    if not user or not user.get("active", True):
        return None, None, None, "UNAUTHENTICATED"

    scopes = set(claims.get("scp") or [])
    if not _oauth_route_allowed(method, path, scopes):
        return None, None, scopes, "OAUTH_SCOPE_DENIED"

    synth_claims = {
        "uid": user["id"],
        "tid": tid,
        "role": user["role"],
        "sid": f"oauth:{claims['jti']}",
        "amr": ["oauth2_pkce"],
    }
    return synth_claims, user, scopes, ""


# =============================================================================
# Live OpenAPI 3.1 Specification Introspector (`GET /api/v1/openapi.json`)
# =============================================================================
_REGEX_PARAM_RE = re.compile(r"\(\?P<(\w+)>[^)]+\)")


def build_openapi_spec(routes: list[tuple[str, Any, str, dict[str, Any]]], route_fns: dict[str, Any]) -> dict[str, Any]:
    """Introspect live `@route(...)` definitions into an OpenAPI 3.1.0 document."""
    paths: dict[str, dict[str, Any]] = {}
    for method, pattern, fn_name, _meta in routes:
        raw_pat = pattern.pattern.lstrip("^").rstrip("$")
        param_names = _REGEX_PARAM_RE.findall(raw_pat)
        oas_path = _REGEX_PARAM_RE.sub(r"{\1}", raw_pat)
        fn = route_fns.get(fn_name)
        is_pub = bool(getattr(fn, "_public", False)) if fn else False
        min_role = getattr(fn, "_min_role", None) if fn else None
        bucket = getattr(fn, "_bucket", None) if fn else None

        parameters = [
            {"name": p, "in": "path", "required": True, "schema": {"type": "string"}}
            for p in param_names
        ]
        op: dict[str, Any] = {
            "operationId": fn_name,
            "summary": fn_name.replace("_", " ").title(),
            "parameters": parameters,
            "responses": {
                "200": {"description": "Successful JSON response"},
                "401": {"description": "Unauthenticated"},
                "403": {"description": "Forbidden scope or RBAC gate"},
            },
        }
        if not is_pub:
            op["security"] = [{"bearerAuth": []}, {"oauth2PKCE": []}]
        if min_role:
            op["x-min-role"] = min_role
        if bucket:
            op["x-rate-bucket"] = bucket
        if method in ("POST", "PUT", "PATCH"):
            op["requestBody"] = {
                "required": False,
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        paths.setdefault(oas_path, {})[method.lower()] = op

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Realtor Estates Revenue OS API",
            "version": "1.37.0",
            "description": "Multi-tenant Real-Estate CRM, Governed MCP Operating Layer, Maharashtra Revenue/RERA Vault & Omnichannel Automation API.",
        },
        "servers": [{"url": "/", "description": "Same-origin workspace edge"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "HMAC-Session"},
                "oauth2PKCE": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "/api/v1/oauth/authorize",
                            "tokenUrl": "/api/v1/oauth/token",
                            "scopes": {s: f"Scoped access: {s}" for s in sorted(VALID_OAUTH_SCOPES)},
                        }
                    },
                },
            }
        },
        "paths": paths,
        "x-route-count": len(routes),
    }
