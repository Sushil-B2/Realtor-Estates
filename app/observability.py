"""
Structured Observability, Request Trace Spans, Per-Route Latency Histograms &
Incident Alerting (`PLANS.md` W13 · `docs/41`).

Bounded in-memory telemetry (`deque(maxlen=1000)`) with zero external
dependencies, zero PII/secret capture, W3C-compatible 32-hex `trace_id`
correlation (`X-Trace-Id`), per-route `p50`/`p95`/`p99` millisecond histograms,
Prometheus/OpenMetrics text formatting, and live SRE alerting wired to
`docs/09` §4 cost drivers, dead-letter runs, and DPDP 30-day grievance SLAs.
"""
from __future__ import annotations

from collections import deque
import base64
import os
import statistics
import threading
import time
from typing import Any

from . import automations as autm
from . import compliance as comp_mod
from . import meters as ut


class TelemetryRegistry:
    def __init__(self, max_spans: int = 1000):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._spans: deque[dict[str, Any]] = deque(maxlen=max_spans)
        self._routes: dict[str, dict[str, Any]] = {}
        self._total_requests = 0
        self._total_errors_5xx = 0
        self._total_4xx = 0

    def new_trace_id(self) -> str:
        return "trc_" + base64.urlsafe_b64encode(os.urandom(12)).decode("ascii").rstrip("=")

    def record_span(
        self,
        *,
        trace_id: str,
        method: str,
        route_key: str,
        status: int,
        duration_ms: float,
        tenant_id: str = "",
    ) -> None:
        dur = round(max(0.0, float(duration_ms)), 3)
        st = int(status)
        key = f"{method.upper()} {route_key}"
        span = {
            "trace_id": trace_id[:32],
            "method": method.upper(),
            "route": route_key,
            "status": st,
            "duration_ms": dur,
            "tenant_id": tenant_id,
            "at": int(time.time()),
        }
        with self._lock:
            self._spans.append(span)
            self._total_requests += 1
            if st >= 500:
                self._total_errors_5xx += 1
            elif st >= 400:
                self._total_4xx += 1
            bucket = self._routes.get(key)
            if bucket is None:
                bucket = {"count": 0, "errors_5xx": 0, "errors_4xx": 0, "samples_ms": deque(maxlen=250)}
                self._routes[key] = bucket
            bucket["count"] += 1
            if st >= 500:
                bucket["errors_5xx"] += 1
            elif st >= 400:
                bucket["errors_4xx"] += 1
            bucket["samples_ms"].append(dur)

    def snapshot(self, *, tenant_id: str = "") -> dict[str, Any]:
        with self._lock:
            uptime_sec = max(1, int(time.time() - self._started_at))
            total = self._total_requests
            err_5xx = self._total_errors_5xx
            err_4xx = self._total_4xx
            spans = list(self._spans)
            route_rows: list[dict[str, Any]] = []
            all_samples: list[float] = []
            for key, b in sorted(self._routes.items(), key=lambda kv: -kv[1]["count"]):
                samples = sorted(b["samples_ms"])
                all_samples.extend(samples)
                p50 = round(statistics.median(samples), 2) if samples else 0.0
                p95 = round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 2) if samples else 0.0
                p99 = round(samples[min(len(samples) - 1, int(len(samples) * 0.99))], 2) if samples else 0.0
                route_rows.append({
                    "route": key,
                    "count": b["count"],
                    "errors_4xx": b["errors_4xx"],
                    "errors_5xx": b["errors_5xx"],
                    "p50_ms": p50,
                    "p95_ms": p95,
                    "p99_ms": p99,
                })

        all_sorted = sorted(all_samples)
        global_p50 = round(statistics.median(all_sorted), 2) if all_sorted else 0.0
        global_p95 = round(all_sorted[min(len(all_sorted) - 1, int(len(all_sorted) * 0.95))], 2) if all_sorted else 0.0
        recent_spans = [
            s for s in reversed(spans)
            if not tenant_id or s.get("tenant_id") in ("", tenant_id)
        ][:25]

        return {
            "uptime_sec": uptime_sec,
            "total_requests": total,
            "errors_4xx": err_4xx,
            "errors_5xx": err_5xx,
            "error_rate_5xx": round(err_5xx / max(1, total), 5),
            "latency_p50_ms": global_p50,
            "latency_p95_ms": global_p95,
            "routes": route_rows[:40],
            "recent_traces": recent_spans,
        }

    def prometheus_text(self) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP re_http_requests_total Total HTTP API requests processed.",
            "# TYPE re_http_requests_total counter",
            f"re_http_requests_total {snap['total_requests']}",
            "# HELP re_http_errors_5xx_total Total HTTP 5xx server errors.",
            "# TYPE re_http_errors_5xx_total counter",
            f"re_http_errors_5xx_total {snap['errors_5xx']}",
            "# HELP re_http_latency_p95_ms Global HTTP latency p95 in milliseconds.",
            "# TYPE re_http_latency_p95_ms gauge",
            f"re_http_latency_p95_ms {snap['latency_p95_ms']}",
            "# HELP re_process_uptime_seconds Process uptime in seconds.",
            "# TYPE re_process_uptime_seconds gauge",
            f"re_process_uptime_seconds {snap['uptime_sec']}",
        ]
        for r in snap["routes"][:30]:
            safe = r["route"].replace('"', "")
            lines.append(f're_route_requests_total{{route="{safe}"}} {r["count"]}')
            lines.append(f're_route_latency_p95_ms{{route="{safe}"}} {r["p95_ms"]}')
        return "\n".join(lines) + "\n"


