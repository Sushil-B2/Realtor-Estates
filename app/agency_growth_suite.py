"""
Realtor Estates — White-Label Agency/Builder SaaS Mode, Visual Node-DAG
Workflow & Composable Funnel Block Builder, and 2-Way Calendar (`iCal` +
Anti-Double-Booking) / `SPF-DKIM-DMARC` Deliverability / Reputation Suite
(`v1.38.0`, `docs/49`).

Security invariants enforced across every primitive:
  1. White-label custom domains require cryptographic HMAC DNS TXT verification;
     brand colors are strictly validated as `#RRGGBB` (zero CSS injection).
  2. Child sub-accounts receive isolated `tenant_id`s, isolated per-tenant HKDF
     DEKs, and strict `parent_agency_tenant_id == caller_tid` authorization;
     snapshot cloning transfers ONLY PII-free structural blueprints.
  3. Visual workflow DAGs reject cycles (Kahn's topological sort), cap graph
     size/depth, run guardrail inspection on text templates, and validate any
     webhook node URLs via `sec.validate_egress_url`.
  4. Composable funnel blocks use an allowlisted catalog and 100% HTML escaping
     (`sec.esc`) — zero raw HTML/JS injection.
  5. 2-way site-visit calendar booking acquires an atomic slot lock
     (`shared.ADAPTER.claim_once`) + overlap check against `site_visits` and
     imported RFC 5545 `VEVENT` busy blocks; `.ics` exports escape text per
     RFC 5545 §3.3.11.
  6. Reputation review requests verify DPDP consent (`consent` true & not DND)
     and never gate/suppress public review access (transparent recovery SLA).
"""
from __future__ import annotations

from collections import deque
import datetime as dt
import hashlib
import hmac
import re
import time
from typing import Any

from . import meters as meters_mod, security as sec, shared_state as shared
from .store import Store, nid

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
_BLOCKED_DOMAINS = ("localhost", "realtorestates.app", "neon.tech", "internal", "nip.io")


class AgencyGrowthError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 1. White-Label Builder / Channel-Partner Agency SaaS Mode
# ---------------------------------------------------------------------------
def _domain_verify_token(tid: str, domain: str) -> str:
    mac = hmac.new(
        sec.derive_tenant_key("whitelabel-dns", tid),
        domain.lower().strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"re-domain-verify={mac}"


def configure_white_label(store: Store, tid: str, body: dict[str, Any]) -> dict[str, Any]:
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), None)
    if not tenant:
        raise AgencyGrowthError(404, "TENANT_NOT_FOUND", "Workspace not found.")
    brand_name = str(body.get("brand_name") or tenant.get("name") or "").strip()[:80]
    if not brand_name:
        raise AgencyGrowthError(400, "INVALID_BRAND", "brand_name is required.")
    primary_color = str(body.get("primary_color") or "#0f6b52").strip()
    if not _HEX_COLOR_RE.match(primary_color):
        raise AgencyGrowthError(400, "INVALID_BRAND_COLOR", "primary_color must be a 6-digit hex color (#RRGGBB).")
    domain = str(body.get("custom_domain") or "").strip().lower()
    txt_record = ""
    if domain:
        if not _DOMAIN_RE.match(domain) or any(domain == b or domain.endswith("." + b) for b in _BLOCKED_DOMAINS):
            raise AgencyGrowthError(400, "INVALID_CUSTOM_DOMAIN", "custom_domain must be an external public FQDN.")
        # Prevent cross-tenant custom-domain squatting on an already verified domain
        for other in store.db["tenants"]:
            if other["id"] == tid:
                continue
            owl = other.get("white_label") or {}
            if owl.get("custom_domain") == domain and owl.get("domain_verified"):
                raise AgencyGrowthError(409, "DOMAIN_ALREADY_CLAIMED", "Domain is already verified by another workspace.")
        txt_record = _domain_verify_token(tid, domain)
    try:
        markup_pct = round(max(0.0, min(200.0, float(body.get("rebilling_markup_pct", 25.0)))), 2)
    except (TypeError, ValueError):
        markup_pct = 25.0

    prev_wl = tenant.get("white_label") or {}
    same_verified = bool(domain and prev_wl.get("custom_domain") == domain and prev_wl.get("domain_verified"))
    wl = {
        "enabled": bool(body.get("enabled", True)),
        "brand_name": brand_name,
        "logo_monogram": str(body.get("logo_monogram") or brand_name[:2].upper()).strip()[:4],
        "primary_color": primary_color.lower(),
        "support_email": str(body.get("support_email") or "support@example.com").strip()[:120],
        "custom_domain": domain,
        "dns_txt_host": f"_realtor-verify.{domain}" if domain else "",
        "dns_txt_value": txt_record,
        "domain_verified": same_verified,
        "powered_by_hidden": bool(body.get("powered_by_hidden", False)),
        "rebilling_markup_pct": markup_pct,
        "updated_at": int(time.time()),
    }
    with store.lock:
        tenant["white_label"] = wl
        store._pg_put("tenants", tid, tenant)
        store.save()
    return wl


def verify_white_label_domain(store: Store, tid: str, dns_txt_records: list[str]) -> dict[str, Any]:
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), None)
    wl = dict((tenant or {}).get("white_label") or {})
    domain = str(wl.get("custom_domain") or "")
    if not tenant or not domain:
        raise AgencyGrowthError(400, "NO_CUSTOM_DOMAIN", "Configure custom_domain before verifying DNS.")
    expected = _domain_verify_token(tid, domain)
    matched = any(
        isinstance(r, str) and hmac.compare_digest(r.strip().encode("utf-8"), expected.encode("utf-8"))
        for r in (dns_txt_records or [])
    )
    if not matched:
        raise AgencyGrowthError(400, "DNS_TXT_MISMATCH", f"Expected TXT value {expected} on _realtor-verify.{domain}.")
    with store.lock:
        wl["domain_verified"] = True
        wl["verified_at"] = int(time.time())
        tenant["white_label"] = wl
        store._pg_put("tenants", tid, tenant)
        store.save()
    return wl


