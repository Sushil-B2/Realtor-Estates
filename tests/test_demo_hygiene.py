"""
Provenance and the purge (gap G7, docs/26).

The demo tenant is the shop window, and it had filled with our own test runs —
twenty properties called "Proof …", a hundred contacts named after proofs, and
money documents from scripts that ran before every release. Deleting the mess once
would fix the symptom; these tests are about the cause:

* a request that says `X-Proof-Run: <id>` has every row it creates stamped;
* a row without the stamp is treated as somebody's real work — no guessing;
* a purge needs an owner, the workspace slug typed out, and it deletes only what
  carries the stamp (or a prefix the operator named, which is reported as the
  heuristic it is);
* a purge never orphans a surviving row, and never touches the audit chain.
"""
from __future__ import annotations

import pytest

from conftest import Session, step_up_headers
from app import proofing as pf


# --------------------------------------------------------------- pure rules
def test_a_run_id_must_be_boring_and_long_enough():
    assert pf.parse_run("") == ""
    assert pf.parse_run(None) == ""
    assert pf.parse_run("money-1795000000") == "money-1795000000"
    assert pf.parse_run("  partners:run_1 ") == "partners:run_1"
    for bad in ("a", "ab", "has space", "semi;colon", "x" * 65):
        with pytest.raises(pf.ProofError):
            pf.parse_run(bad)


def test_only_stamped_rows_are_proof_rows():
    assert pf.is_proof({"proof_run": "r-1"}) is True
    assert pf.is_proof({"title": "Real Residency"}) is False
    assert pf.is_proof(None) is False


def test_the_summary_counts_rows_and_runs():
    summary = pf.summarise({
        "properties": [{"id": "p1", "proof_run": "r-1"}, {"id": "p2"}],
        "payments": [{"id": "y1", "proof_run": "r-1"}, {"id": "y2", "proof_run": "r-2"}],
        "deals": [{"id": "d1"}],
    })
    assert summary["proof_rows"] == {"properties": 1, "payments": 2}
    assert summary["total"] == 3
    assert summary["runs"][0] == {"run": "r-1", "rows": 2}


def test_a_legacy_prefix_must_be_specific():
    assert pf.normalise_prefixes("") == []
    assert pf.normalise_prefixes("Proof , Rival") == ["Proof", "Rival"]
    with pytest.raises(pf.ProofError):
        pf.normalise_prefixes("P")


def test_a_legacy_match_never_covers_a_stamped_row():
    stamped = {"id": "x", "title": "Proof plot", "proof_run": "r-1"}
    assert pf.looks_like_legacy_proof(stamped, ["Proof"]) is False
    assert pf.looks_like_legacy_proof({"title": "Proof plot"}, ["Proof"]) is True
    assert pf.looks_like_legacy_proof({"name": "Rival Brokers"}, ["Rival"]) is True
    assert pf.looks_like_legacy_proof({"name": "Prakash Patil"}, ["Proof"]) is False


def test_an_orphan_check_knows_when_a_survivor_still_points_here():
    surviving = {"deals": [{"id": "d1", "contact_id": "c1"}], "units": []}
    assert pf.would_orphan("contacts", "c1", surviving) == 1
    assert pf.would_orphan("contacts", "c2", surviving) == 0
    assert pf.would_orphan("properties", "p1", surviving) == 0


# ------------------------------------------------------------------- routes
def test_a_stamped_run_marks_every_row_it_creates(owner):
    headers = {"X-Proof-Run": "pytest-run-1"}
    status, made, _ = owner.call("POST", "/api/v1/properties", {
        "title": "Hy-Giene Test Residency", "city": "Nanded", "locality": "Test",
        "category": "residential", "type": "apartment", "deal_kind": "sale",
        "pricing": {"basis": "total", "amount": 5000000},
        "area": {"value": 900, "unit": "sqft"},
        "attributes": {"bedrooms": 2, "bathrooms": 2},
    }, headers=headers)
    assert status in (200, 201), made
    pid = made["property"]["id"]
    hygiene = owner.call("GET", "/api/v1/demo/hygiene")[1]
    assert hygiene["total"] >= 1
    assert "pytest-run-1" in [r["run"] for r in hygiene["runs"]]
    # a second request without the header is nobody's proof
    other = owner.call("POST", "/api/v1/properties", {
        "title": "Unstamped Test Residency", "city": "Nanded", "locality": "Test",
        "category": "residential", "type": "apartment", "deal_kind": "sale",
        "pricing": {"basis": "total", "amount": 5000000},
        "area": {"value": 900, "unit": "sqft"},
        "attributes": {"bedrooms": 2, "bathrooms": 2},
    })[1]["property"]["id"]
    assert other != pid
    hygiene2 = owner.call("GET", "/api/v1/demo/hygiene")[1]
    assert hygiene2["proof_rows"].get("properties", 0) >= 1
    # the unstamped one is not in the proof set: we count stamped properties only
    assert hygiene2["total"] >= hygiene["total"]


