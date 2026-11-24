"""
Shared security state: the rate limiter's counters and the revoked-token set.

`docs/02 §5` has listed "in-process rate limits → Redis token bucket, same
`RateLimiter.check()` signature" as a migration for a long time. The reason it
mattered is not throughput. It is that both of these are **per-process**:

* a rate limit in one process's memory is not a rate limit the moment there are
  two processes. Ten instances multiply `auth_account: 10/15min` into 100
  password guesses per quarter hour, and a load balancer hands the attacker the
  fan-out for free.
* a revoked token (`sec.REVOKED`) lives in the process that revoked it. Log out on
  node A and the token still works on node B for the rest of its lifetime — the
  one thing a logout must never do.

So the state moves behind a small interface with three backends, and each one is
honest about what it is:

| backend | where the state lives | survives a restart | shared by instances |
|---|---|---|---|
| `memory` | this process | no | no |
| `redis` | Redis (`RE_REDIS_URL`) | yes | yes |
| `file` | `RE_SHARED_DIR` on a filesystem both processes can see | yes | **only on one host** |

`file` exists because "run two workers on one box" is a real deployment — gunicorn
with `--workers 4`, or the two servers this repository's own proof scripts boot —
and it needs no new service. It is not a Redis replacement: it does not work
across hosts, its timestamps are wall-clock (so a wide NTP jump can expire or
extend a window), and every operation locks.

The Redis protocol is implemented here rather than pulled in, because
`docs/02`'s first rule is zero third-party runtime packages in the app tier. It is
RESP2 over a plain socket with a strict parser and four commands, which is a
smaller auditable surface than a client library and its dependency tree. It is
**not** TLS: put it on a private network or a unix socket (`rediss://` is refused
rather than silently downgraded).

Boot is fail-closed in production: if `RE_SHARED_STATE=redis` is configured and
Redis does not answer, the process refuses to start rather than degrading to
per-process limits, because a limiter that silently stops working is worse than
one that stops the boot. In development it degrades and says so, loudly, in the
console and on `/healthz`.
"""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import config as cfg

# --------------------------------------------------------------------------
# Decisions a deployment has to make, and what happens when it has not
# --------------------------------------------------------------------------
REDIS_URL = cfg.get("RE_REDIS_URL", "")
SHARED_DIR = cfg.get("RE_SHARED_DIR", "")
REQUESTED = (cfg.get("RE_SHARED_STATE", "") or "").strip().lower()   # memory | redis | file
TIMEOUT = float(cfg.get("RE_REDIS_TIMEOUT", "1.5") or 1.5)

WARNINGS: list[str] = []