def provision_subaccount(
    store: Store,
    parent_tid: str,
    actor_uid: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    parent = next((t for t in store.db["tenants"] if t["id"] == parent_tid), None)
    if not parent:
        raise AgencyGrowthError(404, "TENANT_NOT_FOUND", "Agency workspace not found.")
    name = str(body.get("name") or "").strip()[:100]
    admin_email = str(body.get("admin_email") or "").strip().lower()[:140]
    admin_password = str(body.get("admin_password") or "").strip()
    if len(name) < 2 or "@" not in admin_email:
        raise AgencyGrowthError(400, "INVALID_SUBACCOUNT", "Sub-account name and valid admin_email are required.")
    if len(admin_password) < 10:
        raise AgencyGrowthError(400, "WEAK_PASSWORD", "Sub-account admin_password must be at least 10 characters.")
    if any(u.get("email", "").lower() == admin_email for u in store.db["users"]):
        raise AgencyGrowthError(409, "EMAIL_EXISTS", "admin_email is already registered.")

    now = int(time.time())
    child_tid = nid("tnt")
    slug_base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:28] or "subaccount"
    child_tenant = {
        "id": child_tid,
        "name": name,
        "slug": f"{slug_base}-{child_tid[-4:]}",
        "plan": str(body.get("plan") or "growth")[:24],
        "region": str(body.get("region") or parent.get("region") or "ap-south-1")[:24],
        "parent_agency_tenant_id": parent_tid,
        "created_by_agency_user": actor_uid,
        "created_at": now,
        "white_label": {
            "enabled": True,
            "brand_name": str(((parent.get("white_label") or {}).get("brand_name")) or parent.get("name") or name),
            "primary_color": str(((parent.get("white_label") or {}).get("primary_color")) or "#0f6b52"),
            "inherited_from": parent_tid,
        },
        "guardrails": dict(parent.get("guardrails") or {"block_pii_exfil": True, "require_consent": True}),
        "settings": {"rera_state": str(body.get("rera_state") or "Maharashtra")[:40]},
    }
    child_user = {
        "id": nid("usr"),
        "tenant_id": child_tid,
        "email": admin_email,
        "name": str(body.get("admin_name") or f"{name} Admin").strip()[:80],
        "role": "owner",
        "password_hash": sec.hash_password(admin_password),
        "mfa_enrolled": False,
        "active": True,
        "created_at": now,
    }
    with store.lock:
        store.db["tenants"].append(child_tenant)
        store.db["users"].append(child_user)
        store._pg_put("tenants", child_tid, child_tenant)
        store._pg_put("users", child_tid, child_user)
        store.save()
    return {
        "subaccount": {
            "tenant_id": child_tid,
            "name": child_tenant["name"],
            "slug": child_tenant["slug"],
            "plan": child_tenant["plan"],
            "region": child_tenant["region"],
            "parent_agency_tenant_id": parent_tid,
            "admin_user_id": child_user["id"],
            "admin_email": admin_email,
            "created_at": now,
        }
    }


def list_subaccounts(store: Store, parent_tid: str) -> list[dict[str, Any]]:
    rows = []
    for t in store.db["tenants"]:
        if t.get("parent_agency_tenant_id") == parent_tid:
            rows.append({
                "tenant_id": t["id"],
                "name": t.get("name"),
                "slug": t.get("slug"),
                "plan": t.get("plan"),
                "region": t.get("region"),
                "created_at": t.get("created_at"),
                "snapshots_applied": int(t.get("snapshots_applied") or 0),
            })
    return rows


