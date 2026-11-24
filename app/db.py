"""
Realtor Estates — Postgres (Neon) backend.

The demo ships a JSON file store so it runs with zero installs. This module is
the production path: **Neon Postgres** (or any Postgres) becomes the durable,
multi-process backend while the in-memory document model stays the hot path.

Shape of the integration (deliberate, and documented in docs/12-NEON-BACKEND.md):

    writes  →  Postgres (synchronous, inside the same lock as the memory update)
    reads   →  in-memory working set, loaded once at boot
    audit   →  Postgres, append-only enforced by a trigger *and* the hash chain

That keeps ~2,000 call sites in api.py unchanged, makes every mutation durable,
and leaves the read path to be replaced with SQL in phase 2 when typed tables
arrive. The honest limitation is stated where it matters: the in-memory read
cache assumes a single writer process. Running two API pods on Postgres is safe
for durability but not yet for cache coherence.

Dependencies: `psycopg[binary]` (optional — the app degrades to the JSON store
with a clear log line if it is absent). Nothing else.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Iterable

from . import config as cfg

_missing_psycopg: str = ""
try:  # optional dependency — never a hard import of the app
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
except Exception as exc:  # pragma: no cover - exercised only without psycopg
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore
    _missing_psycopg = exc.__class__.__name__

DSN_VARS = ("DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "PGURL")
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db")


# ---------------------------------------------------------------------------
# Connection string handling
# ---------------------------------------------------------------------------
def normalise_dsn(raw: str) -> str:
    """Normalise one DSN string.

    Neon hands out `postgresql://user:pass@ep-xxx.region.aws.neon.tech/db?sslmode=require`.
    Some tooling emits the legacy `postgres://` scheme, which libpq accepts but
    psycopg2-style parsers do not. We normalise, and we force TLS for hosted
    hosts unless the operator explicitly asked for something else.
    """
    if not raw:
        return ""
    url = raw.strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    hosted = any(h in url for h in (".neon.tech", ".supabase.co", ".rds.amazonaws.com", "render.com", "railway.app"))
    if hosted and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    if "connect_timeout=" not in url:
        url += ("&" if "?" in url else "?") + "connect_timeout=10"
    if "application_name=" not in url:
        url += "&application_name=realtor-estates"
    return url


def dsn() -> str:
    """Resolve the DSN from the environment / .env files and normalise it."""
    raw = ""
    for name in DSN_VARS:
        raw = cfg.get(name, "")
        if raw:
            break
    return normalise_dsn(raw)


def redact_dsn(value: str) -> str:
    """`postgresql://user:***@host/db` — safe to print, log or return in JSON."""
    if not value:
        return ""
    try:
        head, tail = value.split("@", 1)
        scheme, _creds = head.split("://", 1)
        user = _creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{tail}"
    except ValueError:
        return "(unparseable DSN)"


def redacted_dsn() -> str:
    return redact_dsn(dsn())


def admin_dsn() -> str:
    """The privileged DSN: migrations, boot hydration, repair scripts.

    Deliberately separate from `dsn()` (the runtime role). Anything that needs
    to cross tenants or change the schema must say so by asking for this one.
    """
    raw = cfg.get("RE_ADMIN_DATABASE_URL", "") or ""
    return normalise_dsn(raw) if raw else dsn()


def configured() -> bool:
    return bool(dsn())


def driver_available() -> bool:
    return psycopg is not None


def status() -> dict[str, Any]:
    """Never returns credentials — safe for /healthz and the owner console."""
    return {
        "backend": "postgres" if (configured() and driver_available()) else "json",
        "dsn_configured": configured(),
        "dsn": redacted_dsn(),
        "driver": ("psycopg " + getattr(psycopg, "__version__", "?")) if driver_available() else f"missing ({_missing_psycopg})",
        "host": _host_only(),
        "migrations_dir": os.path.relpath(MIGRATIONS_DIR, os.path.dirname(MIGRATIONS_DIR)),
    }


def _host_only() -> str:
    d = dsn()
    if not d:
        return ""
    try:
        return d.split("@", 1)[1].split("/", 1)[0].split("?")[0]
    except IndexError:
        return ""


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------
def migration_files(include_rls: bool = False) -> list[str]:
    if not os.path.isdir(MIGRATIONS_DIR):
        return []
    files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))
    if not include_rls:
        files = [f for f in files if "rls" not in f]
    return files


def _checksum(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:16]


def migrate(conn, include_rls: bool = False, verbose: bool = True) -> list[str]:
    """Idempotent migration runner. Safe to run on every deploy."""
    applied: list[str] = []
    files = migration_files(include_rls)
    if not files:
        return applied
    with conn.cursor() as cur:
        cur.execute(
            """
            create table if not exists re_migrations (
              version text primary key,
              applied_at timestamptz not null default now(),
              checksum text not null
            )
            """
        )
        cur.execute("select version, checksum from re_migrations")
        existing = {r[0]: r[1] for r in cur.fetchall()}
        conn.commit()
        for name in files:
            path = os.path.join(MIGRATIONS_DIR, name)
            sql = open(path, encoding="utf-8").read()
            chk = _checksum(sql)
            if name in existing:
                if existing[name] != chk and verbose:
                    print(f"  ! {name} changed since it was applied (checksum drift) — not re-running")
                continue
            cur.execute(sql)
            cur.execute(
                "insert into re_migrations (version, checksum) values (%s, %s) on conflict do nothing",
                (name, chk),
            )
            conn.commit()
            applied.append(name)
            if verbose:
                print(f"  ✓ applied {name}")
    return applied


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
class StorageUnavailable(RuntimeError):
    """The durable store could not be reached after a reconnect attempt.

    Distinct from a programming error: the caller answers 503 and stays up,
    instead of surfacing a traceback and a 500 (SEN-004). Neon's pooler recycles
    idle server connections, so "connection lost" is a *normal* event here, not
    an exception that should kill a request.
    """


def _is_connection_error(exc: BaseException) -> bool:
    if psycopg is None:  # pragma: no cover
        return False
    names = {c.__name__ for c in (psycopg.OperationalError, psycopg.InterfaceError)}
    names |= {"OperationalError", "InterfaceError", "ConnectionException"}
    return any(cls.__name__ in names for cls in type(exc).__mro__)


class PgBackend:
    """Durable store for the document model. One row per document."""

    def __init__(self, connection_string: str | None = None, ensure_schema: bool = True):
        if psycopg is None:  # pragma: no cover
            raise RuntimeError(
                "Postgres backend requested but psycopg is not installed. "
                "Install with: pip install 'psycopg[binary]'"
            )
        self.dsn_string = connection_string or dsn()
        if not self.dsn_string:
            raise RuntimeError("No DATABASE_URL configured")
        # One psycopg connection is one session, and a session is one transaction
        # at a time. The automation worker (a background thread since v1.11.0)
        # and the request threads share this connection, so without a lock two
        # statements interleave inside one transaction: `set_config(app.tenant_id)`
        # from the worker can land inside a login's insert and the audit row is
        # then refused by RLS for belonging to the wrong workspace. The lock makes
        # a session usable by a threaded server at all; it is not a throughput
        # strategy (that is a pool, see docs/02 §5).
        self._lock = threading.RLock()
        self.conn = self._connect()
        # A second, privileged connection for operations that are deliberately
        # **not** tenant-scoped: schema migrations and the boot-time hydration of
        # the in-memory working set. The runtime role (`re_app` in production)
        # has no BYPASSRLS and no DDL rights, so these two paths must be separate
        # — otherwise "the service can read every tenant" is baked in (SEN-003).
        self._admin_conn = None
        self._admin_attempted = False
        # An explicit connection string (tests, a migration script, a second
        # tenant-scoped pool) means "use exactly this database". Falling back to
        # .env here would silently mix two databases: a test database for writes
        # and production for the boot read.
        self._admin_dsn = "" if connection_string else (cfg.get("RE_ADMIN_DATABASE_URL", "") or "")
        skip_boot_migrate = cfg.get("RE_SKIP_BOOT_MIGRATE", "").strip().lower() in ("1", "true", "yes")
        if ensure_schema and not skip_boot_migrate:
            migrate(self.admin, include_rls=cfg.get("RE_DB_RLS", "") in ("1", "true", "yes"))
        self._server_version: str | None = None

    # -- resilience ------------------------------------------------------
    @property
    def admin(self):
        """Privileged connection for non-tenant-scoped work, or the runtime one."""
        if self._admin_conn is None and not self._admin_attempted and self._admin_dsn:
            self._admin_attempted = True
            try:
                self._admin_conn = psycopg.connect(
                    normalise_dsn(self._admin_dsn), autocommit=False, prepare_threshold=None
                )
            except Exception as exc:  # pragma: no cover - misconfiguration
                print(f"[db] RE_ADMIN_DATABASE_URL unusable ({exc.__class__.__name__}) — "
                      "boot hydration will use the runtime connection")
                self._admin_conn = None
        return self._admin_conn or self.conn

    def _run(self, fn, conn=None):
        """Execute `fn(conn)`; on a lost connection, reconnect once and retry.

        Anything still failing after the retry is a StorageUnavailable (→ 503),
        never a half-handled 500. Non-connection errors keep their own type so a
        bug is not disguised as an outage.
        """
        target = conn if conn is not None else self.conn
        with self._lock:
            try:
                return fn(target)
            except StorageUnavailable:
                raise
            except Exception as exc:
                if not _is_connection_error(exc):
                    # A failed statement leaves the session in an aborted
                    # transaction, and every later statement on it fails with
                    # "current transaction is aborted" — one bad read would take
                    # the whole workspace down until restart. Reset it and let the
                    # real error through.
                    try:
                        target.rollback()
                    except Exception:
                        pass
                    raise
                if conn is not None and conn is not self.conn:
                    raise StorageUnavailable(f"maintenance connection lost: {exc}") from exc
                print(f"[db] connection lost ({exc.__class__.__name__}) — reconnecting")
                if not self._reconnect():
                    raise StorageUnavailable(str(exc)) from exc
                try:
                    return fn(self.conn)
                except Exception as exc2:
                    raise StorageUnavailable(str(exc2)) from exc2

    @staticmethod
    def _set_tenant(cur, tenant_id: str) -> None:
        """Scope the transaction to one tenant so RLS can enforce it.

        `set_config(..., true)` is transaction-local, so a pooled server
        connection can never inherit another request's tenant. It is *also* the
        belt to the application's braces: if a query ever forgets `where
        tenant_id = ...`, the policy still returns nothing.
        """
        cur.execute("select set_config('app.tenant_id', %s, true)", (tenant_id,))

    def _connect(self):
        """Open a connection with server-side prepares disabled.

        Prepared statements are a psycopg optimisation that breaks against
        PGlite's wire emulation ("prepared statement _pg3_0 already exists") and
        against transaction-mode poolers such as PgBouncer, which Neon uses in
        front of pooled endpoints. Explicit parameters are fast enough here.
        """
        try:
            conn = psycopg.connect(self.dsn_string, autocommit=False, prepare_threshold=None)
        except TypeError:  # psycopg2 / older driver
            conn = psycopg.connect(self.dsn_string, autocommit=False)
        # A transaction pooler (Neon's -pooler endpoint, PgBouncer) hands back a
        # server session that may still carry `SET ROLE` / `SET` from whoever used
        # it last. Meeting a connection that is silently running as `re_app` is a
        # real failure mode; reset before doing anything else.
        try:
            with conn.cursor() as cur:
                cur.execute("reset role; reset all")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        return conn

    def _rollback(self) -> None:
        """Undo a failed statement. If the session is unrecoverable, reconnect.

        Real Postgres clears an aborted transaction on ROLLBACK. PGlite (the
        local stand-in) does not: one failed statement poisons the session for
        every later client. Reconnecting keeps a probe from wedging the app.
        """
        try:
            self.conn.rollback()
        except Exception:
            pass
        if not self._healthy():
            self._reconnect()

    def _healthy(self) -> bool:
        try:
            with self.conn.cursor() as cur:
                cur.execute("select 1")
            self.conn.commit()
            return True
        except Exception:
            return False

    def _reconnect(self) -> bool:
        """Drop the poisoned connection and open a fresh one."""
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            import psycopg

            self.conn = self._connect()
            return True
        except Exception:
            return False

    def _version(self) -> str:
        try:
            with self.conn.cursor() as cur:
                cur.execute("select version()")
                row = cur.fetchone()
            self.conn.commit()
            return (row or [""])[0]
        except Exception:
            self._rollback()
            return "unknown"

    def ping(self) -> dict[str, Any]:
        started = time.perf_counter()

        def _ping(conn):
            with conn.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
            conn.commit()

        self._run(_ping)
        if not self._server_version:
            self._server_version = self._version()
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 2), "version": self._server_version}

    def counts(self) -> dict[str, int]:
        def _count(conn):
            with conn.cursor() as cur:
                cur.execute("select collection, count(*) from re_documents group by collection")
                rows = cur.fetchall()
            conn.commit()
            return {r[0]: int(r[1]) for r in rows}

        return self._run(_count, conn=self.admin)

    # -- documents -------------------------------------------------------
    def load_all(self) -> dict[str, list[dict]]:
        """Load the working set.

        Ordering matters twice over:

        * documents are returned oldest-first so seeded lists keep their shape;
        * **audit entries must come back in insertion order**, because the hash
          chain is verified by walking them. `re_audit.seq` is the only true
          insertion order — ordering by `created_at` scrambles entries written in
          the same second and made /api/v1/tenant/audit report a broken chain that
          was not actually broken.
        """
        out: dict[str, list[dict]] = {}

        def _load(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "select collection, doc from re_documents "
                    "where collection <> 'audit' order by created_at asc, id asc"
                )
                for row in cur.fetchall():
                    out.setdefault(row["collection"], []).append(row["doc"])
                cur.execute(
                    """select tenant_id, id, actor, action, target, meta, at, prev_hash, hash
                       from re_audit order by seq asc"""
                )
                audit = [
                    {
                        "tenant_id": r["tenant_id"],
                        "id": r["id"],
                        "actor": r["actor"],
                        "action": r["action"],
                        "target": r["target"] or "",
                        "meta": r["meta"] or {},
                        "at": r["at"],
                        "prev_hash": r["prev_hash"] or "",
                        "hash": r["hash"],
                    }
                    for r in cur.fetchall()
                ]
                conn.commit()
            if audit:
                out["audit"] = audit
            return out

        # Boot hydration is a whole-database read by design: it is the one
        # operation that must cross tenants, so it runs on the maintenance
        # connection, never on the tenant-scoped runtime one.
        result = self._run(_load, conn=self.admin)
        return result

    def documents(self, collection: str, tenant_id: str) -> list[dict]:
        """Read one collection for one tenant, straight from the database.

        The service keeps a boot-time working set in memory, which means a second
        process's writes are invisible until it restarts. A tick is the one place
        where staleness changes behaviour rather than just comfort — it decides
        whether an occurrence has already been claimed — so it re-reads the rows
        it is about to make a decision on. Tenant-scoped connection: RLS, not a
        `where` clause, is what keeps this from crossing workspaces.
        """

        def _read(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                self._set_tenant(cur, tenant_id)
                cur.execute(
                    "select doc from re_documents where collection = %s and tenant_id = %s "
                    "order by created_at asc, id asc",
                    (collection, tenant_id),
                )
                rows = [r["doc"] for r in cur.fetchall()]
                conn.commit()
            return rows

        return self._run(_read)

    def get_document(self, collection: str, tenant_id: str, rid: str) -> dict | None:
        """Read a single document for one tenant under Postgres RLS (`F-01` stateless mode)."""
        if not tenant_id or not rid:
            return None

        def _read(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                self._set_tenant(cur, tenant_id)
                cur.execute(
                    "select doc from re_documents where collection = %s and tenant_id = %s and id = %s limit 1",
                    (collection, tenant_id, rid),
                )
                row = cur.fetchone()
                conn.commit()
            return dict(row["doc"]) if row and row.get("doc") else None

        return self._run(_read)

    def load_collection_admin(self, collection: str) -> list[dict]:
        """Maintenance-role single-collection scan (used only for `tenants` / public `funnels` routing in stateless mode)."""
        def _load(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "select doc from re_documents where collection = %s order by created_at asc, id asc",
                    (collection,),
                )
                rows = [dict(r["doc"]) for r in cur.fetchall()]
                conn.commit()
            return rows

        return self._run(_load, conn=self.admin)

    def load_collections_admin(self, collections: Iterable[str]) -> dict[str, list[dict]]:
        """Batch maintenance-role read across routing collections in one SQL round-trip."""
        wanted = list(dict.fromkeys(collections))
        if not wanted:
            return {}

        def _load(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "select collection, doc from re_documents "
                    "where collection = any(%s) order by created_at asc, id asc",
                    (wanted,),
                )
                out: dict[str, list[dict]] = {name: [] for name in wanted}
                for row in cur.fetchall():
                    out.setdefault(row["collection"], []).append(dict(row["doc"]))
                conn.commit()
            return out

        return self._run(_load, conn=self.admin)

    def documents_many(self, collections: Iterable[str], tenant_id: str) -> dict[str, list[dict]]:
        """One round trip for several collections. See `documents` for why.

        A tick refreshes everything it decides on; seven sequential queries against
        a Neon endpoint is ~3 s of latency for one tick, which is a self-inflicted
        slow tick. One statement costs one round trip.
        """
        wanted = list(collections)

        def _read(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                self._set_tenant(cur, tenant_id)
                cur.execute(
                    "select collection, doc from re_documents "
                    "where tenant_id = %s and collection = any(%s) "
                    "order by created_at asc, id asc",
                    (tenant_id, wanted),
                )
                out: dict[str, list[dict]] = {name: [] for name in wanted}
                for row in cur.fetchall():
                    out.setdefault(row["collection"], []).append(row["doc"])
                conn.commit()
            return out

        return self._run(_read)

    def upsert_documents(self, rows: Iterable[tuple[str, str, dict]]) -> int:
        """rows = (collection, tenant_id, doc). Batched, idempotent."""
        payload = [
            (
                coll,
                tenant,
                doc["id"],
                json.dumps(doc),
                int(doc.get("created_at") or 0),
                int(doc.get("updated_at") or 0),
            )
            for coll, tenant, doc in rows
        ]
        if not payload:
            return 0

        def _write(conn):
            with conn.cursor() as cur:
                # One tenant at a time: the RLS policy is checked per statement
                # against `app.tenant_id`, so a batch spanning tenants would be
                # rejected (which is the point — a cross-tenant batch is a bug).
                for tenant_id in sorted({p[1] for p in payload}):
                    self._set_tenant(cur, tenant_id)
                    cur.executemany(
                        """
                        insert into re_documents (collection, tenant_id, id, doc, created_at, updated_at)
                        values (%s, %s, %s, %s::jsonb, %s, %s)
                        on conflict (collection, tenant_id, id)
                        do update set doc = excluded.doc, updated_at = excluded.updated_at
                        """,
                        [p for p in payload if p[1] == tenant_id],
                    )
                cur.execute("select set_config('app.tenant_id', '', true)")
            conn.commit()
            return len(payload)

        return self._run(_write)

    def upsert_document(self, collection: str, tenant_id: str, doc: dict) -> None:
        self.upsert_documents([(collection, tenant_id, doc)])

    def delete_document(self, collection: str, tenant_id: str, rid: str) -> bool:
        def _delete(conn):
            with conn.cursor() as cur:
                self._set_tenant(cur, tenant_id)
                cur.execute(
                    "delete from re_documents where collection = %s and tenant_id = %s and id = %s",
                    (collection, tenant_id, rid),
                )
                changed = cur.rowcount > 0
            conn.commit()
            return changed

        return self._run(_delete)

    # -- audit -----------------------------------------------------------
    def append_audit(self, entry: dict) -> dict | None:
        """Append one audit entry, chained by the database, not by the caller.

        The hash chain is only sound if `prev_hash` is the true last hash for
        that tenant. Computing it in the application means: two processes (two
        app instances, a worker, a migration script) both read the same tail and
        both chain to it — the log forks and `verify_audit` reports a break that
        no attacker caused. `pg_advisory_xact_lock` gives one writer per tenant
        for the duration of the transaction, so the chain is linear even with
        concurrent writers.

        Returns the entry as actually persisted (with revised prev_hash/hash), or
        None when the row already existed.
        """
        from . import security as sec

        def _append(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                self._set_tenant(cur, entry["tenant_id"])
                cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (entry["tenant_id"],))
                cur.execute(
                    "select hash from re_audit where tenant_id = %s order by seq desc limit 1",
                    (entry["tenant_id"],),
                )
                row = cur.fetchone()
                prev = (row or {}).get("hash") or ""
                chained = dict(entry)
                chained["prev_hash"] = prev
                chained["hash"] = sec.audit_hash(chained)
                cur.execute(
                    """
                    insert into re_audit (tenant_id, id, actor, action, target, meta, at, prev_hash, hash)
                    values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    on conflict (id) do nothing
                    """,
                    (
                        chained["tenant_id"],
                        chained["id"],
                        chained["actor"],
                        chained["action"],
                        chained.get("target", ""),
                        json.dumps(chained.get("meta") or {}),
                        int(chained["at"]),
                        chained["prev_hash"],
                        chained["hash"],
                    ),
                )
                inserted = cur.rowcount
            conn.commit()
            return chained if inserted else None

        return self._run(_append)

    def audit_rows(self, tenant_id: str, limit: int = 5000) -> list[dict]:
        def _read(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                self._set_tenant(cur, tenant_id)
                cur.execute(
                    """
                    select id, tenant_id, actor, action, target, meta, at, prev_hash, hash
                    from re_audit where tenant_id = %s order by seq asc limit %s
                    """,
                    (tenant_id, limit),
                )
                rows = cur.fetchall()
            conn.commit()
            return rows

        return self._run(_read)

    def audit_immutable_check(self) -> dict:
        """Prove the append-only trigger is real, not a comment in a doc.

        Two independent checks, because each alone has a blind spot:

        * the catalogue (`pg_trigger`) says whether the trigger exists at all —
          side-effect free, and the only check that works on an empty table;
        * a live UPDATE/DELETE on a canary row says whether it actually fires.
          A row-matched probe is required: an UPDATE that matches zero rows
          never fires a row trigger, so probing an empty table returns "not
          blocked" and looks like a security hole.
        """
        out: dict = {"trigger_present": False, "update_blocked": False, "delete_blocked": False, "probe_rows": 0}
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    select pg_get_triggerdef(t.oid)
                    from pg_trigger t
                    join pg_class c on c.oid = t.tgrelid
                    where c.relname = 're_audit' and not t.tgisinternal
                    """
                )
                defs = [r[0].upper() for r in cur.fetchall()]
            self.conn.commit()
        except Exception:
            self._rollback()
            defs = []
        out["trigger_present"] = any(
            "BEFORE" in d and "UPDATE" in d and "DELETE" in d for d in defs
        )
        if not out["trigger_present"]:
            return out

        canary = f"aud_probe_{os.urandom(5).hex()}"
        try:
            self.append_audit(
                {
                    "tenant_id": "_probe",
                    "id": canary,
                    "actor": "audit-probe",
                    "action": "db.immutability_probe",
                    "target": "",
                    "meta": {"note": "written by audit_immutable_check()"},
                    "at": int(time.time()),
                    "prev_hash": "",
                    "hash": "probe",
                }
            )
            out["probe_rows"] = 1
        except Exception:
            return out  # nothing to probe against; the catalogue check already passed

        for label, sql in (
            ("update_blocked", "update re_audit set actor = 'tamper-probe' where id = %s"),
            ("delete_blocked", "delete from re_audit where id = %s"),
        ):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql, (canary,))
                self.conn.commit()
                self._rollback()  # statement went through: NOT blocked
            except Exception:
                out[label] = True
                self._rollback()
        out["session_recovered"] = self._healthy() or self._reconnect()
        return out

    # ---- property media (written by the property-photos Neon Function) ----
    def property_media(self, property_id: str, tenant_id: str) -> list[dict]:
        """Variant rows for one property.

        The table is owned by the `property-photos` Function, not by this app:
        the function writes variants, the app only reads them. It is therefore
        scoped by an explicit `tenant_id` predicate rather than RLS — the table
        has no policy, because no app role may insert into it. A missing table
        (migration 005 not applied yet) reads as "no media" instead of a 500.
        """
        def _read(conn):
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._set_tenant(cur, tenant_id)
                    cur.execute(
                        """
                        select variant, object_key, width, height, bytes, checksum, updated_at
                          from re_property_media
                         where property_id = %s and tenant_id = %s
                         order by width asc
                        """,
                        (property_id, tenant_id),
                    )
                    rows = cur.fetchall() or []
                conn.commit()
                return rows
            except Exception as exc:
                if exc.__class__.__name__ in {"UndefinedTable", "ProgrammingError"}:
                    return []
                raise

        return list(self._run(_read) or [])

    def property_media_bulk(self, property_ids: list[str], tenant_id: str) -> dict[str, list[dict]]:
        """Variant rows for many properties in **one** query.

        A catalogue page asks about forty listings at once. Reading them one by one
        turned a 300 ms page into a 6 s page over a pooled Neon connection — the
        kind of regression that only shows up when the network is real. Same tenant
        predicate, same "missing table reads as no media" rule.
        """
        ids = [i for i in dict.fromkeys(property_ids) if i][:500]
        if not ids:
            return {}

        def _read(conn):
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._set_tenant(cur, tenant_id)
                    cur.execute(
                        """
                        select property_id, variant, object_key, width, height, bytes, checksum,
                               updated_at
                          from re_property_media
                         where property_id = any(%s) and tenant_id = %s
                         order by property_id, width asc
                        """,
                        (ids, tenant_id),
                    )
                    rows = cur.fetchall() or []
                conn.commit()
                return rows
            except Exception as exc:
                if exc.__class__.__name__ in {"UndefinedTable", "ProgrammingError"}:
                    return []
                raise

        grouped: dict[str, list[dict]] = {i: [] for i in ids}
        for row in self._run(_read) or []:
            grouped.setdefault(row.get("property_id"), []).append(row)
        return grouped

    # ---- records written by the scheduled / webhook functions ----
    def compliance_runs(self, limit: int = 20) -> list[dict]:
        """Recent rows from `re_compliance_runs` (written by compliance-sweep).

        Platform-level, not tenant-scoped: one sweep covers the whole branch, and
        its counts are aggregates over every workspace. The route that exposes
        this therefore needs `manage_tenant` — but even then the response carries
        no other tenant's identifiers, only counts.
        """
        def _read(conn):
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        select id, trigger_name, scheduled_at, started_at, finished_at,
                               status, retention, audit, media, error
                          from re_compliance_runs
                         order by started_at desc
                         limit %s
                        """,
                        (limit,),
                    )
                    rows = cur.fetchall() or []
                conn.commit()
                return rows
            except Exception as exc:
                if exc.__class__.__name__ in {"UndefinedTable", "ProgrammingError"}:
                    return []
                raise

        return list(self._run(_read) or [])

    def webhook_events(self, tenant_id: str, limit: int = 50) -> list[dict]:
        """Verified payment events for one workspace.

        `tenant_id` is copied from the payment's `notes` by the webhook function,
        so the filter is a column comparison rather than a JSON dig — and an
        event that could not be attributed to a workspace is not returned to any
        workspace at all.
        """
        def _read(conn):
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    self._set_tenant(cur, tenant_id)
                    cur.execute(
                        """
                        select event_id, provider, event_type, status, received_at,
                               payment_id, amount, currency, key_fingerprint
                          from re_webhook_events
                         where tenant_id = %s
                         order by received_at desc
                         limit %s
                        """,
                        (tenant_id, limit),
                    )
                    rows = cur.fetchall() or []
                conn.commit()
                return rows
            except Exception as exc:
                if exc.__class__.__name__ in {"UndefinedTable", "ProgrammingError"}:
                    return []
                raise

        return list(self._run(_read) or [])

    def close(self) -> None:
        for c in (self.conn, self._admin_conn):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI: python3 -m app.db [status|migrate|ping]
# ---------------------------------------------------------------------------
def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if not configured():
        print("DATABASE_URL is not set — nothing to do (the JSON store is active).")
        return 2
    if not driver_available():
        print("psycopg is not installed: pip install 'psycopg[binary]'")
        return 2
    backend = PgBackend()
    if cmd == "ping":
        print(json.dumps(backend.ping(), indent=2))
    elif cmd == "migrate":
        applied = migrate(backend.admin, include_rls=cfg.get("RE_DB_RLS", "") in ("1", "true", "yes"))
        print(f"migrations applied: {applied or 'none (already up to date)'}")
    elif cmd == "counts":
        print(json.dumps(backend.counts(), indent=2))
    else:
        print(__doc__)
    backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(__import__("sys").argv))
