# 32 — The Postgres gate: why a skipped test is not a pass

> **Gap G6.** `PLANS.md` has carried this line for a long time: *"Postgres/RLS
> tests skip by default (14 skipped, incl. 8× 'set `RE_TEST_DATABASE_URL`'). The S0
> gate 'every pytest passes against Postgres' is unverified in a clean checkout."*
>
> That is the worst kind of gap, because the suite looks healthy. Fourteen tests
> disappear into a skip count, the run prints "passed", and the two claims that
> matter most — that row-level security actually filters a non-owner, and that the
> audit hash chain stays linear under concurrent writers — were never executed on a
> machine that was not somebody's laptop-with-a-database.

This document is about the verifier, not about Postgres. It is short because the
idea is short: **say what you proved, and say what you did not.**

---

## 1. Three targets, three different amounts of evidence

```
python3 scripts/verify_postgres.py          # whatever DATABASE_URL points at (Neon)
python3 scripts/verify_postgres.py --real    # a real postgres binary, downloaded once
python3 scripts/verify_postgres.py --local   # the PGlite stand-in
```

The script probes the target **before** it runs anything, and reports what that
target is capable of. The probe is measured, not parsed from a URL: it opens two
connections, records `pg_backend_pid()`, writes a row on one and reads it on the
other.

| target | isolated transactions | role system | can prove |
|---|---|---|---|
| Neon branch / real PostgreSQL | yes | yes | everything |
| PGlite (`--local`) | **no — one session for all clients** | no | schema, migrations, round-trip, tenant scoping, append-only trigger |
| nothing configured | — | — | nothing. Exit 3, with the command to fix it. |

`--real` is the interesting one. `embedded-postgres` is a wheel that carries actual
`postgres` binaries — no root, no Docker, no system package, about 18 MB:

```
pip install embedded-postgres
python3 scripts/verify_postgres.py --real
```

That is enough to run the real thing on a laptop or in CI, which is what closes
this gap rather than deferring it to "when Neon is reachable".

---

## 2. The verdict, and why it has its own function

| exit | meaning |
|---|---|
| **0** | PASS — nothing skipped, RLS and concurrency both proved |
| **2** | PARTIAL — everything this target can prove passed, and the blind spots are printed |
| **1** | FAIL — at least one test failed |
| **3** | no database configured |

`verdict()` is a pure function of `(passed, skipped, failed, capabilities)` and it is
unit-tested in `tests/test_verify_postgres.py`, because "did this pass?" is the one
question the whole script exists to answer and an answer computed inline from a
subprocess's stdout is untestable. Three of its rules are the point:

* **a skip is not a pass.** Any skip at all turns 0 into 2.
* **a target that cannot isolate transactions cannot prove concurrency**, no matter
  what its test report says. Even a clean run against PGlite is PARTIAL.
* **a failure outranks a skip.** 1, never 2 — a partial-read "pass" must not hide a
  real failure.

---

## 3. What running it actually found

Three things, none of which were visible from a green suite:

**The audit chain test was measuring the wrong rows.** It read `load_all()["audit"]`
and filtered by tenant, then asserted ten entries. `re_audit.seq` is a *global*
sequence, so that slice is the whole installation's interleaved history — on a
database that other tests are also writing to, the count is whatever everyone else
did too. It now asks `audit_rows(tenant)`, the same read the product uses.

**The isolation guard was keyed on a port number.** `if ":5433" in pg_url: skip(...)`
assumes the stand-in is always on 5433, which says nothing about which server is
listening there — a real Postgres on 5433 would have been skipped, and a stand-in on
any other port would have produced a spurious "the chain forked". It now probes for
transaction isolation and skips with that reason, which is also what `--real` and
`--local` are distinguished by.

**A probe can deadlock.** The first version of `_capabilities` did its DDL inside an
open transaction on one connection while a second connection was also in a
transaction: each held a lock the other wanted, and both waited. The script sat
there until its timeout. Both probes now use `autocommit` — the isolation question
is answered by backend pid and by what a *committed* row looks like from the other
side, and needs no transaction at all.

None of those would have been found without running the gate. That is the argument
for the gate.

---

## 4. What `--real` proves, in full

```
$ python3 scripts/verify_postgres.py --real
target: postgresql://***@/postgres?host=/tmp/re-pg-real-…/pgdata
migrations: exit 0
  migrations applied: none (already up to date)
server: PostgreSQL 18.6  · isolated transactions: yes  · re_app role before the RLS migrations: yes
rls migrations: exit 0 — 8 rls migration(s) applied, re_app=True

.....................                                                    [100%]
21 passed in 43.59s

RESULT: PASS — 21 passed, nothing skipped, RLS and concurrency proved
```

The four RLS tests, run individually against the same server, for the record:

```
tests/test_rls.py::test_owner_bypasses_rls PASSED
tests/test_rls.py::test_restricted_role_sees_only_the_configured_tenant PASSED
tests/test_rls.py::test_unset_tenant_sees_nothing PASSED
tests/test_rls.py::test_cross_tenant_insert_is_rejected PASSED
```

`re_app` is created by migration 002 and made impersonable by 003 — which is why
the verifier applies the RLS migrations *through `dbmod.migrate(include_rls=True)`*
rather than telling the operator to run a SQL file by hand. A policy nobody can
`set role` into is a policy nobody can test, and that was part of how this gap
survived.

---

## 5. What this still does not prove

* **Not Neon.** `--real` is PostgreSQL 18 on a unix socket. Neon is PostgreSQL with
  a proxy in front: connection pooling, `channel_binding`, a compute that sleeps.
  `--url "$NEON_BRANCH_URL"` is still the run that proves the hosted path, and it
  is the one to run before a release.
* **Not concurrency at scale.** Two writer threads is the minimum that can fork a
  chain. It says nothing about fifty, or about a Neon compute waking up mid-write.
  That belongs in a load test, not a unit suite.
* **Not the `sentinel` scanner.** The gate proves the database behaves; it does not
  prove nobody pasted a credential into a doc (`scripts/check_secrets.py` does that).
* **`--local` is not a substitute for this.** It is kept because it runs anywhere,
  including a sandbox with no way to install a native binary, and because the four
  tests it *can* run are worth running. It is labelled PARTIAL and stays that way.