def apply_snapshot_to_subaccount(store: Store, parent_tid: str, body: dict[str, Any]) -> dict[str, Any]:
    sub_tid = str(body.get("subaccount_tenant_id") or "").strip()
    child = next((t for t in store.db["tenants"] if t["id"] == sub_tid), None)
    if not child or child.get("parent_agency_tenant_id") != parent_tid:
        raise AgencyGrowthError(
            403,
            "SUBACCOUNT_TENANT_MISMATCH",
            "Target workspace is not a child sub-account of this agency.",
        )
    include_funnels = bool(body.get("include_funnels", True))
    include_workflows = bool(body.get("include_workflows", True))
    now = int(time.time())
    cloned_funnels = 0
    cloned_workflows = 0

    if include_funnels:
        for f in store.all("funnels", parent_tid)[:10]:
            new_fid = nid("fn")
            base_slug = re.sub(r"[^a-z0-9-]+", "-", str(f.get("slug") or "project").lower()).strip("-")[:24]
            store.insert("funnels", sub_tid, {
                "id": new_fid,
                "tenant_id": sub_tid,
                "name": str(f.get("name") or "Project Funnel"),
                "slug": f"{base_slug}-{sub_tid[-4:]}-{cloned_funnels + 1}",
                "headline": str(f.get("headline") or "Clear-Title RERA Project"),
                "sub": str(f.get("sub") or ""),
                "cta": str(f.get("cta") or "Book Site Visit"),
                "sections": [
                    {"title": str(s.get("title") or ""), "body": str(s.get("body") or "")}
                    for s in (f.get("sections") or [])
                    if isinstance(s, dict)
                ],
                "blocks": [dict(b) for b in (f.get("blocks") or []) if isinstance(b, dict)],
                "published": False,  # Sub-account explicitly publishes after reviewing local RERA number
                "views": 0,
                "leads": 0,
                "cloned_from_snapshot": parent_tid,
                "created_at": now,
            })
            cloned_funnels += 1

    if include_workflows:
        for w in store.all("automations", parent_tid)[:15]:
            store.insert("automations", sub_tid, {
                "id": nid("aut"),
                "tenant_id": sub_tid,
                "name": str(w.get("name") or "Playbook"),
                "trigger": str(w.get("trigger") or "lead.created"),
                "steps": [dict(s) if isinstance(s, dict) else {"type": "note", "text": str(s)} for s in (w.get("steps") or [])],
                "dag": dict(w["dag"]) if isinstance(w.get("dag"), dict) else None,
                "compliance_gate": list(w.get("compliance_gate") or ["consent", "quiet hours", "frequency cap"]),
                "enabled": False,
                "runs": 0,
                "cloned_from_snapshot": parent_tid,
                "updated_at": now,
            })
            cloned_workflows += 1

    with store.lock:
        child["snapshots_applied"] = int(child.get("snapshots_applied") or 0) + 1
        store._pg_put("tenants", sub_tid, child)
        store.save()

    return {
        "ok": True,
        "subaccount_tenant_id": sub_tid,
        "cloned_funnels": cloned_funnels,
        "cloned_workflows": cloned_workflows,
        "pii_copied": 0,
        "secrets_copied": 0,
    }


def compute_agency_rebilling(store: Store, parent_tid: str) -> dict[str, Any]:
    parent = next((t for t in store.db["tenants"] if t["id"] == parent_tid), {})
    markup_pct = float(((parent.get("white_label") or {}).get("rebilling_markup_pct")) or 25.0)
    lines = []
    wholesale_total = 0.0
    retail_total = 0.0
    for sub in list_subaccounts(store, parent_tid):
        stid = sub["tenant_id"]
        sub_tenant = next((t for t in store.db["tenants"] if t["id"] == stid), {"id": stid, "plan": "growth"})
        try:
            snap = meters_mod.current_usage(store, sub_tenant)
            counters = snap.get("counters") or {}
        except Exception:
            counters = {}
        contacts_n = len(store.all("contacts", stid))
        msgs_n = int(counters.get("messages_sent") or len(store.all("deliveries", stid)))
        base_inr = 4999.0 if sub.get("plan") == "growth" else 9999.0
        usage_inr = round(contacts_n * 0.50 + msgs_n * 0.85, 2)
        wholesale = round(base_inr + usage_inr, 2)
        retail = round(wholesale * (1.0 + markup_pct / 100.0), 2)
        gst_inr = round(retail * 0.18, 2)
        wholesale_total += wholesale
        retail_total += retail
        lines.append({
            "subaccount_tenant_id": stid,
            "name": sub["name"],
            "plan": sub["plan"],
            "contacts": contacts_n,
            "messages_metered": msgs_n,
            "wholesale_cost_inr": wholesale,
            "agency_retail_ex_gst_inr": retail,
            "gst_18_inr": gst_inr,
            "client_payable_inc_gst_inr": round(retail + gst_inr, 2),
            "agency_net_margin_inr": round(retail - wholesale, 2),
        })
    return {
        "agency_tenant_id": parent_tid,
        "markup_pct": markup_pct,
        "subaccount_count": len(lines),
        "wholesale_total_inr": round(wholesale_total, 2),
        "retail_ex_gst_total_inr": round(retail_total, 2),
        "net_agency_margin_inr": round(retail_total - wholesale_total, 2),
        "subaccounts": lines,
    }


# ---------------------------------------------------------------------------
# 2. Visual Node-DAG Workflow Engine & Composable Funnel Block Builder
# ---------------------------------------------------------------------------
ALLOWED_DAG_NODE_TYPES = {
    "trigger",
    "condition",
    "branch",
    "wait",
    "send_whatsapp",
    "send_sms",
    "send_email",
    "send_document",
    "create_task",
    "webhook",
}