class SharedStateError(Exception):
    """The configured backend could not be used. Fatal in production."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# A tiny, strict RESP2 client
# --------------------------------------------------------------------------
class RedisError(Exception):
    pass


class Redis:
    """One connection, one lock, four commands. Small on purpose.

    Every call is a complete round trip; there is no pipelining, no transactions
    and no Lua. The only multi-step operation (`rate`) is expressed as a single
    `EVAL` when scripts are enabled, and falls back to a `SET NX` counter with a
    server-side `PEXPIRE` when they are not (a managed Redis with `EVAL` disabled
    is a real thing).
    """

    name = "redis"

    def __init__(self, url: str, timeout: float = TIMEOUT):
        parsed = urlparse(url)
        if parsed.scheme not in ("redis", "unix"):
            raise SharedStateError(
                "REDIS_SCHEME", f"RE_REDIS_URL scheme {parsed.scheme!r} is not supported — "
                                "use redis:// or unix:// (rediss:// is refused rather than "
                                "silently treated as plaintext)")
        self.unix = parsed.scheme == "unix"
        self.path = parsed.path if self.unix else ""
        self.host = parsed.hostname or "127.0.0.1"
        self.port = int(parsed.port or 6379)
        self.password = parsed.password or ""
        self.db = int((parsed.path or "/0").lstrip("/") or 0) if not self.unix else 0
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self.scripts = True
        self._connect()

    # -- connection -------------------------------------------------------
    def _connect(self) -> None:
        try:
            if self.unix:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(self.timeout)
                s.connect(self.path)
            else:
                s = socket.create_connection((self.host, self.port), timeout=self.timeout)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            raise SharedStateError("REDIS_UNREACHABLE",
                                   f"cannot reach Redis at {'unix:' + self.path if self.unix else self.host + ':' + str(self.port)}: {exc}")
        self._sock = s
        if self.password:
            self._send("AUTH", self.password)
            self._read()
        if self.db:
            self._send("SELECT", str(self.db))
            self._read()

    def close(self) -> None:
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None

    # -- RESP2 ------------------------------------------------------------
    def _send(self, *args: str) -> None:
        out = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = str(a).encode()
            out.append(f"${len(b)}\r\n".encode() + b + b"\r\n")
        assert self._sock is not None
        self._sock.sendall(b"".join(out))

    def _readline(self) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while not buf.endswith(b"\r\n"):
            chunk = self._sock.recv(1)
            if not chunk:
                raise RedisError("connection closed by server")
            buf += chunk
            if len(buf) > 65536:
                raise RedisError("RESP line too long — not a Redis server?")
        return bytes(buf[:-2])

    def _read(self):
        line = self._readline()
        if not line:
            raise RedisError("empty reply")
        kind, rest = line[:1], line[1:]
        if kind == b"+":
            return rest.decode()
        if kind == b"-":
            raise RedisError(rest.decode("utf-8", "replace"))
        if kind == b":":
            return int(rest)
        if kind == b"$":
            n = int(rest)
            if n == -1:
                return None
            assert self._sock is not None
            buf = b""
            while len(buf) < n + 2:
                chunk = self._sock.recv(n + 2 - len(buf))
                if not chunk:
                    raise RedisError("connection closed mid-reply")
                buf += chunk
            return buf[:n].decode("utf-8", "replace")
        if kind == b"*":
            n = int(rest)
            if n == -1:
                return None
            return [self._read() for _ in range(n)]
        raise RedisError(f"unexpected RESP type {kind!r}")

    def cmd(self, *args: str):
        """One command, one reply. Reconnects once on a broken socket."""
        with self._lock:
            try:
                self._send(*args)
                return self._read()
            except RedisError:
                # an error *reply* is a real answer (WRONGTYPE, NOSCRIPT): raise it
                raise
            except (OSError, AssertionError) as exc:
                raise SharedStateError("REDIS_UNREACHABLE", f"Redis connection failed: {exc}")

    def ping(self) -> bool:
        try:
            return self.cmd("PING") == "PONG"
        except (SharedStateError, RedisError):
            return False

    # -- the operations the limiter and the revocation list need -----------
    def incr_window(self, key: str, window: float, limit: int) -> tuple[int, float]:
        """Count one hit in a fixed window that starts on the first hit.

        Returns `(count_after_this_hit, seconds_until_the_window_ends)`. Fixed
        window rather than sliding, because it is one atomic round trip: the
        alternative is a sorted set written and trimmed per request, which is
        three commands and a race that needs Lua to close. A fixed window lets a
        caller send `2 × limit` across a boundary; that is a documented cost, and
        the buckets are sized with it in mind.
        """
        ms = max(1, int(window * 1000))
        try:
            res = self.cmd("EVAL", _RATE_LUA, "1", key, str(ms), str(limit))
            count, ttl = int(res[0]), int(res[1])
        except RedisError as exc:
            if "NOSCRIPT" in str(exc).upper() or "unknown command" in str(exc).lower():
                self.scripts = False
                return self._incr_window_no_script(key, ms, limit)
            raise
        return count, max(0.0, ttl / 1000.0)

    def _incr_window_no_script(self, key: str, ms: int, limit: int) -> tuple[int, float]:
        count = int(self.cmd("INCR", key))
        if count == 1:
            self.cmd("PEXPIRE", key, str(ms))
        ttl = int(self.cmd("PTTL", key))
        return count, max(0.0, (ttl if ttl > 0 else ms) / 1000.0)

    def get(self, key: str) -> str | None:
        return self.cmd("GET", key)

    def incr(self, key: str, ttl: float) -> int:
        n = int(self.cmd("INCR", key))
        if n == 1:
            self.cmd("PEXPIRE", key, str(max(1000, int(ttl * 1000))))
        return n

    def revoke(self, jti: str, ttl: float) -> None:
        self.cmd("SET", f"revoked:{jti}", "1", "PX", str(max(1000, int(ttl * 1000))))

    def is_revoked(self, jti: str) -> bool:
        return self.cmd("EXISTS", f"revoked:{jti}") == 1

    def claim_once(self, key: str, ttl: float) -> bool:
        """Atomic single-winner claim across all instances (`SET ... PX ... NX`)."""
        ms = str(max(1000, int(ttl * 1000)))
        return self.cmd("SET", f"once:{key}", "1", "PX", ms, "NX") == "OK"

    def set_ttl(self, key: str, value: str, ttl: float) -> None:
        ms = str(max(1000, int(ttl * 1000)))
        self.cmd("SET", key, str(value), "PX", ms)

    def ttl_left(self, key: str) -> int:
        ms = int(self.cmd("PTTL", key))
        return max(0, (ms + 999) // 1000) if ms > 0 else 0

    def delete(self, *keys: str) -> None:
        for k in keys:
            self.cmd("DEL", k)

    # -- tenant-scoped realtime event ring (RESP2, no Lua) ------------------
    def publish_event(self, tenant_id: str, event: dict, max_len: int = 256, ttl: float = 3600.0) -> dict:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        ms = str(max(1000, int(ttl * 1000)))
        seq_key = f"evseq:{tenant_id}"
        ring_key = f"evring:{tenant_id}"
        seq = int(self.cmd("INCR", seq_key))
        self.cmd("PEXPIRE", seq_key, ms)
        item = {**event, "id": seq, "tid": tenant_id, "ts": float(event.get("ts") or time.time())}
        self.cmd("RPUSH", ring_key, json.dumps(item, separators=(",", ":")))
        self.cmd("LTRIM", ring_key, str(-max(16, int(max_len))), "-1")
        self.cmd("PEXPIRE", ring_key, ms)
        return item

    def events_since(self, tenant_id: str, since_id: int = 0, limit: int = 100) -> list[dict]:
        if not tenant_id:
            return []
        cur_seq = self.cmd("GET", f"evseq:{tenant_id}")
        if cur_seq is None or int(cur_seq) <= int(since_id):
            return []
        raw_items = self.cmd("LRANGE", f"evring:{tenant_id}", "-256", "-1") or []
        out: list[dict] = []
        for raw in raw_items:
            try:
                doc = json.loads(raw)
            except Exception:
                continue
            if doc.get("tid") == tenant_id and int(doc.get("id") or 0) > int(since_id):
                out.append(doc)
        out.sort(key=lambda r: int(r.get("id") or 0))
        return out[: max(1, int(limit))]

    def info(self) -> dict:
        try:
            raw = self.cmd("INFO", "server")
        except (RedisError, SharedStateError):
            return {}
        out = {}
        for line in str(raw).splitlines():
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
        return out


_RATE_LUA = (
    "local c = redis.call('INCR', KEYS[1]) "
    "if c == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end "
    "return {c, redis.call('PTTL', KEYS[1])}"
)


# --------------------------------------------------------------------------
# The file backend: two processes on one host, no new service
# --------------------------------------------------------------------------
class SharedFiles:
    """Counters and revocations in a directory, with a lock file per operation.

    Honest limits, all of which the console repeats: one host only (the directory
    has to be shared, and `flock` is not a network protocol — NFS locking is
    emulated and unreliable), wall-clock timestamps, and a lock held for the whole
    read-modify-write. It is right for two gunicorn workers on one box and wrong
    for two regions.
    """

    name = "file"

    def __init__(self, directory: str):
        self.dir = Path(directory)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            probe = self.dir / ".probe"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            raise SharedStateError("SHARED_DIR_UNWRITABLE", f"RE_SHARED_DIR {directory} is not writable: {exc}")
        self.lock_path = self.dir / ".lock"

    def _locked(self, fn):
        import fcntl
        with open(self.lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _rate_file(self, key: str) -> Path:
        return self.dir / f"rate-{_sha(key)}.json"

    def incr_window(self, key: str, window: float, limit: int) -> tuple[int, float]:
        path = self._rate_file(key)

        def op():
            now = time.time()
            doc = {"start": now, "count": 0}
            try:
                doc = json.loads(path.read_text())
            except Exception:
                pass
            if now - float(doc.get("start", now)) >= window:      # window expired
                doc = {"start": now, "count": 0}
            doc["count"] = int(doc.get("count", 0)) + 1
            left = max(0.0, window - (now - float(doc["start"])))
            _atomic_write(path, json.dumps(doc).encode())
            return doc["count"], left

        return self._locked(op)

    def get(self, key: str) -> str | None:
        path = self.dir / f"kv-{_sha(key)}.json"
        return self._locked(lambda: _read_kv(path))

    def incr(self, key: str, ttl: float) -> int:
        path = self.dir / f"kv-{_sha(key)}.json"

        def op():
            now = time.time()
            doc = {"v": 0, "exp": now + ttl}
            try:
                doc = json.loads(path.read_text())
            except Exception:
                pass
            if float(doc.get("exp", 0)) <= now:          # expired counter restarts
                doc = {"v": 0, "exp": now + ttl}
            doc["v"] = int(doc.get("v", 0)) + 1
            _atomic_write(path, json.dumps(doc).encode())
            return doc["v"]

        return self._locked(op)

    def revoke(self, jti: str, ttl: float) -> None:
        path = self.dir / "revoked.json"
        expires = time.time() + max(1.0, ttl)

        def op():
            doc = {}
            try:
                doc = json.loads(path.read_text())
            except Exception:
                pass
            now = time.time()
            doc = {k: v for k, v in doc.items() if float(v) > now}     # expire on write
            doc[jti] = expires
            if len(doc) > 20000:
                doc = dict(sorted(doc.items(), key=lambda kv: kv[1])[-20000:])
            _atomic_write(path, json.dumps(doc).encode())

        self._locked(op)

    def is_revoked(self, jti: str) -> bool:
        return self._locked(lambda: _lookup_revoked(self.dir / "revoked.json", jti))

    def claim_once(self, key: str, ttl: float) -> bool:
        path = self.dir / "once.json"
        expires = time.time() + max(1.0, ttl)

        def op():
            doc = {}
            try:
                doc = json.loads(path.read_text())
            except Exception:
                pass
            now = time.time()
            doc = {k: v for k, v in doc.items() if float(v) > now}
            if key in doc:
                _atomic_write(path, json.dumps(doc).encode())
                return False
            doc[key] = expires
            _atomic_write(path, json.dumps(doc).encode())
            return True

        return self._locked(op)

    def set_ttl(self, key: str, value: str, ttl: float) -> None:
        path = self.dir / f"kv-{_sha(key)}.json"

        def op():
            doc = {"v": str(value), "exp": time.time() + max(1.0, ttl)}
            _atomic_write(path, json.dumps(doc).encode())

        self._locked(op)

    def ttl_left(self, key: str) -> int:
        path = self.dir / f"kv-{_sha(key)}.json"

        def op():
            try:
                doc = json.loads(path.read_text())
            except Exception:
                return 0
            rem = float(doc.get("exp", 0)) - time.time()
            return int(rem + 0.999) if rem > 0 else 0

        return self._locked(op)

    def delete(self, *keys: str) -> None:
        def op():
            for k in keys:
                try:
                    (self.dir / f"kv-{_sha(k)}.json").unlink()
                except FileNotFoundError:
                    pass

        self._locked(op)

    def _events_file(self, tenant_id: str) -> Path:
        return self.dir / f"events-{_sha(tenant_id)}.json"

    def publish_event(self, tenant_id: str, event: dict, max_len: int = 256, ttl: float = 3600.0) -> dict:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        path = self._events_file(tenant_id)
        keep = max(16, int(max_len))

        def op():
            now = time.time()
            doc = {"seq": 0, "exp": now + max(1.0, ttl), "items": []}
            try:
                loaded = json.loads(path.read_text())
                if float(loaded.get("exp", 0)) > now:
                    doc = loaded
            except Exception:
                pass
            seq = int(doc.get("seq") or 0) + 1
            item = {**event, "id": seq, "tid": tenant_id, "ts": float(event.get("ts") or now)}
            items = [x for x in (doc.get("items") or []) if x.get("tid") == tenant_id]
            items.append(item)
            doc = {"seq": seq, "exp": now + max(1.0, ttl), "items": items[-keep:]}
            _atomic_write(path, json.dumps(doc, separators=(",", ":")).encode())
            return item

        return self._locked(op)

    def events_since(self, tenant_id: str, since_id: int = 0, limit: int = 100) -> list[dict]:
        if not tenant_id:
            return []
        path = self._events_file(tenant_id)
        cache: dict = getattr(self, "_ev_stat_cache", None) or {}
        self._ev_stat_cache = cache
        try:
            st = path.stat()
        except FileNotFoundError:
            return []
        key_ns = (st.st_mtime_ns, st.st_ino, st.st_size)
        prev = cache.get(tenant_id)
        if prev and prev[0] == key_ns:
            max_seq, items, exp = prev[1], prev[2], prev[3]
            if exp <= time.time() or max_seq <= int(since_id):
                return []
            return [dict(x) for x in items if int(x.get("id") or 0) > int(since_id)][: max(1, int(limit))]

        def op():
            try:
                doc = json.loads(path.read_text())
            except Exception:
                return None
            return doc

        doc = self._locked(op)
        if not doc or float(doc.get("exp", 0)) <= time.time():
            return []
        items = [x for x in (doc.get("items") or []) if x.get("tid") == tenant_id]
        max_seq = int(doc.get("seq") or 0)
        cache[tenant_id] = (key_ns, max_seq, items, float(doc.get("exp", 0)))
        if max_seq <= int(since_id):
            return []
        return [dict(x) for x in items if int(x.get("id") or 0) > int(since_id)][: max(1, int(limit))]

    def info(self) -> dict:
        return {"backend": "file", "directory": str(self.dir),
                "note": "one host only: flock is not a network protocol"}


def _sha(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _read_kv(path: Path) -> str | None:
    try:
        doc = json.loads(path.read_text())
    except Exception:
        return None
    if float(doc.get("exp", 0)) <= time.time():
        return None
    return str(doc.get("v", ""))


def _lookup_revoked(path: Path, jti: str) -> bool:
    try:
        doc = json.loads(path.read_text())
    except Exception:
        return False
    exp = doc.get(jti)
    return bool(exp and float(exp) > time.time())


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Selection: one place that decides, one place that can say why
# --------------------------------------------------------------------------
class _MemoryAdapter:
    """The default: this process only. Not shared, and it says so where it matters
    (`/healthz`, the security console) rather than looking like a backend."""

    name = "memory"
    shared = False
    durable = False

    def __init__(self):
        self._kv: dict[str, tuple[str, float]] = {}
        self._events: dict[str, tuple[int, list[dict], float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            v = self._kv.get(key)
            if not v or v[1] <= time.time():
                return None
            return v[0]

    def incr(self, key: str, ttl: float) -> int:
        with self._lock:
            now = time.time()
            cur = self._kv.get(key)
            n = int(cur[0]) + 1 if cur and cur[1] > now else 1
            self._kv[key] = (str(n), now + ttl)
            return n

    def claim_once(self, key: str, ttl: float) -> bool:
        with self._lock:
            now = time.time()
            k = f"once:{key}"
            cur = self._kv.get(k)
            if cur and cur[1] > now:
                return False
            self._kv[k] = ("1", now + max(1.0, ttl))
            return True

    def set_ttl(self, key: str, value: str, ttl: float) -> None:
        with self._lock:
            self._kv[key] = (str(value), time.time() + max(1.0, ttl))

    def ttl_left(self, key: str) -> int:
        with self._lock:
            cur = self._kv.get(key)
            if not cur:
                return 0
            rem = cur[1] - time.time()
            return int(rem + 0.999) if rem > 0 else 0

    def delete(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._kv.pop(k, None)

    def publish_event(self, tenant_id: str, event: dict, max_len: int = 256, ttl: float = 3600.0) -> dict:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        keep = max(16, int(max_len))
        with self._lock:
            now = time.time()
            cur = self._events.get(tenant_id)
            if not cur or cur[2] <= now:
                seq, ring = 0, []
            else:
                seq, ring = cur[0], cur[1]
            seq += 1
            item = {**event, "id": seq, "tid": tenant_id, "ts": float(event.get("ts") or now)}
            ring = (ring + [item])[-keep:]
            self._events[tenant_id] = (seq, ring, now + max(1.0, ttl))
            return dict(item)

    def events_since(self, tenant_id: str, since_id: int = 0, limit: int = 100) -> list[dict]:
        if not tenant_id:
            return []
        with self._lock:
            cur = self._events.get(tenant_id)
            if not cur or cur[2] <= time.time() or cur[0] <= int(since_id):
                return []
            return [
                dict(x)
                for x in cur[1]
                if x.get("tid") == tenant_id and int(x.get("id") or 0) > int(since_id)
            ][: max(1, int(limit))]

    def info(self) -> dict:
        return {"backend": "memory", "shared": False, "durable": False,
                "note": "per-process: N instances multiply every limit by N"}


LOCK = threading.Lock()


def _select():
    """(adapter, how) — raises `SharedStateError` when a configured backend fails."""
    mode = REQUESTED
    if not mode:
        if REDIS_URL:
            mode = "redis"
        elif SHARED_DIR:
            mode = "file"
        else:
            mode = "memory"
    if mode == "redis":
        if not REDIS_URL:
            raise SharedStateError("REDIS_NOT_CONFIGURED",
                                   "RE_SHARED_STATE=redis but RE_REDIS_URL is empty")
        return Redis(REDIS_URL), "RE_REDIS_URL"
    if mode == "file":
        if not SHARED_DIR:
            raise SharedStateError("SHARED_DIR_NOT_CONFIGURED",
                                   "RE_SHARED_STATE=file but RE_SHARED_DIR is empty")
        return SharedFiles(SHARED_DIR), "RE_SHARED_DIR"
    if mode != "memory":
        raise SharedStateError("SHARED_STATE_UNKNOWN",
                               f"RE_SHARED_STATE={mode!r} — use memory, redis or file")
    return _MemoryAdapter(), "default"


ADAPTER = None
DECIDED_BY = "default"
ERROR = ""

try:
    ADAPTER, DECIDED_BY = _select()
except SharedStateError as exc:
    if cfg.PRODUCTION:
        # not a warning: a production process that cannot share its security state
        # must not start quietly with per-instance limits
        raise RuntimeError(f"refusing to start: {exc.code}: {exc.message}")
    ERROR = f"{exc.code}: {exc.message}"
    WARNINGS.append(ERROR + " — falling back to per-process state; PII-adjacent "
                            "limits are now multiplied by the number of instances")
    ADAPTER, DECIDED_BY = _MemoryAdapter(), "fallback"

NAME = getattr(ADAPTER, "name", "memory")
SHARED = NAME in ("redis", "file")


def describe() -> dict:
    """What `/healthz` and the security console are allowed to say."""
    info = {}
    try:
        info = ADAPTER.info() or {}
    except Exception as exc:      # a broken backend must not break the health check
        info = {"error": str(exc)}
    return {
        "backend": NAME,
        "shared": SHARED,
        "decided_by": DECIDED_BY,
        "requested": REQUESTED or "auto",
        "error": ERROR,
        "warnings": WARNINGS,
        "info": info,
        "claims": {
            "shared_between_instances": SHARED,
            "realtime_fanout": SHARED,
            "survives_restart": NAME == "redis",
            "one_host_only": NAME == "file",
            "tls": False if NAME == "redis" else None,
        },
    }
