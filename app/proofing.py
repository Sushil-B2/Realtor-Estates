"""
Provenance for rows a proof wrote (gap G7).

The demo tenant is the product's shop window, and it had become a museum of our
own test runs: twenty properties titled "Proof …", a hundred contacts named after
proof runs, twenty-two invoices from money proofs, and units booked on real demo
stock by a script that runs before every release. A prospect opening the desk saw
the proof, not the business.

Deleting the mess once would fix the symptom and leave the cause, so the cause is
what is addressed here: **a proof says it is a proof, at the moment it writes.**
A request carrying `X-Proof-Run: <id>` has every row it creates stamped with that
run id; the desk can then show, count or purge them, and a row without the stamp
is presumed to be somebody's real work.

The rules that keep this honest:

1. **The stamp is provenance, not permission.** It grants nothing — a row marked
   as proof is treated exactly like any other row by every read path. Otherwise a
   client could tag its way into something.
2. **A purge deletes only stamped rows, and only for one tenant.** There is no
   "delete everything" mode, because the useful operation is "put the demo back",
   not "lose the demo".
3. **Unstamped legacy rows are never guessed at.** The proof scripts that wrote
   the existing mess predate the stamp; a purge removes them only when an operator
   names the prefix explicitly (`"Proof "`) and accepts that it is a heuristic.
4. **Money rows are not special-cased.** A proof invoice is a row like any other,
   stamped and disposable; a real invoice is not, and cannot be, because nothing
   about this module can invent a stamp on a row that lacks one.

Pure: the routes do storage, audit and scoping.
"""
from __future__ import annotations

import re
from typing import Iterable

HEADER = "x-proof-run"
# Short, boring, sortable — a run id is a label, not a secret.
RUN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}$")

# Collections a purge may touch. Ordered child-first so a demo reset does not leave
# an invoice pointing at a deleted deal, or a unit pointing at a deleted property.
PURGEABLE = (
    # a session row a proof created is noise; it is also the row a *login* creates,
    # so it is stamped like any other (docs/28)
    "sessions",
    # a proof's credit on an *open* month is noise and goes; the same row on a month
    # that has been closed is part of a frozen invoice and is skipped by the route,
    # which is why the closed periods are read before anything is deleted (docs/29)
    "usage_ledger",
    "payments", "invoices",
    "cp_payouts", "cp_registrations",
    "site_visits", "deals", "units", "properties",
    "automations", "runs",
    "contacts", "users",
)

# Collections that only ever hold ingested or generated records; a proof's rows in
# these are noise but not a demo's stock, so they are reported and not purged.
SIDE_EFFECTS = ("webhooks", "deliveries", "activities", "tasks", "conversations")

# Never purged, whatever the stamp says: the audit chain is evidence, and the
# tenant row is the workspace itself.
NEVER_PURGE = (
    "audit", "tenants", "users_self",
    # a snapshot is an invoice. There is no proof stamp that makes a bill disposable.
    "usage_snapshots",
)

# Collections a purge may stamp but must never delete wholesale, whatever the row
# says: the route reads them and decides. Kept explicit so a future collection is
# not silently treated as disposable.
FROZEN_BY_SNAPSHOT = ("usage_ledger",)