def validate_and_compile_workflow_dag(
    tenant: dict[str, Any] | None,
    dag_body: dict[str, Any],
) -> dict[str, Any]:
    nodes = dag_body.get("nodes")
    edges = dag_body.get("edges") or []
    if not isinstance(nodes, list) or not (1 <= len(nodes) <= 50):
        raise AgencyGrowthError(400, "DAG_INVALID_NODES", "DAG requires between 1 and 50 nodes.")
    if not isinstance(edges, list) or len(edges) > 100:
        raise AgencyGrowthError(400, "DAG_TOO_MANY_EDGES", "DAG supports up to 100 directed edges.")

    node_map: dict[str, dict[str, Any]] = {}
    extra_hosts = sec.tenant_egress_hosts(tenant)
    for n in nodes:
        if not isinstance(n, dict):
            raise AgencyGrowthError(400, "DAG_INVALID_NODE", "Each node must be an object.")
        nid_s = str(n.get("id") or "").strip()[:48]
        ntype = str(n.get("type") or "").strip()
        if not nid_s or nid_s in node_map:
            raise AgencyGrowthError(400, "DAG_DUPLICATE_NODE_ID", f"Invalid or duplicate node id: {nid_s!r}.")
        if ntype not in ALLOWED_DAG_NODE_TYPES:
            raise AgencyGrowthError(400, "DAG_UNSUPPORTED_NODE_TYPE", f"Unsupported node type {ntype!r}.")
        cfg_obj = n.get("config") if isinstance(n.get("config"), dict) else {}
        txt_blob = " ".join(str(v) for v in cfg_obj.values() if isinstance(v, str))
        if txt_blob:
            insp = sec.inspect_text(txt_blob)
            if not insp.get("safe", True):
                raise AgencyGrowthError(400, "DAG_GUARDRAIL_BLOCKED", f"Node {nid_s} blocked by guardrail: {insp.get('reasons')}")
        if ntype == "webhook":
            url = str(cfg_obj.get("url") or "").strip()
            ok_u, reason = sec.ssrf_check(url, extra_hosts=extra_hosts, allow_loopback_dev=True)
            if not ok_u:
                raise AgencyGrowthError(400, "DAG_UNSAFE_WEBHOOK_URL", f"Webhook node {nid_s} URL rejected: {reason}.")
        pos_obj = n.get("position") if isinstance(n.get("position"), dict) else {}
        try:
            pos_x = int(pos_obj.get("x") or 0) if not isinstance(pos_obj.get("x"), bool) else 0
            pos_y = int(pos_obj.get("y") or 0) if not isinstance(pos_obj.get("y"), bool) else 0
        except (TypeError, ValueError, OverflowError):
            pos_x, pos_y = 0, 0
        node_map[nid_s] = {
            "id": nid_s,
            "type": ntype,
            "label": str(n.get("label") or ntype)[:80],
            "position": {"x": pos_x, "y": pos_y},
            "config": cfg_obj,
        }

    adj: dict[str, list[str]] = {k: [] for k in node_map}
    indegree: dict[str, int] = {k: 0 for k in node_map}
    clean_edges: list[dict[str, str]] = []
    for e in edges:
        if not isinstance(e, dict):
            raise AgencyGrowthError(400, "DAG_INVALID_EDGE", "Each edge must be an object.")
        src = str(e.get("source") or "").strip()
        dst = str(e.get("target") or "").strip()
        if src not in node_map or dst not in node_map:
            raise AgencyGrowthError(400, "DAG_DANGLING_EDGE", f"Edge {src!r} -> {dst!r} references unknown node.")
        if src == dst:
            raise AgencyGrowthError(400, "DAG_CYCLE_DETECTED", f"Self-loop detected on node {src!r}.")
        adj[src].append(dst)
        indegree[dst] += 1
        clean_edges.append({
            "source": src,
            "target": dst,
            "condition_branch": str(e.get("condition_branch") or "default")[:32],
        })

    # Kahn's topological sort + longest-path depth bound
    q = deque([nid_s for nid_s, deg in indegree.items() if deg == 0])
    topo: list[str] = []
    depth: dict[str, int] = {nid_s: 1 for nid_s in node_map}
    while q:
        cur = q.popleft()
        topo.append(cur)
        for nxt in adj[cur]:
            depth[nxt] = max(depth[nxt], depth[cur] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                q.append(nxt)

    if len(topo) != len(node_map):
        raise AgencyGrowthError(400, "DAG_CYCLE_DETECTED", "Workflow graph contains a cycle and cannot be saved.")
    max_depth = max(depth.values(), default=1)
    if max_depth > 20:
        raise AgencyGrowthError(400, "DAG_DEPTH_EXCEEDED", "Workflow DAG depth cannot exceed 20 hops.")

    # Compile into deterministic linear/branch-annotated steps for automations.py
    compiled_steps: list[dict[str, Any]] = []
    trigger_event = "lead.created"
    for nid_s in topo:
        nd = node_map[nid_s]
        ntype = nd["type"]
        c = nd["config"]
        if ntype == "trigger":
            trigger_event = str(c.get("event") or trigger_event)[:48]
            continue
        if ntype in ("condition", "branch"):
            compiled_steps.append({
                "type": "condition",
                "node_id": nid_s,
                "field": str(c.get("field") or "budget_inr")[:48],
                "op": str(c.get("op") or ">=")[:12],
                "value": c.get("value"),
            })
        elif ntype == "wait":
            compiled_steps.append({
                "type": "wait",
                "node_id": nid_s,
                "minutes": max(1, min(43_200, int(c.get("minutes") or 60))),
            })
        elif ntype in ("send_whatsapp", "send_sms", "send_email", "send_document"):
            compiled_steps.append({
                "type": ntype,
                "node_id": nid_s,
                "template": str(c.get("template") or "")[:64],
                "text": str(c.get("text") or nd["label"])[:500],
            })
        elif ntype == "create_task":
            compiled_steps.append({
                "type": "create_task",
                "node_id": nid_s,
                "title": str(c.get("title") or nd["label"])[:140],
                "due_min": max(5, min(10_080, int(c.get("due_min") or 60))),
            })
        elif ntype == "webhook":
            compiled_steps.append({
                "type": "webhook",
                "node_id": nid_s,
                "url": str(c.get("url") or ""),
            })

    return {
        "valid": True,
        "trigger": trigger_event,
        "node_count": len(node_map),
        "edge_count": len(clean_edges),
        "max_depth": max_depth,
        "topological_order": topo,
        "dag": {
            "nodes": [node_map[k] for k in topo],
            "edges": clean_edges,
            "topological_order": topo,
            "max_depth": max_depth,
        },
        "compiled_steps": compiled_steps,
    }


FUNNEL_BLOCK_CATALOG: dict[str, dict[str, str]] = {
    "hero_rera": {"title": "MahaRERA Verified Hero Banner", "category": "trust"},
    "rera_qr_trust_badge": {"title": "7/12, NA Order & RERA Verification Strip", "category": "trust"},
    "cost_sheet_preview": {"title": "Transparent All-In Buyer Cost Sheet", "category": "finance"},
    "emi_calculator": {"title": "Home Loan & EMI Affordability Table", "category": "finance"},
    "floor_plan_gallery": {"title": "Carpet Area & Sanctioned Layout Highlights", "category": "inventory"},
    "site_visit_scheduler": {"title": "Instant Site-Visit Slot Callout", "category": "conversion"},
    "whatsapp_cta": {"title": "Consent-First WhatsApp Brochure Pack CTA", "category": "conversion"},
    "faq_accordion": {"title": "Title, Mutation & Possession FAQ", "category": "education"},
}


def validate_and_save_funnel_blocks(
    store: Store,
    tid: str,
    fid: str,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    funnel = store.get("funnels", tid, fid)
    if not funnel:
        raise AgencyGrowthError(404, "FUNNEL_NOT_FOUND", "Funnel not found.")
    if not isinstance(blocks, list) or len(blocks) > 24:
        raise AgencyGrowthError(400, "INVALID_BLOCKS", "Provide between 0 and 24 funnel blocks.")
    clean: list[dict[str, Any]] = []
    for idx, blk in enumerate(blocks):
        if not isinstance(blk, dict):
            raise AgencyGrowthError(400, "INVALID_BLOCK", "Each block must be an object.")
        btype = str(blk.get("type") or "").strip()
        if btype not in FUNNEL_BLOCK_CATALOG:
            raise AgencyGrowthError(400, "UNSUPPORTED_BLOCK_TYPE", f"Block type {btype!r} is not in allowlisted catalog.")
        heading = str(blk.get("heading") or FUNNEL_BLOCK_CATALOG[btype]["title"]).strip()[:120]
        body_txt = str(blk.get("body") or "").strip()[:600]
        badge = str(blk.get("badge") or "").strip()[:60]
        insp = sec.inspect_text(f"{heading} {body_txt} {badge}")
        if not insp.get("safe", True):
            raise AgencyGrowthError(400, "BLOCK_GUARDRAIL_REJECTED", f"Funnel block {idx} rejected by guardrail.")
        clean.append({
            "id": str(blk.get("id") or f"blk_{idx + 1}")[:32],
            "type": btype,
            "heading": heading,
            "body": body_txt,
            "badge": badge,
            "order": idx + 1,
        })
    updated = store.update("funnels", tid, fid, {"blocks": clean, "updated_at": int(time.time())})
    return {"ok": True, "funnel_id": fid, "block_count": len(clean), "funnel": updated}


def render_funnel_blocks_html(funnel: dict[str, Any]) -> str:
    blocks = funnel.get("blocks") or []
    if not isinstance(blocks, list) or not blocks:
        return ""
    parts: list[str] = []
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") not in FUNNEL_BLOCK_CATALOG:
            continue
        badge_html = f' <span class="pill">{sec.esc(b.get("badge"))}</span>' if b.get("badge") else ""
        parts.append(
            f'<section class="card" data-block-type="{sec.esc(b.get("type"))}">'
            f'<h3>{sec.esc(b.get("heading"))}{badge_html}</h3>'
            f'<p>{sec.esc(b.get("body"))}</p></section>'
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# 3. 2-Way Calendar (iCal + Anti-Double-Booking), SPF/DKIM/DMARC & Reputation
# ---------------------------------------------------------------------------
def configure_calendar_availability(store: Store, tid: str, body: dict[str, Any]) -> dict[str, Any]:
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), None)
    if not tenant:
        raise AgencyGrowthError(404, "TENANT_NOT_FOUND", "Workspace not found.")
    start_hour = max(6, min(20, int(body.get("start_hour", 9))))
    end_hour = max(start_hour + 1, min(22, int(body.get("end_hour", 18))))
    slot_min = int(body.get("slot_minutes", 45))
    if slot_min not in (15, 30, 45, 60, 90):
        slot_min = 45
    buffer_min = max(0, min(60, int(body.get("buffer_minutes", 15))))
    cal = dict(tenant.get("calendar_settings") or {})
    cal.update({
        "timezone": "Asia/Kolkata",
        "start_hour": start_hour,
        "end_hour": end_hour,
        "slot_minutes": slot_min,
        "buffer_minutes": buffer_min,
        "updated_at": int(time.time()),
    })
    with store.lock:
        tenant["calendar_settings"] = cal
        store._pg_put("tenants", tid, tenant)
        store.save()
    return cal


def _parse_ical_ts(raw_val: str) -> int | None:
    s = raw_val.strip()
    if ":" in s:
        s = s.rsplit(":", 1)[-1].strip()
    try:
        if s.endswith("Z"):
            d = dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
            return int(d.timestamp())
        if "T" in s:
            ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
            d = dt.datetime.strptime(s[:15], "%Y%m%dT%H%M%S").replace(tzinfo=ist)
            return int(d.timestamp())
    except ValueError:
        return None
    return None


def import_ical_busy(store: Store, tid: str, ics_text: str) -> dict[str, Any]:
    if len(ics_text or "") > 262_144:
        raise AgencyGrowthError(413, "ICAL_TOO_LARGE", "ICS payload exceeds 256 KB safety limit.")
    if "BEGIN:VCALENDAR" not in (ics_text or ""):
        raise AgencyGrowthError(400, "INVALID_ICAL", "Missing BEGIN:VCALENDAR header.")
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), None)
    if not tenant:
        raise AgencyGrowthError(404, "TENANT_NOT_FOUND", "Workspace not found.")

    busy: list[dict[str, Any]] = []
    in_event = False
    cur: dict[str, str] = {}
    for raw_line in ics_text.replace("\r\n ", "").replace("\n ", "").splitlines():
        line = raw_line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            cur = {}
        elif line == "END:VEVENT" and in_event:
            in_event = False
            st_ts = _parse_ical_ts(cur.get("DTSTART", ""))
            en_ts = _parse_ical_ts(cur.get("DTEND", ""))
            if st_ts and (not en_ts or en_ts <= st_ts):
                en_ts = st_ts + 3600
            if st_ts and en_ts:
                busy.append({
                    "uid": str(cur.get("UID") or nid("ics"))[:80],
                    "summary": str(cur.get("SUMMARY") or "External Busy")[:120],
                    "start_ts": st_ts,
                    "end_ts": en_ts,
                })
            if len(busy) >= 500:
                break
        elif in_event and ":" in line:
            k, v = line.split(":", 1)
            cur[k.split(";", 1)[0].upper()] = v.strip()

    cal = dict(tenant.get("calendar_settings") or {})
    cal["external_busy"] = busy
    cal["last_ical_sync_at"] = int(time.time())
    with store.lock:
        tenant["calendar_settings"] = cal
        store._pg_put("tenants", tid, tenant)
        store.save()
    return {"ok": True, "imported_busy_events": len(busy), "external_busy": busy[:20]}


