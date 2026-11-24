"""
Cross-instance Realtime Hub for `/api/v1/events` (SSE).

Replaces the per-connection `STORE.all(...)` polling loop in `app/server.py`
with a tenant-partitioned multiplexer backed by `app/shared_state.py`:

* Every mutation that changes an operator's live rail or inbox publishes a
  compact, PII-free envelope (`D14`: IDs, event kind, channel, counts — never
  names, phone numbers, message bodies, or raw IPs) into the tenant's bounded
  event ring (`Redis` RESP2 `INCR` + `RPUSH`/`LTRIM`, `SharedFiles` `flock`
  ring with `stat()` fast-path, or `_MemoryAdapter`).
* Local SSE subscribers on the publishing instance receive the frame in memory
  immediately (`<1 ms`), with zero database or disk reads per socket.
* One lightweight background relay thread per server process polls active
  tenants only (`len(subscribers[tid]) > 0`) every `RELAY_INTERVAL_SEC`,
  delivers remote-instance events to all local SSE queues for that tenant, and
  notifies registered store-invalidation callbacks so subsequent `GET /inbox`
  reads refresh from Postgres.
* Reconnecting `EventSource` clients pass `Last-Event-ID` (or `?since=`) and
  replay missed frames from the shared ring without cross-tenant visibility.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from typing import Any, Callable

from . import security as sec
from . import shared_state as shared

RELAY_INTERVAL_SEC = float(os.environ.get("RE_REALTIME_POLL_MS", "100") or 100) / 1000.0
QUEUE_MAX = 128
RING_MAX = 256
RING_TTL = 3600.0


def _sanitize_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Keep event metadata compact and PII/secret-free (`D14`)."""
    if not isinstance(meta, dict):
        return {}
    clean: dict[str, Any] = {}
    for k, v in list(meta.items())[:12]:
        key = str(k)[:32]
        if isinstance(v, (bool, int, float)) or v is None:
            clean[key] = v
        elif isinstance(v, str):
            clean[key] = sec.redact_secrets(v)[:80]
    return clean