class ProofError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_run(value: str | None) -> str:
    """Validate a run id from a request header, or return `""` for "not a proof"."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if not RUN_PATTERN.match(raw):
        raise ProofError(
            "INVALID_PROOF_RUN",
            "X-Proof-Run must be 3–64 characters of letters, digits, dot, colon, "
            "underscore or hyphen — it is stored on every row the request creates.",
        )
    return raw


def is_proof(row: dict | None) -> bool:
    return bool((row or {}).get("proof_run"))


def stamp(rows: Iterable[dict], run: str) -> int:
    """Count how many rows a run would stamp. Pure bookkeeping for the summary."""
    return len([r for r in rows if not is_proof(r)]) if run else 0


def summarise(by_collection: dict[str, list[dict]]) -> dict:
    """What a purge would touch, without touching it.

    `runs` is the interesting number: three runs leaving rows behind is a script
    that cleans up after itself badly, thirty is a demo nobody reseeds.
    """
    proof_rows = {}
    runs: dict[str, int] = {}
    for coll, rows in by_collection.items():
        marked = [r for r in rows if is_proof(r)]
        if not marked:
            continue
        proof_rows[coll] = len(marked)
        for row in marked:
            runs[row["proof_run"]] = runs.get(row["proof_run"], 0) + 1
    return {
        "proof_rows": proof_rows,
        "total": sum(proof_rows.values()),
        "runs": [{"run": k, "rows": v} for k, v in sorted(runs.items(), key=lambda kv: -kv[1])],
    }


# Who points at whom. A purge deletes children first, and then refuses to delete a
# parent that a *surviving* row still points at — because "put the demo back" must
# not mean "leave the demo with a deal whose buyer no longer exists".
REFERENCES = {
    "properties": (("units", "property_id"), ("site_visits", "property_id")),
    "contacts": (("deals", "contact_id"), ("site_visits", "contact_id"),
                 ("cp_registrations", "contact_id")),
    "users": (("deals", "owner_id"), ("cp_registrations", "cp_user_id"),
              ("cp_payouts", "cp_user_id")),
    "deals": (("payments", "deal_id"), ("invoices", "deal_id"), ("cp_payouts", "deal_id")),
    "units": (("site_visits", "unit_id"), ("payments", "unit_id"), ("invoices", "unit_id")),
}


# A child that cannot exist without its parent goes when the parent goes. This is
# the narrow, safe direction: deleting a proof partner takes their claims and
# payouts with them, because a claim without a claimant is not a demo, it is a
# dangling row. It never cascades *up* — a contact whose deals are real survives.
CASCADE = {
    "properties": (("units", "property_id"),),
    "users": (("cp_registrations", "cp_user_id"), ("cp_payouts", "cp_user_id")),
    "contacts": (("cp_registrations", "contact_id"),),
}


def would_orphan(collection: str, row_id: str, surviving: dict[str, list[dict]]) -> int:
    """How many rows that are staying still point at this one."""
    refs = 0
    for other, key in REFERENCES.get(collection, ()):
        refs += len([r for r in (surviving.get(other) or []) if r.get(key) == row_id])
    return refs


def normalise_prefixes(value: str | None) -> list[str]:
    """One or more comma-separated prefixes; each has to be specific.

    "P" would match a real buyer called Prakash. A legacy purge has to be specific
    enough that nobody has to trust the operator's memory.
    """
    raw = (value or "").strip()
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        prefix = part.strip()
        if not prefix:
            continue
        if len(prefix) < 4:
            raise ProofError("PREFIX_TOO_SHORT",
                             f"'{prefix}' is too short — a legacy prefix must be at least 4 characters.")
        out.append(prefix)
    return out


def looks_like_legacy_proof(row: dict, prefixes: list[str] | str) -> bool:
    """A row from before the stamp, matching a prefix the operator named."""
    if isinstance(prefixes, str):
        prefixes = [prefixes] if prefixes else []
    if not prefixes or is_proof(row):
        return False
    haystack = " ".join(str(row.get(k) or "") for k in
                        ("title", "name", "email", "unit_no", "cp_firm")).lower()
    return any(haystack.startswith(p.lower()) or f" {p.lower()}" in haystack for p in prefixes)


# Money rows carry no name, so a prefix cannot find them. A proof's invoice is
# stamped now; the ones written before that are only separable by time, which is a
# blunter instrument — so it is offered for these two collections only, and the
# caller has to say the date out loud.
DATED_COLLECTIONS = ("payments", "invoices")


def looks_like_dated_proof(row: dict, since: int) -> bool:
    if not since or is_proof(row):
        return False
    try:
        return int(row.get("created_at") or 0) >= int(since)
    except (TypeError, ValueError):
        return False