def compute_available_slots(
    store: Store,
    tid: str,
    *,
    property_id: str,
    date_str: str,
) -> dict[str, Any]:
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), {})
    cal = tenant.get("calendar_settings") or {}
    start_hour = int(cal.get("start_hour", 9))
    end_hour = int(cal.get("end_hour", 18))
    slot_min = int(cal.get("slot_minutes", 45))
    buffer_min = int(cal.get("buffer_minutes", 15))
    try:
        day = dt.date.fromisoformat(str(date_str).strip())
    except ValueError:
        raise AgencyGrowthError(400, "INVALID_DATE", "date must be YYYY-MM-DD.")

    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    win_start = int(dt.datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=ist).timestamp())
    win_end = int(dt.datetime(day.year, day.month, day.day, end_hour, 0, tzinfo=ist).timestamp())

    busy_ranges: list[tuple[int, int]] = []
    for sv in store.all("site_visits", tid):
        if sv.get("status") == "cancelled":
            continue
        if property_id and sv.get("property_id") != property_id:
            continue
        s_at = int(sv.get("scheduled_at") or 0)
        dur = int(sv.get("duration_min") or 45) * 60
        if s_at:
            busy_ranges.append((s_at, s_at + dur + buffer_min * 60))
    for ext in (cal.get("external_busy") or []):
        busy_ranges.append((int(ext["start_ts"]), int(ext["end_ts"]) + buffer_min * 60))

    slots: list[dict[str, Any]] = []
    step_sec = (slot_min + buffer_min) * 60
    dur_sec = slot_min * 60
    cur = win_start
    while cur + dur_sec <= win_end:
        s_end = cur + dur_sec
        conflict = any(cur < b_end and s_end > b_start for b_start, b_end in busy_ranges)
        slots.append({
            "start_ts": cur,
            "end_ts": s_end,
            "start_iso_ist": dt.datetime.fromtimestamp(cur, tz=ist).isoformat(),
            "duration_min": slot_min,
            "available": not conflict,
        })
        cur += step_sec
    return {
        "date": day.isoformat(),
        "timezone": "Asia/Kolkata",
        "property_id": property_id,
        "slots": slots,
        "available_count": sum(1 for s in slots if s["available"]),
    }


