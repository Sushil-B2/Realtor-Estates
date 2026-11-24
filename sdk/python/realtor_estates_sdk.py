"""
Realtor Estates — Official Zero-Dependency Python SDK (`v1.37.0`).

Typed resource clients covering `contacts`, `properties`, `deals`, `funnels`,
`govt`, `mcp`, and `oauth` (with RFC 7636 PKCE `S256` helpers).
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class RealtorEstatesApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, payload: dict[str, Any] | None = None):
        super().__init__(f"HTTP {status} [{code}]: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.payload = payload or {}


def generate_pkce_pair() -> tuple[str, str]:
    """Return `(code_verifier, code_challenge_s256)` for OAuth 2.1 PKCE."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@dataclass
class _Domain:
    client: "RealtorEstatesClient"


class ContactsDomain(_Domain):
    def list(self, *, stage: str = "") -> dict[str, Any]:
        qs = f"?stage={urllib.parse.quote(stage)}" if stage else ""
        return self.client.request("GET", f"/api/v1/contacts{qs}")

    def create(
        self,
        *,
        name: str,
        phone: str,
        city: str = "",
        requirement: str = "",
        consent_granted: bool = True,
        consent: bool | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        c_payload = (
            consent
            if consent is not None
            else {"status": "granted" if consent_granted else "pending", "purpose": "sdk_capture", "channel": "api"}
        )
        return self.client.request("POST", "/api/v1/contacts", {
            "name": name,
            "phone": phone,
            "city": city,
            "requirement": requirement,
            "consent": c_payload,
        })


class PropertiesDomain(_Domain):
    def list(self) -> dict[str, Any]:
        return self.client.request("GET", "/api/v1/properties")

    def trust_pack(self, property_id: str) -> dict[str, Any]:
        return self.client.request("GET", f"/api/v1/properties/{urllib.parse.quote(property_id)}/marketing-trust-pack")


class DealsDomain(_Domain):
    def list(self) -> dict[str, Any]:
        return self.client.request("GET", "/api/v1/deals")


class FunnelsDomain(_Domain):
    def list(self) -> dict[str, Any]:
        return self.client.request("GET", "/api/v1/funnels")


class GovtDomain(_Domain):
    def cost_sheet(
        self,
        *,
        agreement_value_inr: int,
        asr_ready_reckoner_inr: int = 0,
        jurisdiction: str = "maharashtra_tier2_corp",
        property_category: str = "residential_plot_na",
        buyer_gender: str = "general",
    ) -> dict[str, Any]:
        return self.client.request("POST", "/api/v1/govt/cost-sheet", {
            "agreement_value_inr": agreement_value_inr,
            "asr_ready_reckoner_inr": asr_ready_reckoner_inr,
            "jurisdiction": jurisdiction,
            "property_category": property_category,
            "buyer_gender": buyer_gender,
        })


class McpDomain(_Domain):
    def doctor(self) -> dict[str, Any]:
        return self.client.request("GET", "/api/v1/mcp/doctor")

    def list_tools(self, *, profile: str = "curated") -> list[dict[str, Any]]:
        res = self.client.request("POST", f"/api/v1/mcp?profile={urllib.parse.quote(profile)}", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"profile": profile},
        })
        return (res.get("result") or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, profile: str = "curated") -> dict[str, Any]:
        res = self.client.request("POST", f"/api/v1/mcp?profile={urllib.parse.quote(profile)}", {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}, "profile": profile},
        })
        return res.get("result") or res


class RealtorEstatesClient:
    """Type-safe synchronous client for Realtor Estates `/api/v1` and `/api/v1/mcp`."""

    def __init__(self, base_url: str, access_token: str = "", *, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout
        self.contacts = ContactsDomain(self)
        self.properties = PropertiesDomain(self)
        self.deals = DealsDomain(self)
        self.funnels = FunnelsDomain(self)
        self.govt = GovtDomain(self)
        self.mcp = McpDomain(self)

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw_err = exc.read().decode("utf-8", "replace")
            parsed = json.loads(raw_err) if raw_err.strip().startswith("{") else {}
            err = parsed.get("error") or {}
            raise RealtorEstatesApiError(
                exc.code,
                str(err.get("code") or "HTTP_ERROR"),
                str(err.get("message") or raw_err[:200]),
                parsed,
            )
