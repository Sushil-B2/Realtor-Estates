# 26 — Demo hygiene: provenance, and a purge that cannot guess

The demo tenant is the product's shop window. By v1.14 it had become a museum of
our own test runs: 44 properties of which 20 were titled "Proof …", 144 contacts
named after proof runs, 28 payments and 26 invoices from money proofs, eight
"Rival Brokers" partner logins left by repeated partner proofs, and units booked
on real demo stock by a script that runs before every release.

Deleting the mess once would have fixed the symptom and left the cause. **The
cause is that a proof's rows were indistinguishable from a client's rows**, so
every future proof would rebuild the same mess.

## 1. A proof says it is a proof, at the moment it writes

A request carrying `X-Proof-Run: <id>` has every row it creates stamped with
`proof_run = <id>`. It is one header, honoured at the single choke point every
route passes through (`api.handle`), and the store records what it created during
that request on a thread-local list, so no route has to know that proofing exists.
A route added next year is covered without being touched.

```
POST /api/v1/deals  X-Proof-Run: money-1795000000
        │
        ├─ store.begin_capture()            ← per request, per thread
        ├─ route inserts deals, units, …
        └─ on return: every captured row gets proof_run = "money-1795000000"
```

**Who may declare a run:** an authenticated caller, or a *signed* provider
delivery. Payments are written by the webhook route, which is public by design —
but it verifies an HMAC before it writes anything, so only someone holding the
webhook secret can stamp a payment as disposable. An anonymous caller on any other
public route cannot, because "stamp a real payment as a proof's" would be a
deletion primitive.

The header is validated (`3–64` characters of letters, digits, `.`, `:`, `_`, `-`)
and a bad value is a `400 INVALID_PROOF_RUN` **before** anything is written.

## 2. Four rules that keep a purge honest

1. **The stamp is provenance, not permission.** A stamped row is read exactly like
   any other; nothing in the app branches on `proof_run` except the hygiene report
   and the purge. A tag that granted something would be a way in.
2. **A purge deletes only stamped rows, and only for one tenant.** There is no
   "delete everything" mode, because the useful operation is *put the demo back*,
   not *lose the demo*.
3. **Unstamped rows are never guessed at** — except when an operator names a
   legacy prefix of at least four characters (`"Proof "`, `"Rival"`), and the API
   returns the match counts *before* the purge runs. `"P"` is refused because it
   would match a buyer called Prakash.
4. **Money rows need a blunter instrument, and it is labelled as one.** An invoice
   carries no name, so a legacy money purge matches on `created_at >= <epoch>`,
   limited to `payments` and `invoices`, and requires the operator to say the date
   out loud.

## 3. A purge never orphans the demo

Deleting a proof's property while a real deal still points at it does not clean the
demo, it breaks it. So the purge **decides first, deletes second**:

* the whole delete set is computed, then
* children whose parent is going are cascaded down — a claim without a claimant, a
  unit without its property — and
* any remaining row that a *surviving* row still points at is **skipped** and
  reported.

`app/proofing.py::REFERENCES` is the map of who points at whom; `would_orphan()`
answers the question for one row. A purge returns `skipped: {"users": 7}` and that
is a feature: it says the demo kept something because something real needed it.

The audit chain and the tenant row are never purged (`NEVER_PURGE`); the purge
itself is an audit entry (`demo.proof_rows_purged`), with the counts and run ids.

## 4. Routes and the tool

| route | who | what |
|---|---|---|
| `GET /api/v1/demo/hygiene` | `manage_tenant` | rows per collection, proof rows by collection and run, optional `legacy_prefix` / `legacy_since` previews |
| `POST /api/v1/demo/proof-rows/purge` | owner only | body `{confirm: <slug>, legacy_prefix?, legacy_since?}`; deletes stamped rows, cascades downward, skips orphans, audits |

```
python3 scripts/reset_demo.py                                  # report only
python3 scripts/reset_demo.py --purge                          # delete proof rows
python3 scripts/reset_demo.py --purge --legacy-prefix "Proof ,Rival"
python3 scripts/reset_demo.py --purge --legacy-since 1794000000
```

`--purge` is not the default. A demo can be reseeded; a prospect's first
impression cannot. The script prints what will go before it goes, and warns, in
words, when a heuristic is in play.

## 5. What the first real cleanup looked like

```
legacy prefix "Proof ,Rival" would match      contacts 7 · users 7
money rows at or after 1794000000             payments 28 · invoices 26
removed 82 row(s)   legacy: payments 28 · invoices 26 · cp_registrations 14 · contacts 7 · users 7
```

Before: 44 properties (20 called "Proof …"), 144 contacts, 28 payments, 26
invoices, nine partner logins. After: **24 real properties, 100 real contacts, one
partner login, and a money board that is honestly empty** — because no money has
moved in the demo since the seeded receipt, and an operator can now demonstrate the
flow live (`POST /deals/{id}/invoice`, or a signed token payment) instead of
showing twenty-two invoices a script wrote last Tuesday.

The purge also reported 24 tasks skipped: they are in `SIDE_EFFECTS`, which a purge
reports and does not touch.

## 6. Proof and tests

`tests/test_demo_hygiene.py` — 13 tests:

* the pure rules: run-id validation, `is_proof`, the summary's run counts, prefix
  specificity, a stamped row never being "legacy", the orphan check;
* the wire: a stamped request stamps its rows and an unstamped one does not; a bad
  run id is refused **before** anything is written; a viewer cannot read or purge;
  the slug must match; a short prefix is refused; a purge removes the proof's
  property and keeps the unstamped one; the purge audits itself; the hygiene report
  previews a prefix without deleting; an unsigned webhook cannot declare a run.

The three proof scripts now send the header (`money-…`, `partners-…`, `images-…`),
so everything they write is disposable by construction. Verified live: a money
proof run stamped 8 rows (4 invoices, 3 payments, 1 unit), the purge removed
exactly those 8, and the demo's own rows were untouched.

## 7. Deliberately not built

* **No proof-mode tenant.** Running proofs against a throwaway tenant is the other
  way to solve this, and it is cleaner — but it needs cross-tenant plumbing for
  every proof's login, and the tag buys most of the value for a fraction of it.
* **No automatic cleanup after every run.** A proof that deletes its own rows
  cannot be inspected after it fails, which is exactly when you want to look.
* **No telemetry on `proof_run` in production.** The header is accepted from any
  authenticated caller, but nothing in the product reads the tag outside this
  report. If it ever becomes a billing or analytics dimension, that is a decision
  with its own document.
* **No UI.** Hygiene is an operator's job, not a salesperson's; the desk stays
  clean rather than gaining a button that can delete a workspace.