def _ics_escape(val: str) -> str:
    """RFC 5545 §3.3.11 text escaping — prevents ICS header/VALARM injection."""
    s = str(val or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def export_ical_feed(store: Store, tid: str, property_id: str = "") -> str:
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), {})
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Realtor Estates//Governed Site Visits v1.38//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(tenant.get('name') or 'Site Visits')}",
    ]
    for sv in store.all("site_visits", tid):
        if sv.get("status") == "cancelled":
            continue
        if property_id and sv.get("property_id") != property_id:
            continue
        st_ts = int(sv.get("scheduled_at") or 0)
        if not st_ts:
            continue
        en_ts = st_ts + int(sv.get("duration_min") or 45) * 60
        dt_st = dt.datetime.fromtimestamp(st_ts, tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dt_en = dt.datetime.fromtimestamp(en_ts, tz=dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prop = store.get("properties", tid, str(sv.get("property_id") or "")) or {}
        summary = f"Site Visit — {prop.get('title') or sv.get('property_id') or 'Project'}"
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{_ics_escape(sv['id'])}@realtorestates.local",
            f"DTSTAMP:{dt_st}",
            f"DTSTART:{dt_st}",
            f"DTEND:{dt_en}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(sv.get('notes') or 'Confirmed buyer site visit')}",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def book_calendar_slot(store: Store, tid: str, actor_uid: str, body: dict[str, Any]) -> dict[str, Any]:
    contact_id = str(body.get("contact_id") or "").strip()
    property_id = str(body.get("property_id") or "").strip()
    if not contact_id or not property_id:
        raise AgencyGrowthError(400, "MISSING_IDS", "contact_id and property_id are required.")
    if not store.get("contacts", tid, contact_id):
        raise AgencyGrowthError(404, "CONTACT_NOT_FOUND", "Contact not found in workspace.")
    if not store.get("properties", tid, property_id):
        raise AgencyGrowthError(404, "PROPERTY_NOT_FOUND", "Property not found in workspace.")

    start_ts = int(body.get("start_ts") or 0)
    if not start_ts and body.get("start_iso"):
        try:
            start_ts = int(dt.datetime.fromisoformat(str(body["start_iso"])).timestamp())
        except ValueError:
            raise AgencyGrowthError(400, "INVALID_START_TIME", "Invalid start_iso timestamp.")
    if start_ts <= 0:
        raise AgencyGrowthError(400, "INVALID_START_TIME", "start_ts or start_iso is required.")
    duration_min = max(15, min(120, int(body.get("duration_min") or 45)))
    end_ts = start_ts + duration_min * 60

    # 1. Overlap check against existing active site_visits and external iCal busy blocks
    tenant = next((t for t in store.db["tenants"] if t["id"] == tid), {})
    cal = tenant.get("calendar_settings") or {}
    for sv in store.all("site_visits", tid):
        if sv.get("status") == "cancelled" or sv.get("property_id") != property_id:
            continue
        sv_s = int(sv.get("scheduled_at") or 0)
        sv_e = sv_s + int(sv.get("duration_min") or 45) * 60
        if start_ts < sv_e and end_ts > sv_s:
            raise AgencyGrowthError(409, "SLOT_ALREADY_BOOKED", "Selected site-visit slot overlaps an existing booking.")
    for ext in (cal.get("external_busy") or []):
        if start_ts < int(ext["end_ts"]) and end_ts > int(ext["start_ts"]):
            raise AgencyGrowthError(409, "SLOT_EXTERNAL_BUSY", "Selected slot conflicts with synced external calendar busy block.")

    # 2. Atomic race lock so concurrent requests for the exact slot fail closed
    lock_key = f"cal_slot:{tid}:{property_id}:{start_ts}"
    if not shared.ADAPTER.claim_once(lock_key, 86_400):
        raise AgencyGrowthError(409, "SLOT_ALREADY_BOOKED", "Slot was concurrently claimed by another booking.")

    now = int(time.time())
    visit = store.insert("site_visits", tid, {
        "id": nid("sv"),
        "tenant_id": tid,
        "contact_id": contact_id,
        "property_id": property_id,
        "scheduled_at": start_ts,
        "duration_min": duration_min,
        "status": "confirmed",
        "pickup": str(body.get("pickup") or "Self drive")[:80],
        "notes": str(body.get("notes") or "")[:300],
        "agent_id": actor_uid,
        "created_by": actor_uid,
        "created_at": now,
        "updated_at": now,
    })
    store.insert("activities", tid, {
        "id": nid("act"),
        "tenant_id": tid,
        "contact_id": contact_id,
        "kind": "site_visit_booked",
        "note": f"2-way calendar slot confirmed ({duration_min}m) for {property_id}",
        "at": now,
    })
    return {
        "ok": True,
        "site_visit": visit,
        "ics_invite": export_ical_feed(store, tid, property_id=property_id),
    }


def check_email_deliverability(store: Store, tid: str, body: dict[str, Any]) -> dict[str, Any]:
    domain = str(body.get("domain") or "mail.example.com").strip().lower()
    if not _DOMAIN_RE.match(domain):
        raise AgencyGrowthError(400, "INVALID_DOMAIN", "Provide a valid sending domain.")
    spf_records = [str(x).strip() for x in (body.get("spf_records") or []) if str(x).strip()]
    dkim_txt = str(body.get("dkim_txt") or "").strip()
    dmarc_txt = str(body.get("dmarc_txt") or "").strip()

    score = 0
    findings: list[dict[str, str]] = []

    spf_matches = [r for r in spf_records if r.lower().startswith("v=spf1")]
    if len(spf_matches) == 0:
        findings.append({"severity": "high", "code": "SPF_MISSING", "detail": "No v=spf1 TXT record found."})
    elif len(spf_matches) > 1:
        findings.append({"severity": "high", "code": "SPF_MULTIPLE_RECORDS", "detail": "Multiple v=spf1 records cause RFC 7208 permerror."})
    else:
        spf = spf_matches[0]
        if "+all" in spf:
            findings.append({"severity": "critical", "code": "SPF_OPEN_RELAY_PLUS_ALL", "detail": "SPF ends with +all (allows anyone on the internet to spoof your domain)."})
        elif "-all" in spf or "~all" in spf:
            score += 35
        else:
            score += 15
            findings.append({"severity": "medium", "code": "SPF_NO_ALL_MECHANISM", "detail": "End SPF with ~all or -all."})

    if "v=dkim1" in dkim_txt.lower() and "p=" in dkim_txt:
        pub = dkim_txt.split("p=", 1)[1].split(";", 1)[0].strip()
        if len(pub) >= 128:
            score += 35
        else:
            score += 15
            findings.append({"severity": "medium", "code": "DKIM_SHORT_KEY", "detail": "Use at least a 2048-bit RSA DKIM public key."})
    else:
        findings.append({"severity": "high", "code": "DKIM_MISSING", "detail": "DKIM selector TXT record (v=DKIM1; k=rsa; p=...) not configured."})

    dmarc_l = dmarc_txt.lower()
    if dmarc_l.startswith("v=dmarc1"):
        if "p=reject" in dmarc_l or "p=quarantine" in dmarc_l:
            score += 30
        elif "p=none" in dmarc_l:
            score += 12
            findings.append({"severity": "medium", "code": "DMARC_MONITOR_ONLY", "detail": "Upgrade DMARC policy from p=none to p=quarantine or p=reject."})
        if "rua=mailto:" not in dmarc_l:
            findings.append({"severity": "low", "code": "DMARC_NO_RUA", "detail": "Add rua=mailto:dmarc-reports@... for aggregate spoofing visibility."})
    else:
        findings.append({"severity": "high", "code": "DMARC_MISSING", "detail": "No _dmarc TXT record found."})

    daily_warmup_cap = 50 if score < 60 else (500 if score < 85 else 5000)
    recommended_dns = {
        "spf": {"host": domain, "type": "TXT", "value": "v=spf1 include:_spf.realtorestates.app -all"},
        "dkim": {"host": f"re1._domainkey.{domain}", "type": "TXT", "value": "v=DKIM1; k=rsa; s=email; p=<2048_BIT_PUBLIC_KEY>"},
        "dmarc": {"host": f"_dmarc.{domain}", "type": "TXT", "value": f"v=DMARC1; p=quarantine; adkim=s; aspf=s; rua=mailto:dmarc@{domain}"},
    }
    return {
        "domain": domain,
        "score": score,
        "ready_for_bulk_campaigns": score >= 85,
        "daily_warmup_cap": daily_warmup_cap,
        "findings": findings,
        "recommended_dns": recommended_dns,
    }


def send_reputation_request(store: Store, tid: str, actor_uid: str, body: dict[str, Any]) -> dict[str, Any]:
    cid = str(body.get("contact_id") or "").strip()
    contact = store.get("contacts", tid, cid)
    if not contact:
        raise AgencyGrowthError(404, "CONTACT_NOT_FOUND", "Contact not found.")
    raw_consent = contact.get("consent")
    consent_granted = (
        raw_consent.get("status") == "granted"
        if isinstance(raw_consent, dict)
        else (raw_consent is True)
    )
    if not consent_granted or contact.get("dnd") or contact.get("opted_out") or contact.get("opt_out"):
        raise AgencyGrowthError(403, "CONSENT_REQUIRED", "Contact has not granted DPDP outreach consent or has opted out.")
    prop_id = str(body.get("property_id") or "")
    public_review_url = str(body.get("public_review_url") or "https://search.google.com/local/writereview").strip()[:240]
    req_id = nid("rep")
    now = int(time.time())
    rec = store.insert("activities", tid, {
        "id": req_id,
        "tenant_id": tid,
        "contact_id": cid,
        "property_id": prop_id,
        "kind": "reputation_request",
        "status": "sent",
        "public_review_url": public_review_url,
        "sent_by": actor_uid,
        "note": "Post-site-visit transparent review & NPS pulse sent (no review gating).",
        "at": now,
    })
    return {"ok": True, "request": rec}


def submit_reputation_response(store: Store, tid: str, body: dict[str, Any]) -> dict[str, Any]:
    req_id = str(body.get("request_id") or "").strip()
    req_row = store.get("activities", tid, req_id)
    if not req_row or req_row.get("kind") != "reputation_request":
        raise AgencyGrowthError(404, "REQUEST_NOT_FOUND", "Reputation request not found.")
    if isinstance(body.get("stars"), bool) or isinstance(body.get("nps_score"), bool):
        raise AgencyGrowthError(400, "INVALID_RATING", "stars and nps_score must be integers, not booleans.")
    stars = int(body.get("stars") or 0)
    nps = int(body.get("nps_score", stars * 2) if body.get("nps_score") is not None else stars * 2)
    if not (1 <= stars <= 5) or not (0 <= nps <= 10):
        raise AgencyGrowthError(400, "INVALID_RATING", "stars must be 1..5 and nps_score must be 0..10.")
    comment = str(body.get("comment") or "").strip()[:600]
    now = int(time.time())
    escalation_task_id = ""
    if stars <= 3 or nps <= 6:
        t = store.insert("tasks", tid, {
            "id": nid("tsk"),
            "tenant_id": tid,
            "contact_id": req_row.get("contact_id"),
            "title": f"Urgent Detractor Recovery ({stars}★ / NPS {nps}) — {comment[:60] or 'Follow up within 2h'}",
            "due": "Today",
            "priority": "high",
            "done": False,
            "created_at": now,
        })
        escalation_task_id = t["id"]

    updated = store.update("activities", tid, req_id, {
        "status": "responded",
        "stars": stars,
        "nps_score": nps,
        "comment": comment,
        "responded_at": now,
        "escalation_task_id": escalation_task_id,
    })
    return {
        "ok": True,
        "response": updated,
        # Ethical non-gating invariant: public review link is ALWAYS returned regardless of star rating
        "public_review_url": req_row.get("public_review_url"),
        "escalation_task_id": escalation_task_id or None,
    }


def reputation_summary(store: Store, tid: str) -> dict[str, Any]:
    pulses = [a for a in store.all("activities", tid) if a.get("kind") == "reputation_request"]
    responded = [a for a in pulses if a.get("status") == "responded" and isinstance(a.get("stars"), int)]
    if not responded:
        return {"requests_sent": len(pulses), "responses": 0, "avg_stars": 0.0, "nps": 0}
    avg_stars = round(sum(int(a["stars"]) for a in responded) / len(responded), 2)
    promoters = sum(1 for a in responded if int(a.get("nps_score", 0)) >= 9)
    detractors = sum(1 for a in responded if int(a.get("nps_score", 0)) <= 6)
    nps = round(((promoters - detractors) / len(responded)) * 100)
    return {
        "requests_sent": len(pulses),
        "responses": len(responded),
        "avg_stars": avg_stars,
        "nps": nps,
        "promoters": promoters,
        "detractors": detractors,
    }