class Subscription:
    """One open SSE client's tenant-locked queue."""

    def __init__(self, hub: "RealtimeHub", tenant_id: str, q: "queue.Queue[dict[str, Any]]"):
        self._hub = hub
        self.tenant_id = tenant_id
        self._q = q
        self._closed = False

    def get(self, timeout: float = 4.0) -> dict[str, Any] | None:
        while not self._closed:
            try:
                ev = self._q.get(timeout=max(0.01, timeout))
            except queue.Empty:
                return None
            # Belt-and-suspenders tenant fence: drop anything not stamped for this workspace.
            if isinstance(ev, dict) and ev.get("tid") == self.tenant_id:
                return ev
        return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._hub._unsubscribe(self.tenant_id, self._q)

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class RealtimeHub:
    """Tenant-partitioned SSE multiplexer with cross-process relay."""

    def __init__(self, adapter: Any = None, relay_interval: float = RELAY_INTERVAL_SEC):
        self._adapter = adapter
        self.relay_interval = max(0.02, float(relay_interval))
        self.instance_id = f"inst_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self._lock = threading.Lock()
        self._subs: dict[str, set[queue.Queue[dict[str, Any]]]] = {}
        self._cursors: dict[str, int] = {}
        self._hooks: list[Callable[[str, dict[str, Any]], None]] = []
        self._published = 0
        self._relayed = 0
        self._stop = threading.Event()
        self._relay_thread: threading.Thread | None = None

    def _backend(self) -> Any:
        return self._adapter if self._adapter is not None else shared.ADAPTER

    def on_remote_event(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        """Register a callback `(tenant_id, event)` invoked when another node publishes."""
        with self._lock:
            self._hooks.append(fn)

    def _ensure_relay(self) -> None:
        with self._lock:
            if self._relay_thread is not None and self._relay_thread.is_alive():
                return
            t = threading.Thread(target=self._relay_loop, name=f"realtime-relay-{self.instance_id}", daemon=True)
            self._relay_thread = t
            t.start()

    def _push_local(self, tenant_id: str, ev: dict[str, Any]) -> int:
        if ev.get("tid") != tenant_id:
            return 0
        with self._lock:
            targets = list(self._subs.get(tenant_id) or ())
        delivered = 0
        for q in targets:
            try:
                q.put_nowait(ev)
                delivered += 1
            except queue.Full:
                # Slow client (e.g. stalled 2G radio): drop its oldest frame so
                # the publisher and healthy peers never block.
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(ev)
                    delivered += 1
                except queue.Full:
                    pass
        return delivered

    def publish(
        self,
        tenant_id: str,
        event_type: str,
        target: str = "",
        meta: dict[str, Any] | None = None,
        collections: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        """Publish one tenant-scoped event locally and into the shared ring."""
        if not tenant_id:
            return {}
        raw = {
            "type": str(event_type)[:48],
            "target": str(target)[:80],
            "meta": _sanitize_meta(meta),
            "collections": [str(c)[:32] for c in collections][:8],
            "origin": self.instance_id,
            "ts": time.time(),
        }
        backend = self._backend()
        try:
            ev = backend.publish_event(tenant_id, raw, max_len=RING_MAX, ttl=RING_TTL)
        except Exception:
            # Degrade to local fan-out rather than failing the business write.
            ev = {**raw, "id": int(time.time() * 1000), "tid": tenant_id}
        with self._lock:
            self._published += 1
        self._push_local(tenant_id, ev)
        return ev

    def events_since(self, tenant_id: str, since_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if not tenant_id:
            return []
        try:
            rows = self._backend().events_since(tenant_id, int(since_id), limit=limit)
        except Exception:
            return []
        return [r for r in rows if isinstance(r, dict) and r.get("tid") == tenant_id]

    def subscribe(self, tenant_id: str, since_id: int = 0) -> Subscription:
        """Open a tenant-scoped SSE subscription, optionally replaying `> since_id`."""
        if not tenant_id:
            raise ValueError("tenant_id is required")
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=QUEUE_MAX)

        need_init = False
        with self._lock:
            if tenant_id not in self._cursors:
                need_init = True
        if need_init:
            tail = self.events_since(tenant_id, 0, limit=RING_MAX)
            latest = int(tail[-1]["id"]) if tail else 0
            with self._lock:
                self._cursors.setdefault(tenant_id, latest)

        if since_id > 0:
            for missed in self.events_since(tenant_id, since_id, limit=QUEUE_MAX):
                try:
                    q.put_nowait(missed)
                except queue.Full:
                    break

        with self._lock:
            self._subs.setdefault(tenant_id, set()).add(q)
        self._ensure_relay()
        return Subscription(self, tenant_id, q)

    def _unsubscribe(self, tenant_id: str, q: "queue.Queue[dict[str, Any]]") -> None:
        with self._lock:
            bucket = self._subs.get(tenant_id)
            if bucket is not None:
                bucket.discard(q)
                if not bucket:
                    self._subs.pop(tenant_id, None)

    def _poll_once(self) -> int:
        """Poll active tenant rings once and fan out remote events. Returns count relayed."""
        with self._lock:
            active = [(tid, self._cursors.get(tid, 0)) for tid, qs in self._subs.items() if qs]
            hooks = list(self._hooks)
        if not active:
            return 0
        relayed_now = 0
        for tid, cur in active:
            events = self.events_since(tid, cur, limit=100)
            if not events:
                continue
            max_id = cur
            for ev in events:
                eid = int(ev.get("id") or 0)
                if eid > max_id:
                    max_id = eid
                if ev.get("origin") == self.instance_id:
                    # Already pushed locally inside `publish()`.
                    continue
                for hook in hooks:
                    try:
                        hook(tid, ev)
                    except Exception:
                        pass
                self._push_local(tid, ev)
                relayed_now += 1
            with self._lock:
                if max_id > self._cursors.get(tid, 0):
                    self._cursors[tid] = max_id
                self._relayed += relayed_now
        return relayed_now

    def _relay_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception:
                pass
            self._stop.wait(self.relay_interval)

    def close(self) -> None:
        self._stop.set()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            conns = sum(len(qs) for qs in self._subs.values())
            tenants = sum(1 for qs in self._subs.values() if qs)
            published = self._published
            relayed = self._relayed
        backend = self._backend()
        bname = getattr(backend, "name", "memory")
        return {
            "instance_id": self.instance_id,
            "backend": bname,
            "shared": bname in ("redis", "file"),
            "active_connections": conns,
            "active_tenants": tenants,
            "published": published,
            "relayed": relayed,
        }


HUB = RealtimeHub()