REGISTRY = TelemetryRegistry()


def evaluate_alerts(store, tenant: dict) -> dict[str, Any]:
    """Evaluate SRE, Cost-Driver (`docs/09` §4), DLQ, and DPDP SLA alerts for `tenant`."""
    tid = tenant["id"]
    snap = REGISTRY.snapshot(tenant_id=tid)
    cd = ut.cost_drivers_report(store, tenant)
    dlq = autm.dead_letters(store, tid)
    grievances = [comp_mod.format_grievance(g) for g in store.all("grievances", tid)]
    overdue_grievances = [g for g in grievances if g.get("overdue")]

    alerts: list[dict[str, Any]] = []
    if snap["error_rate_5xx"] > 0.02 and snap["total_requests"] >= 10:
        alerts.append({
            "id": "sre_5xx_rate",
            "severity": "critical",
            "title": "Elevated HTTP 5xx error rate",
            "detail": f"5xx error rate is {round(snap['error_rate_5xx'] * 100, 2)}% (> 2% threshold).",
            "runbook": "docs/07-COMPLIANCE.md §7",
        })
    if dlq:
        alerts.append({
            "id": "automation_dlq_nonempty",
            "severity": "high",
            "title": f"{len(dlq)} dead-lettered automation run(s)",
            "detail": "Inspect and replay or dismiss dead-letter runs in /#/automation.",
            "runbook": "docs/40-COMPLIANCE-RELIABILITY-AND-BILLING.md §2",
        })
    if overdue_grievances:
        alerts.append({
            "id": "dpdp_grievance_sla_breach",
            "severity": "critical",
            "title": f"{len(overdue_grievances)} DPDP grievance ticket(s) past 30-day statutory SLA",
            "detail": "Immediate Grievance Officer resolution required under DPDP §8(9).",
            "runbook": "docs/07-COMPLIANCE.md §1.2",
        })
    for drv in cd["drivers"]:
        if drv["status"] in ("critical", "watch"):
            alerts.append({
                "id": f"cost_driver_{drv['id']}",
                "severity": "high" if drv["status"] == "critical" else "medium",
                "title": f"{drv['label']}: {drv['value']} {drv['unit']}",
                "detail": f"Target {drv['target']} {drv['unit']} (status: {drv['status']}).",
                "runbook": "docs/09-ROADMAP-AND-ECONOMICS.md §4",
            })

    return {
        "evaluated_at": int(time.time()),
        "status": "firing" if any(a["severity"] in ("critical", "high") for a in alerts) else ("watch" if alerts else "healthy"),
        "alert_count": len(alerts),
        "alerts": alerts,
    }