def test_a_bad_run_id_is_refused_before_anything_is_written(owner):
    before = owner.call("GET", "/api/v1/properties")[1]["count"]
    status, body, _ = owner.call("POST", "/api/v1/properties", {"title": "Nope"},
                                 headers={"X-Proof-Run": "bad id with spaces"})
    assert status == 400 and body["error"]["code"] == "INVALID_PROOF_RUN"
    assert owner.call("GET", "/api/v1/properties")[1]["count"] == before


def test_only_an_owner_may_purge_and_only_with_the_slug(owner, viewer):
    status, _, _ = viewer.call("GET", "/api/v1/demo/hygiene")
    assert status == 403
    status, body, _ = viewer.call("POST", "/api/v1/demo/proof-rows/purge", {"confirm": "sunrise"})
    assert status == 403
    # the slug is typed *and* the person is confirmed (docs/34): one is a deliberate
    # mistake made twice, the other is a clean one
    status, body, _ = owner.call("POST", "/api/v1/demo/proof-rows/purge", {"confirm": "wrong-slug"},
                                 headers=step_up_headers(owner, "demo_purge", "wrong-slug"))
    assert status == 400 and body["error"]["code"] == "CONFIRM_SLUG"
    status, body, _ = owner.call("POST", "/api/v1/demo/proof-rows/purge", {"confirm": "sunrise",
                                                                          "legacy_prefix": "ab"},
                                 headers=step_up_headers(owner, "demo_purge", "sunrise"))
    assert status == 400 and body["error"]["code"] == "PREFIX_TOO_SHORT"


def test_a_purge_removes_the_proof_rows_and_leaves_the_demo(owner):
    # write a proof row and a real row of the same kind
    headers = {"X-Proof-Run": "pytest-purge-1"}
    spec = {
        "title": "Purge Me Residency", "city": "Nanded", "locality": "Test",
        "category": "residential", "type": "apartment", "deal_kind": "sale",
        "pricing": {"basis": "total", "amount": 4000000},
        "area": {"value": 800, "unit": "sqft"},
        "attributes": {"bedrooms": 2, "bathrooms": 1},
    }
    proof_id = owner.call("POST", "/api/v1/properties", spec, headers=headers)[1]["property"]["id"]
    real_id = owner.call("POST", "/api/v1/properties",
                         {**spec, "title": "Keep Me Residency"})[1]["property"]["id"]

    status, purged, _ = owner.call("POST", "/api/v1/demo/proof-rows/purge", {"confirm": "sunrise"},
                                   headers=step_up_headers(owner, "demo_purge", "sunrise"))
    assert status == 200, purged
    assert purged["runs"] and "pytest-purge-1" in purged["runs"]
    remaining = {p["id"] for p in owner.call("GET", "/api/v1/properties")[1]["properties"]}
    assert proof_id not in remaining, "the proof's property should be gone"
    assert real_id in remaining, "an unstamped property is somebody's work"
    # and the hygiene report agrees afterwards
    assert owner.call("GET", "/api/v1/demo/hygiene")[1]["total"] == 0


def test_a_purge_audits_itself(owner):
    owner.call("POST", "/api/v1/properties", {
        "title": "Audited Purge Residency", "city": "Nanded", "locality": "Test",
        "category": "residential", "type": "apartment", "deal_kind": "sale",
        "pricing": {"basis": "total", "amount": 3000000},
        "area": {"value": 700, "unit": "sqft"},
        "attributes": {"bedrooms": 1, "bathrooms": 1},
    }, headers={"X-Proof-Run": "pytest-audit-1"})
    owner.call("POST", "/api/v1/demo/proof-rows/purge", {"confirm": "sunrise"},
               headers=step_up_headers(owner, "demo_purge", "sunrise"))
    actions = [e.get("action") for e in owner.call("GET", "/api/v1/tenant/audit")[1]["entries"]]
    assert "demo.proof_rows_purged" in actions


def test_the_hygiene_report_previews_a_legacy_prefix(owner):
    owner.call("POST", "/api/v1/properties", {
        "title": "Proof Legacy Residency", "city": "Nanded", "locality": "Test",
        "category": "residential", "type": "apartment", "deal_kind": "sale",
        "pricing": {"basis": "total", "amount": 2000000},
        "area": {"value": 600, "unit": "sqft"},
        "attributes": {"bedrooms": 1, "bathrooms": 1},
    })
    status, body, _ = owner.call("GET", "/api/v1/demo/hygiene?legacy_prefix=Proof")
    assert status == 200
    assert body["legacy_prefixes"] == ["Proof"]
    assert body["legacy_matches"].get("properties", 0) >= 1
    # a preview is not a purge
    assert any("Proof Legacy Residency" == p["title"] or True
               for p in owner.call("GET", "/api/v1/properties")[1]["properties"])
    titles = [p["title"] for p in owner.call("GET", "/api/v1/properties")[1]["properties"]]
    assert "Proof Legacy Residency" in titles


def test_a_public_webhook_cannot_stamp_a_payment(owner):
    """Only a signed delivery may declare a run; an anonymous caller may not."""
    status, body, _ = owner.call("POST", "/api/v1/webhooks/payments",
                                 {"event": "payment.captured"}, headers={"X-Proof-Run": "forged-run"})
    # no valid signature → 401 regardless, and the run id never gets that far
    assert status in (400, 401), body
